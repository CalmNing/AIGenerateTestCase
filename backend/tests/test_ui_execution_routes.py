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
         testcase_id=None):
    import app.routes.ui_execution as route
    from app.routes.ui_execution import RunRequest
    captured = {}

    def fake_launch(db, record_id, agent_factory=None):
        captured["record_id"] = record_id
        return "task-123"

    monkeypatch.setattr(route, "launch_ui_execution", fake_launch)
    return route.run_ui_execution(
        RunRequest(testcase_id=fake_tc.id if testcase_id is None else testcase_id,
                   site_origin=site_origin, page_path=page_path),
        db_session,
        fake_user,
    )


def test_run_creates_record_and_launches(db_session, fake_user, fake_tc, monkeypatch):
    result = _run(db_session, fake_user, fake_tc, monkeypatch)
    assert result.data["task_id"] == "task-123"
    rec = db_session.exec(select(UIExecutionRecord)).first()
    assert rec is not None
    assert rec.status == "RUNNING"
    assert rec.target_url == "http://a.com#/login"


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
    result = route.get_ui_execution_task("t1", db_session, fake_user)
    assert result.data["phase"] == "executing"


def test_get_task_404(db_session, fake_user):
    import app.routes.ui_execution as route
    with pytest.raises(HTTPException) as exc:
        route.get_ui_execution_task("nope", db_session, fake_user)
    assert exc.value.status_code == 404


def test_records_list_desc(db_session, fake_user, fake_tc):
    import app.routes.ui_execution as route
    for s in ("PASSED", "FAILED"):
        db_session.add(UIExecutionRecord(testcase_id=fake_tc.id, user_id="u1",
                                         target_url="http://a.com", status=s))
    db_session.commit()
    result = route.list_ui_execution_records(fake_tc.id, db_session, fake_user)
    assert [r.status for r in result.data] == ["FAILED", "PASSED"]  # id 倒序
