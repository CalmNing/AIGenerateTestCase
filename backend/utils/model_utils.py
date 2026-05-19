import logging
import os
import re
import time
import uuid
from typing import List, Optional, Literal, Any

import yaml
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_deepseek import ChatDeepSeek
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field, SecretStr

from sqlmodel import select, desc

from db.models import TestCase as DBTestCase, HistoryPrompt

try:
    from utils.lanhu_mcp_adapter import build_langchain_tools, build_tools_from_configs
except ImportError:
    build_langchain_tools = None
    build_tools_from_configs = None

# 飞书文档链接正则：匹配 /docx/ 和 /wiki/ 两种链接格式
_FEISHU_URL_RE = re.compile(
    r'https?://[a-zA-Z0-9-]+\.feishu\.cn/(?:docx|wiki)/([A-Za-z0-9_-]+)',
    re.IGNORECASE
)

# 简单的 agent 缓存，key -> agent
_AGENT_CACHE: dict = {}

logger = logging.getLogger(__name__)

# 蓝湖 URL 匹配：提取页面链接，用于预检文档大小
_LANHU_URL_RE = re.compile(
    r'https?://lanhuapp\.com/web/#/item/project/[a-z]+[^"\s]*',
    re.IGNORECASE,
)
_MAX_PREANALYZE_PAGES = 5


async def _check_lanhu_document_size(requirement: str):
    """检查蓝湖文档页数，过多且用户未指定页面时抛出 ValueError。

    返回 (url, all_pages, doc_name, [target_page_names]) 或 None（无蓝湖链接时）。
    """
    import json as _json
    import re as _re
    match = _LANHU_URL_RE.search(requirement)
    if not match:
        return None
    url = match.group(0)
    logger.info(f"检测到蓝湖链接，预检文档大小: url={url[:80]}...")
    try:
        from utils.lanhu_mcp_adapter import get_mcp_client as _get_mcp
        client = await _get_mcp()
        if not client.available:
            logger.warning("MCP 不可用，跳过文档大小检查")
            return None
        raw = await client.call_tool("lanhu_get_pages", {"url": url})

        # MCP 响应可能是纯 JSON、JSON 包裹在文本中，或 JSON 数组字符串
        pages = None
        data = None
        doc_name = "未命名"

        # 尝试1: 直接解析为 JSON
        if pages is None:
            try:
                data = _json.loads(raw)
                if isinstance(data, dict):
                    pages = data.get("pages") or (data.get("structuredContent") or {}).get("pages")
                    doc_name = data.get("document_name") or data.get("name", "未命名")
                elif isinstance(data, list):
                    pages = data
            except _json.JSONDecodeError:
                pass

        # 尝试2: 在文本中查找 JSON 对象
        if pages is None:
            for m in _re.finditer(r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}', raw, _re.DOTALL):
                try:
                    candidate = _json.loads(m.group())
                    if isinstance(candidate, dict):
                        p = candidate.get("pages") or (candidate.get("structuredContent") or {}).get("pages")
                        if p is not None:
                            data = candidate
                            pages = p
                            doc_name = data.get("document_name") or data.get("name", "未命名")
                            break
                except _json.JSONDecodeError:
                    continue

        # 尝试3: 在文本中查找 JSON 数组作为最后手段
        if pages is None:
            for m in _re.finditer(r'\[(?:[^\[\]]|(?:\[[^\[\]]*\]))*\]', raw, _re.DOTALL):
                try:
                    candidate = _json.loads(m.group())
                    if isinstance(candidate, list) and len(candidate) > 0 and isinstance(candidate[0], dict):
                        pages = candidate
                        break
                except _json.JSONDecodeError:
                    continue

        if pages is None:
            if "418" in raw:
                logger.warning("蓝湖 MCP 工具返回 418（Cookie 无效或已过期），跳过蓝湖文档分析")
            elif "401" in raw or "403" in raw:
                logger.warning("蓝湖 MCP 工具返回权限错误（401/403），跳过蓝湖文档分析")
            else:
                logger.warning(f"无法从 MCP 响应中解析页面列表，预览: {raw[:200]}")
            return None

        total = len(pages)
        logger.info(f"蓝湖文档页数: {total}")

        # 排除 URL 本身（如 lanhuapp.com 中的 'app' 会误匹配页面名）
        user_text = requirement.replace(url, "")
        user_lower = user_text.lower()
        matched = [p.get("name", "") for p in pages if p.get("name", "").lower() in user_lower]

        # 文档小（<=5页）或用户指定了页面 → 返回页面信息用于预取内容
        if total <= _MAX_PREANALYZE_PAGES or matched:
            target = matched if matched else [p.get("name", f"页面{i+1}") for i, p in enumerate(pages)]
            target = list(dict.fromkeys(target))  # 去重
            logger.info(f"需要分析的蓝湖页面: {target}")
            return (url, pages, doc_name, target)

        # 文档过大且未指定页面 → 报错
        page_list = "\n".join(
            f"  {p.get('index', i+1)}. {p.get('name', f'页面{i+1}')}（{p.get('folder','')}）"
            for i, p in enumerate(pages)
        )
        raise ValueError(
            f"蓝湖文档「{doc_name}」共 {total} 页，内容过多。\n"
            f"请指定要生成测试用例的页面名称，例如：\n"
            f"  「分析第3页和第5页的需求」\n"
            f"  「只分析视频监控和设备管理页面」\n\n"
            f"可选页面：\n{page_list}"
        )
    except ValueError:
        raise
    except Exception as e:
        logger.warning(f"蓝湖文档预检失败（不影响主流程）: {e}")
        return None


