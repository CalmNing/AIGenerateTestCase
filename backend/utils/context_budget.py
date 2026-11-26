"""对话上下文预算管理（参考 DeepSeek Harness compaction 的轻量版）

三层机制：
1. estimate_tokens  启发式 token 估算（中文 ~0.6 token/字，英文 ~0.25 token/字符，
   加消息框架开销），不追求精确，只要求**稳定偏保守**（高估比低估安全）。
2. prune_message    单条长消息修剪：超阈值保留头尾，中间替换为省略标记
   （对应 DSH tool-result-pruner 的 head/middle/tail 策略）。
3. trim_history     历史预算裁剪：从最旧开始丢弃，保住 system prompt 与最近
   消息；预算 = 模型窗口 - 输出预留 - headroom - system prompt。

设计约束：
- 纯函数、无 IO、无模型调用（不做 LLM checkpoint 压缩，那是二期）；
- 估算宁高勿低：高估会提前裁剪（安全），低估会撞真实窗口（截断）。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ===== 默认预算参数 =====

# DeepSeek chat 默认上下文窗口（保守值；兼容 gateway 未上报真实窗口的场景）
DEFAULT_MODEL_WINDOW = 65536

# 上下文 headroom：预留给对话增长与估算误差的缓冲
DEFAULT_HEADROOM = 8192

# 单条消息修剪阈值（字符）：超过才修剪，对齐 DSH tool-result-pruner 的低摩擦默认
PRUNE_THRESHOLD_CHARS = 8192
PRUNE_HEAD_CHARS = 4096
PRUNE_TAIL_CHARS = 1024
PRUNE_MARKER = "\n\n[……中间内容已省略以控制上下文长度……]\n\n"


def estimate_tokens(text: str) -> int:
    """启发式 token 估算：稳定偏保守。

    中文按 ~1.6 字/token（0.625 token/字），非 CJK 按 ~4 字符/token；
    取两者分段计算。混合文本按字符类别逐段累加。
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        # CJK 统一表意文字 + 常用全角标点
        code = ord(ch)
        if (
            0x4E00 <= code <= 0x9FFF      # CJK 基本区
            or 0x3400 <= code <= 0x4DBF   # CJK 扩展 A
            or 0x3000 <= code <= 0x303F   # CJK 标点
            or 0xFF00 <= code <= 0xFFEF   # 全角形式
        ):
            cjk += 1
        else:
            other += 1
    return int(cjk * 0.625 + other * 0.25) + 1


def estimate_message_tokens(role: str, content: str) -> int:
    """单条消息估算：正文 + role 框架开销（对齐 OpenAI 每消息 ~4 token 框架）。"""
    return estimate_tokens(content) + 8


def prune_long_message(content: str) -> str:
    """单条超长消息修剪：保头尾，中间打省略标记。

    头部保留多于尾部（开头通常是问题定义，结尾是最新细节）。
    未超阈值原样返回。
    """
    if len(content) <= PRUNE_THRESHOLD_CHARS:
        return content
    return (
        content[:PRUNE_HEAD_CHARS]
        + PRUNE_MARKER
        + content[-PRUNE_TAIL_CHARS:]
    )


def resolve_model_window(api_base_url: str, api_model: str) -> int:
    """解析模型上下文窗口：config.json 的 model_window 优先，未知模型回退保守默认。

    与生成链路（pipeline/model_utils 的 get_model_window）同源，
    运维改一次配置，对话与生成同时生效。
    """
    try:
        from config import config_manager

        configured = int(config_manager.get_model_window())
        if configured > 0:
            return configured
    except Exception:  # noqa: BLE001 - 配置不可用时按默认值
        pass
    model = (api_model or "").lower()
    if "deepseek-v4" in model or "deepseek-chat" in model or "deepseek-reasoner" in model:
        return 131072
    return DEFAULT_MODEL_WINDOW


def build_history_budget(
    model_window: int,
    max_tokens: int,
    system_tokens: int,
    headroom: int = DEFAULT_HEADROOM,
) -> int:
    """历史消息可用 token 预算 = 窗口 - 输出预留 - system - headroom。

    预算耗尽是正常路径（裁剪兜底），返回值最小 0。
    """
    return max(0, model_window - max_tokens - system_tokens - headroom)


