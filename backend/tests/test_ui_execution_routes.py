"""ui_execution 路由校验逻辑测试(HTTPException 400 分支 + record 创建)。"""
import pytest
from fastapi import HTTPException
from sqlmodel import select

from db.models import TestCase, UIExecutionRecord


@pytest.fixture()
def fake_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester")


@pytest.fixture()
def fake_tc(db_session):
    tc = TestCase(case_name="功能用例", steps=["打开页面", "点击按钮"], expected_results=["页面变化"])
    db_session.add(tc)
    db_session.commit()
    db_session.refresh(tc)
    return tc


def _run(db_session, fake_user, fake_tc, monkeypatch, site_origin="http://a.com", page_path="#/login",
         testcase_id=None, max_steps=0, captured=None):
    import asyncio
    import app.routes.ui_execution as route
    from app.routes.ui_execution import RunRequest
    captured = captured if captured is not None else {}

    def fake_launch(db, record_id, agent_factory=None, login_overrides=None, max_steps=None):
        captured["record_id"] = record_id
        captured["login_overrides"] = login_overrides
        captured["max_steps"] = max_steps
        return "task-123"

    monkeypatch.setattr(route, "launch_ui_execution", fake_launch)
    # run_ui_execution 为 async def(Finding 1:后台 create_task 需真实事件循环)
    return asyncio.run(route.run_ui_execution(
        RunRequest(testcase_id=fake_tc.id if testcase_id is None else testcase_id,
                   site_origin=site_origin, page_path=page_path, max_steps=max_steps),
        db_session,
        fake_user,
    ))


def test_run_creates_record_and_launches(db_session, fake_user, fake_tc, monkeypatch):
    result = _run(db_session, fake_user, fake_tc, monkeypatch)
    assert result.data["task_id"] == "task-123"
    rec = db_session.exec(select(UIExecutionRecord)).first()
    assert rec is not None
    assert rec.status == "RUNNING"
    assert rec.target_url == "http://a.com#/login"


def test_run_defaults_max_steps_to_backend_default(db_session, fake_user, fake_tc, monkeypatch):
    """未传 max_steps 时,透传后端默认预算(而非 0)。"""
    from app.services.browser_agent import MAX_STEPS

    captured = {}
    _run(db_session, fake_user, fake_tc, monkeypatch, captured=captured)
    assert captured["max_steps"] == MAX_STEPS


def test_run_clamps_max_steps_to_limit(db_session, fake_user, fake_tc, monkeypatch):
    """显式传入的 max_steps 透传,超过上限时被裁剪。"""
    from app.services.browser_agent import MAX_STEPS_LIMIT

    captured = {}
    _run(db_session, fake_user, fake_tc, monkeypatch, max_steps=42, captured=captured)
    assert captured["max_steps"] == 42

    captured = {}
    _run(db_session, fake_user, fake_tc, monkeypatch, max_steps=99999, captured=captured)
    assert captured["max_steps"] == MAX_STEPS_LIMIT


def test_launch_creates_task_under_live_loop(db_session, fake_user, fake_tc):
    """Finding 1 pin:launch_ui_execution 在真实事件循环下 asyncio.create_task 不抛错。

    不 stub launch_ui_execution,直接在 asyncio.run 中调用真实实现;后台任务以
    真实 DB 中不存在的 record_id 运行,会快速失败(record 缺失),不会启动真浏览器。
    """
    import asyncio
    from types import SimpleNamespace

    import app.services.browser_agent as ba

    class StubAgent:
        async def run(self, max_steps=25):
            return SimpleNamespace(success=True, final_result=lambda: "stub")

    def stub_factory(llm, prompt, storage_state=None):
        return StubAgent()

    sentinel_record_id = 999_999_999  # 真实 DB 中不存在,后台任务不会误写任何记录

    async def _call_from_loop():
        # launch_ui_execution 是同步函数,但其内部 create_task 需要运行中的事件循环 ——
        # 复现路由 async def 后(Findng 1 修复)的调用上下文。
        return ba.launch_ui_execution(db_session, sentinel_record_id, agent_factory=stub_factory)

    task_id = asyncio.run(_call_from_loop())
    assert task_id in ba._ui_tasks
    assert ba._ui_tasks[task_id]["record_id"] == sentinel_record_id


def test_run_passes_login_overrides(db_session, fake_user, fake_tc, monkeypatch):
    """执行弹窗填写的登录配置必须经 login_overrides 传给 runner(不落库)。"""
    import asyncio
    import app.routes.ui_execution as route
    from app.routes.ui_execution import RunRequest
    captured = {}

    def fake_launch(db, record_id, agent_factory=None, login_overrides=None, max_steps=None):
        captured["login_overrides"] = login_overrides
        return "task-123"

    monkeypatch.setattr(route, "launch_ui_execution", fake_launch)
    asyncio.run(route.run_ui_execution(
        RunRequest(
            testcase_id=fake_tc.id,
            site_origin="http://a.com",
            page_path="#/login",
            login_url="http://sso.example.com/login",
            login_method="统一身份认证",
            login_username="tester",
            login_password="secret",
            login_company="建设集团公司",
        ),
        db_session,
        fake_user,
    ))
    assert captured["login_overrides"] == {
        "login_url": "http://sso.example.com/login",
        "login_method": "统一身份认证",
        "username": "tester",
        "password": "secret",
        "company": "建设集团公司",
    }


