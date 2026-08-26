"""Tests for project-mock request validation (required / length / range / enum)."""
import pytest

from app.services.mock_validator import (
    ValidationFailed,
    validate_query_params,
    validate_request_body,
)


def _expect_errors(fn, *args):
    with pytest.raises(ValidationFailed) as exc_info:
        fn(*args)
    return exc_info.value.errors


# --- 1. 必填字段 ----------------------------------------------------------------

def test_missing_required_field_is_rejected():
    schema = {
        "type": "object",
        "required": ["name", "type"],
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string"},
        },
    }
    errors = _expect_errors(validate_request_body, '{"name": "合同"}', schema)
    assert any("type" in e and "必填" in e for e in errors)


def test_all_required_present_passes():
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    }
    validate_request_body('{"name": "x"}', schema)  # 不抛异常


def test_empty_body_with_required_fields_is_rejected():
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    errors = _expect_errors(validate_request_body, None, schema)
    assert any("必填" in e for e in errors)


def test_nested_required_field_reports_path():
    schema = {
        "type": "object",
        "properties": {
            "party": {
                "type": "object",
                "required": ["partyId"],
                "properties": {"partyId": {"type": "string"}},
            },
        },
    }
    errors = _expect_errors(validate_request_body, '{"party": {}}', schema)
    assert any("party.partyId" in e for e in errors)


# --- 2. 字符串长度 ---------------------------------------------------------------

def test_string_min_length_violation():
    schema = {"type": "object", "properties": {"code": {"type": "string", "minLength": 5}}}
    errors = _expect_errors(validate_request_body, '{"code": "ab"}', schema)
    assert any("最小长度" in e for e in errors)


def test_string_max_length_violation():
    schema = {"type": "object", "properties": {"remark": {"type": "string", "maxLength": 3}}}
    errors = _expect_errors(validate_request_body, '{"remark": "太长了太长了"}', schema)
    assert any("最大长度" in e for e in errors)


def test_string_length_ok():
    schema = {"type": "object", "properties": {"code": {"type": "string", "minLength": 2, "maxLength": 5}}}
    validate_request_body('{"code": "abc"}', schema)


# --- 3. 数值范围 -----------------------------------------------------------------

def test_number_minimum_violation():
    schema = {"type": "object", "properties": {"amount": {"type": "number", "minimum": 100}}}
    errors = _expect_errors(validate_request_body, '{"amount": 50}', schema)
    assert any("小于最小值" in e for e in errors)


def test_number_maximum_violation():
    schema = {"type": "object", "properties": {"ratio": {"type": "number", "maximum": 100}}}
    errors = _expect_errors(validate_request_body, '{"ratio": 150}', schema)
    assert any("大于最大值" in e for e in errors)


def test_exclusive_bounds_numeric_openapi_style():
    # OpenAPI 3: exclusiveMinimum/Maximum 直接是数值
    schema = {"type": "object", "properties": {"n": {"type": "integer", "exclusiveMinimum": 0}}}
    errors = _expect_errors(validate_request_body, '{"n": 0}', schema)
    assert any("必须大于" in e for e in errors)
    validate_request_body('{"n": 1}', schema)


def test_exclusive_bounds_boolean_jsonschema_style():
    # JSON Schema 草案: exclusiveMinimum 为布尔，修饰 minimum
    schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0, "exclusiveMinimum": True}}}
    errors = _expect_errors(validate_request_body, '{"n": 0}', schema)
    assert any("必须大于" in e for e in errors)


def test_boolean_false_exclusive_bounds_are_ignored_not_treated_as_zero():
    """regression: exclusiveMaximum: false 曾被当成数字 0，导致「必须小于 False」。"""
    schema = {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "maximum": 1000, "exclusiveMaximum": False},
            "ratio": {"type": "number", "minimum": 0, "exclusiveMinimum": False},
        },
    }
    validate_request_body('{"amount": 100, "ratio": 5}', schema)   # 正常值不应报错
    errors = _expect_errors(validate_request_body, '{"amount": 2000, "ratio": -1}', schema)
    assert any("大于最大值 1000" in e for e in errors)
    assert any("小于最小值 0" in e for e in errors)
    assert not any("False" in e or "True" in e for e in errors)


def test_integer_type_rejects_non_numeric_query_value():
    errors = _expect_errors(
        validate_query_params,
        {"page": "abc"},
        [{"key": "page", "in": "query", "required": True, "schema": {"type": "integer"}}],
        None,
    )
    assert any("合法的 integer" in e for e in errors)


# --- 4. 枚举校验 ------------------------------------------------------------------

def test_enum_violation():
    schema = {"type": "object", "properties": {"status": {"enum": ["ACTIVE", "CLOSED"]}}}
    errors = _expect_errors(validate_request_body, '{"status": "DELETED"}', schema)
    assert any("枚举" in e and "ACTIVE" in e for e in errors)


def test_enum_ok_and_numeric_tolerance():
    schema = {"type": "object", "properties": {"level": {"type": "integer", "enum": [1, 2, 3]}}}
    validate_request_body('{"level": 2}', schema)          # 整型
    validate_request_body('{"level": 2.0}', schema)        # 数值枚举容忍 int/float


def test_enum_in_array_items():
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string", "enum": ["A", "B"]},
            },
        },
    }
    errors = _expect_errors(validate_request_body, '{"items": ["A", "C"]}', schema)
    assert any("items[1]" in e and "枚举" in e for e in errors)


# --- 其它边界 ---------------------------------------------------------------------

def test_invalid_json_body_is_rejected():
    errors = _expect_errors(validate_request_body, '{not json', {"type": "object"})
    assert any("JSON" in e for e in errors)


def test_empty_schema_skips_validation():
    validate_request_body('{"anything": 123}', {})
    validate_request_body(None, None)


def test_array_min_max_items():
    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "minItems": 2, "maxItems": 3}},
    }
    errors = _expect_errors(validate_request_body, '{"tags": ["a"]}', schema)
    assert any("少于最小" in e for e in errors)
    errors2 = _expect_errors(validate_request_body, '{"tags": ["a","b","c","d"]}', schema)
    assert any("超过最大" in e for e in errors2)


def test_multiple_errors_collected_together():
    schema = {
        "type": "object",
        "required": ["type"],
        "properties": {
            "type": {"enum": ["A"]},
            "name": {"type": "string", "minLength": 10},
        },
    }
    body = '{"name": "短"}'
    errors = _expect_errors(validate_request_body, body, schema)
    assert len(errors) >= 2  # 缺 type + name 太短