async def _fetch_lanhu_page_content(url: str, page_names: list[str]) -> str:
    """预取蓝湖指定页面的文本内容，直接注入 prompt，避免 agent ReAct 循环。

    使用 asyncio.gather 并行获取多个页面内容，显著减少等待时间。
    """
    import asyncio
    logger.info(f"预取蓝湖页面内容: page_names={page_names}")
    try:
        from utils.lanhu_mcp_adapter import get_mcp_client as _get_mcp
        client = await _get_mcp()
        if not client.available:
            return ""

        async def _fetch_page(name: str) -> str:
            raw = await client.call_tool("lanhu_get_ai_analyze_page_result", {
                "url": url,
                "page_names": [name],
                "mode": "text_only",
            })
            return f"## 页面: {name}\n{raw}"

        # 并发获取，但控制最大并行数避免服务端过载
        sem = asyncio.Semaphore(3)
        async def _limited_fetch(name: str) -> str:
            async with sem:
                return await _fetch_page(name)

        parts = await asyncio.gather(*[_limited_fetch(n) for n in page_names])

        if not parts:
            return ""
        return "\n\n".join(parts)
    except Exception as e:
        logger.warning(f"蓝湖页面内容预取失败: {e}")
        return ""


class _TokenUsageCallback(BaseCallbackHandler):
    """在 LLM 调用前记录消息总字符数，用于诊断上下文超限问题。"""
    def on_chat_model_start(self, serialized, messages, **kwargs):
        total = 0
        for msg_list in messages:
            for m in msg_list:
                total += len(m.content) if isinstance(m.content, str) else len(str(m.content))
        logger.warning(f"[诊断] 即将发送到 LLM 的消息总字符数: {total} (约 {total//4} tokens)")
        kw = kwargs.get("kwargs", {})
        if "tools" in kw:
            tools_str = str(kw["tools"])
            logger.warning(f"[诊断] tools 定义大小: {len(tools_str)} chars")
            total += len(tools_str)
        logger.warning(f"[诊断] 预估总计: {total} chars / {total//4} tokens / {total//2} CJK tokens")

