"""必填字段确定性补全测试（B6 头号质量问题修复）。

背景：质量基线在 1574 条存量用例上实测出 442 处必填字段缺失
（cmpId 214 次等），都是 LLM 没遵守 request_schema.required。

本测试锁住三件事：
1. 只补缺失、绝不覆盖已有值（幂等）；
2. 补齐的值类型合法（复用 _schema_example）；
3. 嵌套对象/数组也能补；找不到 schema 时不乱补。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json

from app.services.required_field_filler import (
    dependency_candidates,
    fill_missing_required,
    fill_step_body,
    fill_testcase_steps,
    is_id_like,
)


# --------------------------------------------------------------- id 判定

def test_is_id_like():
    assert is_id_like("id") is True
    assert is_id_like("cmpId") is True
    assert is_id_like("user_id") is True
    assert is_id_like("resId") is True
    assert is_id_like("name") is False
    assert is_id_like("amount") is False
    assert is_id_like("") is False


# --------------------------------------------------------------- 基础补全

def test_fill_missing_required_basic():
    schema = {
        "required": ["cmpId", "amount"],
        "properties": {
            "cmpId": {"type": "string"},
            "amount": {"type": "number"},
        },
    }
    body, filled = fill_missing_required(schema, {"name": "x"})
    assert body["cmpId"] == "string"
    assert body["amount"] == 1.0
    assert sorted(filled) == ["amount", "cmpId"]


def test_fill_never_overwrites_existing():
    schema = {
        "required": ["cmpId"],
        "properties": {"cmpId": {"type": "string"}},
    }
    body, filled = fill_missing_required(schema, {"cmpId": "real-id-123"})
    assert body["cmpId"] == "real-id-123"
    assert filled == []


def test_fill_is_idempotent():
    schema = {
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    once, f1 = fill_missing_required(schema, {})
    assert f1 == ["a"]
    twice, f2 = fill_missing_required(schema, dict(once))
    assert f2 == [], "二次调用不应再补任何字段"


def test_fill_no_required_returns_unchanged():
    body, filled = fill_missing_required({"properties": {}}, {"x": 1})
    assert body == {"x": 1}
    assert filled == []


def test_fill_empty_schema_safe():
    assert fill_missing_required({}, {"x": 1}) == ({"x": 1}, [])
    assert fill_missing_required(None, {"x": 1}) == ({"x": 1}, [])


# --------------------------------------------------------------- 类型派生

def test_fill_respects_enum():
    schema = {
        "required": ["type"],
        "properties": {"type": {"type": "string", "enum": ["A", "B"]}},
    }
    body, _ = fill_missing_required(schema, {})
    assert body["type"] in ("A", "B")


def test_fill_respects_example():
    schema = {
        "required": ["inst"],
        "properties": {"inst": {"type": "string", "example": "INST-001"}},
    }
    body, _ = fill_missing_required(schema, {})
    assert body["inst"] == "INST-001"


def test_fill_boolean_and_array():
    schema = {
        "required": ["flag", "items"],
        "properties": {
            "flag": {"type": "boolean"},
            "items": {"type": "array", "items": {"type": "string"}},
        },
    }
    body, _ = fill_missing_required(schema, {})
    # _schema_example 对 boolean 给 True（合法值即可，不要求必须是 False）
    assert body["flag"] in (True, False)
    assert isinstance(body["items"], list)


def test_fill_nested_object():
    schema = {
        "required": ["actionPayload"],
        "properties": {
            "actionPayload": {
                "type": "object",
                "required": ["comment"],
                "properties": {"comment": {"type": "object"}},
            },
        },
    }
    body, filled = fill_missing_required(schema, {})
    # 顶层补上 actionPayload；其内部 required 由 _schema_example 一并构造
    assert "actionPayload" in filled
    assert isinstance(body["actionPayload"], dict)
    # 内层 required 必须存在（无论由示例派生还是递归补）
    assert "comment" in body["actionPayload"]


def test_fill_array_items():
    schema = {
        "required": ["list"],
        "properties": {
            "list": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["k"],
                    "properties": {"k": {"type": "string"}},
                },
            },
        },
    }
    body, _ = fill_missing_required(schema, {"list": [{}]})
    assert "k" in body["list"][0]


# --------------------------------------------------------------- 步骤级

def test_fill_step_body_json_string():
    schema = {"required": ["a"], "properties": {"a": {"type": "string"}}}
    step = {"type": "api_call", "body": json.dumps({"x": 1})}
    new_step, filled = fill_step_body(step, schema)
    assert filled == ["a"]
    parsed = json.loads(new_step["body"])
    assert parsed["a"] == "string"
    assert parsed["x"] == 1


def test_fill_step_body_dict():
    schema = {"required": ["a"], "properties": {"a": {"type": "string"}}}
    step = {"type": "api_call", "body": {"x": 1}}
    new_step, filled = fill_step_body(step, schema)
    assert new_step["body"]["a"] == "string"


def test_fill_step_body_creates_body_when_missing():
    schema = {"required": ["a"], "properties": {"a": {"type": "string"}}}
    step = {"type": "api_call"}
    new_step, filled = fill_step_body(step, schema)
    assert filled == ["a"]
    assert json.loads(new_step["body"])["a"] == "string"


def test_fill_step_no_schema_returns_unchanged():
    step = {"body": {"x": 1}}
    new_step, filled = fill_step_body(step, None)
    assert filled == []
    assert new_step is step


# --------------------------------------------------------------- 用例级

def test_fill_testcase_steps_by_endpoint_id():
    steps = [{"type": "api_call", "endpoint_id": 7, "body": "{}"}]
    schemas = {7: {"required": ["a"], "properties": {"a": {"type": "string"}}}}
    new_steps, filled = fill_testcase_steps(steps, schemas)
    assert filled and "endpoint#7" in filled[0]
    assert json.loads(new_steps[0]["body"])["a"] == "string"


def test_fill_testcase_steps_by_ordinal():
    steps = [{"type": "api_call", "endpoint_ref": 1, "body": "{}"}]
    schemas = {9: {"required": ["a"], "properties": {"a": {"type": "string"}}}}
    new_steps, filled = fill_testcase_steps(
        steps, schemas, endpoint_id_by_ordinal={1: 9},
    )
    assert filled


def test_fill_testcase_steps_skips_non_api_steps():
    steps = [{"action": "点击按钮"}]
    new_steps, filled = fill_testcase_steps(steps, {7: {"required": ["a"]}})
    assert filled == []


def test_fill_testcase_steps_no_schema_for_endpoint():
    steps = [{"type": "api_call", "endpoint_id": 99, "body": "{}"}]
    new_steps, filled = fill_testcase_steps(steps, {7: {"required": ["a"]}})
    assert filled == []


# --------------------------------------------------------------- 依赖候选

def test_dependency_candidates_detects_id_like():
    assert dependency_candidates(["cmpId", "amount"]) == ["cmpId"]
    assert dependency_candidates(["a.b.cmpId"]) == ["a.b.cmpId"]
    assert dependency_candidates(["a[0].resId"]) == ["a[0].resId"]
    assert dependency_candidates([]) == []