"""Single-step / single-endpoint execution and unit-test step generation.

Extracted verbatim from api_test_tool.py (Phase 2 split).
"""
import json
import time
from copy import deepcopy
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlmodel import Session

from db.models import ApiEndpoint, ApiProject, ApiScenario
from app.routes.proxy import (
    build_param_map, is_valid_url, substitute_in_data, substitute_in_headers,
    substitute_in_params, substitute_variables,
)
from app.services.assertion_engine import (
    _apply_pre_actions, _client_error_assertions, _extract_post_actions,
    _pairs_to_map, _run_assertions, _schema_invalid_assertions, _success_assertions,
)
from app.services.schema_utils import (
    MAX_SCHEMA_EXAMPLE_DEPTH, _coerce_int, _json_equal, _json_fingerprint,
    _maximum_number, _minimum_number, _schema_example, _schema_properties,
    _schema_required, _schema_type, _valid_number_for_schema,
)
from app.services.swagger_parser import _body_to_text, _endpoint_body_example


API_TEST_HTTP_LIMITS = httpx.Limits(max_keepalive_connections=0)
MAX_GENERATED_UNIT_STEPS = 24


def _merge_endpoint_step(endpoint: ApiEndpoint, step: dict) -> dict:
    data = endpoint.model_dump()
    for key in ("name", "method", "path", "url", "headers", "parameters", "body", "pre_actions", "post_actions", "assertions"):
        if key in step and step[key] is not None:
            data[key] = step[key]
    data["continue_on_failure"] = bool(step.get("continue_on_failure", False))
    data["step_name"] = step.get("name") or data.get("name")
    return data

def _apply_path_parameters(path: str, params: list[dict] | None, variables: dict, unresolved: set) -> str:
    result = path
    for item in params or []:
        if not isinstance(item, dict) or item.get("in") != "path" or not item.get("key"):
            continue
        value = substitute_variables(str(item.get("value", "")), variables, unresolved)
        key = str(item["key"])
        result = result.replace("{" + key + "}", value).replace(":" + key, value)
    return result

async def _execute_endpoint_step(
    client: httpx.AsyncClient,
    *,
    project: ApiProject,
    endpoint: ApiEndpoint,
    step: dict,
    variables: dict,
    default_base_url: str,
    index: int,
) -> tuple[dict, bool]:
    merged = _merge_endpoint_step(endpoint, step)
    pre_updates = _apply_pre_actions(merged.get("pre_actions"), variables)
    unresolved: set[str] = set()
    path = substitute_variables(str(merged.get("url") or merged.get("path") or ""), variables, unresolved)
    path = _apply_path_parameters(path, merged.get("parameters"), variables, unresolved)
    base_url = (step.get("base_url") or default_base_url or project.base_url or "").rstrip("/")
    url = path if path.startswith(("http://", "https://")) else urljoin(base_url + "/", path.lstrip("/"))
    project_headers = step.get("project_headers") if "project_headers" in step else getattr(project, "headers", None)
    header_map = _pairs_to_map(project_headers)
    header_map.update(_pairs_to_map(merged.get("headers")))
    headers = substitute_in_headers(header_map, variables, unresolved)
    params = substitute_in_params(_pairs_to_map(merged.get("parameters")), variables, unresolved)
    body_text = substitute_variables(merged.get("body") or "", variables, unresolved)

    data: Any = None
    if body_text:
        try:
            data = json.loads(body_text)
        except json.JSONDecodeError:
            data = body_text
        data = substitute_in_data(data, variables, unresolved)

    request_snapshot = {
        "method": merged.get("method", "GET"),
        "url": url,
        "headers": headers,
        "params": params,
        "body": data,
    }

    if unresolved:
        return ({
            "index": index,
            "name": merged.get("step_name"),
            "status": "error",
            "detail": f"变量未定义: {', '.join(sorted(unresolved))}",
            "request": request_snapshot,
        }, False)

    if not is_valid_url(url):
        return ({
            "index": index,
            "name": merged.get("step_name"),
            "status": "error",
            "detail": "无效的 URL",
            "request": request_snapshot,
        }, False)

    start = time.monotonic()
    try:
        request_kwargs = {
            "method": merged.get("method", "GET"),
            "url": url,
            "headers": headers,
            "params": params,
            "json": data if isinstance(data, (dict, list)) else None,
            "content": data if isinstance(data, str) else None,
            "timeout": 30.0,
        }
        try:
            response = await client.request(**request_kwargs)
        except httpx.RemoteProtocolError:
            retry_headers = dict(headers)
            retry_headers["Connection"] = "close"
            response = await client.request(**{**request_kwargs, "headers": retry_headers})
        elapsed_ms = int((time.monotonic() - start) * 1000)
        try:
            response_data = response.json()
        except Exception:
            response_data = response.text
        assertion_results = _run_assertions(
            merged.get("assertions"),
            response_data,
            response.status_code,
            elapsed_ms,
            variables,
        )
        extracted = _extract_post_actions(merged.get("post_actions"), response_data, variables)
        step_passed = (
            all(a["passed"] for a in assertion_results)
            if assertion_results
            else 200 <= response.status_code < 400
        )
        return ({
            "index": index,
            "name": merged.get("step_name"),
            "status": "passed" if step_passed else "failed",
            "request": request_snapshot,
            "response": {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response_data,
                "elapsed_ms": elapsed_ms,
            },
            "pre_updates": pre_updates,
            "extracted": extracted,
            "assertions": assertion_results,
        }, step_passed)
    except Exception as exc:
        return ({
            "index": index,
            "name": merged.get("step_name"),
            "status": "error",
            "detail": str(exc),
            "request": request_snapshot,
        }, False)

