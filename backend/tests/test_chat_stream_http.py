"""chat 直连 HTTP 流式路径测试（_stream_plain_model_http）。

langchain_openai 流式会丢弃 DeepSeek reasoning_content，因此无工具路径
绕过 langchain 直连 OpenAI 兼容接口。本测试用 httpx.MockTransport 模拟
SSE 上游，验证 reasoning/content 事件的解析与 thinking 开关透传。不触网。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest

from app.routes.chat import ChatMessage, _stream_plain_model_http


def _sse_payload(deltas):
    """把 delta 列表转成上游 SSE 文本。"""
    lines = []
    for d in deltas:
        lines.append("data: " + json.dumps({"choices": [{"delta": d}]}))
    lines.append("data: [DONE]")
    return "\n".join(lines) + "\n"


async def _collect(agen):
    return [frame async for frame in agen]


def test_stream_http_parses_reasoning_and_content(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        captured["auth"] = request.headers.get("Authorization")
        deltas = [
            {"reasoning_content": "先"},
            {"reasoning_content": "想想"},
            {"content": "答"},
            {"content": "案"},
            {},
        ]
        return httpx.Response(200, text=_sse_payload(deltas), headers={"content-type": "text/event-stream"})

    real_client = httpx.AsyncClient

    class _Factory:
        def __call__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _Factory(),
    )

    msgs = [ChatMessage(role="user", content="17*23=?")]
    frames = asyncio.run(_collect(_stream_plain_model_http(
        api_key="sk-test",
        api_base_url="https://api.example.com",
        api_model="m",
        system_content="sys",
        history_messages=msgs,
        max_tokens=256,
        show_thinking=True,
        request_id="req-9",
    )))
    collected = []
    for f in frames:
        event = f.split("\n")[0][7:]
        data_lines = [l[6:] for l in f.split("\n") if l.startswith("data: ")]
        collected.append((event, "".join(data_lines)))

    events = [e for e, _ in collected]
    assert events == ["reasoning", "reasoning", "content", "content", "done"]
    assert collected[0][1] == "先"
    assert collected[2][1] == "答"
    assert collected[-1][1] == "req-9"

    # 请求体校验：thinking 透传 + 消息结构
    body = captured["body"]
    assert body["thinking"] == {"type": "enabled"}
    assert body["stream"] is True
    assert body["max_tokens"] == 256
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1] == {"role": "user", "content": "17*23=?"}
    assert captured["auth"] == "Bearer sk-test"


def test_stream_http_thinking_disabled_flag(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, text=_sse_payload([{"content": "ok"}]), headers={"content-type": "text/event-stream"})

    real_client = httpx.AsyncClient

    class _Factory:
        def __call__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Factory())

    frames = asyncio.run(_collect(_stream_plain_model_http(
        api_key="sk-test",
        api_base_url="https://api.example.com",
        api_model="m",
        system_content="s",
        history_messages=[],
        max_tokens=64,
        show_thinking=False,
        request_id="r",
    )))
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert len(frames) == 2  # content + done


def test_stream_http_non_200_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"detail":"bad key"}')

    real_client = httpx.AsyncClient

    class _Factory:
        def __call__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Factory())

    with pytest.raises(RuntimeError, match="401"):
        asyncio.run(_collect(_stream_plain_model_http(
            api_key="sk-bad",
            api_base_url="https://api.example.com",
            api_model="m",
            system_content="s",
            history_messages=[],
            max_tokens=64,
            show_thinking=True,
            request_id="r",
        )))
def test_stream_http_length_truncation_emits_error(monkeypatch):
    """finish_reason=length 时发 error 事件并终止（不再继续收 content）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        deltas = [
            {"content": "部分答案"},
            {"content": "还是部分"},
            {"content": "不应该出现"},
        ]
        lines = []
        for i, d in enumerate(deltas):
            chunk = {"choices": [{"delta": d}]}
            if i == 1:
                chunk["choices"][0]["finish_reason"] = "length"
            lines.append("data: " + json.dumps(chunk))
        lines.append("data: [DONE]")
        return httpx.Response(200, text="\n".join(lines) + "\n", headers={"content-type": "text/event-stream"})

    real_client = httpx.AsyncClient

    class _Factory:
        def __call__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Factory())

    frames = asyncio.run(_collect(_stream_plain_model_http(
        api_key="sk-test",
        api_base_url="https://api.example.com",
        api_model="m",
        system_content="s",
        history_messages=[ChatMessage(role="user", content="q")],
        max_tokens=64,
        show_thinking=False,
        request_id="rt",
    )))
    events = [f.split("\n")[0][7:] for f in frames]
    assert events == ["content", "content", "error", "done"], events
    # 截断后不再接收后续 content
    assert "不应该出现" not in "".join(frames)