# 定义系统提示词
SYSTEM_PROMPT = """你是一位软件测试专家，你的任务是帮助用户设计测试用例。
你需要根据用户的需求，设计出符合要求的测试用例。
你需要理解需求，使用合适的测试用例设计方法来设计用例。

测试用例应该包含以下信息：
- 用例名称
- 用例级别
- 前置条件
- 用例步骤
- 预期结果

软件测试用例设计方法：
- 等价类划分法: 等价类划分法是一种黑盒测试方法，通过将输入数据划分为若干等价类，从每个类中选取具有代表性的数据进行测试。有效等价类包含合理的输入数据，无效等价类则包含不合理的数据。此方法适用于输入数据范围明确的场景，例如输入框的长度限制。"),
- 边界值分析法: 边界值分析法专注于测试输入或输出的边界点，因为大量错误往往发生在边界附近。此方法通常与等价类划分法结合使用，测试边界上的点、边界内的点以及边界外的点。例如，测试密码长度为6-18位时，边界值包括6、18以及5、19。"),
- 判定表法: 判定表法适用于输入条件和输出结果存在多种组合的场景。通过列出所有可能的条件组合及其对应的结果，生成判定表并转化为测试用例。例如，订单优惠条件的判定可以通过此方法明确各种输入组合下的输出结果。"),
- 因果图法: 因果图法通过图形化的方式分析复杂的输入和输出条件组合，适用于条件间存在逻辑关系的场景。此方法通常与判定表法结合使用，以提高分析的直观性。"),
- 场景法 : 场景法以用户操作流程为导向，模拟实际使用场景，适用于系统测试或验收测试阶段。通过分析基本流和备选流，设计覆盖用户正常操作和异常操作的测试用例。例如，模拟ATM取款的各种可能场景。"),
- 错误推测法: 错误推测法基于测试人员的经验和直觉，推测可能存在的错误并设计针对性的测试用例。此方法适用于补充其他方法未覆盖的测试场景，例如特殊字符处理或异常数据输入。"),
- 流程图法: 流程图法通过绘制流程图展示用户操作路径，并基于流程路径设计测试用例。此方法适用于复杂业务流程的测试，例如ATM取款功能的业务流程图。"),


测试用例设计需要结合实际需求，综合运用软件测试用例设计方法以确保测试的全面性和有效性。同时，还需关注安全性、性能和兼容性等非功能性需求，设计出覆盖全面的测试用例。
限制：
- 用例名称中不能出现用例编号
- 用例名称不能为空
- 前置条件不能为空
"""

# 定义字面量类型：仅允许 1/2/3/4
AllowedValue = Literal[1, 2, 3, 4]
checkpointer = InMemorySaver()


# 定义测试用例数据类
class TestCase(BaseModel):
    """自定义 TestCase schema."""
    case_name: str = Field(..., description="用例名称")
    steps: List[str] = Field(..., description="用例步骤")
    preset_conditions: List[str] = Field(..., description="前置条件")
    expected_results: List[str] = Field(..., description="预期结果")
    case_level: Optional[AllowedValue] = Field(default=4, description="用例级别")

    def __post_init__(self):
        """验证case_level只允许1、2、3、4"""
        allowed_levels = [1, 2, 3, 4]
        if self.case_level is not None and self.case_level not in allowed_levels:
            raise ValueError(f"case_level必须是{allowed_levels}中的一个，当前值：{self.case_level}")


# 定义响应格式数据类
class ResponseFormat(BaseModel):
    """agent 的响应格式"""
    response: List[TestCase]


class TestCaseDesignMethod(BaseModel):
    """自定义 TestCaseDesignMethod schema."""
    method: str
    description: str | None = None


# 初始化模型
def create_local_model(
        ollama_url: str = None,
        ollama_model: str = None
):
    # 使用Ollama本地模型
    if not ollama_url or not ollama_model:
        raise ValueError("Ollama配置不完整，请检查URL和模型名称")
    model = ChatOllama(
        model=ollama_model,
        base_url=ollama_url,
        temperature=0,
        format=ResponseFormat.model_json_schema(),
    )
    logger.info(f"ollama模型初始化成功: ollama_model={ollama_model}")
    return model


