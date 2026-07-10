"""API 用例一致性检查与置信度打分的纯规则测试。"""
import json

from app.services.api_consistency import check_testcase_consistency, score_confidence


def _ep(request_schema=None, response_schema=None, method="POST"):
    class _E:
        pass
    e = _E()
    e.method = method
    e.path = "/x"
    e.request_schema = request_schema
    e.response_schema = response_schema
    return e


def test_consistency_no_extra_fields_and_required():
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "status": {"type": "string", "enum": ["active", "disabled"]},
        },
    }
    ep = _ep(request_schema=schema, response_schema={"type": "object", "properties": {"code": {"type": "integer"}}})
    index_to_id = {1: "epA"}
    endpoints = {"epA": ep}
    # 含多余字段 foo + 缺必填 name
    case = {
        "steps": [
            {"endpoint_ref": 1, "body": json.dumps({"status": "active", "foo": 1})}
        ],
        "assertions": [{"type": "jsonpath_exists", "jsonpath": "$.code"}],
    }
    report = check_testcase_consistency(case, index_to_id, endpoints)
    assert report["passed"] is False
    kinds = {c["check"] for c in report["checks"]}
    assert "no_extra_body_fields" in kinds
    assert "required_fields_present" in kinds
    assert "enum_valid" in kinds
    assert "assertion_fields_exist" in kinds
    # foo 是多余字段
    extra = next(c for c in report["checks"] if c["check"] == "no_extra_body_fields")
    assert extra["passed"] is False


def test_consistency_clean_case_passes():
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}, "status": {"type": "string", "enum": ["active"]}},
    }
    ep = _ep(request_schema=schema, response_schema={"type": "object", "properties": {"code": {"type": "integer"}}})
    index_to_id = {1: "epA"}
    endpoints = {"epA": ep}
    case = {
        "steps": [{"endpoint_ref": 1, "body": json.dumps({"name": "x", "status": "active"})}],
        "assertions": [{"type": "jsonpath_exists", "jsonpath": "$.code"}],
    }
    report = check_testcase_consistency(case, index_to_id, endpoints)
    assert report["passed"] is True
    assert report["pass_rate"] == 1.0


def test_score_confidence_high_for_clean_case():
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    }
    ep = _ep(request_schema=schema, response_schema={"type": "object", "properties": {"code": {"type": "integer"}}})
    case = {
        "steps": [{"endpoint_ref": 1, "body": json.dumps({"name": "x"}),
                   "assertions": [{"type": "jsonpath_exists", "jsonpath": "$.code"}]}],
        "preconditions": ["已登录"],
        "case_level": 1,
    }
    report = check_testcase_consistency(case, {1: "epA"}, {"epA": ep})
    score, level = score_confidence(report, case)
    assert level == "high"
    assert score >= 0.8
