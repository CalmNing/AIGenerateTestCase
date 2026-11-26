"""通用 AI 对话 API

提供通用 AI 对话能力，支持业务模式（需求澄清 / 用例优化）、业务上下文注入、
MCP 工具调用和 Skills 方法论注入。
"""
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps import CurrentUser, SessionDep
from app.services.chat_prompts import (
    MCP_TOOL_HEADER,
    MODE_PROMPTS,
    REQUIREMENT_LIST_TEMPLATE,
    SKILLS_HEADER,
    VALID_MODES,
)
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# 业务上下文注入上限：超长需求文档等场景防止撑爆请求
_CONTEXT_LIMIT = 12000
_CONTEXT_HEAD_KEEP = 8000
_CONTEXT_TAIL_KEEP = 3000
_CONTEXT_ELLIPSIS = "\n……（内容过长已截断）……\n"

# 业务上下文注入小节标题
_CONTEXT_SECTION_TITLE = "## 当前业务上下文"


class ChatMessage(BaseModel):
    role: str  # "user" 或 "assistant"
    content: str
    # 用户消息附带的截图（data URL，data:image/png;base64,...）。仅 user 角色有效；
    # 有图时 content 构造为多模态 blocks 交给模型。上限单张 2MB（与文档解析一致）。
    images: Optional[list[str]] = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    selected_skills: Optional[list[str]] = None
    # 会话标识：同一 client_session_id 的多轮对话共享一条 LangGraph thread，
    # 从而支持服务端记忆（前端目前每次都回传完整 messages，传了这个字段即可受益）。
    client_session_id: Optional[str] = None
    # 对话模式：general（默认）/ requirement_clarify（需求澄清）/ testcase_optimize（用例优化）；
    # 非法值回退 general，向后兼容旧客户端。
    mode: Optional[str] = None
    # 业务上下文（需求正文、用例清单摘要等），注入 system prompt
    context: Optional[str] = None
    # 测试用例会话 ID：仅 testcase_optimize 模式使用。携带时挂载用例查询/新增/更新/删除
    # 工具（权限在工具内部自检）；旧客户端不传该字段，行为不变（向后兼容）。
    testcase_session_id: Optional[int] = None
    # 当前模块筛选：create_testcase 落库归属 + update 防移出当前筛选；全部模块视图不传。
    testcase_module_id: Optional[int] = None


class StreamingChatRequest(ChatRequest):
    # 是否开启 DeepSeek thinking（展示思考过程）；仅流式端点支持，默认开启
    show_thinking: bool = True


def _normalize_mode(mode: Optional[str]) -> str:
    """规范化对话模式；None/未知值回退 general。"""
    if mode and mode in VALID_MODES:
        return mode
    return "general"


def _truncate_context(context: str) -> str:
    """超长上下文截断：保留头尾，中间以省略标记衔接。"""
    if len(context) <= _CONTEXT_LIMIT:
        return context
    return context[:_CONTEXT_HEAD_KEEP] + _CONTEXT_ELLIPSIS + context[-_CONTEXT_TAIL_KEEP:]


def _user_content_blocks(msg) -> Any:
    """用户消息转 LangChain content：带截图时构造多模态 blocks，否则纯文本。

    images 是 data URL 列表（data:image/png;base64,...）；单张超 2MB 或非 data:
    前缀的忽略（前端已限制，这里兜底）。截图让模型能看到页面实况，配合
    browse_page 的元素摘要做「用例 vs 页面实际」比对。
    """
    images = getattr(msg, "images", None) or []
    if msg.role != "user" or not images:
        return msg.content
    blocks: list = [{"type": "text", "text": msg.content or "（见图）"}]
    for data_url in images:
        if isinstance(data_url, str) and data_url.startswith("data:image/") and len(data_url) <= 3 * 1024 * 1024:
            blocks.append({"type": "image_url", "image_url": {"url": data_url}})
    return blocks if len(blocks) > 1 else msg.content