def test_run_does_not_persist_credentials_in_record(db_session, fake_user, fake_tc, monkeypatch):
    """凭据不得写入 UIExecutionRecord(避免落库泄露)。"""
    _run(db_session, fake_user, fake_tc, monkeypatch)
    rec = db_session.exec(select(UIExecutionRecord)).first()
    assert "secret" not in str(rec.model_dump())


def test_run_rejects_api_call_case(db_session, fake_user, fake_tc, monkeypatch):
    fake_tc.steps = [{"type": "api_call", "endpoint_id": 1}]
    db_session.add(fake_tc)
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        _run(db_session, fake_user, fake_tc, monkeypatch)
    assert exc.value.status_code == 400
    assert "接口测试用例" in exc.value.detail


def test_run_rejects_empty_origin(db_session, fake_user, fake_tc, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _run(db_session, fake_user, fake_tc, monkeypatch, site_origin="  ")
    assert exc.value.status_code == 400


def test_run_rejects_missing_case(db_session, fake_user, fake_tc, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _run(db_session, fake_user, fake_tc, monkeypatch, testcase_id=99999)
    assert exc.value.status_code == 404


def test_get_task_returns_state(db_session, fake_user, fake_tc, monkeypatch):
    import app.routes.ui_execution as route
    route._ui_tasks["t1"] = {"task_id": "t1", "status": "running", "phase": "executing",
                             "progress": 40, "message": "执行步骤 2", "record_id": 1,
                             "result_status": None, "error": None, "created_at": "2026-01-01"}
    try:
        result = route.get_ui_execution_task("t1", db_session, fake_user)
        assert result.data["phase"] == "executing"
    finally:
        route._ui_tasks.pop("t1", None)  # 不污染模块级 _ui_tasks(record_id=1 会与 runner 测试碰撞)


def test_get_task_404(db_session, fake_user):
    import app.routes.ui_execution as route
    with pytest.raises(HTTPException) as exc:
        route.get_ui_execution_task("nope", db_session, fake_user)
    assert exc.value.status_code == 404


def test_stop_task_404(db_session, fake_user):
    import app.routes.ui_execution as route
    with pytest.raises(HTTPException) as exc:
        route.stop_ui_execution_task("nope", db_session, fake_user)
    assert exc.value.status_code == 404


def test_stop_task_other_user_404(db_session, fake_user, fake_tc):
    import app.routes.ui_execution as route
    rec = UIExecutionRecord(testcase_id=fake_tc.id, user_id="someone-else",
                            target_url="http://a.com", status="RUNNING")
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)
    route._ui_tasks["stop-other"] = {"task_id": "stop-other", "status": "running", "record_id": rec.id}
    try:
        with pytest.raises(HTTPException) as exc:
            route.stop_ui_execution_task("stop-other", db_session, fake_user)
        assert exc.value.status_code == 404
    finally:
        route._ui_tasks.pop("stop-other", None)


def test_stop_task_success(db_session, fake_user, fake_tc, monkeypatch):
    import app.routes.ui_execution as route
    rec = UIExecutionRecord(testcase_id=fake_tc.id, user_id="u1",
                            target_url="http://a.com", status="RUNNING")
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)
    route._ui_tasks["stop-ok"] = {"task_id": "stop-ok", "status": "running", "record_id": rec.id}
    monkeypatch.setattr(route, "cancel_ui_execution", lambda tid: True)
    try:
        result = route.stop_ui_execution_task("stop-ok", db_session, fake_user)
        assert result.data["stopped"] is True
    finally:
        route._ui_tasks.pop("stop-ok", None)


def test_stop_task_already_finished(db_session, fake_user, fake_tc, monkeypatch):
    import app.routes.ui_execution as route
    rec = UIExecutionRecord(testcase_id=fake_tc.id, user_id="u1",
                            target_url="http://a.com", status="PASSED")
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)
    route._ui_tasks["stop-done"] = {"task_id": "stop-done", "status": "done", "record_id": rec.id}
    monkeypatch.setattr(route, "cancel_ui_execution", lambda tid: False)
    try:
        result = route.stop_ui_execution_task("stop-done", db_session, fake_user)
        assert result.data["stopped"] is False
    finally:
        route._ui_tasks.pop("stop-done", None)


def test_records_list_desc(db_session, fake_user, fake_tc):
    import app.routes.ui_execution as route
    for s in ("PASSED", "FAILED"):
        db_session.add(UIExecutionRecord(testcase_id=fake_tc.id, user_id="u1",
                                         target_url="http://a.com", status=s))
    db_session.commit()
    result = route.list_ui_execution_records(db_session, fake_user, fake_tc.id)
    assert [r.status for r in result.data] == ["FAILED", "PASSED"]  # id 倒序


def test_run_supports_site_origin_variable(db_session, fake_user, fake_tc, monkeypatch):
    """站点地址支持 {{var}}:执行时按默认环境参数解析,未定义则 400。"""
    from db.models import GlobalParameter

    db_session.add(GlobalParameter(
        name="默认环境", is_default=True,
        parameters=[{"key": "web_url", "value": "http://env-a.com"}],
    ))
    db_session.commit()

    rec = _run(db_session, fake_user, fake_tc, monkeypatch, site_origin="{{web_url}}", page_path="#/login")
    row = db_session.exec(select(UIExecutionRecord)).first()
    assert row.target_url == "http://env-a.com#/login"

    import pytest
    from fastapi import HTTPException  # noqa: F811

    with pytest.raises(HTTPException) as exc:
        _run(db_session, fake_user, fake_tc, monkeypatch, site_origin="{{nope}}", page_path="#/login")
    assert exc.value.status_code == 400
    assert "nope" in exc.value.detail
