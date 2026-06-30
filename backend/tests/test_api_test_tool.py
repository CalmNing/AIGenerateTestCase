"""Characterization tests for the pure functions in api_test_tool.

Protects behavior across the Phase 2 module split (swagger_parser /
assertion_engine / step_runner / scenario_runner). No network/LLM/DB-file.
"""
import json

import pytest

from app.services.api_test_tool import (
    parse_spec_text,
    detect_base_url,
    endpoints_from_spec,
    build_body_from_schema,
    generate_unit_test_steps,
    _run_assertions,
)


_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "demo", "version": "1.0"},
    "servers": [{"url": "https://api.example.com/v1"}],
    "paths": {
        "/users": {
            "post": {
                "summary": "创建用户",
                "operationId": "createUser",
                "tags": ["users"],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "age": {"type": "integer", "minimum": 0, "maximum": 120},
                                },
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/users/{id}": {
            "get": {
                "summary": "查询用户",
                "operationId": "getUser",
                "tags": ["users"],
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                ],
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


def test_parse_spec_text_json():
    spec = parse_spec_text(json.dumps(_SPEC))
    assert spec["openapi"] == "3.0.0"
    assert "/users" in spec["paths"]


def test_parse_spec_text_yaml():
    import yaml
    spec = parse_spec_text(yaml.safe_dump(_SPEC))
    assert "/users/{id}" in spec["paths"]


def test_parse_spec_text_rejects_non_spec():
    with pytest.raises(Exception):
        parse_spec_text('{"foo": "bar"}')


def test_detect_base_url():
    assert detect_base_url(_SPEC) == "https://api.example.com/v1"


def test_endpoints_from_spec_builds_two_endpoints():
    eps = endpoints_from_spec(_SPEC, project_id=1, user_id=None)
    by_path = {(e.method, e.path) for e in eps}
    assert ("POST", "/users") in by_path
    assert ("GET", "/users/{id}") in by_path

    post_ep = next(e for e in eps if e.method == "POST")
    assert post_ep.name == "创建用户"
    assert post_ep.tags == ["users"]
    # default success assertion attached
    assert post_ep.assertions == [{"type": "jsonpath_equals", "value": 200, "jsonpath": "$.code"}]
    # POST with a body gets a Content-Type header
    assert any(h.get("key") == "Content-Type" for h in post_ep.headers)

    get_ep = next(e for e in eps if e.method == "GET")
    # path parameter id captured
    assert any(p.get("key") == "id" and p.get("in") == "path" for p in get_ep.parameters)


def test_build_body_from_schema_produces_valid_json():
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer", "minimum": 0, "maximum": 120},
        },
    }
    body = build_body_from_schema(schema)
    parsed = json.loads(body)
    assert "name" in parsed
    assert "age" in parsed


def test_generate_unit_test_steps_nonempty_for_post():
    eps = endpoints_from_spec(_SPEC, project_id=1, user_id=None)
    post_ep = next(e for e in eps if e.method == "POST")
    steps = generate_unit_test_steps(post_ep)
    assert isinstance(steps, list)
    assert len(steps) >= 1
    # each step has a name and assertions
    for s in steps:
        assert "name" in s


def test_run_assertions_status_code_pass_and_fail():
    # status_code assertion: pass
    results = _run_assertions(
        [{"type": "status_code", "value": 200}], {"code": 0}, 200, 12.0
    )
    assert all(r["passed"] for r in results)

    # status_code assertion: fail
    results = _run_assertions(
        [{"type": "status_code", "value": 200}], {"code": 0}, 404, 12.0
    )
    assert any(not r["passed"] for r in results)


def test_run_assertions_jsonpath_equals():
    results = _run_assertions(
        [{"type": "jsonpath_equals", "value": 0, "jsonpath": "$.code"}],
        {"code": 0}, 200, 5.0,
    )
    assert all(r["passed"] for r in results)