async def _budgeted_history(
    req,
    system_content: str,
    chat_max_tokens: int,
    api_key: str,
    api_base_url: str,
    api_model: str,
) -> list["ChatMessage"]:
    """按 token 预算裁剪对话历史（轻量 compaction + LLM checkpoint）。

    三层策略：
    1. 长消息修剪：>8K 字符保头尾（无 LLM）。
    2. 预算裁剪：从最旧丢弃，至少保最后 2 条（无 LLM）。
    3. checkpoint 压缩：被丢弃的内容 >= 4096 token 时，用一次低预算 LLM 调用
       把旧对话总结成结构化检查点，插在保留消息之前 —— 丢掉的语义找回来了；
       总结失败降级为直接丢弃，不影响主流程。

    预算 = 模型窗口 - 输出预留(max_tokens) - system prompt - headroom(8192)。
    """
    from utils.context_budget import (
        build_history_budget,
        estimate_tokens,
        resolve_model_window,
        should_compact,
        summarize_dropped_messages,
        trim_history,
        wrap_checkpoint,
    )

    window = resolve_model_window("", api_model)
    system_tokens = estimate_tokens(system_content)
    budget = build_history_budget(window, chat_max_tokens, system_tokens)

    items = [
        {"role": m.role, "content": m.content, "images": list(getattr(m, "images", None) or [])}
        for m in req.messages
    ]
    kept, stats = trim_history(items, budget)

    # checkpoint 压缩：丢得多才值得一次额外 LLM 调用
    checkpoint_message = None
    if should_compact(stats["dropped_items"], stats["dropped_tokens"]):
        summary = await summarize_dropped_messages(
            stats["dropped_items"], api_key, api_base_url, api_model,
        )
        if summary:
            checkpoint_message = wrap_checkpoint(summary)
            logger.info(
                "checkpoint 压缩完成: 摘要 %d 字符替换 %d 条旧消息（~%d token）",
                len(summary), stats["dropped"], stats["dropped_tokens"],
            )

    if stats["dropped"] or stats["pruned"] or checkpoint_message:
        logger.info(
            "历史预算裁剪: 窗口=%d 预算=%d system_tokens=%d 原始=%d 保留=%d 丢弃=%d 修剪=%d checkpoint=%s",
            window, budget, system_tokens, len(req.messages), stats["kept"],
            stats["dropped"], stats["pruned"], "有" if checkpoint_message else "无",
        )

    messages: list[ChatMessage] = []
    if checkpoint_message:
        # checkpoint 计入预算之外的安全垫（约几百 token，headroom 已覆盖）
        messages.append(ChatMessage(role=checkpoint_message["role"], content=checkpoint_message["content"]))
    messages.extend(
        ChatMessage(
            role=item["role"],
            content=item["content"],
            images=item.get("images") or None,
        )
        for item in kept
    )
    return messages


def _build_chat_system_prompt(
    selected_skills: list[str] | None = None,
    has_tools: bool = False,
    mode: str = "general",
    context: str | None = None,
    has_testcase_tools: bool = False,
) -> str:
    """构建 system prompt：模式提示词 + Skills 方法论 + 工具说明 + 业务上下文。"""
    from utils.helpers import _load_skill_bodies

    from app.services.chat_prompts import TESTCASE_TOOLS_HEADER

    system_content = MODE_PROMPTS[_normalize_mode(mode)]

    # 注入 Skills 方法论
    if selected_skills:
        skill_bodies = _load_skill_bodies(selected_skills)
        if skill_bodies:
            system_content += SKILLS_HEADER + skill_bodies

    # 注入 MCP 工具说明
    if has_tools:
        system_content += MCP_TOOL_HEADER

    # 注入用例操作工具说明（testcase_optimize 模式）
    if has_testcase_tools:
        system_content += TESTCASE_TOOLS_HEADER

    # 注入需求清单模板（仅需求澄清模式，供"整理需求清单"时按骨架输出）
    if _normalize_mode(mode) == "requirement_clarify":
        system_content += "\n\n" + REQUIREMENT_LIST_TEMPLATE

    # 注入业务上下文
    trimmed = (context or "").strip()
    if trimmed:
        system_content += (
            f"\n\n{_CONTEXT_SECTION_TITLE}\n\n{_truncate_context(trimmed)}\n"
        )

    return system_content