async def run_endpoint(db: Session, project: ApiProject, endpoint: ApiEndpoint, overrides: dict | None = None) -> dict:
    overrides = overrides or {}
    env_id = overrides.get("environment_id") or endpoint.environment_id or project.environment_id
    variables = build_param_map(db, env_id, overrides.get("variables") or [])
    default_base_url = (overrides.get("base_url") or project.base_url or "").rstrip("/")
    async with httpx.AsyncClient(limits=API_TEST_HTTP_LIMITS) as client:
        step, passed = await _execute_endpoint_step(
            client,
            project=project,
            endpoint=endpoint,
            step=overrides,
            variables=variables,
            default_base_url=default_base_url,
            index=1,
        )
    return {"passed": passed, "variables": variables, "step": step}

async def run_endpoint_steps(db: Session, executable_steps: list[dict]) -> dict:
    if not executable_steps:
        return {"passed": False, "variables": {}, "steps": []}

    first = executable_steps[0]
    first_overrides = first.get("overrides") or {}
    first_endpoint = first.get("endpoint")
    first_project = first.get("project")
    env_id = (
        first_overrides.get("environment_id")
        or getattr(first_endpoint, "environment_id", None)
        or getattr(first_project, "environment_id", None)
    )
    variables = build_param_map(db, env_id, first_overrides.get("variables") or [])
    results = []
    passed = True

    async with httpx.AsyncClient(limits=API_TEST_HTTP_LIMITS) as client:
        for index, item in enumerate(executable_steps, 1):
            endpoint = item.get("endpoint")
            project = item.get("project")
            overrides = item.get("overrides") or {}
            if not endpoint or not project:
                results.append({
                    "index": index,
                    "status": "error",
                    "detail": "接口步骤配置不完整",
                    "testcase_step": item.get("testcase_step"),
                })
                passed = False
                break

            default_base_url = (overrides.get("base_url") or project.base_url or "").rstrip("/")
            step_result, step_passed = await _execute_endpoint_step(
                client,
                project=project,
                endpoint=endpoint,
                step=overrides,
                variables=variables,
                default_base_url=default_base_url,
                index=index,
            )
            step_result["endpoint_id"] = endpoint.id
            step_result["endpoint_name"] = endpoint.name
            step_result["endpoint_method"] = endpoint.method
            step_result["endpoint_path"] = endpoint.path
            step_result["project_id"] = project.id
            step_result["project_name"] = project.name
            step_result["testcase_step"] = item.get("testcase_step")
            results.append(step_result)
            passed = passed and step_passed
            if not step_passed and not overrides.get("continue_on_failure"):
                break

    return {"passed": passed, "variables": variables, "steps": results}

def _unit_step(endpoint: ApiEndpoint, name: str, *, body: Any = None, parameters: list[dict] | None = None, headers: list[dict] | None = None, assertions: list[dict] | None = None) -> dict:
    return {
        "endpoint_id": endpoint.id,
        "name": name,
        "enabled": True,
        "continue_on_failure": True,
        "headers": headers if headers is not None else list(endpoint.headers or []),
        "parameters": parameters if parameters is not None else list(endpoint.parameters or []),
        "body": _body_to_text(body),
        "pre_actions": list(endpoint.pre_actions or []),
        "post_actions": list(endpoint.post_actions or []),
        "assertions": assertions if assertions is not None else _success_assertions(),
    }

def _path_label(path: tuple[Any, ...]) -> str:
    if not path:
        return "$"
    label = ""
    for item in path:
        if isinstance(item, int):
            label += f"[{item}]"
        else:
            label += f".{item}" if label else str(item)
    return label