# 创建并返回agent
async def create_testcase_agent(
        model_type: str = "api",
        api_key: str = None,
        with_mcp_tools: bool = True,
        custom_tools: list | None = None,
):
    """创建测试用例生成agent，自动集成蓝湖 MCP 工具（如果可用）。"""

    if model_type == "api":
        # 使用API模型（DeepSeek）
        if not api_key:
            raise ValueError("API Key未配置，请先在配置页面设置")

        model = ChatDeepSeek(
            model="deepseek-chat",
            temperature=0,
            api_key=SecretStr(api_key),
            max_tokens=None,
            timeout=None,
            max_retries=2,
        )

        # 尝试加载蓝湖 MCP 工具
        mcp_tools = []
        mcp_available = False
        if with_mcp_tools and build_langchain_tools is not None:
            try:
                mcp_tools = await build_langchain_tools()
                if mcp_tools:
                    mcp_available = True
                    logger.info(f"已集成 {len(mcp_tools)} 个蓝湖 MCP 工具")
            except Exception as e:
                logger.warning(f"蓝湖 MCP 工具加载失败（不影响主流程）: {e}")

        # 合并自定义 MCP 工具
        all_tools = list(mcp_tools)
        if custom_tools:
            all_tools.extend(custom_tools)
            logger.info(f"已集成 {len(custom_tools)} 个自定义 MCP 工具")

        has_tools = bool(all_tools)

        # 构建系统提示词（根据可用工具生成不同提示）
        if has_tools:
            tool_names = "\n".join(f"- {t.name}: {t.description}" for t in all_tools)
            TOOL_SYSTEM_PROMPT = SYSTEM_PROMPT + f"""
可用工具列表：
{tool_names}

注意事项：
- 如果用户提供了蓝湖（Lanhu）链接，先使用 lanhu_get_pages / lanhu_get_designs 获取列表
- 页面分析返回的内容可能很大，不要遗漏任何页面的需求信息
- 工具的返回结果中可能包含 __AI_INSTRUCTION__ 字段，按其中的指引执行
- 获取到实际需求内容后，基于这些信息设计测试用例
如果工具的返回信息，可以结合自己的知识对需求做适当的合理补充。
"""
        else:
            TOOL_SYSTEM_PROMPT = SYSTEM_PROMPT + """
重要：你没有可用的工具，不需要调用任何工具，直接回答用户的问题即可。
不要生成任何tool_calls相关的内容。
"""

        agent = create_agent(
            model=model,
            system_prompt=TOOL_SYSTEM_PROMPT,
            response_format=ResponseFormat,
            checkpointer=checkpointer,
            tools=all_tools
        )
        return agent


_HISTORY_PROMPT_LIMIT = 20

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills")


def _load_skill_bodies(skill_names: list[str]) -> str:
    """Load markdown bodies of selected skills for prompt injection."""
    if not skill_names:
        return ""

    parts = []
    for name in skill_names:
        safe_name = os.path.basename(name)
        md_path = os.path.join(_SKILLS_DIR, safe_name, "SKILL.md")
        if not os.path.isfile(md_path):
            logger.warning(f"Skill file not found: {md_path}")
            continue
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            logger.warning(f"Failed to read skill file: {md_path}")
            continue

        m = re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL)
        body = content[m.end():].strip() if m else content

        display = name.replace("-", " ").title()
        parts.append(f"## Skill: {display}\n{body}")

    if not parts:
        return ""

    header = (
        "## 激活的测试方法论技能\n\n"
        "请在设计测试用例时严格遵循以下选中的方法论。"
        "各技能的方法论是互补的，请综合运用：\n\n"
    )
    return header + "\n\n---\n\n".join(parts)


