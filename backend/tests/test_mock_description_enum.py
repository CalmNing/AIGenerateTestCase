"""从 description 解析「1奖励 2处罚」式取值说明，约束 mock 候选值。"""
import json

from db.models import ApiEndpoint
from app.routes.mock_server import _build_schema_mock_body
from app.services.mock_data import _description_enum_values


def _data_for(schema: dict) -> dict:
    endpoint = ApiEndpoint(project_id=1, method="GET", path="/x", response_schema=schema)
    return json.loads(_build_schema_mock_body(endpoint))["data"]


def test_parse_description_enum_basic():
    schema = {"type": "integer", "description": "奖罚类型：1奖励 2处罚", "format": "int32"}
    assert sorted(_description_enum_values(schema)) == [1, 2]


def test_parse_description_enum_with_separators():
    assert sorted(_description_enum_values(
        {"description": "支付状态：1-待支付，2-已支付；3、已退款"})) == [1, 2, 3]
    assert sorted(_description_enum_values(
        {"description": "-1已作废 0待审核 1通过"})) == [-1, 0, 1]
    assert sorted(_description_enum_values(
        {"description": "0:未开始 1:进行中 2:已完成"})) == [0, 1, 2]


def test_parse_description_enum_requires_two_distinct_values():
    # 单个数字的范围描述不算枚举，避免「最长30天」误判
    assert _description_enum_values({"description": "最长30天"}) == []
    assert _description_enum_values({"description": ""}) == []
    assert _description_enum_values({}) == []


def test_integer_schema_returns_only_described_values():
    schema = {
        "type": "object",
        "properties": {
            "rewardPunishmentType": {
                "type": "integer",
                "description": "奖罚类型：1奖励 2处罚",
                "format": "int32",
            },
        },
    }
    values = {_data_for(schema)["rewardPunishmentType"] for _ in range(30)}
    assert values <= {1, 2}
    assert len(values) == 2  # 两个取值都可能出现


def test_string_schema_returns_code_as_string():
    schema = {
        "type": "object",
        "properties": {
            "payStatus": {"type": "string", "description": "1-待支付，2-已支付"},
        },
    }
    values = {_data_for(schema)["payStatus"] for _ in range(20)}
    assert values <= {"1", "2"}


def test_description_candidates_inside_nested_items():
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "status": {"type": "integer", "description": "状态：0停用 1启用"},
            },
        },
    }
    data = _data_for(schema)
    assert data and all(item["status"] in (0, 1) for item in data)


def test_enum_and_example_still_win_over_description():
    endpoint_schema = {
        "type": "object",
        "properties": {
            "a": {"type": "integer", "enum": [7], "description": "1奖励 2处罚"},
            "b": {"type": "integer", "example": 5, "description": "1奖励 2处罚"},
        },
    }
    data = _data_for(endpoint_schema)
    assert data["a"] == 7
    assert data["b"] == 5
