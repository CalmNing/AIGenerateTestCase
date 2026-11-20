"""chat 工具加载门控测试。

业务模式（requirement_clarify / testcase_optimize）的正文已注入上下文，
必须跳过 MCP 工具加载（省 STDIO 启动与工具 schema token，走直连流式）；
通用模式保持原有加载行为。不触网、不启动真实子进程。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routes.chat import _load_chat_tools


def test_business_modes_skip_mcp(monkeypatch):
    """业务模式即使配置了 MCP 服务器也不加载工具。"""
    called = False

    def _boom(session, user):
        nonlocal called
        called = True
        return [{"name": "lark"}]

    monkeypatch.setattr("app.routes.chat._load_user_mcp_configs", _boom)
    for mode in ("requirement_clarify", "testcase_optimize"):
        tools = asyncio.run(_load_chat_tools(None, None, mode))
        assert tools == []
    assert not called, "业务模式不应触碰 MCP 配置加载"


def test_general_mode_loads_tools(monkeypatch):
    """通用模式保持原有行为：加载配置并构建工具。"""
    monkeypatch.setattr(
        "app.routes.chat._load_user_mcp_configs",
        lambda session, user: [{"name": "lark"}],
    )

    async def _fake_build(configs):
        assert configs == [{"name": "lark"}]
        return ["tool-a", "tool-b"]

    import utils.lanhu_mcp_adapter as adapter
    monkeypatch.setattr(adapter, "build_tools_from_configs", _fake_build)
    tools = asyncio.run(_load_chat_tools(None, None, "general"))
    assert tools == ["tool-a", "tool-b"]


def test_general_mode_build_failure_returns_empty(monkeypatch):
    """工具构建失败时降级为空列表，不影响对话主流程。"""
    monkeypatch.setattr(
        "app.routes.chat._load_user_mcp_configs",
        lambda session, user: [{"name": "lark"}],
    )

    async def _boom(configs):
        raise RuntimeError("mcp down")

    import utils.lanhu_mcp_adapter as adapter
    monkeypatch.setattr(adapter, "build_tools_from_configs", _boom)
    tools = asyncio.run(_load_chat_tools(None, None, "general"))
    assert tools == []


def test_general_mode_no_configs_returns_empty(monkeypatch):
    monkeypatch.setattr("app.routes.chat._load_user_mcp_configs", lambda session, user: [])
    tools = asyncio.run(_load_chat_tools(None, None, "general"))
    assert tools == []