def _get_path_value(value: Any, path: tuple[Any, ...]) -> Any:
    current = value
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                raise KeyError(path)
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                raise KeyError(path)
            current = current[part]
    return current

def _path_exists(value: Any, path: tuple[Any, ...]) -> bool:
    try:
        _get_path_value(value, path)
        return True
    except KeyError:
        return False

def _modify_body_path(body: Any, path: tuple[Any, ...], *, value: Any = None, remove: bool = False) -> Any:
    result = deepcopy(body)
    if not path:
        return value
    parent = result
    for part in path[:-1]:
        parent = parent[part]
    last = path[-1]
    if remove:
        if isinstance(parent, dict) and not isinstance(last, int):
            parent.pop(last, None)
        elif isinstance(parent, list) and isinstance(last, int) and last < len(parent):
            parent.pop(last)
    elif isinstance(parent, dict) and not isinstance(last, int):
        parent[last] = value
    elif isinstance(parent, list) and isinstance(last, int) and last < len(parent):
        parent[last] = value
    return result

def _iter_schema_fields(schema: dict | None, path: tuple[Any, ...] = (), depth: int = 0) -> list[dict]:
    if not isinstance(schema, dict) or depth > MAX_SCHEMA_EXAMPLE_DEPTH:
        return []

    fields: list[dict] = []
    properties = _schema_properties(schema)
    if properties:
        required = set(_schema_required(schema))
        for key, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            field_path = (*path, str(key))
            fields.append({
                "path": field_path,
                "schema": prop,
                "required": str(key) in required,
            })
            fields.extend(_iter_schema_fields(prop, field_path, depth + 1))
        return fields

    if _schema_type(schema) == "array" and isinstance(schema.get("items"), dict):
        fields.extend(_iter_schema_fields(schema["items"], (*path, 0), depth + 1))
    return fields

def _request_value_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)

def _invalid_values_for_property(prop: dict, current: Any) -> list[tuple[str, Any]]:
    prop_type = _schema_type(prop)
    cases: list[tuple[str, Any]] = []
    enum = prop.get("enum")
    if isinstance(enum, list) and enum:
        cases.append(("枚举外取值", "__invalid_enum__"))

    if prop_type in {"integer", "number"}:
        minimum = _minimum_number(prop)
        maximum = _maximum_number(prop)
        if minimum is not None:
            cases.append(("小于最小值", int(minimum - 1) if prop_type == "integer" else minimum - 1))
        if maximum is not None:
            cases.append(("大于最大值", int(maximum + 1) if prop_type == "integer" else maximum + 1))
        if prop.get("multipleOf"):
            base = _valid_number_for_schema(prop, integer=prop_type == "integer")
            cases.append(("不满足倍数约束", base + 1))
        cases.append(("类型错误", "not-a-number"))
    elif prop_type == "boolean":
        cases.append(("类型错误", "not-a-boolean"))
    elif prop_type == "array":
        min_items = _coerce_int(prop.get("minItems"))
        max_items = _coerce_int(prop.get("maxItems"))
        item = _schema_example({}, prop.get("items", {}))
        if min_items and min_items > 0:
            cases.append(("少于最小元素数", [item for _ in range(max(min_items - 1, 0))]))
        if max_items is not None:
            cases.append(("超过最大元素数", [item for _ in range(min(max_items + 1, 6))]))
        cases.append(("类型错误", "not-an-array"))
    elif prop_type == "object":
        cases.append(("类型错误", "not-an-object"))
    elif prop_type == "string" or prop_type is None:
        min_length = _coerce_int(prop.get("minLength"))
        max_length = _coerce_int(prop.get("maxLength"))
        if min_length and min_length > 0:
            cases.append(("短于最小长度", "x" * max(min_length - 1, 0)))
        if max_length is not None:
            cases.append(("长于最大长度", "x" * min(max_length + 1, 256)))
        if prop.get("pattern"):
            cases.append(("不匹配格式", "__invalid_pattern__"))
        cases.append(("类型错误", 12345))

    if not cases:
        cases.append(("非法值", None if current is not None else "__invalid__"))
    return cases