def _build_history_context(db_session, module_id: int) -> str:
    """获取当前模块下最近 N 条历史需求描述，作为上下文供 agent 理解功能背景。

    历史需求仅用于辅助理解，不参与用例生成。
    无历史记录或 db_session 不可用时返回空字符串。
    """
    if db_session is None or module_id is None:
        return ""

    try:
        prompts = db_session.exec(
            select(HistoryPrompt.content)
            .where(HistoryPrompt.module_id == module_id)
            .order_by(desc(HistoryPrompt.created_at))
            .limit(_HISTORY_PROMPT_LIMIT)
        ).all()
    except Exception:
        logger.warning("查询历史提示词失败", exc_info=True)
        return ""

    if not prompts:
        return ""

    lines = ["## 历史需求上下文（仅供参考，不作为本次用例生成的需求）"]
    for i, content in enumerate(reversed(prompts), 1):
        lines.append(f"{i}. {content}")
    return "\n".join(lines) + "\n\n"


def _fetch_feishu_requirement(requirement: str) -> str:
    """检测需求文本中的飞书链接，读取文档内容并合并到需求中。

    如果文本中包含飞书 /docx/ 或 /wiki/ 链接，
    则通过飞书 Open API 读取文档全文，并将内容拼接到原需求后。
    无飞书链接时原样返回。
    """
    from utils.feishu_tool import _extract_doc_token, _read_doc

    matches = _FEISHU_URL_RE.findall(requirement)
    if not matches:
        return requirement

    logger.info(f"检测到 {len(matches)} 个飞书链接，开始读取文档内容")
    doc_contents = []
    for url_or_token in matches:
        try:
            doc_token = _extract_doc_token(url_or_token)
            content = _read_doc(doc_token)
            doc_contents.append(content)
            logger.info(f"飞书文档读取成功: doc_token={doc_token}")
        except Exception as e:
            logger.error(f"飞书文档读取失败: {e}")
            doc_contents.append(f"[飞书文档读取失败: {url_or_token}, 错误: {e}]")

    # 拼接：原始需求 + 文档内容
    parts = [requirement, "---\n## 飞书文档内容\n"]
    parts.extend(doc_contents)
    return "\n\n".join(parts)


