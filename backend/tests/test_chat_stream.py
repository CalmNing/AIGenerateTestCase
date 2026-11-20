"""chat 流式对话辅助函数测试。

覆盖点：SSE 帧构造、增量提取（content / reasoning_content）、
事件流组装（plain 模型 / Agent 模式）与异常终止。
不触网、不调真实 LLM。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.routes.chat import (
    _SSE_CONTENT_EVENT,
    _SSE_DONE_EVENT,
    _SSE_ERROR_EVENT,
    _SSE_REASONING_EVENT,
    _SSE_TOOL_EVENT,
    _sse_chunk,
    _sse_event,
    _stream_agent,
    _stream_plain_model,
)


# ===== SSE 帧构造 =====

def test_sse_event_single_line():
    frame = _sse_event("content", "你好")
    assert frame == "event: content\ndata: 你好\n\n"


def test_sse_event_multiline_data():
    frame = _sse_event("content", "第一行\n第二行")
    assert "data: 第一行" in frame
    assert "data: 第二行" in frame
    assert frame.endswith("\n\n")


def test_sse_event_types():
    assert _SSE_REASONING_EVENT == "reasoning"
    assert _SSE_CONTENT_EVENT == "content"
    assert _SSE_TOOL_EVENT == "tool"
    assert _SSE_ERROR_EVENT == "error"
    assert _SSE_DONE_EVENT == "done"


# ===== 增量提取 =====

class _Chunk:
    def __init__(self, kwargs):
        self.additional_kwargs = kwargs


def test_sse_chunk_content():
    # 正文增量在 chunk.content（langchain 标准），不在 additional_kwargs
    class _ContentChunk:
        def __init__(self, content):
            self.content = content
            self.additional_kwargs = {}

    assert _sse_chunk(_ContentChunk("abc"), "content") == "abc"


def test_sse_chunk_content_list_blocks():
    # 多模态 block 列表：拼接 text 块
    class _ContentChunk:
        def __init__(self, content):
            self.content = content
            self.additional_kwargs = {}

    chunk = _ContentChunk([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    assert _sse_chunk(chunk, "content") == "ab"


def test_sse_chunk_content_legacy_kwargs_still_supported():
    # additional_kwargs.content 兜底（chunk 无 content 属性的对象，如 dict 数据）
    assert _sse_chunk(_Chunk({"content": "abc"}), "content") == "abc"


def test_sse_chunk_content_empty_on_plain_object():
    assert _sse_chunk(object(), "content") == ""


def test_sse_chunk_reasoning():
    assert _sse_chunk(_Chunk({"reasoning_content": "想"}), "reasoning_content") == "想"


def test_sse_chunk_missing_returns_empty():
    assert _sse_chunk(_Chunk({}), "content") == ""
    assert _sse_chunk(_Chunk({}), "reasoning_content") == ""
    assert _sse_chunk(_Chunk({"content": None}), "content") == ""


def test_sse_chunk_non_chunk_object_returns_empty():
    assert _sse_chunk(object(), "content") == ""


# ===== plain 模型事件流 =====

async def _collect(agen):
    return [frame async for frame in agen]


def test_stream_plain_model_merges_reasoning_and_content():
    import asyncio

    chunks = [
        _Chunk({"reasoning_content": "先想"}),
        _Chunk({"reasoning_content": "一下"}),
        _Chunk({"content": "答案"}),
        _Chunk({"content": "A\nB"}),
    ]

    class _Model:
        def __init__(self):
            self.calls = None

        async def astream(self, messages):
            self.calls = messages
            for c in chunks:
                yield c

    model = _Model()
    frames = asyncio.run(_collect(_stream_plain_model(model, ["m"], "req-1")))

    assert frames[0].startswith("event: reasoning\ndata: 先想\n\n")
    assert frames[1].startswith("event: reasoning\ndata: 一下\n\n")
    assert frames[2].startswith("event: content\ndata: 答案\n\n")
    # 多行正文拆成多个 data 行
    assert "data: A" in frames[3] and "data: B" in frames[3]
    assert frames[-1].startswith("event: done\ndata: req-1\n\n")
    assert model.calls == ["m"]


def test_stream_plain_model_skips_empty_chunks():
    import asyncio

    class _Model:
        async def astream(self, messages):
            yield _Chunk({})
            yield _Chunk({"content": "x"})

    frames = asyncio.run(_collect(_stream_plain_model(_Model(), [], "req-2")))
    assert len(frames) == 2
    assert frames[0].startswith("event: content")
    assert frames[1].startswith("event: done")


# ===== Agent 模式事件流 =====

def test_stream_agent_events():
    import asyncio

    events = [
        {"event": "on_chat_model_stream", "data": {"chunk": _Chunk({"reasoning_content": "思考"})}},
        {"event": "on_tool_start", "name": "wiki_v2_space_getNode"},
        {"event": "on_chat_model_stream", "data": {"chunk": _Chunk({"content": "回答"})}},
        {"event": "on_chain_end", "data": {}},
    ]

    class _Agent:
        def __init__(self):
            self.cfg = None
            self.input_messages = None

        async def astream_events(self, inp, config=None, version=None):
            self.cfg = config
            self.input_messages = inp
            assert version == "v2"
            for e in events:
                yield e

    agent = _Agent()
    frames = asyncio.run(_collect(_stream_agent(agent, ["最新消息"], "thread-1", "req-3")))

    assert frames[0].startswith("event: reasoning\ndata: 思考\n\n")
    assert frames[1].startswith("event: tool\ndata: wiki_v2_space_getNode\n\n")
    assert frames[2].startswith("event: content\ndata: 回答\n\n")
    assert frames[-1].startswith("event: done\ndata: req-3\n\n")
    assert agent.input_messages == {"messages": ["最新消息"]}
    assert agent.cfg["configurable"]["thread_id"] == "thread-1"
    assert agent.cfg["recursion_limit"] == 100


def test_stream_agent_ignores_empty_chunks_and_missing_names():
    import asyncio

    events = [
        {"event": "on_chat_model_stream", "data": {}},
        {"event": "on_chat_model_stream", "data": {"chunk": _Chunk({})}},
        {"event": "on_tool_start", "name": ""},
        {"event": "on_chat_model_stream", "data": {"chunk": _Chunk({"content": "ok"})}},
    ]

    class _Agent:
        async def astream_events(self, inp, config=None, version=None):
            for e in events:
                yield e

    frames = asyncio.run(_collect(_stream_agent(_Agent(), [], "t", "req-4")))
    assert len(frames) == 2
    assert frames[0].startswith("event: content")
    assert frames[1].startswith("event: done")