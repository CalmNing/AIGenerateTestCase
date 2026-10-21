"""Tests for WebSocket 订阅路由：地址归一化、变量替换、消息采集与日志落库。

纯单元测试，使用内存 SQLite（conftest.db_session）；网络部分用本机 127.0.0.1 上临时起的
websockets.serve 测试服务器，不访问任何外网 / 真实服务。
"""
import asyncio
import base64

import pytest
import websockets
from sqlmodel import select

import app.routes.websocket as ws_mod
from app.auth import UserInfo
from app.routes.websocket import (
    WsConnectRequest,
    WsTestConnectionRequest,
    _collect_messages,
    _normalize_ws_url,
    connect_ws,
    test_ws_connection as ws_test_connection,
)
from db.models import GlobalParameter, PushLog, SavedRequest


def _user(roles=None, user_id="u1") -> UserInfo:
    """构造测试用户（roles 为空时无任何权限）"""
    return UserInfo(user_id=user_id, username="tester", roles=roles or [])


def _connect(db_session, user, **kwargs):
    """在事件循环里调用连接接口（接口本身是 async 的）"""
    request = WsConnectRequest(**kwargs)
    return asyncio.run(connect_ws(request=request, user=user, db=db_session))


def _test_conn(user, **kwargs):
    """在事件循环里调用连通性测试接口"""
    request = WsTestConnectionRequest(**kwargs)
    return asyncio.run(ws_test_connection(request=request, user=user))


def _run_with_server(handler, coro_factory):
    """在 127.0.0.1 随机端口起本地测试服务器，执行传入的协程后关闭服务器"""

    async def _main():
        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await coro_factory(f"ws://127.0.0.1:{port}")
        finally:
            server.close()
            await server.wait_closed()

    return asyncio.run(_main())


class TestNormalizeWsUrl:
    def test_ws_url_unchanged(self):
        assert _normalize_ws_url("ws://127.0.0.1:9000/api/logs?id=1") == \
            "ws://127.0.0.1:9000/api/logs?id=1"

    def test_wss_url_unchanged(self):
        assert _normalize_ws_url("wss://example.com/socket") == "wss://example.com/socket"

    def test_bare_host_gets_ws_scheme(self):
        assert _normalize_ws_url("example.com:9000/logs") == "ws://example.com:9000/logs"

    def test_bare_host_without_port(self):
        assert _normalize_ws_url("example.com") == "ws://example.com"

    def test_http_maps_to_ws(self):
        assert _normalize_ws_url("http://example.com/socket") == "ws://example.com/socket"

    def test_https_maps_to_wss(self):
        assert _normalize_ws_url("https://example.com/socket") == "wss://example.com/socket"

    def test_strips_surrounding_spaces(self):
        assert _normalize_ws_url("  ws://example.com/x  ") == "ws://example.com/x"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _normalize_ws_url("")

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError):
            _normalize_ws_url("ftp://example.com/x")


