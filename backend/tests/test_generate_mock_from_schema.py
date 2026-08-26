"""Tests for project-level dynamic Mock (driven by imported response schemas).

Turning on a project's mock switch makes every endpoint in that project callable
at /api/mock{mock_prefix}{path}, with the body generated from response_schema at
request time. No MockConfig rows are created.
"""
import json

from app.routes.api_test_tool import _default_mock_prefix, _normalize_mock_prefix
from app.routes.mock_server import _build_schema_mock_body, _match_endpoint_path
from db.models import ApiEndpoint, ApiProject


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "email": {"type": "string", "format": "email"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}


# --- prefix normalization -----------------------------------------------------

def test_normalize_mock_prefix_adds_leading_slash_and_strips_trailing():
    assert _normalize_mock_prefix("contract") == "/contract"
    assert _normalize_mock_prefix("/contract") == "/contract"
    assert _normalize_mock_prefix("/contract/") == "/contract"
    assert _normalize_mock_prefix("  /a/b  ") == "/a/b"


def test_normalize_mock_prefix_handles_empty():
    assert _normalize_mock_prefix("") == ""
    assert _normalize_mock_prefix(None) == ""
    assert _normalize_mock_prefix("///") == ""


def test_default_mock_prefix_slugifies_project_name():
    assert _default_mock_prefix("Contract Service v2") == "/contract-service-v2"
    assert _default_mock_prefix("合同中心") == "/合同中心"
    assert _default_mock_prefix("") == ""


# --- endpoint path matching ---------------------------------------------------

def test_match_endpoint_path_with_path_param():
    assert _match_endpoint_path("/users/{id}", "/users/123")[0] is True
    assert _match_endpoint_path("/users/{id}", "/users/abc-9")[0] is True


def test_match_endpoint_path_rejects_extra_segments():
    assert _match_endpoint_path("/users/{id}", "/users/123/roles")[0] is False
    assert _match_endpoint_path("/users/{id}", "/users")[0] is False


def test_match_endpoint_path_exact():
    assert _match_endpoint_path("/health", "/health")[0] is True
    assert _match_endpoint_path("/health", "/healthz")[0] is False


def test_match_endpoint_path_multiple_params():
    assert _match_endpoint_path("/orgs/{org}/users/{id}", "/orgs/acme/users/7")[0] is True


def test_match_endpoint_path_empty_is_no_match():
    assert _match_endpoint_path("", "/anything")[0] is False


# --- response body generation -------------------------------------------------

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "email": {"type": "string", "format": "email"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}


def _no_placeholder_text(body: dict) -> bool:
    """生成的数据里不允许再出现 Swagger 的 "string" 占位符。"""
    return '"string"' not in json.dumps(body, ensure_ascii=False)


def test_build_schema_mock_body_wraps_in_envelope():
    endpoint = ApiEndpoint(project_id=1, method="GET", path="/users/{id}", response_schema=RESPONSE_SCHEMA)
    body = json.loads(_build_schema_mock_body(endpoint))

    assert set(body.keys()) == {"code", "msg", "success", "data"}
    assert body["code"] == 200
    assert body["msg"] == "Success"
    assert body["success"] is True
    assert "@example.com" in body["data"]["email"]
    assert isinstance(body["data"]["tags"], list)
    assert _no_placeholder_text(body)


def test_build_schema_mock_body_generates_realistic_values():
    """字段名感知：公司名/金额/编号/文件名等生成业务真实感数据，而非占位值。"""
    schema = {
        "type": "object",
        "properties": {
            "resName": {"type": "string"},
            "partyAName": {"type": "string"},
            "amount": {"type": "number"},
            "ratio": {"type": "number"},
            "no": {"type": "string"},
            "remark": {"type": "string"},
            "attachments": {"type": "array", "items": {"type": "object", "properties": {
                "fileUuid": {"type": "string", "format": "uuid"},
                "fileName": {"type": "string"},
                "fileSize": {"type": "integer"},
            }}},
            "instanceId": {"type": "string", "format": "uuid"},
        },
    }
    endpoint = ApiEndpoint(project_id=1, method="GET", path="/contracts/x", response_schema=schema)
    data = json.loads(_build_schema_mock_body(endpoint))["data"]

    import re as re_mod
    import uuid as uuid_mod

    assert len(data["partyAName"]) > 4  # 中文公司名，非 "string"
    assert 0 < data["amount"] < 100_000_000
    assert 0 <= data["ratio"] <= 100
    assert re_mod.match(r"^NO-\d{6,}$|^[A-Z]+-\d+", data["no"])  # 编号样式
    uuid_mod.UUID(data["instanceId"])
    uuid_mod.UUID(data["attachments"][0]["fileUuid"])
    assert data["attachments"][0]["fileName"].endswith((".pdf", ".xlsx", ".jpg", ".docx"))
    assert data["attachments"][0]["fileSize"] > 0
    assert _no_placeholder_text(data)