def _resolve_chat_model_config(session) -> "tuple[str, str, str]":
    """解析对话模型配置：系统配置（设置页）优先，api_key 为空时回退 data/config.json。

    与需求文档解析链路（requirement_doc._get_effective_model_config）同源：
    设置页全局行（user_id IS NULL）的 api_key 为空时，使用 config.json 的配置，
    避免只配置了 config.json 的部署环境对话接口 500。

    返回 (api_key, api_base_url, api_model)；两处都未配置时 api_key 为空。
    """
    from config import config_manager

    api_key = ""
    api_base_url = "https://api.deepseek.com"
    api_model = "deepseek-v4-flash"

    try:
        from app.services.system_config import get_shared_system_config
        db_config = get_shared_system_config(session)
    except Exception as e:
        logger.error("获取配置失败: %s", e)
        db_config = None

    if db_config and (db_config.api_key or "").strip():
        api_key = (db_config.api_key or "").strip()
        api_base_url = db_config.api_base_url or api_base_url
        api_model = db_config.api_model or api_model
    else:
        api_key = (config_manager.get_api_key() or "").strip()
        api_base_url = config_manager.get_api_base_url() or api_base_url
        api_model = config_manager.get_api_model() or api_model

    return api_key, api_base_url, api_model


def _load_user_mcp_configs(session, current_user) -> list[dict]:
    """加载用户配置的 MCP 服务器列表。"""
    try:
        from app.services.mcp_loader import load_user_mcp_configs
        return load_user_mcp_configs(session, current_user)
    except Exception as e:
        logger.warning("加载 MCP 配置失败: %s", e)
        return []


def _load_testcase_tools(
    mode: str,
    testcase_session_id: Optional[int],
    current_user,
    testcase_module_id: Optional[int] = None,
) -> list:
    """加载 AI 用例对话写操作工具（查询/新增/更新/删除）；仅 testcase_optimize 模式。

    与 MCP 工具相互独立：这里失败只降级为无写工具，不影响对话主流程。
    """
    if mode != "testcase_optimize" or not testcase_session_id:
        return []
    try:
        from app.services.testcase_chat_tools import load_testcase_write_tools
        write_tools = load_testcase_write_tools(mode, testcase_session_id, current_user, testcase_module_id)
    except Exception as e:
        logger.warning("加载用例操作工具失败: %s", e)
        write_tools = []
    try:
        from app.services.page_insight_tools import load_page_insight_tools
        insight_tools = load_page_insight_tools(mode, testcase_session_id, current_user)
    except Exception as e:
        logger.warning("加载页面洞察工具失败: %s", e)
        insight_tools = []
    return write_tools + insight_tools


async def _load_chat_tools(session, current_user, mode: str) -> list:
    """加载对话可用的 MCP 工具；仅通用模式加载。

    业务模式（requirement_clarify / testcase_optimize）的需求正文 / 用例清单
    已作为上下文注入，不需要工具取数能力：跳过加载可省掉 STDIO 子进程启动
    与大量工具 schema 的 token 负担，并走更快的直连 LLM 流式路径。
    """
    if mode != "general":
        logger.info("业务模式 %s 跳过 MCP 工具加载（上下文已注入，走直连流式）", mode)
        return []

    mcp_configs = _load_user_mcp_configs(session, current_user)
    if not mcp_configs:
        return []
    try:
        from utils.lanhu_mcp_adapter import build_tools_from_configs
        tools = await build_tools_from_configs(mcp_configs)
        if tools:
            logger.info("已加载 %d 个 MCP 工具", len(tools))
        return tools
    except Exception as e:
        logger.warning("加载 MCP 工具失败: %s", e)
        return []


def _extract_reply(response) -> str:
    """从 LLM 响应中提取文本回复。"""
    raw_content = response.content if hasattr(response, "content") else str(response)
    if isinstance(raw_content, list):
        texts = []
        for item in raw_content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts) if texts else str(raw_content)
    return str(raw_content)


# ===== 流式对话（SSE） =====

