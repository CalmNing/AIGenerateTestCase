"""定时任务 file_params 分支进程内转发测试。

回归背景：调度器原先通过 HTTP 自调用 /api/proxy/forward（无 Bearer token，
必然 401 Not authenticated）。现改为进程内直调 forward_core(user=None)。

覆盖：
- 调度器确实走 forward_core（不再发起 HTTP 自调用）
- user=None 时环境权限按「无权限」降级：传入的 environment_id 被忽略，
  落到 build_param_map 的默认环境降级链
- 有认证用户且具备 global_parameter:manage 权限时 environment_id 生效
- 上游非 2xx 响应原样透传 status_code/body
- 上游抛 HTTPException 时调度器 except 分支产出 status=error 结果
- _save_push_log 对 user=None 安全（PushLog.user_id 落 NULL）
"""
import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException
from sqlmodel import select

import app.scheduler as scheduler_mod
import app.routes.proxy as proxy_mod
from app.routes.proxy import ProxyRequest, forward_core
from db.models import GlobalParameter, PushLog, SavedRequest, ScheduledTask


class FakeUser:
    def __init__(self, roles):
        self.user_id = "user-1"
        self.roles = roles


def _stub_http(monkeypatch=None, response_status=200, response_json=None):
    """把 httpx.AsyncClient.request 打桩成固定响应，并捕获请求 URL。"""
    captured = {"url": None, "kwargs": None}

    class FakeResponse:
        status_code = response_status
        headers = {}

        def json(self):
            return response_json if response_json is not None else {}

    async def fake_request(self, method=None, url=None, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    if monkeypatch is not None:
        monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    else:
        original = httpx.AsyncClient.request
        httpx.AsyncClient.request = fake_request
    return captured


def _restore_http():
    """手写桩的还原交给 monkeypatch 的用例不需要；非 monkeypatch 用例用 try/finally。"""
    pass


# ---- forward_core 单元行为 ----


def test_forward_core_none_user_ignores_environment_id(db_session, monkeypatch):
    """user=None（内部链路）时 environment_id 必须被忽略，走默认环境降级。"""
    env = GlobalParameter(
        name="指定环境",
        parameters=[{"key": "mark", "value": "from-env"}],
        is_default=False,
    )
    default_env = GlobalParameter(
        name="默认环境",
        parameters=[{"key": "mark", "value": "from-default"}],
        is_default=True,
    )
    db_session.add(env)
    db_session.add(default_env)
    db_session.commit()

    captured = _stub_http(monkeypatch)

    result = asyncio.run(forward_core(
        ProxyRequest(url="http://{{mark}}/ping", method="GET", environment_id=env.id),
        user=None,
        db=db_session,
    ))

    assert result["status_code"] == 200
    # environment_id 被忽略，{{mark}} 落到默认环境的值
    assert captured["url"] == "http://from-default/ping"


def test_forward_core_privileged_user_uses_environment_id(db_session, monkeypatch):
    """有认证用户且具备 global_parameter:manage 权限时 environment_id 生效。"""
    env = GlobalParameter(
        name="指定环境",
        parameters=[{"key": "mark", "value": "from-env"}],
        is_default=False,
    )
    default_env = GlobalParameter(
        name="默认环境",
        parameters=[{"key": "mark", "value": "from-default"}],
        is_default=True,
    )
    db_session.add(env)
    db_session.add(default_env)
    db_session.commit()

    captured = _stub_http(monkeypatch)

    result = asyncio.run(forward_core(
        ProxyRequest(url="http://{{mark}}/ping", method="GET", environment_id=env.id),
        user=FakeUser(["admin"]),
        db=db_session,
    ))

    assert result["status_code"] == 200
    assert captured["url"] == "http://from-env/ping"


def test_forward_core_unprivileged_user_ignores_environment_id(db_session, monkeypatch):
    """无 global_parameter:manage 权限的用户 environment_id 降级为 None。"""
    env = GlobalParameter(
        name="指定环境",
        parameters=[{"key": "mark", "value": "from-env"}],
        is_default=False,
    )
    default_env = GlobalParameter(
        name="默认环境",
        parameters=[{"key": "mark", "value": "from-default"}],
        is_default=True,
    )
    db_session.add(env)
    db_session.add(default_env)
    db_session.commit()

    captured = _stub_http(monkeypatch)

    asyncio.run(forward_core(
        ProxyRequest(url="http://{{mark}}/ping", method="GET", environment_id=env.id),
        user=FakeUser(["user"]),
        db=db_session,
    ))

    assert captured["url"] == "http://from-default/ping"


def test_forward_core_upstream_non_2xx_passthrough(db_session, monkeypatch):
    """上游非 2xx 响应原样透传 status_code 与 body。"""
    captured = _stub_http(monkeypatch, response_status=500, response_json={"code": 50000, "msg": "err"})

    result = asyncio.run(forward_core(
        ProxyRequest(url="http://upstream.example.com/x", method="GET"),
        user=None,
        db=db_session,
    ))

    assert result["status_code"] == 500
    assert result["data"] == {"code": 50000, "msg": "err"}


def test_forward_core_http_exception_propagates(db_session, monkeypatch):
    """上游连接失败时 HTTPException 原样抛出，供调度器 except 捕获。"""

    async def fake_request(self, method=None, url=None, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(forward_core(
            ProxyRequest(url="http://upstream.example.com/x", method="GET"),
            user=None,
            db=db_session,
        ))

    assert exc_info.value.status_code == 500
    assert "boom" in str(exc_info.value.detail)


def test_forward_core_none_user_push_log_safe(db_session, monkeypatch):
    """user=None 时推送日志必须能落库（PushLog.user_id 为 NULL）。"""
    _stub_http(monkeypatch, response_json={"ok": True})

    result = asyncio.run(forward_core(
        ProxyRequest(url="http://upstream.example.com/x", method="GET"),
        user=None,
        db=db_session,
    ))

    assert result["status_code"] == 200
    logs = db_session.exec(select(PushLog)).all()
    assert len(logs) == 1
    assert logs[0].user_id is None
    assert logs[0].response_status == 200


def test_forward_core_file_params_injected_into_param_map(db_session, monkeypatch):
    """file_params 的 fileId 按 key 注入变量表，{{key}} 解析为 fileId。"""
    captured = _stub_http(monkeypatch, response_json={"ok": True})

    result = asyncio.run(forward_core(
        ProxyRequest(
            url="http://upstream.example.com/upload",
            method="POST",
            data={"file": "{{f}}"},
            file_params=[{"key": "f", "fileId": "fid-123", "fileName": "a.jpg"}],
        ),
        user=None,
        db=db_session,
    ))

    assert result["status_code"] == 200
    assert captured["kwargs"].get("json") == {"file": "fid-123"}


# ---- 调度器集成：file_params 分支 ----


def _setup_scheduler_env(db_session, monkeypatch, db_engine, env_id=None):
    """插入真实的 ScheduledTask 与 SavedRequest，并把调度器指到测试引擎。

    不 patch Session：调度器自建会话写结果并 commit，测试会话负责读取断言，
    避免会话关闭导致的实例分离问题。
    """
    task = ScheduledTask(
        name="t", schedule_type="interval", interval_seconds=60,
        request_ids=[1], environment_id=env_id, enabled=True,
    )
    saved = SavedRequest(
        name="带文件参数的请求",
        method="POST",
        url="http://upstream.example.com/api",
        headers=[],
        parameters=[{"key": "f", "type": "file", "value": "fid-1", "fileName": "a.jpg"}],
    )
    db_session.add(task)
    db_session.add(saved)
    db_session.commit()
    monkeypatch.setattr(scheduler_mod, "engine", db_engine)
    return task


def test_scheduler_uses_forward_core_not_http_self_call(monkeypatch, db_session, db_engine):
    """file_params 分支必须进程内直调 forward_core(user=None)。"""
    task = _setup_scheduler_env(db_session, monkeypatch, db_engine, env_id=7)

    calls = []

    async def fake_forward_core(request, user, db):
        calls.append({"request": request, "user": user})
        return {"status_code": 200, "headers": {}, "data": {"ok": True}}

    monkeypatch.setattr(scheduler_mod, "forward_core", fake_forward_core)

    asyncio.run(scheduler_mod.execute_scheduled_task(task.id))

    assert len(calls) == 1
    assert calls[0]["user"] is None
    req = calls[0]["request"]
    assert isinstance(req, proxy_mod.ProxyRequest)
    assert req.file_params == [{"key": "f", "fileId": "fid-1", "fileName": "a.jpg"}]
    assert req.environment_id == 7


def test_scheduler_error_entry_on_http_exception(monkeypatch, db_session, db_engine):
    """forward_core 抛 HTTPException 时，执行结果里必须是 status=error 条目。"""
    task = _setup_scheduler_env(db_session, monkeypatch, db_engine)

    async def fake_forward_core(request, user, db):
        raise HTTPException(status_code=500, detail="Proxy error: boom")

    monkeypatch.setattr(scheduler_mod, "forward_core", fake_forward_core)

    asyncio.run(scheduler_mod.execute_scheduled_task(task.id))
    db_session.expire(task)
    db_session.refresh(task)

    result = json.loads(task.last_run_result)
    assert result[0]["status"] == "error"
    assert "boom" in result[0]["detail"]


def test_scheduler_success_entry_written(monkeypatch, db_session, db_engine):
    """forward_core 返回 2xx 时，执行结果必须是 status=success 条目。"""
    task = _setup_scheduler_env(db_session, monkeypatch, db_engine)

    async def fake_forward_core(request, user, db):
        return {"status_code": 200, "headers": {}, "data": {"ok": True}}

    monkeypatch.setattr(scheduler_mod, "forward_core", fake_forward_core)

    asyncio.run(scheduler_mod.execute_scheduled_task(task.id))
    db_session.expire(task)
    db_session.refresh(task)

    result = json.loads(task.last_run_result)
    assert result[0]["status"] == "success"
    assert result[0]["status_code"] == 200
    assert result[0]["response"]["body"] == {"ok": True}


def test_scheduler_no_http_self_call_constant():
    """源代码里不允许再出现硬编码的本地回环自调用地址。"""
    import inspect

    source = inspect.getsource(scheduler_mod)
    assert "127.0.0.1:8000" not in source
    assert "/api/proxy/forward" not in source