def test_enum_values_are_preserved():
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["ACTIVE", "CLOSED"]}},
    }
    endpoint = ApiEndpoint(project_id=1, method="GET", path="/x", response_schema=schema)
    for _ in range(5):
        data = json.loads(_build_schema_mock_body(endpoint))["data"]
        assert data["status"] in ("ACTIVE", "CLOSED")


def test_meaningful_example_wins_but_swagger_placeholder_is_ignored():
    # 有意义的 example 应被采用
    endpoint = ApiEndpoint(
        project_id=1, method="GET", path="/x",
        response_schema={"type": "object", "properties": {
            "grade": {"type": "string", "example": "一级"}}})
    assert json.loads(_build_schema_mock_body(endpoint))["data"]["grade"] == "一级"

    # Swagger 自动填的 example: "string" 必须被忽略并重新生成
    endpoint2 = ApiEndpoint(
        project_id=1, method="GET", path="/x",
        response_schema={"type": "object", "properties": {
            "partyAName": {"type": "string", "example": "string"}}})
    assert json.loads(_build_schema_mock_body(endpoint2))["data"]["partyAName"] != "string"


def test_randomness_across_calls():
    endpoint = ApiEndpoint(project_id=1, method="GET", path="/x", response_schema={
        "type": "object", "properties": {"amount": {"type": "number"}}})
    values = {json.loads(_build_schema_mock_body(endpoint))["data"]["amount"] for _ in range(8)}
    assert len(values) > 1  # 每次请求返回不同随机值


def test_build_schema_mock_body_does_not_double_wrap():
    schema = {
        "type": "object",
        "properties": {
            "code": {"type": "integer", "example": 200},
            "message": {"type": "string", "example": "success"},
            "data": {"type": "object", "properties": {"id": {"type": "integer"}}},
        },
    }
    endpoint = ApiEndpoint(project_id=1, method="GET", path="/x", response_schema=schema)
    body = json.loads(_build_schema_mock_body(endpoint))

    assert set(body.keys()) == {"code", "msg", "success", "data"}
    # schema 自带 data 字段 -> 其内容直接放到外层 data 下，不出现 data.data
    assert set(body["data"].keys()) == {"id"}


def test_build_schema_mock_body_handles_array_schema():
    endpoint = ApiEndpoint(
        project_id=1, method="GET", path="/users",
        response_schema={"type": "array", "items": RESPONSE_SCHEMA},
    )
    body = json.loads(_build_schema_mock_body(endpoint))

    assert isinstance(body["data"], list) and body["data"]
    assert "@example.com" in body["data"][0]["email"]


def test_build_schema_mock_body_handles_empty_schema():
    endpoint = ApiEndpoint(project_id=1, method="GET", path="/x", response_schema={})
    body = json.loads(_build_schema_mock_body(endpoint))

    assert body["code"] == 200
    # 无 schema 时 data 为 null，且整体仍是合法包裹结构
    assert set(body.keys()) == {"code", "msg", "success", "data"}


def test_top_level_string_schema_returns_uuid_hex():
    """response schema 是纯字符串时，直接返回一个 uuid hex。"""
    import uuid as uuid_mod

    endpoint = ApiEndpoint(project_id=1, method="GET", path="/x", response_schema={"type": "string"})
    data = json.loads(_build_schema_mock_body(endpoint))["data"]

    uuid_mod.UUID(data)                       # 可解析
    assert "-" not in data                    # hex 格式，无连字符
    assert len(data) == 32


def test_all_uuid_values_use_hex_format():
    """format:uuid 与字段名含 uuid 的字符串都输出无连字符的 hex。"""
    schema = {
        "type": "object",
        "properties": {
            "instanceId": {"type": "string", "format": "uuid"},
            "fileUuid": {"type": "string"},
        },
    }
    endpoint = ApiEndpoint(project_id=1, method="GET", path="/x", response_schema=schema)
    data = json.loads(_build_schema_mock_body(endpoint))["data"]

    for key in ("instanceId", "fileUuid"):
        assert "-" not in data[key] and len(data[key]) == 32, f"{key} 应为 uuid hex: {data[key]}"