# SSE 事件类型：
#   reasoning — DeepSeek thinking 增量（reasoning_content delta）
#   content   — 正文增量
#   tool      — Agent 工具调用开始（仅 Agent 模式）
#   error     — 失败（message 为人类可读错误）
#   done      — 终止标记
_SSE_REASONING_EVENT = "reasoning"
_SSE_CONTENT_EVENT = "content"
_SSE_TOOL_EVENT = "tool"
_SSE_ERROR_EVENT = "error"
_SSE_DONE_EVENT = "done"


def _sse_event(event: str, data: str) -> str:
    """构造一条 SSE 帧（data 内换行按 SSE 规范拆成多行）。"""
    lines = [f"data: {line}" for line in str(data).split("\n")]
    return f"event: {event}\n" + "\n".join(lines) + "\n\n"


def _sse_chunk(response, content_key: str) -> str:
    """从一条流式响应 chunk 中提取增量文本。

    content_key 取值：
    - "content"：正文增量。langchain 的正文在 chunk.content（str 或多模态 block 列表），
      不在 additional_kwargs 里。
    - "reasoning_content"：DeepSeek thinking 增量，位于 additional_kwargs.reasoning_content。
    """
    if content_key == "content":
        response_extra = getattr(response, "additional_kwargs", None)
        if not hasattr(response, "content") and response_extra is not None:
            # 兜底：对象没有 content 属性时按 kwargs 取（测试替身/边缘实现）
            return response_extra.get("content", "") or ""
        raw = getattr(response, "content", None)
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            # 多模态 block 列表：拼接其中的 text 块
            parts = []
            for item in raw:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        return ""

    extra = getattr(response, "additional_kwargs", None) or {}
    delta = extra.get(content_key, "")
    return delta or ""


async def _stream_plain_model_http(
    api_key: str,
    api_base_url: str,
    api_model: str,
    system_content: str,
    history_messages: list["ChatMessage"],
    max_tokens: int,
    show_thinking: bool,
    request_id: str,
):
    """无工具路径：绕过 langchain，直连 OpenAI 兼容接口流式输出。

    langchain_openai 的流式实现会丢弃 DeepSeek 的 reasoning_content 增量
    （实测 additional_kwargs 恒为空），所以这里用 httpx 直接解析 SSE：
    delta.reasoning_content → reasoning 事件，delta.content → content 事件。
    """
    import httpx

    url = api_base_url.rstrip("/") + "/chat/completions"
    messages = [{"role": "system", "content": system_content}]
    for msg in history_messages:
        images = getattr(msg, "images", None)
        if msg.role == "user" and images:
            # 多模态消息：文本 + 截图（image_url blocks，OpenAI 兼容格式）
            blocks: list = [{"type": "text", "text": msg.content or "（见图）"}]
            for data_url in images:
                blocks.append({"type": "image_url", "image_url": {"url": data_url}})
            messages.append({"role": "user", "content": blocks})
        else:
            messages.append({"role": msg.role, "content": msg.content})

    body = {
        "model": api_model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "thinking": {"type": "enabled" if show_thinking else "disabled"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30.0)) as client:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"上游 API {resp.status_code}: {detail}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                import json as _json

                try:
                    obj = _json.loads(payload)
                except Exception:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                reasoning = delta.get("reasoning_content") or ""
                if reasoning:
                    yield _sse_event(_SSE_REASONING_EVENT, reasoning)
                content = delta.get("content") or ""
                if content:
                    yield _sse_event(_SSE_CONTENT_EVENT, content)
                finish_reason = choices[0].get("finish_reason") or ""
                if finish_reason == "length":
                    # 达到 max_tokens 被截断：发 error 事件让前端显式提示
                    yield _sse_event(
                        _SSE_ERROR_EVENT,
                        "回复达到最大输出长度被截断，可发送“继续”让我接着写完",
                    )
                    break
    yield _sse_event(_SSE_DONE_EVENT, request_id)


