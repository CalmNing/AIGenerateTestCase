"""P3 执行验证闭环测试。

全部用例都不发真实 HTTP 请求：用 mock 的 run_scenario 驱动各种执行结果，
验证失败归因、修复指令生成、汇总统计与降级行为。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import case_verifier as cv


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- 归因

def test_classify_assertion_mismatch():
    cause, detail = cv._classify_step_failure({
        "status": "failed",
        "assertions": [
            {"type": "jsonpath_equals", "passed": False,
             "expected": "0", "actual": "500"},
        ],
    })
    assert cause == cv.CAUSE_ASSERTION
    assert "jsonpath_equals" in detail


def test_classify_status_code_mismatch():
    cause, _ = cv._classify_step_failure({
        "status": "failed",
        "assertions": [
            {"type": "status_code", "passed": False, "expected": 200, "actual": 500},
        ],
    })
    assert cause == cv.CAUSE_STATUS


def test_classify_client_error_as_request_problem():
    cause, _ = cv._classify_step_failure({
        "status": "failed",
        "status_code": 400,
        "detail": "缺少必填字段 username",
    })
    assert cause == cv.CAUSE_REQUEST


def test_classify_server_error_as_environment_cause():
    cause, _ = cv._classify_step_failure({
        "status": "failed",
        "status_code": 503,
        "detail": "service unavailable",
    })
    assert cause == cv.CAUSE_CONNECTION


def test_classify_connection_error():
    cause, _ = cv._classify_step_failure({
        "status": "error",
        "detail": "Connection refused",
    })
    assert cause == cv.CAUSE_CONNECTION


def test_classify_timeout():
    cause, _ = cv._classify_step_failure({
        "status": "error",
        "detail": "Request timed out after 30s",
    })
    assert cause == cv.CAUSE_TIMEOUT


def test_environment_causes_are_not_fixable():
    assert cv.CAUSE_CONNECTION in cv._ENVIRONMENT_CAUSES
    assert cv.CAUSE_TIMEOUT in cv._ENVIRONMENT_CAUSES
    assert cv.CAUSE_ASSERTION not in cv._ENVIRONMENT_CAUSES


# --------------------------------------------------------------- 反馈生成

def test_build_failure_feedback_empty_when_all_passed():
    results = [cv.VerifyResult(testcase_id=1, status=cv.STATUS_PASSED, passed=True)]
    assert cv.build_failure_feedback(results) == ""


def test_build_failure_feedback_excludes_environment_failures():
    """环境类失败不应进修复指令——否则 LLM 会反复修它控制不了的问题。"""
    results = [
        cv.VerifyResult(
            testcase_id=1, status=cv.STATUS_FAILED, cause=cv.CAUSE_CONNECTION,
            cause_detail="Connection refused", fixable=False,
        ),
    ]
    assert cv.build_failure_feedback(results) == ""


def test_build_failure_feedback_includes_fixable_failures():
    results = [
        cv.VerifyResult(
            testcase_id=7, status=cv.STATUS_FAILED, cause=cv.CAUSE_ASSERTION,
            cause_detail="jsonpath_equals(期望='0', 实际='500')", fixable=True,
            failed_assertions=[
                {"step_index": 1, "type": "jsonpath_equals",
                 "expected": "0", "actual": "500"},
            ],
            failed_steps=[{"index": 1, "status": "failed", "http_status": 500}],
        ),
    ]
    text = cv.build_failure_feedback(results, case_names={7: "登录成功"})
    assert "登录成功" in text
    assert "用例 id=7" in text
    assert "jsonpath_equals" in text


# --------------------------------------------------------------- 汇总

def test_summarize_verification():
    results = [
        cv.VerifyResult(testcase_id=1, status=cv.STATUS_PASSED, passed=True),
        cv.VerifyResult(testcase_id=2, status=cv.STATUS_FAILED, fixable=True),
        cv.VerifyResult(testcase_id=3, status=cv.STATUS_FAILED, fixable=False),
        cv.VerifyResult(testcase_id=4, status=cv.STATUS_SKIPPED),
    ]
    s = cv.summarize_verification(results)
    assert s["total"] == 4
    assert s["passed"] == 1
    assert s["failed"] == 2
    assert s["skipped"] == 1
    assert s["executed"] == 3
    assert s["fixable"] == 1
    assert s["pass_rate"] == round(1 / 3, 3)


def test_summarize_verification_empty():
    s = cv.summarize_verification([])
    assert s["total"] == 0 and s["pass_rate"] == 0.0


# --------------------------------------------------------------- 单条验证

class _FakeScenario:
    def __init__(self, project_id=1):
        self.project_id = project_id
        self.steps = []
        self.environment_id = None
        self.variables = []


class _FakeProject:
    def __init__(self, base_url="http://x"):
        self.base_url = base_url
        self.environment_id = None


class _FakeCase:
    def __init__(self, id=1, scenario_id=1):
        self.id = id
        self.scenario_id = scenario_id


class _FakeDB:
    def __init__(self, scenario=None, project=None):
        self._scenario = scenario
        self._project = project

    def get(self, model, ident):
        name = getattr(model, "__name__", "")
        if "Scenario" in name:
            return self._scenario
        if "Project" in name:
            return self._project
        return None


def test_verify_skips_without_scenario():
    db = _FakeDB(scenario=None, project=_FakeProject())
    result = _run(cv.verify_testcase(db, _FakeCase(scenario_id=None)))
    assert result.status == cv.STATUS_SKIPPED
    assert result.cause == cv.CAUSE_NO_SCENARIO


def test_verify_skips_without_project():
    db = _FakeDB(scenario=_FakeScenario(), project=None)
    result = _run(cv.verify_testcase(db, _FakeCase()))
    assert result.status == cv.STATUS_SKIPPED
    assert result.cause == cv.CAUSE_NO_PROJECT


def test_verify_skips_when_no_base_url():
    db = _FakeDB(scenario=_FakeScenario(), project=_FakeProject(base_url=""))
    result = _run(cv.verify_testcase(db, _FakeCase()))
    assert result.status == cv.STATUS_SKIPPED


def test_verify_reports_passed(monkeypatch):
    db = _FakeDB(scenario=_FakeScenario(), project=_FakeProject())

    async def fake_run_scenario(*a, **k):
        return {"passed": True, "variables": {}, "steps": [
            {"index": 1, "status": "passed", "assertions": [
                {"type": "status_code", "passed": True, "expected": 200, "actual": 200},
            ]},
        ]}

    monkeypatch.setattr(
        "app.services.scenario_runner.run_scenario", fake_run_scenario,
    )
    result = _run(cv.verify_testcase(db, _FakeCase()))
    assert result.status == cv.STATUS_PASSED
    assert result.passed is True


def test_verify_reports_failure_with_cause(monkeypatch):
    db = _FakeDB(scenario=_FakeScenario(), project=_FakeProject())

    async def fake_run_scenario(*a, **k):
        return {"passed": False, "variables": {}, "steps": [
            {"index": 1, "status": "failed", "status_code": 200, "assertions": [
                {"type": "jsonpath_equals", "passed": False,
                 "expected": "0", "actual": "500"},
            ]},
        ]}

    monkeypatch.setattr(
        "app.services.scenario_runner.run_scenario", fake_run_scenario,
    )
    result = _run(cv.verify_testcase(db, _FakeCase()))
    assert result.status == cv.STATUS_FAILED
    assert result.passed is False
    assert result.cause == cv.CAUSE_ASSERTION
    assert result.fixable is True
    assert len(result.failed_assertions) == 1


def test_verify_swallows_execution_exception(monkeypatch):
    db = _FakeDB(scenario=_FakeScenario(), project=_FakeProject())

    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "app.services.scenario_runner.run_scenario", boom,
    )
    result = _run(cv.verify_testcase(db, _FakeCase()))
    # 执行异常不应抛出；归因为可判定的原因
    assert result.status == cv.STATUS_ERROR
    assert result.error


def test_verify_batch_marks_each_case(monkeypatch):
    db = _FakeDB(scenario=_FakeScenario(), project=_FakeProject())

    async def fake_run_scenario(*a, **k):
        return {"passed": True, "variables": {}, "steps": []}

    monkeypatch.setattr(
        "app.services.scenario_runner.run_scenario", fake_run_scenario,
    )
    cases = [_FakeCase(id=1), _FakeCase(id=2)]
    results = _run(cv.verify_batch(db, cases))
    assert [r.testcase_id for r in results] == [1, 2]
    assert all(r.status == cv.STATUS_PASSED for r in results)