class TestCollectMessages:
    def test_receives_text_messages(self):
        """正常收消息：连接成功、内容/类型/序号正确"""

        async def handler(ws):
            await ws.send("hello")
            await ws.send("world")
            await asyncio.sleep(0.2)

        def make(url):
            async def run():
                return await _collect_messages({
                    "url": url, "max_messages": 2, "timeout_seconds": 2,
                })
            return run()

        result = _run_with_server(handler, make)
        assert result["connected"] is True
        assert result["success"] is True
        assert result["received"] == 2
        assert [m["content"] for m in result["messages"]] == ["hello", "world"]
        assert [m["seq"] for m in result["messages"]] == [0, 1]
        assert all(m["type"] == "text" for m in result["messages"])
        assert all(m["size"] == len(m["content"].encode("utf-8"))
                   for m in result["messages"])
        assert result["closed_by"] == "max_messages"
        assert result["error"] is None
        assert result["_last_content"] == "world"

    def test_stops_at_max_messages(self):
        """收满 max_messages 立即退出，不多收"""

        async def handler(ws):
            for i in range(10):
                await ws.send(f"msg-{i}")
            await asyncio.sleep(0.5)

        def make(url):
            async def run():
                return await _collect_messages({
                    "url": url, "max_messages": 3, "timeout_seconds": 2,
                })
            return run()

        result = _run_with_server(handler, make)
        assert result["received"] == 3
        assert [m["content"] for m in result["messages"]] == ["msg-0", "msg-1", "msg-2"]
        assert result["closed_by"] == "max_messages"

    def test_server_close_is_reported_as_server(self):
        """服务端主动关闭：closed_by=server，已收到的消息保留"""

        async def handler(ws):
            await ws.send("bye")
            await ws.close()

        def make(url):
            async def run():
                return await _collect_messages({
                    "url": url, "max_messages": 50, "timeout_seconds": 3,
                })
            return run()

        result = _run_with_server(handler, make)
        assert result["connected"] is True
        assert result["received"] == 1
        assert result["messages"][0]["content"] == "bye"
        assert result["closed_by"] == "server"
        # 正常关闭不算错误
        assert result["error"] is None

    def test_idle_timeout(self):
        """服务端不推送：空闲超时后返回 timeout，消息为空"""

        async def handler(ws):
            await asyncio.sleep(5)

        def make(url):
            async def run():
                return await _collect_messages({
                    "url": url, "max_messages": 10, "timeout_seconds": 1,
                })
            return run()

        result = _run_with_server(handler, make)
        assert result["connected"] is True
        assert result["received"] == 0
        assert result["closed_by"] == "timeout"
        assert result["error"] is None

    def test_binary_message_encoded_as_base64(self):
        """二进制消息：type=binary、content 占位、base64 有值"""

        async def handler(ws):
            await ws.send(b"\x00\x01\x02binary")
            await asyncio.sleep(0.2)

        def make(url):
            async def run():
                return await _collect_messages({
                    "url": url, "max_messages": 1, "timeout_seconds": 2,
                })
            return run()

        result = _run_with_server(handler, make)
        msg = result["messages"][0]
        assert msg["type"] == "binary"
        assert msg["content"] == "<binary 9 bytes>"
        assert msg["size"] == 9
        assert base64.b64decode(msg["base64"]) == b"\x00\x01\x02binary"

    def test_send_message_sent_on_connect(self):
        """连接成功后先发送 send_message"""
        received = []

        async def handler(ws):
            received.append(await ws.recv())
            await ws.send("ack")
            await asyncio.sleep(0.2)

        def make(url):
            async def run():
                return await _collect_messages({
                    "url": url, "max_messages": 1, "timeout_seconds": 2,
                    "send_message": "subscribe:all",
                })
            return run()

        result = _run_with_server(handler, make)
        assert received == ["subscribe:all"]
        assert result["messages"][0]["content"] == "ack"

    def test_connection_refused_is_error(self):
        """端口无人监听：connected=False、closed_by=error、带错误信息"""
        # 先占用一个端口再释放，确保该端口无人监听
        async def _get_closed_port():
            server = await websockets.serve(lambda ws: None, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            server.close()
            await server.wait_closed()
            return port

        port = asyncio.run(_get_closed_port())
        result = asyncio.run(_collect_messages({
            "url": f"ws://127.0.0.1:{port}/x", "max_messages": 1, "timeout_seconds": 1,
        }))
        assert result["connected"] is False
        assert result["success"] is False
        assert result["closed_by"] == "error"
        assert result["error"]
        assert result["messages"] == []


class TestConnectVariables:
    def test_substitutes_url_and_headers(self, db_session, monkeypatch):
        env = GlobalParameter(
            name="env", is_default=True,
            parameters=[
                {"key": "host", "value": "127.0.0.1"},
                {"key": "token", "value": "abc"},
            ],
        )
        db_session.add(env)
        db_session.commit()

        captured = {}

        async def fake_collect(cfg):
            captured.update(cfg)
            return {
                "success": True, "url": cfg["url"], "connected": True,
                "messages": [], "received": 0, "duration_ms": 5,
                "closed_by": "timeout", "error": None, "_last_content": None,
            }

        monkeypatch.setattr(ws_mod, "_collect_messages", fake_collect)
        res = _connect(
            db_session, _user(),
            url="ws://{{host}}:9000/api/logs?token={{token}}",
            headers={"Authorization": "Bearer {{token}}"},
        )

        assert res.code == 200
        assert res.data["success"] is True
        assert captured["url"] == "ws://127.0.0.1:9000/api/logs?token=abc"
        assert captured["headers"]["Authorization"] == "Bearer abc"

    def test_unresolved_variable_returns_400_and_logs(self, db_session, monkeypatch):
        called = []

        async def fake_collect(cfg):
            called.append(1)
            return {}

        monkeypatch.setattr(ws_mod, "_collect_messages", fake_collect)
        res = _connect(
            db_session, _user(),
            url="ws://example.com/logs?id={{unknown_var}}",
        )

        assert res.code == 400
        assert res.data["success"] is False
        assert "unknown_var" in res.message
        assert called == []

        log = db_session.exec(select(PushLog)).one()
        assert log.protocol == "websocket"
        assert log.error and "unknown_var" in log.error

    def test_invalid_url_returns_400(self, db_session, monkeypatch):
        async def fake_collect(cfg):
            return {}

        monkeypatch.setattr(ws_mod, "_collect_messages", fake_collect)
        res = _connect(db_session, _user(), url="ftp://example.com/x")
        assert res.code == 400
        assert res.data["success"] is False
        assert "WebSocket 地址非法" in res.message

    def test_mapping_parameters_are_substituted(self, db_session, monkeypatch):
        """前端传映射形态 parameters 时变量同样生效"""
        captured = {}

        async def fake_collect(cfg):
            captured.update(cfg)
            return {
                "success": True, "url": cfg["url"], "connected": True,
                "messages": [], "received": 0, "duration_ms": 3,
                "closed_by": "timeout", "error": None, "_last_content": None,
            }

        monkeypatch.setattr(ws_mod, "_collect_messages", fake_collect)
        res = _connect(
            db_session, _user(),
            url="ws://example.com/logs?id={{device}}",
            parameters={"device": "sensor-1"},
        )
        assert res.code == 200
        assert captured["url"] == "ws://example.com/logs?id=sensor-1"

    def test_environment_downgraded_without_permission(self, db_session, monkeypatch):
        """无 global_parameter:manage 权限时 environment_id 降级，改用默认环境参数"""
        target = GlobalParameter(
            name="restricted", is_default=False,
            parameters=[{"key": "device", "value": "restricted"}],
        )
        default = GlobalParameter(
            name="default", is_default=True,
            parameters=[{"key": "device", "value": "default-device"}],
        )
        db_session.add_all([target, default])
        db_session.commit()
        db_session.refresh(target)

        captured = {}

        async def fake_collect(cfg):
            captured.update(cfg)
            return {
                "success": True, "url": cfg["url"], "connected": True,
                "messages": [], "received": 0, "duration_ms": 3,
                "closed_by": "timeout", "error": None, "_last_content": None,
            }

        monkeypatch.setattr(ws_mod, "_collect_messages", fake_collect)
        res = _connect(
            db_session, _user(),
            url="ws://example.com/logs?id={{device}}",
            environment_id=target.id,
        )
        assert res.code == 200
        assert captured["url"] == "ws://example.com/logs?id=default-device"
        log = db_session.exec(select(PushLog)).one()
        assert log.environment_id is None


class TestConnectLog:
    def test_success_writes_websocket_log(self, db_session, monkeypatch):
        async def fake_collect(cfg):
            return {
                "success": True, "url": cfg["url"], "connected": True,
                "messages": [
                    {"seq": 0, "type": "text", "size": 5, "content": "hello", "ts": 0.1},
                ],
                "received": 1, "duration_ms": 12, "closed_by": "max_messages",
                "error": None, "_last_content": "hello",
            }

        monkeypatch.setattr(ws_mod, "_collect_messages", fake_collect)
        res = _connect(
            db_session, _user(user_id="u9"),
            url="ws://example.com/logs?id=1",
            headers={"X-Token": "t1"},
            send_message="sub",
        )

        assert res.code == 200
        assert res.data["received"] == 1
        assert res.data["closed_by"] == "max_messages"
        log = db_session.exec(select(PushLog)).one()
        assert log.protocol == "websocket"
        assert log.request_method == "WS"
        assert log.source_url == "ws://example.com/logs?id=1"
        assert log.final_url == "ws://example.com/logs?id=1"
        assert log.request_headers == {"X-Token": "t1"}
        assert log.request_body == "sub"
        assert log.final_body == "hello"
        assert log.response_status == 0
        assert log.response_headers == {}
        assert log.error is None
        assert log.user_id == "u9"

    def test_final_body_truncated_to_4000_chars(self, db_session, monkeypatch):
        long_text = "x" * 5000

        async def fake_collect(cfg):
            return {
                "success": True, "url": cfg["url"], "connected": True,
                "messages": [
                    {"seq": 0, "type": "text", "size": 5000, "content": long_text, "ts": 0.1},
                ],
                "received": 1, "duration_ms": 9, "closed_by": "max_messages",
                "error": None, "_last_content": long_text,
            }

        monkeypatch.setattr(ws_mod, "_collect_messages", fake_collect)
        _connect(db_session, _user(), url="ws://example.com/logs")
        log = db_session.exec(select(PushLog)).one()
        assert len(log.final_body) == 4000

    def test_failure_writes_failed_log(self, db_session, monkeypatch):
        async def fake_collect(cfg):
            return {
                "success": False, "url": cfg["url"], "connected": False,
                "messages": [], "received": 0, "duration_ms": 15,
                "closed_by": "error", "error": "connection refused",
                "_last_content": None,
            }

        monkeypatch.setattr(ws_mod, "_collect_messages", fake_collect)
        res = _connect(
            db_session, _user(user_id="u2"), url="ws://example.com/logs",
        )

        assert res.code == 500
        assert res.data["success"] is False
        assert "WebSocket 连接失败" in res.message
        assert res.data["error"] == "connection refused"

        log = db_session.exec(select(PushLog)).one()
        assert log.protocol == "websocket"
        assert log.error == "connection refused"
        assert log.user_id == "u2"

    def test_log_save_failure_does_not_break_response(self, db_session, monkeypatch):
        async def fake_collect(cfg):
            return {
                "success": True, "url": cfg["url"], "connected": True,
                "messages": [], "received": 0, "duration_ms": 3,
                "closed_by": "timeout", "error": None, "_last_content": None,
            }

        monkeypatch.setattr(ws_mod, "_collect_messages", fake_collect)

        def broken_commit():
            raise RuntimeError("db down")

        monkeypatch.setattr(db_session, "commit", broken_commit)
        monkeypatch.setattr(db_session, "rollback", lambda: None)

        res = _connect(db_session, _user(), url="ws://example.com/logs")
        assert res.code == 200
        assert res.data["success"] is True


class TestTestConnection:
    def test_connected_success_against_local_server(self):
        """真实握手成功（本地测试服务器）"""

        async def handler(ws):
            await asyncio.sleep(0.1)

        async def _main():
            server = await websockets.serve(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            try:
                return await ws_test_connection(
                    request=WsTestConnectionRequest(url=f"ws://127.0.0.1:{port}/x"),
                    user=_user(),
                )
            finally:
                server.close()
                await server.wait_closed()

        res = asyncio.run(_main())
        assert res.code == 200
        assert res.data["connected"] is True
        assert res.data["message"] == "连接成功"

    def test_connected_failure_keeps_code_200(self, monkeypatch):
        async def boom(cfg):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(ws_mod, "_probe_connection", boom)
        res = _test_conn(_user(), url="ws://example.com/logs")
        assert res.code == 200
        assert res.data["connected"] is False
        assert "connection refused" in res.data["message"]

    def test_invalid_url_returns_not_connected(self):
        res = _test_conn(_user(), url="ftp://example.com/x")
        assert res.code == 200
        assert res.data["connected"] is False
        assert "WebSocket 地址非法" in res.data["message"]

    def test_passes_headers_and_protocols(self, monkeypatch):
        captured = {}

        async def fake_probe(cfg):
            captured.update(cfg)

        monkeypatch.setattr(ws_mod, "_probe_connection", fake_probe)
        _test_conn(
            _user(), url="wss://example.com/logs",
            headers={"Authorization": "Bearer t"}, protocols=["graphql-ws"],
        )
        assert captured["url"] == "wss://example.com/logs"
        assert captured["headers"] == {"Authorization": "Bearer t"}
        assert captured["protocols"] == ["graphql-ws"]


class TestSavedRequestWsConfig:
    def test_ws_config_persists_after_commit_and_refresh(self, db_session):
        """SavedRequest.ws_config 落库后读回内容一致（JSON 字段序列化/反序列化）"""
        ws_config = {
            "url": "ws://example/ws",
            "maxMessages": 10,
            "timeoutSeconds": 5,
        }
        saved = SavedRequest(
            protocol="websocket",
            ws_config=ws_config,
            name="ws-test",
            method="GET",
            url="ws://example/ws",
        )
        db_session.add(saved)
        db_session.commit()
        db_session.refresh(saved)

        assert saved.id is not None
        assert saved.protocol == "websocket"
        assert saved.ws_config == ws_config