async def _stream_plain_model(model, langchain_messages, request_id: str):
    """无工具路径：langchain 模型流式输出，逐块产出 SSE 帧。

    保留用于测试与非 OpenAI 兼容模型；生产主路径是 _stream_plain_model_http
    （langchain 会丢弃 reasoning_content）。
    """
    async for chunk in model.astream(langchain_messages):
        reasoning = _sse_chunk(chunk, "reasoning_content")
        if reasoning:
            yield _sse_event(_SSE_REASONING_EVENT, reasoning)
        content = _sse_chunk(chunk, "content")
        if content:
            yield _sse_event(_SSE_CONTENT_EVENT, content)
    yield _sse_event(_SSE_DONE_EVENT, request_id)


async def _stream_agent(agent, input_messages, thread_id: str, request_id: str):
    """Agent 模式流式输出：content 为正文增量，tool 为工具调用事件。

    reasoning 增量来自 thinking 模型写入 additional_kwargs 的部分。
    """
    async for event in agent.astream_events(
        {"messages": input_messages},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": 100},
        version="v2",
    ):
        kind = event.get("event")
        if kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk is None:
                continue
            reasoning = _sse_chunk(chunk, "reasoning_content")
            if reasoning:
                yield _sse_event(_SSE_REASONING_EVENT, reasoning)
            content = _sse_chunk(chunk, "content")
            if content:
                yield _sse_event(_SSE_CONTENT_EVENT, content)
        elif kind == "on_tool_start":
            tool_name = event.get("name") or ""
            if tool_name:
                yield _sse_event(_SSE_TOOL_EVENT, tool_name)
    yield _sse_event(_SSE_DONE_EVENT, request_id)


