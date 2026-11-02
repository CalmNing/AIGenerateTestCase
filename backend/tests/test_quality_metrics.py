"""B6 质量指标与一致性检查器 bug 回归测试。

两个被修复的真 bug（都曾导致质量指标严重失真）：
1. `check_testcase_consistency` 只认 `endpoint_ref`（序号），不认落库后的
   `endpoint_id`（主键）→ 存量用例全被判"接口步骤不存在"，
   实测 200 条接口用例一致率 0%、全通过 0 条。
2. 必填字段校验只看 body 顶层 → 嵌套 body 全被误报"缺必填字段"。

指标本身：
- `normalize_title` / `find_duplicates`：重复判定
- `evaluate_testcase`：单条用例各项指标
- `summarize_metrics`：汇总基线
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import api_consistency as ac
from app.services.quality_metrics import (
    evaluate_testcase,
    find_duplicates,
    format_baseline_report,
    normalize_title,
    summarize_metrics,
)


# --------------------------------------------------------------- bug 1：接口引用格式

class _Ep:
    def __init__(self, method="POST", path="/x", req=None, resp=None):
        self.method = method
        self.path = path
        self.request_schema = req or {}
        self.response_schema = resp or {}


def test_resolve_endpoint_id_prefers_explicit_id():
    eid, kind = ac._resolve_step_endpoint_id(
        {"endpoint_id": 7}, {1: 100},
    )
    assert eid == 7
    assert kind == "endpoint_id"


def test_resolve_endpoint_ref_as_ordinal():
    eid, kind = ac._resolve_step_endpoint_id(
        {"endpoint_ref": 1}, {1: 100, 2: 200},
    )
    assert eid == 100
    assert kind == "endpoint_ref"


def test_resolve_endpoint_ref_fallback_to_id():
    """序号映射不到时，按数据库主键解释（不误判为无效）。"""
    eid, kind = ac._resolve_step_endpoint_id(
        {"endpoint_ref": 55}, {1: 100},
    )
    assert eid == 55
    assert kind == "endpoint_ref_as_id"


def test_resolve_no_reference():
    eid, kind = ac._resolve_step_endpoint_id({}, {1: 100})
    assert eid is None


def test_consistency_accepts_endpoint_id_format():
    """回归：步骤用 endpoint_id（落库格式）时必须能通过接口引用校验。"""
    ep = _Ep(req={"properties": {"u": {"type": "string"}}, "required": ["u"]})
    case = {
        "steps": [{"type": "api_call", "endpoint_id": 7, "body": '{"u": "x"}'}],
    }
    report = ac.check_testcase_consistency(case, {}, {7: ep})
    ref_checks = [c for c in report["checks"] if c["check"] == "endpoint_ref_valid"]
    assert ref_checks and all(c["passed"] for c in ref_checks), (
        "endpoint_id 格式必须被接受"
    )


def test_consistency_endpoint_ref_still_works():
    ep = _Ep(req={"properties": {"u": {"type": "string"}}, "required": ["u"]})
    case = {
        "steps": [{"type": "api_call", "endpoint_ref": 1, "body": '{"u": "x"}'}],
    }
    report = ac.check_testcase_consistency(case, {1: 7}, {7: ep})
    ref_checks = [c for c in report["checks"] if c["check"] == "endpoint_ref_valid"]
    assert ref_checks and all(c["passed"] for c in ref_checks)


# --------------------------------------------------------------- bug 2：嵌套必填字段

def test_schema_path_present_top_level():
    schema = {"properties": {"a": {"type": "string"}}}
    assert ac._schema_path_present(schema, "a", {"a": "x"}) is True
    assert ac._schema_path_present(schema, "a", {"b": "x"}) is False


def test_schema_path_present_nested_body():
    """body 是嵌套结构时，必填字段应在任意层级被找到。"""
    schema = {
        "properties": {
            "toAssignee": {"type": "object", "properties": {"type": {"type": "string"}}},
            "actionPayload": {"type": "object"},
        },
    }
    # toAssignee 真的不在 body 里 → False
    assert ac._schema_path_present(
        schema, "toAssignee", {"actionPayload": {"comment": {"value": "x"}}}
    ) is False
    # 补上后 → True
    assert ac._schema_path_present(
        schema, "toAssignee", {"toAssignee": {"type": "x"}}
    ) is True


def test_schema_path_present_in_array():
    schema = {"properties": {"value": {"type": "string"}}}
    assert ac._schema_path_present(
        schema, "value", {"list": [{"value": "a"}]}
    ) is True


def test_consistency_nested_body_not_false_positive():
    """回归：嵌套 body 不应被误报"缺必填字段"。"""
    schema = {
        "properties": {
            "actionPayload": {"type": "object", "properties": {"comment": {"type": "object"}}},
        },
        "required": ["actionPayload"],
    }
    ep = _Ep(req=schema)
    case = {
        "steps": [{
            "type": "api_call", "endpoint_id": 7,
            "body": '{"actionPayload": {"comment": {"value": "x"}}}',
        }],
    }
    report = ac.check_testcase_consistency(case, {}, {7: ep})
    req_checks = [c for c in report["checks"] if c["check"] == "required_fields_present"]
    assert req_checks and all(c["passed"] for c in req_checks), (
        "嵌套 body 的必填字段不应误报"
    )


# --------------------------------------------------------------- 指标

class _TC:
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.case_name = kw.get("case_name", "")
        self.case_level = kw.get("case_level", 1)
        self.steps = kw.get("steps", [])
        self.expected_results = kw.get("expected_results", [])
        self.preset_conditions = kw.get("preset_conditions", [])
        self.assertions = kw.get("assertions", [])
        self.scenario_id = kw.get("scenario_id")
        self.api_endpoint_id = kw.get("api_endpoint_id")


def test_normalize_title_strips_prefix():
    assert normalize_title("1. 登录成功") == "登录成功"
    assert normalize_title("登录 成功") == "登录成功"
    assert normalize_title("  登录成功 ") == "登录成功"


def test_normalize_title_empty():
    assert normalize_title("") == ""
    assert normalize_title(None) == ""


def test_find_duplicates():
    results = [
        {"id": 1, "normalized_name": "登录成功"},
        {"id": 2, "normalized_name": "登录成功"},
        {"id": 3, "normalized_name": "别的"},
    ]
    dup = find_duplicates(results)
    assert dup == {"登录成功": [1, 2]}


def test_find_duplicates_none():
    assert find_duplicates([{"id": 1, "normalized_name": "a"}]) == {}


def test_evaluate_testcase_complete():
    tc = _TC(
        id=1, case_name="登录成功", case_level=1,
        steps=[{"action": "打开登录页"}],
        expected_results=["跳转到首页"],
        preset_conditions=["已注册"],
    )
    r = evaluate_testcase(tc)
    assert r["has_steps"] and r["has_expected"] and r["has_preconditions"]
    assert r["issues"] == []
    assert r["level_valid"] is True


def test_evaluate_testcase_missing_expected():
    tc = _TC(id=2, steps=[{"action": "x"}], expected_results=[])
    r = evaluate_testcase(tc)
    assert "missing_expected" in r["issues"]


def test_evaluate_testcase_invalid_level():
    tc = _TC(id=3, case_level=9, steps=[{"action": "x"}], expected_results=["y"])
    r = evaluate_testcase(tc)
    assert r["level_valid"] is False


def test_evaluate_testcase_structure_issues():
    tc = _TC(
        id=4,
        steps=[{"type": "api_call", "endpoint_id": 1}],  # 无描述
        expected_results=["y"],
    )
    r = evaluate_testcase(tc)
    assert r["structure_ok"] is False
    assert any("no_description" in i for i in r["structure_issues"])


def test_evaluate_testcase_api_step_without_ref():
    tc = _TC(
        id=5,
        steps=[{"type": "api_call", "description": "调用"}],
        expected_results=["y"],
    )
    r = evaluate_testcase(tc)
    assert any("no_endpoint_ref" in i for i in r["structure_issues"])


def test_evaluate_testcase_executable_flag():
    assert evaluate_testcase(_TC(id=6, scenario_id=3))["executable"] is True
    assert evaluate_testcase(_TC(id=7, api_endpoint_id="1,2"))["executable"] is True
    assert evaluate_testcase(_TC(id=8))["executable"] is False


def test_summarize_metrics_empty():
    s = summarize_metrics([])
    assert s["total"] == 0
    assert s["completeness_rate"] == 0.0


def test_summarize_metrics_basic():
    results = [
        {"id": 1, "normalized_name": "a", "has_steps": True, "has_expected": True,
         "level_valid": True, "level": 1, "assertion_count": 2,
         "structure_ok": True, "executable": True, "issues": [],
         "consistency_checks": [], "consistency_pass_rate": 1.0,
         "consistency_passed": True,
         # B6 口径修正后的新字段
         "is_api_case": True, "expected_verifiable": True},
        {"id": 2, "normalized_name": "a", "has_steps": True, "has_expected": False,
         "level_valid": True, "level": 2, "assertion_count": 0,
         "structure_ok": False, "executable": False, "issues": ["missing_expected"],
         "consistency_checks": [], "consistency_pass_rate": 1.0,
         "consistency_passed": True,
         "is_api_case": False, "expected_verifiable": False},
    ]
    s = summarize_metrics(results)
    assert s["total"] == 2
    assert s["completeness_rate"] == 0.5
    assert s["duplicate_rate"] == 1.0        # 两条同名
    assert s["duplicate_groups"] == 1
    assert s["level_valid_rate"] == 1.0
    # 口径修正后：断言覆盖率只算接口用例（1 条接口里 1 条有断言）
    assert s["api_case_count"] == 1
    assert s["assertion_coverage_rate"] == 1.0
    assert s["functional_case_count"] == 1
    assert s["expected_verifiable_rate"] == 0.0
    assert s["avg_assertions_per_case"] == 1.0
    assert s["structure_ok_rate"] == 0.5
    assert s["level_distribution"] == {"1": 1, "2": 1}
    assert s["issue_counts"]["missing_expected"] == 1


def test_assertion_coverage_only_counts_api_cases():
    """回归：功能用例不该有 HTTP 断言，混进分母会失真（旧口径 13.9% 的根源）。"""
    results = [
        # 2 条接口用例都有断言
        {"id": 1, "is_api_case": True, "assertion_count": 3,
         "has_steps": True, "has_expected": True, "level_valid": True,
         "level": 1, "structure_ok": True, "executable": True, "issues": [],
         "normalized_name": "x", "consistency_checks": [],
         "consistency_pass_rate": 1.0, "consistency_passed": True,
         "expected_verifiable": True},
        {"id": 2, "is_api_case": True, "assertion_count": 2,
         "has_steps": True, "has_expected": True, "level_valid": True,
         "level": 1, "structure_ok": True, "executable": True, "issues": [],
         "normalized_name": "y", "consistency_checks": [],
         "consistency_pass_rate": 1.0, "consistency_passed": True,
         "expected_verifiable": True},
        # 10 条功能用例都没断言（正常，它们不该有）
        *[
            {"id": i, "is_api_case": False, "assertion_count": 0,
             "has_steps": True, "has_expected": True, "level_valid": True,
             "level": 2, "structure_ok": True, "executable": False, "issues": [],
             "normalized_name": f"f{i}", "consistency_checks": [],
             "consistency_pass_rate": 1.0, "consistency_passed": True,
             "expected_verifiable": True}
            for i in range(3, 13)
        ],
    ]
    s = summarize_metrics(results)
    # 旧口径：2/12 = 16.7%（失真）；新口径：2/2 = 100%（真实）
    assert s["assertion_coverage_rate"] == 1.0
    assert s["api_case_count"] == 2
    assert s["functional_case_count"] == 10
    assert s["expected_verifiable_rate"] == 1.0


def test_expected_verifiable_detection():
    """预期可判定性判定：具体（字段/状态/数值/文案）vs 笼统（成功/正常）。"""
    def mk(exp_text):
        return {"id": 1, "normalized_name": "a", "has_steps": True,
                "has_expected": True, "level_valid": True, "level": 1,
                "assertion_count": 0, "structure_ok": True, "executable": False,
                "issues": [], "normalized_name": "a", "consistency_checks": [],
                "consistency_pass_rate": 1.0, "consistency_passed": True,
                "is_api_case": False,
                # 注意：expected_results 是 list（与库内存储一致），判定的就是它
                "expected_results": [exp_text],
                "expected_verifiable": None}

    # 直接用判定器测（绕开 summarize 需要的其它字段）
    from app.services.quality_metrics import _expected_verifiable_for_test

    verifiable = [
        "订单状态从 PENDING 变为 APPROVED",
        "提示语为「用户名不能为空」",
        "列表显示 3 条记录",
    ]
    assert all(_expected_verifiable_for_test([t]) for t in verifiable)

    vague = [
        "操作成功",
        "页面显示正常",
        "数据保存成功",
        "删除成功",
        "查询成功，返回数据",
    ]
    assert not any(_expected_verifiable_for_test([t]) for t in vague)


def test_summarize_metrics_consistency_only_for_checked():
    results = [
        {"id": 1, "normalized_name": "a", "has_steps": True, "has_expected": True,
         "level_valid": True, "level": 1, "assertion_count": 1,
         "structure_ok": True, "executable": True, "issues": [],
         "consistency_checks": [{"check": "x", "passed": True}],
         "consistency_pass_rate": 0.5, "consistency_passed": False},
        {"id": 2, "normalized_name": "b", "has_steps": True, "has_expected": True,
         "level_valid": True, "level": 1, "assertion_count": 1,
         "structure_ok": True, "executable": True, "issues": [],
         "consistency_checks": [], "consistency_pass_rate": 1.0,
         "consistency_passed": True},
    ]
    s = summarize_metrics(results)
    # 只有 1 条真正做过一致性检查，分母应为 1
    assert s["consistency_checked"] == 1
    assert s["consistency_pass_rate"] == 0.5
    assert s["consistency_passed_rate"] == 0.0


def test_format_baseline_report():
    s = summarize_metrics([
        {"id": 1, "normalized_name": "a", "has_steps": True, "has_expected": True,
         "level_valid": True, "level": 1, "assertion_count": 1,
         "structure_ok": True, "executable": True, "issues": [],
         "consistency_checks": [], "consistency_pass_rate": 1.0,
         "consistency_passed": True},
    ])
    text = format_baseline_report(s)
    assert "用例质量基线" in text
    assert "用例总数" in text


def test_format_baseline_report_empty():
    assert "无数据" in format_baseline_report(summarize_metrics([]))
