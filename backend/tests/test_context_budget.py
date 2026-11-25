"""上下文预算管理测试。

覆盖：token 估算（CJK/英文/混合）、长消息修剪、模型窗口解析、
预算计算、历史裁剪（丢旧留新、最少保留 2 条）、checkpoint 压缩
（触发门控、prompt 构建、降级路径）、_budgeted_history 集成。
不触网、不调真实 LLM。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.context_budget import (
    COMPACT_MIN_DROPPED_TOKENS,
    DEFAULT_HEADROOM,
    DEFAULT_MODEL_WINDOW,
    CHECKPOINT_TITLE,
    build_compaction_prompt,
    build_history_budget,
    estimate_message_tokens,
    estimate_tokens,
    prune_long_message,
    resolve_model_window,
    should_compact,
    trim_history,
    wrap_checkpoint,
)


# ===== 估算 =====

def test_estimate_tokens_cjk_and_ascii():
    assert estimate_tokens("你好世界") == 3      # 4 CJK
    assert estimate_tokens("hello world!") == 4  # 12 ASCII
    assert estimate_tokens("") == 0


def test_estimate_message_tokens_includes_frame():
    assert estimate_message_tokens("user", "") == 8
    assert estimate_message_tokens("user", "hi") == estimate_tokens("hi") + 8


# ===== 长消息修剪 =====

def test_prune_long_message_head_tail_marker():
    content = "A" * 9000 + "MIDDLE" + "B" * 100
    pruned = prune_long_message(content)
    assert pruned.startswith("A")
    assert pruned.endswith("B")
    assert "省略" in pruned
    assert len(pruned) < 6000


def test_prune_short_message_unchanged():
    assert prune_long_message("hello") == "hello"
    edge = "x" * 8192
    assert prune_long_message(edge) == edge


# ===== 窗口与预算 =====

def test_resolve_model_window_config_first(monkeypatch):
    from config import config_manager

    # 配置优先：config.json 的 model_window 生效
    monkeypatch.setattr(config_manager, "get_model_window", lambda: 65536)
    assert resolve_model_window("", "anything") == 65536

def test_resolve_model_window_fallback_by_model_name(monkeypatch):
    from config import config_manager

    # 配置异常时按模型名识别 DeepSeek 系列
    def boom():
        raise RuntimeError("config unavailable")
    monkeypatch.setattr(config_manager, "get_model_window", boom)
    assert resolve_model_window("https://api.deepseek.com", "deepseek-v4-flash") == 131072
    assert resolve_model_window("", "") == DEFAULT_MODEL_WINDOW


def test_build_history_budget_formula():
    assert build_history_budget(131072, 32768, 2000) == 131072 - 32768 - 2000 - DEFAULT_HEADROOM
    assert build_history_budget(8192, 100000, 5000) == 0


# ===== 历史裁剪 =====

def _msg(role, content):
    return {"role": role, "content": content}


def test_trim_history_drops_oldest_first():
    msgs = [_msg("user", chr(0x4E00) * 1000) for _ in range(10)]
    kept, stats = trim_history(msgs, 2000)
    assert stats["dropped"] > 0
    assert kept[-1] == {"role": "user", "content": msgs[-1]["content"]}
    assert len(kept) < len(msgs)
    assert len(kept) >= 2


def test_trim_history_never_below_two_messages():
    msgs = [_msg("user", chr(0x4E00) * 10000) for _ in range(5)]
    kept, stats = trim_history(msgs, 100)
    assert len(kept) == 2
    # 长消息修剪对最后 2 条也生效
    assert all(len(m["content"]) < 6000 for m in kept)
    assert [m["role"] for m in kept] == ["user", "user"]


def test_trim_history_no_drop_within_budget():
    msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
    kept, stats = trim_history(msgs, 10 ** 9)
    assert stats["dropped"] == 0 and len(kept) == 2
    assert stats["dropped_items"] == []


def test_trim_history_prunes_long_messages():
    long = "A" * 20000
    msgs = [_msg("user", long), _msg("assistant", "ok")]
    kept, stats = trim_history(msgs, 10 ** 9)
    assert stats["pruned"] == 1
    assert len(kept[0]["content"]) < 6000


def test_trim_history_tracks_dropped_items_and_tokens():
    big = chr(0x4E00) * 1000  # ~625 token/条
    msgs = [_msg("user", big) for _ in range(10)]
    kept, stats = trim_history(msgs, 2000)
    assert stats["dropped"] > 0
    assert len(stats["dropped_items"]) == stats["dropped"]
    assert stats["dropped_tokens"] > 0
    # 被丢弃的都是最旧的
    assert stats["dropped_items"][0]["content"] == msgs[0]["content"]


def test_trim_history_empty():
    kept, stats = trim_history([], 1000)
    assert kept == [] and stats["dropped"] == 0 and stats["dropped_items"] == []


# ===== checkpoint 压缩 =====

def test_should_compact_threshold():
    assert should_compact([_msg("user", "x")], COMPACT_MIN_DROPPED_TOKENS) is True
    assert should_compact([_msg("user", "x")], COMPACT_MIN_DROPPED_TOKENS - 1) is False
    assert should_compact([], COMPACT_MIN_DROPPED_TOKENS) is False


def test_build_compaction_prompt_structure():
    msgs = [_msg("user", "问题A"), _msg("assistant", "答案B")]
    prompt = build_compaction_prompt(msgs)
    assert "用户：问题A" in prompt
    assert "助手：答案B" in prompt
    # 5 节结构齐全
    for section in ("用户目标", "已确认的事实与结论", "关键内容", "未完成事项", "下一步"):
        assert "## " + section in prompt
    # 禁止编造与自指规则在指令里
    assert "不要编造" in prompt and "不要提到本摘要请求" in prompt


def test_wrap_checkpoint_shape():
    wrapped = wrap_checkpoint("摘要正文")
    assert wrapped["role"] == "user"
    assert CHECKPOINT_TITLE in wrapped["content"]
    assert "摘要正文" in wrapped["content"]


# ===== chat 端点集成 =====

def test_budgeted_history_passes_through_small_history():
    from app.routes.chat import ChatMessage, ChatRequest, _budgeted_history
    import asyncio

    req = ChatRequest(messages=[ChatMessage(role="user", content="hi")])
    out = asyncio.run(_budgeted_history(
        req, "system", 16384, "k", "https://api.deepseek.com", "deepseek-v4-flash",
    ))
    assert len(out) == 1 and out[0].content == "hi"


def test_budgeted_history_drops_old_and_keeps_recent():
    from app.routes.chat import ChatMessage, ChatRequest, _budgeted_history
    import asyncio

    big = chr(0x4E00) * 20000  # 修剪后 ~3.2K token
    msgs = [ChatMessage(role="user", content=big) for _ in range(20)]
    req = ChatRequest(messages=msgs)
    huge_system = chr(0x4E00) * 45000  # ~28K token → 预算 ~62K
    out = asyncio.run(_budgeted_history(
        req, huge_system, 32768, "k", "https://api.deepseek.com", "deepseek-v4-flash",
    ))
    assert len(out) < 20, f"应丢弃旧消息，实际保留 {len(out)}"
    assert len(out) >= 2
    assert out[-1].role == "user"
    assert "省略" in out[-1].content


def test_budgeted_history_compact_degrades_without_llm():
    """LLM 不可达时 checkpoint 压缩必须静默降级，不影响裁剪结果。"""
    from app.routes.chat import ChatMessage, ChatRequest, _budgeted_history
    import asyncio

    big = chr(0x4E00) * 1000  # 不触发修剪，~626 token/条
    msgs = [ChatMessage(role="user", content=big) for _ in range(200)]  # ~125K token
    req = ChatRequest(messages=msgs)
    out = asyncio.run(_budgeted_history(
        req, "system", 32768, "bad-key", "http://127.0.0.1:1", "deepseek-v4-flash",
    ))
    assert len(out) >= 2
    assert len(out) < 200
    # 降级：没有 checkpoint 消息（摘要失败）
    assert all("系统自动压缩" not in m.content for m in out)


# ===== 端点级回归：变量定义顺序（曾触发 UnboundLocalError）=====

def test_chat_stream_endpoint_budgeted_messages_defined_before_use():
    """回归：流式端点曾把 for msg in budgeted_messages 写在定义之前。

    断言两个端点的源码顺序都是先定义后使用。
    """
    import inspect

    from app.routes import chat as chat_mod

    stream_src = inspect.getsource(chat_mod.chat_stream)
    assert stream_src.index("budgeted_messages = await") < stream_src.index("for msg in budgeted_messages")

    chat_src = inspect.getsource(chat_mod.chat)
    assert chat_src.index("budgeted_messages = await") < chat_src.index("for msg in budgeted_messages")


def test_chat_stream_endpoint_smoke_no_unbound_error():
    """烟测：mock 掉预算与模型依赖，端点必须走通预算路径
    （直连失败也只发 error 事件，绝不抛 UnboundLocalError）。"""
    import asyncio

    from app.routes import chat as chat_mod
    from app.routes.chat import ChatMessage, StreamingChatRequest

    async def fake_budget(req, system_content, chat_max_tokens, api_key, api_base_url, api_model):
        fake_budget.called = True
        return [ChatMessage(role=m.role, content=m.content) for m in req.messages]

    fake_budget.called = False

    saved = (chat_mod._budgeted_history, chat_mod._resolve_chat_model_config,
             chat_mod._load_chat_tools, chat_mod._load_testcase_tools)
    chat_mod._budgeted_history = fake_budget
    chat_mod._resolve_chat_model_config = lambda session: ("k", "http://127.0.0.1:1", "m")
    async def no_tools(session, user, mode):
        return []
    chat_mod._load_chat_tools = no_tools
    chat_mod._load_testcase_tools = lambda mode, sid, user, module_id=None: []
    try:
        req = StreamingChatRequest(messages=[ChatMessage(role="user", content="hi")], show_thinking=False)
        response = asyncio.run(chat_mod.chat_stream(req, session=None, current_user=None))

        async def drain():
            frames = []
            async for frame in response.body_iterator:
                text_frame = frame.decode("utf-8", errors="replace") if isinstance(frame, bytes) else str(frame)
                frames.append(text_frame)
                if "event: done" in text_frame:
                    break
            return frames

        frames = asyncio.run(drain())
        assert fake_budget.called, "_budgeted_history 必须被调用"
        assert not any("UnboundLocalError" in f for f in frames)
        assert any("event: done" in f for f in frames)
    finally:
        (chat_mod._budgeted_history, chat_mod._resolve_chat_model_config,
         chat_mod._load_chat_tools, chat_mod._load_testcase_tools) = saved

# ===== reasoning_content 保留（Agent 路径思考过程回归）=====

def test_deepseek_compat_model_preserves_reasoning_in_stream_chunks():
    """回归：langchain_openai 的 delta 转换会丢弃 reasoning_content，

    导致 Agent 路径（AI 用例对话挂工具后）思考过程消失。
    DeepSeekCompatChatOpenAI 必须把它补进 additional_kwargs。
    """
    from utils.llm_factory import DeepSeekCompatChatOpenAI, build_chat_model
    from langchain_openai import ChatOpenAI

    model = build_chat_model(
        api_key="k", api_base_url="https://api.deepseek.com",
        api_model="deepseek-v4-flash", max_tokens=64,
    )
    assert isinstance(model, DeepSeekCompatChatOpenAI)
    assert isinstance(model, ChatOpenAI)

    chunk = {
        "id": "c1",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "", "reasoning_content": "思考"}, "finish_reason": None}],
    }
    gen = model._convert_chunk_to_generation_chunk(chunk, None, None)
    assert gen is not None
    assert gen.message.additional_kwargs.get("reasoning_content") == "思考"

    chunk2 = {
        "id": "c2",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "答"}, "finish_reason": None}],
    }
    gen2 = model._convert_chunk_to_generation_chunk(chunk2, None, None)
    assert gen2.message.content == "答"
    assert not gen2.message.additional_kwargs.get("reasoning_content")