@router.post("")
async def chat(
    req: ChatRequest,
    session: SessionDep = None,
    current_user: CurrentUser = None,
):
    """通用 AI 对话接口

    支持：
    - 多轮对话历史
    - 业务模式（需求澄清 / 用例优化）与业务上下文注入
    - Skills 方法论注入
    - MCP 工具调用（通过用户配置的 MCP 服务器）
    - 飞书/蓝湖文档读取
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="消息不能为空")

    # 解析模型配置：设置页系统配置优先，api_key 为空时回退 data/config.json
    api_key, api_base_url, api_model = _resolve_chat_model_config(session)

    if not api_key:
        raise HTTPException(status_code=500, detail="未配置 API Key")

    # 加载 MCP 工具（业务模式跳过：上下文已注入，无需取数工具）
    mode = _normalize_mode(req.mode)
    all_tools = await _load_chat_tools(session, current_user, mode)

    # 用例操作工具（testcase_optimize 模式且携带会话 ID 时挂载）
    all_tools = all_tools + _load_testcase_tools(mode, req.testcase_session_id, current_user, req.testcase_module_id)

    # 构建 system prompt
    system_content = _build_chat_system_prompt(
        req.selected_skills,
        has_tools=bool(all_tools),
        mode=mode,
        context=req.context,
        has_testcase_tools=bool(req.testcase_session_id) and mode == "testcase_optimize",
    )

    # 长回复（14 节需求清单 / 用例缺口分析）需要充足输出预算，避免 finish_reason=length 截断
    chat_max_tokens = 16384
    # 历史预算裁剪：输入 + 输出必须在模型窗口内（轻量 compaction）
    budgeted_messages = await _budgeted_history(req, system_content, chat_max_tokens, api_key, api_base_url, api_model)

    # 构建消息
    langchain_messages = []
    from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

    langchain_messages.append(SystemMessage(content=system_content))
    for msg in budgeted_messages:
        if msg.role == "user":
            langchain_messages.append(HumanMessage(content=_user_content_blocks(msg)))
        elif msg.role == "assistant":
            langchain_messages.append(AIMessage(content=msg.content))

    try:
        from utils.llm_factory import build_chat_model

        logger.info("创建 LLM 模型: base_url=%s model=%s tools=%d",
                    api_base_url, api_model, len(all_tools))
        model = build_chat_model(
            api_key=api_key,
            api_base_url=api_base_url,
            api_model=api_model,
            max_tokens=chat_max_tokens,
            temperature=0.7,
        )

        if all_tools:
            # 有工具时使用 agent（不强制结构化输出），并启用记忆（checkpointer + store）
            from langchain.agents import create_agent

            from utils.agent_memory import agent_memory_context, build_thread_id

            # 前端未传 client_session_id 时退化为"单次会话"（thread 仅当次有效），
            # 不影响原有行为；传入后即可跨请求续接同一条轨迹。
            # 安全（P1 审查修复）：匿名请求绝不能共享固定 thread——固定 "anonymous" 会让
            # 所有匿名用户共用同一条轨迹（上下文泄漏），且全量 messages 反复追加导致
            # 持久化状态无限膨胀（实测两次请求后翻倍）。改为每次匿名请求一个独立 thread。
            if req.client_session_id:
                thread_id = build_thread_id(
                    scope="chat",
                    user_id=current_user.user_id if current_user else None,
                    session_id=req.client_session_id,
                    module_id=None,
                )
            else:
                thread_id = build_thread_id(
                    scope="chat",
                    user_id=current_user.user_id if current_user else None,
                    session_id=f"ephemeral-{uuid.uuid4().hex[:12]}",
                    module_id=None,
                )
            logger.info("使用 Agent 模式（含 %d 个工具，thread=%s）", len(all_tools), thread_id)

            async with agent_memory_context() as (checkpointer, store):
                agent = create_agent(
                    model=model,
                    tools=all_tools,
                    system_prompt=system_content,
                    checkpointer=checkpointer,
                    store=store,
                )
                # 关键：只有"已存在该 thread 的轨迹"时才像增量对话那样只发新消息。
                # 否则前端每次回传完整 messages，会被 checkpointer 的 add_messages
                # 原样追加，导致历史在持久化状态里无限膨胀（重复）。
                has_history = False
                if req.client_session_id:
                    try:
                        has_history = await checkpointer.aget_tuple(
                            {"configurable": {"thread_id": thread_id}}
                        ) is not None
                    except Exception:  # noqa: BLE001 - 记忆读取失败按无历史处理
                        has_history = False

                if has_history:
                    # system_prompt 已由 create_agent 注入，这里只补最新一条用户消息
                    input_messages = [langchain_messages[-1]]
                    logger.info("命中已有记忆轨迹，仅提交最新一条消息（thread=%s）", thread_id)
                else:
                    input_messages = langchain_messages

                response = await agent.ainvoke(
                    {"messages": input_messages},
                    config={
                        "configurable": {"thread_id": thread_id},
                        "recursion_limit": 100,
                    },
                )
            # agent 返回的是 dict，需要提取最后一条消息
            if isinstance(response, dict) and "messages" in response:
                last_msg = response["messages"][-1]
                reply = _extract_reply(last_msg)
            else:
                reply = _extract_reply(response)
        else:
            # 无工具时直接调用 LLM
            logger.info("使用直接 LLM 调用模式（无工具）")
            response = await model.ainvoke(langchain_messages)
            reply = _extract_reply(response)

        logger.info("AI 对话回复: user=%s reply_len=%d",
                    current_user.user_id if current_user else "unknown", len(reply))

        return Response(data={"reply": reply})

    except HTTPException:
        raise
    except Exception as e:
        logger.error("AI 对话失败: %s", type(e).__name__, exc_info=True)
        error_msg = str(e) if str(e) else type(e).__name__
        raise HTTPException(status_code=500, detail=f"AI 对话失败: {error_msg}")


@router.post("/stream")
async def chat_stream(
    req: StreamingChatRequest,
    session: SessionDep = None,
    current_user: CurrentUser = None,
):
    """流式 AI 对话接口（SSE）。

    事件流：reasoning（思考增量）→ content（正文增量）→ [tool]（工具调用，仅 Agent 模式）→ done。
    失败时发 error 事件再结束，前端把已收到的内容照常展示。

    多轮记忆与 /chat 同源：前端回传历史（无工具路径）+ client_session_id thread（有工具路径）。
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="消息不能为空")

    api_key, api_base_url, api_model = _resolve_chat_model_config(session)
    if not api_key:
        raise HTTPException(status_code=500, detail="未配置 API Key")

    mode = _normalize_mode(req.mode)
    request_id = uuid.uuid4().hex[:12]

    # 与 /chat 相同的准备工作（工具 / prompt / 消息）；业务模式跳过 MCP 加载，
    # 但 testcase_optimize 模式会挂载用例操作工具（走 Agent 路径）。
    all_tools = await _load_chat_tools(session, current_user, mode)
    all_tools = all_tools + _load_testcase_tools(mode, req.testcase_session_id, current_user, req.testcase_module_id)

    system_content = _build_chat_system_prompt(
        req.selected_skills,
        has_tools=bool(all_tools),
        mode=mode,
        context=req.context,
        has_testcase_tools=bool(req.testcase_session_id) and mode == "testcase_optimize",
    )

    from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

    # 需求澄清要输出整份 14 节清单，用例优化常输出长缺口分析：统一给足输出空间，
    # 否则 DeepSeek 默认/低上限会在长回复中途截断（finish_reason=length）。
    # 注意：thinking 开启时 reasoning_tokens 计入 max_tokens 预算（实测），
    # 长思考会把正文挤掉 —— 开思考时再抬高上限。
    chat_max_tokens = 32768 if req.show_thinking else 16384
    # 历史预算裁剪：thinking 开启时输出预留更大，历史预算相应收紧（轻量 compaction）
    budgeted_messages = await _budgeted_history(req, system_content, chat_max_tokens, api_key, api_base_url, api_model)

    langchain_messages = [SystemMessage(content=system_content)]
    for msg in budgeted_messages:
        if msg.role == "user":
            langchain_messages.append(HumanMessage(content=_user_content_blocks(msg)))
        elif msg.role == "assistant":
            langchain_messages.append(AIMessage(content=msg.content))

    async def _event_stream():
        try:
            from utils.llm_factory import build_chat_model

            logger.info("流式对话: base_url=%s model=%s tools=%d thinking=%s",
                        api_base_url, api_model, len(all_tools), req.show_thinking)
            model = build_chat_model(
                api_key=api_key,
                api_base_url=api_base_url,
                api_model=api_model,
                max_tokens=chat_max_tokens,
                temperature=0.7,
                thinking=bool(req.show_thinking),
            )

            if all_tools:
                from langchain.agents import create_agent
                from utils.agent_memory import agent_memory_context, build_thread_id

                if req.client_session_id:
                    thread_id = build_thread_id(
                        scope="chat",
                        user_id=current_user.user_id if current_user else None,
                        session_id=req.client_session_id,
                        module_id=None,
                    )
                else:
                    thread_id = build_thread_id(
                        scope="chat",
                        user_id=current_user.user_id if current_user else None,
                        session_id=f"ephemeral-{uuid.uuid4().hex[:12]}",
                        module_id=None,
                    )

                async with agent_memory_context() as (checkpointer, store):
                    agent = create_agent(
                        model=model,
                        tools=all_tools,
                        system_prompt=system_content,
                        checkpointer=checkpointer,
                        store=store,
                    )
                    has_history = False
                    if req.client_session_id:
                        try:
                            has_history = await checkpointer.aget_tuple(
                                {"configurable": {"thread_id": thread_id}}
                            ) is not None
                        except Exception:  # noqa: BLE001
                            has_history = False

                    input_messages = [langchain_messages[-1]] if has_history else langchain_messages
                    async for frame in _stream_agent(agent, input_messages, thread_id, request_id):
                        yield frame
            else:
                # 直连 HTTP 流式：langchain 会丢弃 reasoning_content，思考过程必须绕过它
                async for frame in _stream_plain_model_http(
                    api_key=api_key,
                    api_base_url=api_base_url,
                    api_model=api_model,
                    system_content=system_content,
                    history_messages=budgeted_messages,
                    max_tokens=chat_max_tokens,
                    show_thinking=bool(req.show_thinking),
                    request_id=request_id,
                ):
                    yield frame

            logger.info("流式对话完成: user=%s", current_user.user_id if current_user else "unknown")
        except Exception as e:
            logger.error("流式对话失败: %s", type(e).__name__, exc_info=True)
            error_msg = str(e) if str(e) else type(e).__name__
            yield _sse_event(_SSE_ERROR_EVENT, error_msg)
            yield _sse_event(_SSE_DONE_EVENT, request_id)

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