def _boundary_values_for_property(prop: dict, current: Any) -> list[tuple[str, Any]]:
    prop_type = _schema_type(prop)
    cases: list[tuple[str, Any]] = []

    if prop_type == "string":
        min_length = _coerce_int(prop.get("minLength"))
        max_length = _coerce_int(prop.get("maxLength"))
        if min_length is not None:
            cases.append(("最小长度", "x" * min_length))
        if max_length is not None:
            cases.append(("最大长度", "x" * min(max_length, 256)))
    elif prop_type in {"integer", "number"}:
        minimum = _minimum_number(prop)
        maximum = _maximum_number(prop)
        if minimum is not None:
            cases.append(("最小值", int(minimum) if prop_type == "integer" else minimum))
        if maximum is not None:
            cases.append(("最大值", int(maximum) if prop_type == "integer" else maximum))
    elif prop_type == "array":
        min_items = _coerce_int(prop.get("minItems"))
        max_items = _coerce_int(prop.get("maxItems"))
        item = _schema_example({}, prop.get("items", {}))
        if min_items is not None:
            cases.append(("最小元素数", [item for _ in range(min(min_items, 6))]))
        if max_items is not None:
            cases.append(("最大元素数", [item for _ in range(min(max_items, 6))]))
    elif isinstance(prop.get("enum"), list) and prop["enum"]:
        cases.append(("枚举合法值", prop["enum"][0]))

    unique_cases: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for label, value in cases:
        fingerprint = _json_fingerprint(value)
        if fingerprint not in seen and not _json_equal(value, current):
            unique_cases.append((label, value))
            seen.add(fingerprint)
    return unique_cases

def _replace_pair_value(items: list[dict] | None, target: dict, value: Any) -> list[dict]:
    result = []
    target_key = str(target.get("key") or "")
    target_location = str(target.get("in") or "")
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cloned = dict(item)
        if str(cloned.get("key") or "") == target_key and str(cloned.get("in") or "") == target_location:
            cloned["value"] = _request_value_text(value)
        result.append(cloned)
    return result

def _append_unit_step(steps: list[dict], seen: set[str], step: dict) -> None:
    if len(steps) >= MAX_GENERATED_UNIT_STEPS:
        return
    fingerprint = _json_fingerprint({
        "name": step.get("name"),
        "headers": step.get("headers"),
        "parameters": step.get("parameters"),
        "body": step.get("body"),
        "assertions": step.get("assertions"),
    })
    if fingerprint in seen:
        return
    steps.append(step)
    seen.add(fingerprint)

