"""Mock 响应中时间字段统一返回毫秒级时间戳的行为。"""
import json
import time

from db.models import ApiEndpoint
from app.routes.mock_server import _build_schema_mock_body


def _data_for(schema: dict) -> dict:
    endpoint = ApiEndpoint(project_id=1, method="GET", path="/x", response_schema=schema)
    return json.loads(_build_schema_mock_body(endpoint))["data"]


def _assert_ms(value) -> None:
    assert isinstance(value, int), f"应为毫秒级时间戳整数，实际: {value!r}"
    now_ms = int(time.time() * 1000)
    assert now_ms - 366 * 86_400_000 <= value <= now_ms + 1, f"应在过去一年内: {value}"


def test_string_time_fields_return_ms_timestamp():
    data = _data_for({
        "type": "object",
        "properties": {
            "createdAt": {"type": "string"},
            "updateTime": {"type": "string"},
            "signDate": {"type": "string"},
            "startDate": {"type": "string"},
        },
    })
    for key in ("createdAt", "updateTime", "signDate", "startDate"):
        _assert_ms(data[key])


def test_format_date_and_datetime_strings_return_ms_timestamp():
    data = _data_for({
        "type": "object",
        "properties": {
            "createTime": {"type": "string", "format": "date-time"},
            "expireDate": {"type": "string", "format": "date"},
        },
    })
    _assert_ms(data["createTime"])
    _assert_ms(data["expireDate"])


def test_integer_and_number_time_fields_return_ms_timestamp():
    data = _data_for({
        "type": "object",
        "properties": {
            "createdAt": {"type": "integer"},
            "auditTime": {"type": "number"},
        },
    })
    _assert_ms(data["createdAt"])
    _assert_ms(data["auditTime"])


def test_time_fields_inside_nested_objects_and_arrays():
    data = _data_for({
        "type": "object",
        "properties": {
            "list": {"type": "array", "items": {
                "type": "object",
                "properties": {"payTime": {"type": "string"}},
            }},
        },
    })
    assert data["list"]
    for item in data["list"]:
        _assert_ms(item["payTime"])


def test_non_time_fields_are_not_converted():
    data = _data_for({
        "type": "object",
        "properties": {
            "remark": {"type": "string"},
            "amount": {"type": "number"},
            "createdBy": {"type": "string"},
        },
    })
    assert isinstance(data["remark"], str)
    assert isinstance(data["amount"], float)
    assert isinstance(data["createdBy"], str)


def test_enum_still_wins_over_time_semantics():
    data = _data_for({
        "type": "object",
        "properties": {"timeType": {"type": "string", "enum": ["DAY", "MONTH"]}},
    })
    assert data["timeType"] in ("DAY", "MONTH")
