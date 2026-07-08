"""Regression tests for utils.helpers normalization functions.

These cover the refactor hotspots in Phase 1/2: _parse_response extraction
and any movement of normalization logic. Pure functions, no DB/LLM.
"""
import pytest

from utils.helpers import (
    _normalize_level,
    _normalize_testcase_dict,
    _repair_json,
    _extract_json_with_response,
    _normalize_extracted_json,
)
from utils.models import ApiCallStep


@pytest.mark.parametrize("raw,expected", [
    (1, 1), (2, 2), (3, 3), (4, 4),
    (0, 4), (5, 4), (99, 4),          # out of range -> 4
    ("1", 1), ("4", 4), ("7", 4),     # numeric strings
    ("P0", 1), ("P1", 1), ("P2", 2), ("P3", 3), ("P4", 4),
    ("功能测试", 1), ("边界测试", 2), ("异常测试", 3), ("场景测试", 4),
    ("garbage", 4), ("", 4), (None, 4),
])
def test_normalize_level(raw, expected):
    assert _normalize_level(raw) == expected


def test_repair_json_passthrough_valid():
    valid = '{"a": 1, "b": "ok"}'
    assert _repair_json(valid) == valid


def test_repair_json_fixes_cjk_inner_quotes():
    # An unescaped quote sitting *between* CJK content (with ASCII value
    # boundaries) is rewritten to a full-width quote, making the JSON parse.
    # Note: the heuristic only handles quotes adjacent to CJK; a value that
    # *starts* with CJK is not repairable (its opening quote is CJK-adjacent).
    import json
    broken = '{"msg": "a点"赞b"}'
    repaired = _repair_json(broken)
    parsed = json.loads(repaired)
    assert parsed["msg"].startswith("a点")


def test_repair_json_returns_original_when_unfixable():
    unfixable = '{this is not json at all'
    assert _repair_json(unfixable) == unfixable


def test_extract_json_object_with_response_key():
    text = 'noise before {"response": [{"name": "tc1"}]} noise after'
    data = _extract_json_with_response(text)
    assert data is not None
    assert "response" in data
    assert data["response"][0]["name"] == "tc1"


def test_extract_json_object_with_test_cases_key():
    text = 'prefix {"test_cases": [{"name": "tc1"}]} suffix'
    data = _extract_json_with_response(text)
    assert data is not None
    assert "test_cases" in data


def test_extract_top_level_array_wraps_as_test_cases():
    text = '[{"name": "tc1"}, {"name": "tc2"}]'
    data = _extract_json_with_response(text)
    assert data is not None
    assert data["test_cases"][1]["name"] == "tc2"


def test_extract_returns_none_when_no_match():
    assert _extract_json_with_response("just plain text, no json") is None


def test_normalize_testcase_dict_english_keys():
    raw = {
        "name": "登录成功",
        "level": "P0",
        "precondition": "用户已注册",
        "expected_result": "跳转首页",
        "steps": ["输入账号", "输入密码", "点击登录"],
    }
    out = _normalize_testcase_dict(raw)
    assert out["case_name"] == "登录成功"
    assert out["case_level"] == 1
    assert out["preset_conditions"] == ["用户已注册"]
    assert out["expected_results"] == ["跳转首页"]
    assert out["steps"] == ["输入账号", "输入密码", "点击登录"]


def test_normalize_testcase_dict_chinese_keys():
    raw = {
        "用例名称": "边界值校验",
        "用例级别": "边界测试",
        "前置条件": ["前提1"],
        "预期结果": ["结果1", "结果2"],
        "用例步骤": ["步骤1"],
    }
    out = _normalize_testcase_dict(raw)
    assert out["case_name"] == "边界值校验"
    assert out["case_level"] == 2
    assert out["preset_conditions"] == ["前提1"]
    assert out["expected_results"] == ["结果1", "结果2"]
    assert out["steps"] == ["步骤1"]


def test_normalize_testcase_dict_missing_fields_defaults():
    out = _normalize_testcase_dict({})
    assert out["case_name"] == ""
    assert out["case_level"] == 1  # 默认为功能测试
    assert out["preset_conditions"] == []
    assert out["expected_results"] == []
    assert out["steps"] == []


def test_normalize_testcase_dict_splits_inline_numbered_preconditions():
    raw = {
        "name": "合同登记成功",
        "preconditions": "1. 用户已登录建管平台 2. 用户有合同登记权限 3. 准备一个合法的PDF格式合同文件（大小不超过系统限制）",
    }
    out = _normalize_testcase_dict(raw)
    assert out["preset_conditions"] == [
        "1. 用户已登录建管平台",
        "2. 用户有合同登记权限",
        "3. 准备一个合法的PDF格式合同文件（大小不超过系统限制）",
    ]


def test_normalize_testcase_dict_api_call_step_in_steps():
    raw = {
        "name": "调用接口",
        "steps": [
            {"api_call": {"endpoint_ref": "[2]", "description": "创建用户"}},
        ],
    }
    out = _normalize_testcase_dict(raw)
    assert len(out["steps"]) == 1
    step = out["steps"][0]
    assert isinstance(step, ApiCallStep)
    assert step.endpoint_ref == 2
    assert step.description == "创建用户"


def test_normalize_testcase_dict_api_endpoint_ref_string():
    out = _normalize_testcase_dict({"name": "x", "api_endpoint_ref": "[1,3]"})
    assert out["api_endpoint_ref"] == [1, 3]


def test_normalize_extracted_json_maps_test_cases_to_response():
    data = {"test_cases": [{"name": "tc1", "level": 1}]}
    out = _normalize_extracted_json(data)
    assert "response" in out
    assert out["response"][0]["case_name"] == "tc1"


def test_normalize_extracted_json_normalizes_existing_response():
    data = {"response": [{"name": "tc1", "level": "P2"}]}
    out = _normalize_extracted_json(data)
    assert out["response"][0]["case_level"] == 2


def test_normalize_extracted_json_passthrough_when_neither_key():
    data = {"other": 1}
    assert _normalize_extracted_json(data) == {"other": 1}
