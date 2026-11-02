"""P3 只读保护测试：写操作接口在只读模式下必须被跳过。

背景：P3 会向被测接口发**真实请求**。若直接执行 POST/PUT/DELETE，
会真实改动被测系统的数据。默认配置 verification_read_only=True，
只允许 GET/HEAD/OPTIONS。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import case_verifier as cv


class _FakeEndpoint:
    def __init__(self, method="GET"):
        self.method = method


class _FakeScenario:
    def __init__(self, steps=None, project_id=1):
        self.project_id = project_id
        self.steps = steps or []


class _FakeProject:
    def __init__(self, base_url="http://x"):
        self.base_url = base_url
        self.environment_id = None


class _FakeCase:
    def __init__(self, id=1, scenario_id=1):
        self.id = id
        self.scenario_id = scenario_id


class _FakeDB:
    def __init__(self, scenario=None, project=None, endpoint=None):
        self._scenario = scenario
        self._project = project
        self._endpoint = endpoint

    def get(self, model, ident):
        name = getattr(model, "__name__", "")
        if "Scenario" in name:
            return self._scenario
        if "Project" in name:
            return self._project
        if "Endpoint" in name:
            return self._endpoint
        return None


def _run(coro):
    return asyncio.run(coro)


def test_read_only_methods_are_allowed():
    scenario = _FakeScenario(steps=[
        {"method": "GET", "endpoint_id": 1},
        {"method": "HEAD", "endpoint_id": 1},
    ])
    db = _FakeDB(endpoint=_FakeEndpoint("GET"))
    assert cv._is_read_only_scenario(db, scenario) is True


def test_write_method_is_not_read_only():
    scenario = _FakeScenario(steps=[{"method": "POST", "endpoint_id": 1}])
    db = _FakeDB(endpoint=_FakeEndpoint("POST"))
    assert cv._is_read_only_scenario(db, scenario) is False


def test_method_resolved_from_endpoint_when_missing():
    scenario = _FakeScenario(steps=[{"endpoint_id": 1}])
    db = _FakeDB(endpoint=_FakeEndpoint("DELETE"))
    assert cv._is_read_only_scenario(db, scenario) is False


def test_empty_scenario_is_treated_as_read_only():
    db = _FakeDB(endpoint=_FakeEndpoint("GET"))
    assert cv._is_read_only_scenario(db, _FakeScenario(steps=[])) is True


def test_verify_skips_write_case_in_read_only_mode(monkeypatch):
    scenario = _FakeScenario(steps=[{"method": "POST", "endpoint_id": 1}])
    db = _FakeDB(scenario=scenario, project=_FakeProject(), endpoint=_FakeEndpoint("POST"))

    called = {"n": 0}

    async def fake_run_scenario(*a, **k):
        called["n"] += 1
        return {"passed": True, "variables": {}, "steps": []}

    monkeypatch.setattr(
        "app.services.scenario_runner.run_scenario", fake_run_scenario,
    )
    result = _run(cv.verify_testcase(db, _FakeCase(), read_only=True))
    assert result.status == cv.STATUS_SKIPPED
    assert result.cause == cv.CAUSE_READONLY_SKIPPED
    assert called["n"] == 0, "只读模式下不得发出任何写请求"


def test_verify_executes_write_case_when_read_only_disabled(monkeypatch):
    scenario = _FakeScenario(steps=[{"method": "POST", "endpoint_id": 1}])
    db = _FakeDB(scenario=scenario, project=_FakeProject(), endpoint=_FakeEndpoint("POST"))

    called = {"n": 0}

    async def fake_run_scenario(*a, **k):
        called["n"] += 1
        return {"passed": True, "variables": {}, "steps": []}

    monkeypatch.setattr(
        "app.services.scenario_runner.run_scenario", fake_run_scenario,
    )
    result = _run(cv.verify_testcase(db, _FakeCase(), read_only=False))
    assert result.status == cv.STATUS_PASSED
    assert called["n"] == 1


def test_read_only_config_default_is_true():
    from config import config_manager

    assert config_manager.get_verification_read_only() is True