def trim_history(
    messages: list,
    budget_tokens: int,
    estimate_fn=None,
) -> tuple[list[dict], dict]:
    """从最旧开始丢弃历史，直到总预算内；返回 (保留的消息, 统计)。

    返回的消息统一为 {role, content} dict（输入可为对象或 dict）。

    规则：
    - 至少保留最后 2 条（最新一轮对话），即使超预算（保证请求可用）；
    - 每条消息先做长消息修剪再计量；
    - 丢弃的条数与 token 计入统计，便于日志观测。

    estimate_fn(role, content) -> int 可注入自定义估算（测试用）。
    """
    est = estimate_fn or estimate_message_tokens
    stats = {"dropped": 0, "pruned": 0, "kept": len(messages)}

    # 先逐条修剪长消息（images 等附加字段原样保留）
    pruned_items = []
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else msg.get("content", "")
        role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
        new_content = prune_long_message(content)
        if new_content != content:
            stats["pruned"] += 1
        item = {"role": role, "content": new_content}
        # dict 输入的附加键（images 等）原样带回
        if not hasattr(msg, "content") and isinstance(msg, dict):
            for key, value in msg.items():
                if key not in item:
                    item[key] = value
        pruned_items.append(item)

    # 从最旧开始丢弃
    kept = list(pruned_items)
    total = sum(est(item["role"], item["content"]) for item in kept)
    dropped_items: list[dict] = []
    while len(kept) > 2 and total > budget_tokens:
        dropped_item = kept.pop(0)
        total -= est(dropped_item["role"], dropped_item["content"])
        stats["dropped"] += 1
        dropped_items.append(dropped_item)

    stats["kept"] = len(kept)
    stats["estimated_total"] = total
    stats["dropped_items"] = dropped_items
    stats["dropped_tokens"] = sum(est(i["role"], i["content"]) for i in dropped_items)
    return kept, stats


# ===== 二期：LLM checkpoint 压缩 =====

# 触发压缩的最小丢弃 token 数：丢弃太少不值得多一次 LLM 调用
COMPACT_MIN_DROPPED_TOKENS = 4096

# checkpoint 消息的角色与标题（拼进 user 消息，随历史走）
CHECKPOINT_ROLE = "user"
CHECKPOINT_TITLE = "【早期对话摘要（系统自动压缩，原文已裁剪）】"

# 总结指令：章节骨架对齐 DeepSeek Harness compaction-basic 的 checkpoint，
# 按业务对话场景精简为 5 节
CHECKPOINT_INSTRUCTION = """请把以上早期对话压缩成结构化检查点，供后续对话无损续接。严格按以下章节输出（空章节写"（无）"，不得删章节），用简洁要点，保留具体事实：

## 用户目标
- 用户最初与演进中的诉求，关键原话直接引用

## 已确认的事实与结论
- 对话中确认的数据、决策、偏好（含修正与反悔）

## 关键内容
- 出现过的长文本要点：需求名称、用例名称与 ID、报错原文关键词、文件路径、命令

## 未完成事项
- 用户明确要求但还没做的事

## 下一步
- 按最近消息推断的唯一下一步行动（无则写（无））

规则：不要提到本摘要请求本身；不要编造对话中不存在的内容；只输出检查点文本。"""


def should_compact(dropped_items: list[dict], dropped_tokens: int) -> bool:
    """丢弃内容足够多时才值得压缩（省一次 LLM 调用）。"""
    return bool(dropped_items) and dropped_tokens >= COMPACT_MIN_DROPPED_TOKENS


def build_compaction_prompt(dropped_items: list[dict]) -> str:
    """把被丢弃的旧消息拼成待总结文本（含角色标注）。"""
    parts = []
    for item in dropped_items:
        prefix = "用户" if item["role"] == "user" else "助手"
        parts.append(prefix + "：" + item["content"])
    return CHECKPOINT_TITLE + "\n" + "\n".join(parts) + "\n" + CHECKPOINT_INSTRUCTION


async def summarize_dropped_messages(
    dropped_items: list[dict],
    api_key: str,
    api_base_url: str,
    api_model: str,
) -> Optional[str]:
    """调用 LLM 把被丢弃的旧消息总结成 checkpoint；失败返回 None（降级为直接丢弃）。

    使用独立低 max_tokens 的模型调用（摘要不需要长输出预算），
    任何异常都不影响对话主流程 —— 压缩是增强，不是依赖。
    """
    try:
        from utils.llm_factory import build_chat_model

        model = build_chat_model(
            api_key=api_key,
            api_base_url=api_base_url,
            api_model=api_model,
            max_tokens=4096,
            temperature=0.3,
        )
        response = await model.ainvoke(build_compaction_prompt(dropped_items))
        content = getattr(response, "content", None)
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        text = (content or "").strip()
        if not text:
            return None
        return text
    except Exception as exc:  # noqa: BLE001 - 压缩失败一律降级
        logger.warning("checkpoint 压缩失败，降级为直接丢弃: %s", exc)
        return None


def wrap_checkpoint(checkpoint_text: str) -> dict:
    """把 checkpoint 文本包装成可插入历史的消息（user 角色 + 系统标注）。"""
    return {
        "role": CHECKPOINT_ROLE,
        "content": CHECKPOINT_TITLE + "\n" + checkpoint_text,
    }