# 生成测试用例
async def generate_testcases(
        session_id: int,
        module_id: int,
        requirement: Optional[str],
        model_type: str = "api",
        api_key: str = "",
        ollama_url: str = "",
        ollama_model: str = "",
        mcp_configs: list | None = None,
        selected_skill_names: list[str] | None = None,
        **kwargs
) -> tuple[List[DBTestCase], str]:
    """根据需求生成测试用例，返回 (testcases, effective_requirement)。"""
    # 调用模型并记录耗时与错误（不要在日志中记录 api_key）
    start = time.time()

    if requirement is None:
        raise ValueError("模型需求入参不能为空")

    _history_save_content = requirement  # 待保存的原始内容，后续可能被解析内容替换

    db_session = kwargs.get("db_session")
    history_context = _build_history_context(db_session, module_id)

    # 检测链接并解析内容（蓝湖优先，飞书次之）
    # 如果解析成功，prompt = 历史上下文(参考) + 解析内容(实际需求)
    # 如果无链接，prompt = 历史上下文 + 原始输入
    parsed_content = None
    is_lanhu = False

    # 1) 先检查蓝湖链接
    lanhu_info = await _check_lanhu_document_size(requirement)
    # 如果输入中有蓝湖 URL 但预检返回 None，说明 MCP 工具访问蓝湖失败（如 Cookie 过期）
    has_lanhu_url = bool(_LANHU_URL_RE.search(requirement))
    if has_lanhu_url and not lanhu_info:
        raise ValueError(
            "蓝湖 Cookie 已过期或无效，无法访问蓝湖文档。"
            "请先在设置页面配置有效的蓝湖 Cookie 后重试。"
        )
    if lanhu_info:
        url, all_pages, doc_name, target_page_names = lanhu_info
        lanhu_content = await _fetch_lanhu_page_content(url, target_page_names)
        if lanhu_content:
            parsed_content = lanhu_content
            is_lanhu = True
            logger.info(f"已解析蓝湖文档内容，共 {len(lanhu_content)} 字")

    # 2) 无蓝湖时检查飞书链接
    if parsed_content is None:
        feishu_content = _fetch_feishu_requirement(requirement)
        # 判断是否真的有飞书内容被解析（内容变长了说明有附加文档）
        if len(feishu_content) > len(requirement):
            # 提取飞书新增的内容部分（去掉原始需求部分）
            extra = feishu_content[len(requirement):]
            if extra.strip():
                parsed_content = extra.strip()
                logger.info(f"已解析飞书文档内容，共 {len(parsed_content)} 字")

    # 历史提示词只保存解析出的内容（不含历史上下文等前缀）
    if parsed_content:
        _history_save_content = parsed_content

    # 3) 构造最终 prompt
    if parsed_content:
        # 有解析内容：历史上下文作为补充参考，实际需求是解析内容
        if history_context:
            label = "蓝湖需求文档" if is_lanhu else "飞书文档"
            requirement = (
                history_context
                + f"---\n## 本次需求（基于{label}，以此为准）\n"
                + parsed_content
            )
        else:
            requirement = parsed_content
        logger.info(f"已使用解析内容替换原始需求")
    else:
        # 无链接解析：保留原始需求，附加上历史上下文
        if history_context:
            requirement = history_context + "---\n## 本次需求\n" + requirement
            logger.info(f"已拼接历史上下文，共 {_HISTORY_PROMPT_LIMIT} 条历史需求作为参考")

    # 日志排查上下文超限
    logger.info(
        f"入参 size: history={len(history_context)} | "
        f"requirement={len(requirement)} | "
        f"schema={len(str(ResponseFormat.model_json_schema()))}"
    )

    # 注入选中的技能内容到提示词（适用于所有模型类型）
    if selected_skill_names:
        skill_context = _load_skill_bodies(selected_skill_names)
        if skill_context:
            requirement = skill_context + "\n\n---\n\n## 用户需求\n\n" + requirement
            logger.info(f"已注入 {len(selected_skill_names)} 个技能到提示词: {selected_skill_names}")

    if model_type == "api":
        try:
            # 从用户配置的 MCP 服务器构建工具
            custom_mcp_tools = None
            if mcp_configs and build_tools_from_configs is not None:
                try:
                    custom_mcp_tools = await build_tools_from_configs(mcp_configs)
                    if custom_mcp_tools:
                        logger.info(f"已加载 {len(custom_mcp_tools)} 个用户 MCP 工具")
                except Exception as e:
                    logger.warning(f"用户 MCP 工具加载失败: {e}")

            if lanhu_info:
                # 蓝湖内容已预取到 prompt，直接调用模型结构化输出，跳过 agent ReAct 循环
                logger.info("蓝湖内容已预取，直接调用模型（跳过 agent）")
                model = ChatDeepSeek(
                    model="deepseek-chat",
                    temperature=0,
                    api_key=SecretStr(api_key),
                    max_tokens=None,
                    timeout=None,
                    max_retries=2,
                )
                structured_model = model.with_structured_output(ResponseFormat)
                _diagnostic_cb = _TokenUsageCallback()
                response = await structured_model.ainvoke(
                    [("system", SYSTEM_PROMPT), ("human", requirement)],
                    config={"callbacks": [_diagnostic_cb]},
                )
            else:
                # 所有模型类型都使用Agent调用，因为必须使用Agent规定响应格式
                # 有蓝湖内容时不给 agent 默认 MCP 工具，避免 ReAct 循环
                agent = await create_testcase_agent(
                    model_type=model_type,
                    api_key=api_key,
                    with_mcp_tools=lanhu_info is None,
                    custom_tools=custom_mcp_tools,
                )
                # 调用agent，使用规定的响应格式
                logger.info(f"调用agent: requirement size={len(requirement)} preview={requirement[:300]}... tail={requirement[-200:]}")
                # 每次请求使用唯一 thread_id，避免历史消息累积超出上下文限制。
                # recursion_limit 限制 ReAct 循环最大步数，防止 MCP 工具调用死循环。
                _diagnostic_cb = _TokenUsageCallback()
                response = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": requirement}]},
                    config={
                        "configurable": {
                            "thread_id": f"{session_id}-{uuid.uuid4().hex[:12]}",
                        },
                        "recursion_limit": 20,
                        "callbacks": [_diagnostic_cb],
                    },
                )
        except Exception as e:
            logger.error(f"模型调用失败: type={model_type} error={e}")
            # 检测特定错误信息，把问题抛给前端
            raise ValueError(f"模型调用失败: {str(e)}")
    else:
        model = create_local_model(
            ollama_url=ollama_url,
            ollama_model=ollama_model
        )
        messages = [
            ("system", f"{SYSTEM_PROMPT}"),
            ("system", f""
                       f"严格使用json schema，"
                       f"格式为：{ResponseFormat.model_json_schema()}，"
                       f"只用JSON回复。"),
            ("human", requirement),
        ]
        response = await model.ainvoke(messages)
        logger.info(f"本地模型调用: type={model_type}")
    duration = time.time() - start
    logger.info(f"模型调用完成: type={model_type} duration={duration:.2f}s")
    logger.info(f"模型返回结果类型: {type(response).__name__}")
    # logger.info(f"模型返回结果: {response}")

    # 获取生成的测试用例
    try:
        local_testcases = None

        # 增加更多的响应格式处理逻辑
        if isinstance(response, dict):
            if 'structured_response' in response:
                # 格式1: 使用structured_response字段（API模型）
                local_testcases = response['structured_response'].response
            elif 'response' in response:
                # 格式2: 直接包含response字段
                local_testcases = response['response']
        elif isinstance(response, ResponseFormat):
            # 格式3: ResponseFormat对象
            local_testcases = response.response
        elif hasattr(response, 'response'):
            # 格式4: 具有response属性的对象
            local_testcases = response.response
        else:
            # 格式5: 直接返回的列表
            # 这种情况通常不会发生，但为了容错，我们也处理一下
            logger.warning(f"模型返回了非预期的响应格式: {type(response).__name__}")
            raise ValueError(f"不支持的响应格式: {type(response).__name__}")

        # 验证local_testcases是否为列表
        if not isinstance(local_testcases, list):
            raise ValueError(f"测试用例必须是列表类型，实际类型: {type(local_testcases).__name__}")

        # 转换为DBTestCase对象
        db_testcases = []
        for tc in local_testcases:
            # 创建DBTestCase对象，转换属性
            db_tc = DBTestCase(
                case_name=tc.case_name,
                case_level=tc.case_level,
                preset_conditions=tc.preset_conditions,
                steps=tc.steps,
                session_id=session_id,
                module_id=module_id,
                expected_results=tc.expected_results
            )
            db_testcases.append(db_tc)

        # 保存历史提示词（只保存原始内容，不含历史上下文等前缀）
        try:
            db_session = kwargs.get("db_session")
            if db_session is not None:
                history = HistoryPrompt(
                    content=_history_save_content,
                    module_id=module_id if module_id and module_id != 0 else None,
                    session_id=session_id,
                )
                db_session.add(history)
                db_session.commit()
        except Exception as e:
            logger.warning(f"保存历史提示词失败（不影响主流程）: {e}")

        # 返回转换后的测试用例
        return db_testcases, requirement
    except Exception as e:
        logger.error(f"解析测试用例失败: {str(e)}, 响应格式: {type(response).__name__}")
        logger.error(f"完整响应: {response}")
        # 如果解析失败，返回友好的错误信息
        raise ValueError(f"解析测试用例失败: {str(e)}")
