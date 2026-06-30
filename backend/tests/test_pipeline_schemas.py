"""Schema parse/validation tests for the 4-phase pipeline structured output.

Protects the structured_output contracts that Phase 1 (agent dedup) and
Phase 2 (pipeline concurrency) must preserve. Pure Pydantic, no DB/LLM.
"""
from app.pipeline.schemas import (
    RequirementAnalysisResult,
    TestPointGenerationResult,
    TestCaseOut,
    TestCaseDesignResult,
    ApiTestCaseOut,
    ApiCallStepOut,
    ApiCallKeyValueOut,
    AssertionRuleOut,
    ApiTestCaseDesignResult,
)


def test_requirement_analysis_result_parses_points():
    result = RequirementAnalysisResult.model_validate({
        "requirement_points": [
            {"content": "登录功能", "module": "用户", "priority": "high", "order_index": 1},
            {"content": "登出功能"},  # other fields use defaults
        ]
    })
    assert len(result.requirement_points) == 2
    assert result.requirement_points[0].priority == "high"
    assert result.requirement_points[1].module == ""
    assert result.requirement_points[1].priority == "medium"


def test_requirement_analysis_result_empty_default():
    result = RequirementAnalysisResult.model_validate({})
    assert result.requirement_points == []


def test_test_point_generation_result_defaults():
    result = TestPointGenerationResult.model_validate({
        "test_points": [{"content": "校验空输入"}]
    })
    tp = result.test_points[0]
    assert tp.test_type == "functional"
    assert tp.priority == "medium"
    assert tp.design_method == ""


def test_testcase_out_parses_steps():
    tc = TestCaseOut.model_validate({
        "title": "登录成功用例",
        "test_steps": [
            {"step": 1, "action": "打开登录页", "expected": "显示表单"},
            {"step": 2, "action": "提交凭证", "expected": "登录成功"},
        ],
    })
    assert tc.preconditions == "无"   # default
    assert tc.description == ""
    assert len(tc.test_steps) == 2
    assert tc.test_steps[1].action == "提交凭证"


def test_testcase_design_result_collects_cases():
    result = TestCaseDesignResult.model_validate({
        "test_cases": [
            {"title": "用例A", "test_steps": []},
            {"title": "用例B", "test_steps": []},
        ]
    })
    assert [c.title for c in result.test_cases] == ["用例A", "用例B"]


def test_api_call_key_value_in_alias():
    # The "in" field is a Python keyword; schema aliases it to in_field.
    kv = ApiCallKeyValueOut.model_validate({"key": "userId", "value": "1", "in": "query"})
    assert kv.in_field == "query"
    # populate_by_name also allows the python name
    kv2 = ApiCallKeyValueOut.model_validate({"key": "x", "in_field": "path"})
    assert kv2.in_field == "path"


def test_api_call_step_out_full():
    step = ApiCallStepOut.model_validate({
        "endpoint_ref": 2,
        "description": "创建订单",
        "parameters": [{"key": "id", "value": "{{orderId}}", "in": "path"}],
        "assertions": [{"type": "status_code", "value": 200}],
    })
    assert step.endpoint_ref == 2
    assert step.parameters[0].in_field == "path"
    assert step.assertions[0].type == "status_code"


def test_api_testcase_out_defaults_and_refs():
    tc = ApiTestCaseOut.model_validate({
        "title": "下单流程",
        "test_steps": [{"endpoint_ref": 1}],
        "api_endpoint_ref": [1, 2],
    })
    assert tc.preconditions == "无"
    assert tc.test_steps[0].endpoint_ref == 1
    assert tc.api_endpoint_ref == [1, 2]


def test_assertion_rule_out_status_code_range():
    rule = AssertionRuleOut.model_validate({
        "type": "status_code_range", "min": 200, "max": 299
    })
    assert rule.min == 200 and rule.max == 299
    assert rule.value is None


def test_api_testcase_design_result_collects():
    result = ApiTestCaseDesignResult.model_validate({
        "test_cases": [{"title": "T", "test_steps": [{"endpoint_ref": 1}]}]
    })
    assert result.test_cases[0].test_steps[0].endpoint_ref == 1
