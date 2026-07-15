"""「验证」按钮后端：真实执行回写（run_scenario 以 monkeypatch 模拟，避免外部网络）。

真实 HTTP 执行路径由 app/services/scenario_runner 与 test_api_test_tool.py 覆盖，
此处重点验证：场景构建 + validation_status/detail/at 正确回写。
"""
import asyncio

from db.models import ApiEndpoint, ApiProject, ApiScenario, TestCase

from app.routes.testcase import _run_validation


def _make_tc(db_session, passed_scenario=True):
    project = ApiProject(name="p", base_url="http://api.test")
    db_session.add(project)
    db_session.commit()
    ep = ApiEndpoint(project_id=project.id, name="c", method="POST", path="/x",
                     request_schema={"type": "object", "properties": {"name": {"type": "string"}}})
    db_session.add(ep)
    db_session.commit()
    tc = TestCase(case_name="t", api_project_id=project.id,
                  api_endpoint_id=str(ep.id),
                  steps=[{"endpoint_ref": 1, "body": '{"name":"ok"}',
                          "assertions": [{"type": "status_code", "value": 200}]}],
                  validation_status="pending")
    db_session.add(tc)
    db_session.commit()
    db_session.refresh(tc)
    return project, ep, tc


def test_run_validation_writes_status_passed(db_session, monkeypatch):
    import app.services.scenario_runner as sr
    project, ep, tc = _make_tc(db_session)

    async def fake_run_scenario(db, scenario, proj, session_headers=None):
        assert scenario.steps[0]["endpoint_id"] == ep.id
        return {"passed": True, "steps": [{"index": 1, "status": "passed"}]}

    monkeypatch.setattr(sr, "run_scenario", fake_run_scenario)

    result = asyncio.run(_run_validation(db_session, tc))
    assert result["validation_status"] == "passed"
    db_session.refresh(tc)
    assert tc.validation_status == "passed"
    assert tc.validation_detail is not None
    assert tc.validation_at is not None
    assert tc.scenario_id is not None


def test_run_validation_writes_status_failed(db_session, monkeypatch):
    import app.services.scenario_runner as sr
    project, ep, tc = _make_tc(db_session)

    async def fake_run_scenario(db, scenario, proj, session_headers=None):
        return {"passed": False, "steps": [{"index": 1, "status": "failed"}]}

    monkeypatch.setattr(sr, "run_scenario", fake_run_scenario)

    result = asyncio.run(_run_validation(db_session, tc))
    assert result["validation_status"] == "failed"
    db_session.refresh(tc)
    assert tc.validation_status == "failed"