# --- 请求值回填与分页 -------------------------------------------------------------

def test_request_values_override_response_fields():
    """请求 body/query/param 中出现且响应里也存在的字段，用请求值回填。"""
    from app.services.mock_data import build_mock_response_body

    schema = {
        "type": "object",
        "properties": {
            "resName": {"type": "string"},
            "amount": {"type": "number"},
            "partyBName": {"type": "string"},
            "otherField": {"type": "string"},
        },
    }
    request_values = {
        "resName": "施工合同",
        "amount": 88888.88,
        "pageSize": 5,
    }
    data = json.loads(build_mock_response_body(schema, request_values))["data"]

    assert data["resName"] == "施工合同"      # body 值回填
    assert data["amount"] == 88888.88
    assert "partyBName" in data              # 请求中没有的字段保持 mock 随机值
    assert "otherField" in data


def test_page_size_controls_array_length():
    """请求带 pageSize 时，响应中的数组裁剪到对应条数。"""
    from app.services.mock_data import build_mock_response_body

    list_item = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
    }
    schema = {
        "type": "object",
        "properties": {
            "list": {"type": "array", "items": list_item},
            "total": {"type": "integer"},
        },
    }
    request_values = {"pageSize": 1}
    data = json.loads(build_mock_response_body(schema, request_values))["data"]
    assert len(data["list"]) == 1

    request_values10 = {"page_size": 10}
    data10 = json.loads(build_mock_response_body(schema, request_values10))["data"]
    assert len(data10["list"]) == 10  # pageSize 传多少就生成多少条


def test_top_level_array_response_respects_page_size():
    from app.services.mock_data import build_mock_response_body

    schema = {
        "type": "array",
        "items": {"type": "object", "properties": {"id": {"type": "string"}}},
    }
    data = json.loads(build_mock_response_body(schema, {"limit": 1}))["data"]
    assert isinstance(data, list) and len(data) == 1


def test_request_overrides_apply_inside_nested_arrays():
    from app.services.mock_data import apply_request_overrides

    payload = {"items": [{"sectionId": "X", "n": 1}, {"sectionId": "Y"}]}
    result = apply_request_overrides(payload, {"sectionId": "SEC-9"})
    assert result["items"][0]["sectionId"] == "SEC-9"
    assert result["items"][1]["sectionId"] == "SEC-9"


def test_collect_request_values_merges_sources():
    from app.services.mock_data import collect_request_values

    values = collect_request_values(
        {"name": "合同A", "nested": {"code": "C1"}},
        {"pageSize": "2", "flag": "on"},
        [{"key": "id", "in": "path", "value": "42"}, {"key": "q", "value": None}],
    )
    assert values["name"] == "合同A"
    assert values["code"] == "C1"
    assert values["pageSize"] == 2       # 数值化
    assert values["flag"] == "on"
    assert values["id"] == 42            # 数值化
    assert "q" not in values             # 空值参数跳过


def test_path_params_are_matched_and_captured():
    """路径参数（如 /users/{id}）要被捕获并用于响应回填。"""
    from app.routes.mock_server import _match_endpoint_path

    is_match, params = _match_endpoint_path("/users/{id}", "/users/123")
    assert is_match is True
    assert params == {"id": "123"}

    is_match2, params2 = _match_endpoint_path("/orgs/{org}/users/{uid}", "/orgs/acme/users/9")
    assert is_match2 is True
    assert params2 == {"org": "acme", "uid": "9"}

    no_match, no_params = _match_endpoint_path("/users/{id}", "/users/1/extra")
    assert no_match is False and no_params == {}


# --- prefix resolution in the toggle route ------------------------------------

def test_blank_prefix_falls_back_to_project_name_slug():
    """在项目配置里清空前缀时，应回退到项目名 slug，而不是变成空前缀。"""
    project = ApiProject(name="Contract Center", mock_prefix="/old", user_id="u1")

    # 复现路由逻辑：显式传入空串 -> 规范化为 "" -> 回退到 slug
    project.mock_prefix = _normalize_mock_prefix("")
    if not project.mock_prefix:
        project.mock_prefix = _default_mock_prefix(project.name)

    assert project.mock_prefix == "/contract-center"


def test_prefix_can_be_updated_without_touching_enabled_state():
    project = ApiProject(name="Contract", mock_prefix="/contract", mock_enabled=True, user_id="u1")

    project.mock_prefix = _normalize_mock_prefix("billing/")

    assert project.mock_prefix == "/billing"
    assert project.mock_enabled is True