def generate_unit_test_steps(endpoint: ApiEndpoint) -> list[dict]:
    base_body = _endpoint_body_example(endpoint)
    steps: list[dict] = [
        _unit_step(endpoint, "有效等价类：schema 合法请求", body=base_body, assertions=_success_assertions())
    ]
    seen = {
        _json_fingerprint({
            "name": steps[0].get("name"),
            "headers": steps[0].get("headers"),
            "parameters": steps[0].get("parameters"),
            "body": steps[0].get("body"),
            "assertions": steps[0].get("assertions"),
        })
    }

    if isinstance(base_body, (dict, list)):
        fields = _iter_schema_fields(endpoint.request_schema)

        for field in fields:
            if len(steps) >= MAX_GENERATED_UNIT_STEPS:
                break
            path = field["path"]
            if not field["required"] or not _path_exists(base_body, path):
                continue
            body = _modify_body_path(base_body, path, remove=True)
            _append_unit_step(steps, seen, _unit_step(
                endpoint,
                f"缺少必填请求体字段：{_path_label(path)}",
                body=body,
                assertions=_client_error_assertions(51001),
            ))

        for field in fields:
            if len(steps) >= MAX_GENERATED_UNIT_STEPS:
                break
            path = field["path"]
            prop = field["schema"]
            if not _path_exists(base_body, path):
                continue
            current = _get_path_value(base_body, path)
            for reason, value in _invalid_values_for_property(prop, current)[:1]:
                if len(steps) >= MAX_GENERATED_UNIT_STEPS:
                    break
                invalid_body = _modify_body_path(base_body, path, value=value)
                _append_unit_step(steps, seen, _unit_step(
                    endpoint,
                    f"非法值：{_path_label(path)}（{reason}）",
                    body=invalid_body,
                    assertions=_schema_invalid_assertions(reason),
                ))

        for field in fields:
            if len(steps) >= MAX_GENERATED_UNIT_STEPS:
                break
            path = field["path"]
            prop = field["schema"]
            if not _path_exists(base_body, path):
                continue
            current = _get_path_value(base_body, path)
            for reason, value in _boundary_values_for_property(prop, current)[:1]:
                if len(steps) >= MAX_GENERATED_UNIT_STEPS:
                    break
                boundary_body = _modify_body_path(base_body, path, value=value)
                _append_unit_step(steps, seen, _unit_step(
                    endpoint,
                    f"边界值：{_path_label(path)}（{reason}）",
                    body=boundary_body,
                    assertions=_success_assertions(),
                ))

    for param in endpoint.parameters or []:
        if len(steps) >= MAX_GENERATED_UNIT_STEPS:
            break
        if not isinstance(param, dict) or not param.get("required") or param.get("in") == "path":
            continue
        key = param.get("key")
        params = [item for item in (endpoint.parameters or []) if not (isinstance(item, dict) and item.get("key") == key and item.get("in") == param.get("in"))]
        _append_unit_step(steps, seen, _unit_step(
            endpoint,
            f"缺少必填参数：{key}",
            body=base_body,
            parameters=params,
            assertions=_client_error_assertions(51001),
        ))

    for header in endpoint.headers or []:
        if len(steps) >= MAX_GENERATED_UNIT_STEPS:
            break
        if not isinstance(header, dict) or not header.get("required"):
            continue
        key = header.get("key")
        headers = [item for item in (endpoint.headers or []) if not (isinstance(item, dict) and item.get("key") == key)]
        _append_unit_step(steps, seen, _unit_step(
            endpoint,
            f"缺少必填 Header：{key}",
            body=base_body,
            headers=headers,
            assertions=_client_error_assertions(51001),
        ))

    for param in endpoint.parameters or []:
        if len(steps) >= MAX_GENERATED_UNIT_STEPS:
            break
        if not isinstance(param, dict) or not isinstance(param.get("schema"), dict) or not param.get("key"):
            continue
        current = param.get("value")
        for reason, value in _invalid_values_for_property(param["schema"], current)[:1]:
            if len(steps) >= MAX_GENERATED_UNIT_STEPS:
                break
            _append_unit_step(steps, seen, _unit_step(
                endpoint,
                f"非法参数：{param.get('in', 'query')}.{param.get('key')}（{reason}）",
                body=base_body,
                parameters=_replace_pair_value(endpoint.parameters, param, value),
                assertions=_schema_invalid_assertions(reason),
            ))

    for header in endpoint.headers or []:
        if len(steps) >= MAX_GENERATED_UNIT_STEPS:
            break
        if not isinstance(header, dict) or not isinstance(header.get("schema"), dict) or not header.get("key"):
            continue
        current = header.get("value")
        for reason, value in _invalid_values_for_property(header["schema"], current)[:1]:
            if len(steps) >= MAX_GENERATED_UNIT_STEPS:
                break
            _append_unit_step(steps, seen, _unit_step(
                endpoint,
                f"非法 Header：{header.get('key')}（{reason}）",
                body=base_body,
                headers=_replace_pair_value(endpoint.headers, header, value),
                assertions=_schema_invalid_assertions(reason),
            ))

    for param in endpoint.parameters or []:
        if len(steps) >= MAX_GENERATED_UNIT_STEPS:
            break
        if not isinstance(param, dict) or not isinstance(param.get("schema"), dict) or not param.get("key"):
            continue
        current = param.get("value")
        for reason, value in _boundary_values_for_property(param["schema"], current)[:1]:
            if len(steps) >= MAX_GENERATED_UNIT_STEPS:
                break
            _append_unit_step(steps, seen, _unit_step(
                endpoint,
                f"边界参数：{param.get('in', 'query')}.{param.get('key')}（{reason}）",
                body=base_body,
                parameters=_replace_pair_value(endpoint.parameters, param, value),
                assertions=_success_assertions(),
            ))

    for header in endpoint.headers or []:
        if len(steps) >= MAX_GENERATED_UNIT_STEPS:
            break
        if not isinstance(header, dict) or not isinstance(header.get("schema"), dict) or not header.get("key"):
            continue
        current = header.get("value")
        for reason, value in _boundary_values_for_property(header["schema"], current)[:1]:
            if len(steps) >= MAX_GENERATED_UNIT_STEPS:
                break
            _append_unit_step(steps, seen, _unit_step(
                endpoint,
                f"边界 Header：{header.get('key')}（{reason}）",
                body=base_body,
                headers=_replace_pair_value(endpoint.headers, header, value),
                assertions=_success_assertions(),
            ))

    return steps[:MAX_GENERATED_UNIT_STEPS]

def create_unit_test_scenario(db: Session, project: ApiProject, endpoint: ApiEndpoint, user_id: str | None) -> ApiScenario:
    scenario = ApiScenario(
        project_id=project.id,
        name=f"{endpoint.name or endpoint.method + ' ' + endpoint.path} 接口单测 {time.strftime('%Y%m%d%H%M%S')}",
        description=f"自动生成接口单测：{endpoint.method} {endpoint.path}",
        base_url=project.base_url,
        environment_id=endpoint.environment_id or project.environment_id,
        variables=[],
        steps=generate_unit_test_steps(endpoint),
        user_id=user_id,
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario
