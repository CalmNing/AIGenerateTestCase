import json
import re
import time
from copy import deepcopy
from typing import Any
from urllib.parse import urljoin

import httpx
import yaml
from jsonpath_ng import parse as parse_jsonpath
from sqlmodel import Session, select

from app.routes.proxy import (
    build_param_map,
    is_valid_url,
    substitute_in_data,
    substitute_in_headers,
    substitute_in_params,
    substitute_variables,
)
from db.models import ApiEndpoint, ApiProject, ApiScenario, GlobalParameter


HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
REMOVED_FROM_SPEC_TAG = "__removed_from_spec__"
MAX_GENERATED_UNIT_STEPS = 24
API_TEST_HTTP_LIMITS = httpx.Limits(max_keepalive_connections=0)
DEFAULT_SUCCESS_ASSERTIONS = [{"type": "jsonpath_equals", "value": 200, "jsonpath": "$.code"}]


def parse_spec_text(text: str) -> dict:
    spec = yaml.safe_load(text)
    if not isinstance(spec, dict):
        raise ValueError("Swagger/OpenAPI 文档格式不正确")
    if "openapi" not in spec and "swagger" not in spec:
        raise ValueError("未识别到 OpenAPI 或 Swagger 标识")
    return spec


def detect_base_url(spec: dict) -> str:
    servers = spec.get("servers")
    if isinstance(servers, list) and servers:
        first = servers[0] if isinstance(servers[0], dict) else {}
        if first.get("url"):
            return str(first["url"])

    schemes = spec.get("schemes") if isinstance(spec.get("schemes"), list) else ["http"]
    scheme = schemes[0] if schemes else "http"
    host = spec.get("host") or ""
    base_path = spec.get("basePath") or ""
    if host:
        return f"{scheme}://{host}{base_path}".rstrip("/")
    return ""


MAX_SCHEMA_EXAMPLE_DEPTH = 8


def _resolve_ref(spec: dict, value: Any) -> Any:
    if not isinstance(value, dict) or "$ref" not in value:
        return value
    ref = value["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return value
    cur: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(cur, dict) or part not in cur:
            return value
        cur = cur[part]
    return cur


def _ref_key(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("$ref"), str):
        return value["$ref"]
    return None


def _schema_type(schema: dict) -> str | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        for item in schema_type:
            if item != "null":
                return str(item)
        return "null" if schema_type else None
    if schema_type:
        return str(schema_type)
    if "properties" in schema or "additionalProperties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        value = schema["enum"][0]
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int) and not isinstance(value, bool):
            return "integer"
        if isinstance(value, float):
            return "number"
    for composite_key in ("allOf", "oneOf", "anyOf"):
        choices = schema.get(composite_key)
        if isinstance(choices, list) and choices:
            for item in choices:
                if isinstance(item, dict):
                    item_type = _schema_type(item)
                    if item_type:
                        return item_type
    return None


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_schema_value(schema: dict) -> Any:
    if "example" in schema:
        return schema["example"]
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return examples[0]
    if "default" in schema:
        return schema["default"]
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        for item in enum:
            if item is not None:
                return item
        return enum[0]
    return None


def _minimum_number(schema: dict) -> int | float | None:
    exclusive = schema.get("exclusiveMinimum")
    if isinstance(exclusive, bool):
        minimum = _coerce_float(schema.get("minimum"))
        if minimum is None:
            return None
        return minimum + 1
    if exclusive is not None:
        value = _coerce_float(exclusive)
        return value + 1 if value is not None else None
    return _coerce_float(schema.get("minimum"))


def _maximum_number(schema: dict) -> int | float | None:
    exclusive = schema.get("exclusiveMaximum")
    if isinstance(exclusive, bool):
        maximum = _coerce_float(schema.get("maximum"))
        if maximum is None:
            return None
        return maximum - 1
    if exclusive is not None:
        value = _coerce_float(exclusive)
        return value - 1 if value is not None else None
    return _coerce_float(schema.get("maximum"))


def _valid_number_for_schema(schema: dict, *, integer: bool) -> int | float:
    minimum = _minimum_number(schema)
    maximum = _maximum_number(schema)
    value: int | float = 1
    if minimum is not None:
        value = minimum
    elif maximum is not None:
        value = maximum
    if integer:
        return int(value)
    return float(value)


def _valid_string_for_schema(schema: dict) -> str:
    explicit = _first_schema_value(schema)
    if explicit is not None:
        return str(explicit)

    fmt = schema.get("format")
    if fmt == "email":
        value = "user@example.com"
    elif fmt == "uuid":
        value = "00000000-0000-4000-8000-000000000000"
    elif fmt == "date":
        value = "2026-01-01"
    elif fmt == "date-time":
        value = "2026-01-01T00:00:00Z"
    elif fmt in {"uri", "url"}:
        value = "https://example.com"
    else:
        value = "string"

    min_length = _coerce_int(schema.get("minLength"), 0) or 0
    max_length = _coerce_int(schema.get("maxLength"))
    if len(value) < min_length:
        value = "x" * min_length
    if max_length is not None and len(value) > max_length:
        value = "x" * max_length
    return value


def _valid_array_for_schema(spec: dict, schema: dict, *, depth: int, seen_refs: set[str]) -> list[Any]:
    min_items = max(_coerce_int(schema.get("minItems"), 1) or 1, 0)
    max_items = _coerce_int(schema.get("maxItems"))
    count = min_items
    if max_items is not None:
        count = min(count, max_items)
    count = min(count, 3)
    item = _schema_example(spec, schema.get("items", {}), depth=depth + 1, seen_refs=seen_refs)
    return [item for _ in range(count)]


def _schema_example(spec: dict, schema: Any, *, depth: int = 0, seen_refs: set[str] | None = None) -> Any:
    if depth > MAX_SCHEMA_EXAMPLE_DEPTH:
        return None

    seen_refs = seen_refs or set()
    ref = _ref_key(schema)
    if ref:
        if ref in seen_refs:
            return None
        seen_refs = {*seen_refs, ref}

    schema = _resolve_ref(spec, schema)
    if not isinstance(schema, dict):
        return None
    explicit = _first_schema_value(schema)
    if explicit is not None:
        return explicit

    for composite_key in ("allOf", "oneOf", "anyOf"):
        choices = schema.get(composite_key)
        if isinstance(choices, list) and choices:
            if composite_key == "allOf":
                merged = {}
                for item in choices:
                    value = _schema_example(spec, item, depth=depth + 1, seen_refs=seen_refs)
                    if isinstance(value, dict):
                        merged.update(value)
                return merged or None
            return _schema_example(spec, choices[0], depth=depth + 1, seen_refs=seen_refs)

    schema_type = _schema_type(schema)
    if schema_type == "object" or "properties" in schema:
        result = {}
        for key, prop in (schema.get("properties") or {}).items():
            result[key] = _schema_example(spec, prop, depth=depth + 1, seen_refs=seen_refs)
        if not result and isinstance(schema.get("additionalProperties"), dict):
            result["key"] = _schema_example(
                spec,
                schema["additionalProperties"],
                depth=depth + 1,
                seen_refs=seen_refs,
            )
        return result
    if schema_type == "array":
        return _valid_array_for_schema(spec, schema, depth=depth, seen_refs=seen_refs)
    if schema_type == "integer":
        return _valid_number_for_schema(schema, integer=True)
    if schema_type == "number":
        return _valid_number_for_schema(schema, integer=False)
    if schema_type == "boolean":
        return True
    if schema_type == "null":
        return None
    return _valid_string_for_schema(schema)


def _resolve_schema_deep(spec: dict, schema: Any, *, depth: int = 0, seen_refs: set[str] | None = None) -> Any:
    if depth > MAX_SCHEMA_EXAMPLE_DEPTH:
        return schema

    seen_refs = seen_refs or set()
    ref = _ref_key(schema)
    if ref:
        if ref in seen_refs:
            return {"$ref": ref}
        seen_refs = {*seen_refs, ref}

    schema = _resolve_ref(spec, schema)
    if isinstance(schema, dict):
        resolved = {}
        for key, value in schema.items():
            if key == "$ref":
                continue
            resolved[key] = _resolve_schema_deep(spec, value, depth=depth + 1, seen_refs=seen_refs)
        return resolved
    if isinstance(schema, list):
        return [_resolve_schema_deep(spec, item, depth=depth + 1, seen_refs=seen_refs) for item in schema]
    return schema


def _param_example(spec: dict, param: dict) -> str:
    if "example" in param:
        return str(param["example"])
    if "default" in param:
        return str(param["default"])
    value = _schema_example(spec, param.get("schema", {}))
    if value is None and "type" in param:
        value = _schema_example(spec, {"type": param.get("type"), "enum": param.get("enum")})
    return "" if value is None else str(value)


def _parameter_schema(spec: dict, param: dict) -> dict:
    schema = param.get("schema")
    if isinstance(schema, dict):
        return _resolve_schema_deep(spec, schema)

    inline_keys = {
        "type",
        "format",
        "enum",
        "default",
        "example",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "items",
        "collectionFormat",
    }
    inline_schema = {key: param[key] for key in inline_keys if key in param}
    if isinstance(inline_schema.get("items"), dict):
        inline_schema["items"] = _resolve_schema_deep(spec, inline_schema["items"])
    return inline_schema


def _operation_body(spec: dict, operation: dict) -> str:
    request_body = _resolve_ref(spec, operation.get("requestBody"))
    if isinstance(request_body, dict):
        content = request_body.get("content") or {}
        for content_type in ("application/json", "application/*+json"):
            media = content.get(content_type)
            if isinstance(media, dict):
                if "example" in media:
                    return json.dumps(media["example"], ensure_ascii=False, indent=2)
                example = _schema_example(spec, media.get("schema", {}))
                return json.dumps(example if example is not None else {}, ensure_ascii=False, indent=2)
        if content:
            first_media = next(iter(content.values()))
            if isinstance(first_media, dict):
                example = first_media.get("example") or _schema_example(spec, first_media.get("schema", {}))
                return json.dumps(example if example is not None else {}, ensure_ascii=False, indent=2)

    for param in operation.get("parameters") or []:
        param = _resolve_ref(spec, param)
        if isinstance(param, dict) and param.get("in") == "body":
            example = _schema_example(spec, param.get("schema", {}))
            return json.dumps(example if example is not None else {}, ensure_ascii=False, indent=2)
    return ""


def _operation_request_schema(spec: dict, operation: dict) -> dict:
    request_body = _resolve_ref(spec, operation.get("requestBody"))
    if isinstance(request_body, dict):
        content = request_body.get("content") or {}
        for content_type in ("application/json", "application/*+json"):
            media = content.get(content_type)
            if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                return _resolve_schema_deep(spec, media["schema"])
        for media in content.values():
            if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                return _resolve_schema_deep(spec, media["schema"])

    for param in operation.get("parameters") or []:
        param = _resolve_ref(spec, param)
        if isinstance(param, dict) and param.get("in") == "body" and isinstance(param.get("schema"), dict):
            return _resolve_schema_deep(spec, param["schema"])
    return {}


def _operation_response_schema(spec: dict, operation: dict) -> dict:
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return {}

    preferred_keys = ["200", "201", "202", "204", "default"]
    preferred_keys.extend(key for key in sorted(responses.keys()) if isinstance(key, str) and key.startswith("2"))

    for key in preferred_keys:
        response = _resolve_ref(spec, responses.get(key))
        if not isinstance(response, dict):
            continue
        content = response.get("content") or {}
        if isinstance(content, dict):
            for content_type in ("application/json", "application/*+json"):
                media = content.get(content_type)
                if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                    return _resolve_schema_deep(spec, media["schema"])
            for media in content.values():
                if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                    return _resolve_schema_deep(spec, media["schema"])
        if isinstance(response.get("schema"), dict):
            return _resolve_schema_deep(spec, response["schema"])
    return {}


def endpoints_from_spec(spec: dict, project_id: int, user_id: str | None) -> list[ApiEndpoint]:
    endpoints = []
    paths = spec.get("paths") or {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_parameters = path_item.get("parameters") or []
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            all_params = list(path_parameters) + list(operation.get("parameters") or [])
            headers = []
            params = []
            for raw_param in all_params:
                param = _resolve_ref(spec, raw_param)
                if not isinstance(param, dict):
                    continue
                item = {
                    "key": param.get("name", ""),
                    "value": _param_example(spec, param),
                    "in": param.get("in", "query"),
                    "required": bool(param.get("required")),
                }
                schema = _parameter_schema(spec, param)
                if schema:
                    item["schema"] = schema
                if item["in"] == "header":
                    headers.append({
                        "key": item["key"],
                        "value": item["value"],
                        "required": item["required"],
                        "schema": item.get("schema", {}),
                    })
                elif item["in"] in {"query", "path"}:
                    params.append(item)

            body = _operation_body(spec, operation)
            if body and not any(h.get("key", "").lower() == "content-type" for h in headers):
                headers.insert(0, {"key": "Content-Type", "value": "application/json"})

            endpoints.append(
                ApiEndpoint(
                    project_id=project_id,
                    name=operation.get("summary") or operation.get("operationId") or f"{method.upper()} {path}",
                    method=method.upper(),
                    path=path,
                    tags=operation.get("tags") if isinstance(operation.get("tags"), list) else [],
                    headers=headers,
                    parameters=params,
                    body=body,
                    request_schema=_operation_request_schema(spec, operation),
                    response_schema=_operation_response_schema(spec, operation),
                    assertions=deepcopy(DEFAULT_SUCCESS_ASSERTIONS),
                    user_id=user_id,
                )
            )
    return endpoints


def _endpoint_key(endpoint: ApiEndpoint | dict) -> tuple[str, str]:
    method = endpoint.get("method") if isinstance(endpoint, dict) else endpoint.method
    path = endpoint.get("path") if isinstance(endpoint, dict) else endpoint.path
    return (str(method or "GET").upper(), str(path or ""))


def _json_fingerprint(value: Any) -> str:
    return json.dumps(value if value is not None else None, ensure_ascii=False, sort_keys=True)


def _json_equal(left: Any, right: Any) -> bool:
    return _json_fingerprint(left) == _json_fingerprint(right)


def _load_json_text(value: str | None) -> Any:
    if value is None or not str(value).strip():
        return None
    return json.loads(str(value))


def _body_equal(left: str | None, right: str | None) -> bool:
    try:
        return _load_json_text(left) == _load_json_text(right)
    except Exception:
        return (left or "").strip() == (right or "").strip()


def _merge_pair_list(existing: list[dict] | None, incoming: list[dict] | None, *, include_location: bool = False) -> list[dict]:
    result = [item for item in (existing or []) if isinstance(item, dict)]
    seen: set[tuple[str, str]] = set()
    for item in result:
        key = str(item.get("key") or "").strip().lower()
        location = str(item.get("in") or "").strip().lower() if include_location else ""
        if key:
            seen.add((key, location))

    for item in incoming or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        location = str(item.get("in") or "").strip().lower() if include_location else ""
        identity = (key.lower(), location)
        if identity not in seen:
            result.append(item)
            seen.add(identity)
    return result


def _merge_unique_dicts(existing: list[dict] | None, incoming: list[dict] | None) -> list[dict]:
    result = [item for item in (existing or []) if isinstance(item, dict)]
    seen = {_json_fingerprint(item) for item in result}
    for item in incoming or []:
        if not isinstance(item, dict):
            continue
        fingerprint = _json_fingerprint(item)
        if fingerprint not in seen:
            result.append(item)
            seen.add(fingerprint)
    return result


def _merge_body(existing: str | None, incoming: str | None) -> str | None:
    if not existing or not str(existing).strip():
        return incoming
    if not incoming or not str(incoming).strip():
        return existing
    try:
        existing_obj = _load_json_text(existing)
        incoming_obj = _load_json_text(incoming)
    except Exception:
        return existing
    if isinstance(existing_obj, dict) and isinstance(incoming_obj, dict):
        merged = dict(existing_obj)
        for key, value in incoming_obj.items():
            if key not in merged:
                merged[key] = value
        return json.dumps(merged, ensure_ascii=False, indent=2)
    return existing


def _field_was_edited(current: ApiEndpoint, baseline: ApiEndpoint | None, field: str) -> bool:
    if baseline is None:
        return True
    current_value = getattr(current, field, None)
    baseline_value = getattr(baseline, field, None)
    if field == "body":
        return not _body_equal(current_value, baseline_value)
    return not _json_equal(current_value, baseline_value)


def _merge_protected_field(current: ApiEndpoint, incoming: ApiEndpoint, baseline: ApiEndpoint | None, field: str) -> Any:
    incoming_value = getattr(incoming, field, None)
    if not _field_was_edited(current, baseline, field):
        return incoming_value
    current_value = getattr(current, field, None)
    if field == "headers":
        return _merge_pair_list(current_value, incoming_value)
    if field == "parameters":
        return _merge_pair_list(current_value, incoming_value, include_location=True)
    if field == "body":
        return _merge_body(current_value, incoming_value)
    return _merge_unique_dicts(current_value, incoming_value)


def sync_project_from_spec(db: Session, project: ApiProject, raw_spec: str, user_id: str | None) -> dict:
    new_spec = parse_spec_text(raw_spec)
    new_endpoints = endpoints_from_spec(new_spec, project.id, user_id)

    old_endpoint_map: dict[tuple[str, str], ApiEndpoint] = {}
    if project.raw_spec:
        try:
            old_spec = parse_spec_text(project.raw_spec)
            old_endpoint_map = {
                _endpoint_key(endpoint): endpoint
                for endpoint in endpoints_from_spec(old_spec, project.id, user_id)
            }
        except Exception:
            old_endpoint_map = {}

    existing_endpoints = db.exec(
        select(ApiEndpoint).where(ApiEndpoint.project_id == project.id)
    ).all()
    existing_map = {_endpoint_key(endpoint): endpoint for endpoint in existing_endpoints}

    created = 0
    updated = 0
    marked_removed = 0
    incoming_keys = set()

    for incoming in new_endpoints:
        key = _endpoint_key(incoming)
        incoming_keys.add(key)
        current = existing_map.get(key)
        if current is None:
            db.add(incoming)
            created += 1
            continue

        baseline = old_endpoint_map.get(key)
        current.name = incoming.name
        current.method = incoming.method
        current.path = incoming.path
        current.url = incoming.url
        current.tags = [tag for tag in (incoming.tags or []) if tag != REMOVED_FROM_SPEC_TAG]
        current.request_schema = incoming.request_schema
        current.response_schema = incoming.response_schema
        current.headers = _merge_protected_field(current, incoming, baseline, "headers")
        current.parameters = _merge_protected_field(current, incoming, baseline, "parameters")
        current.body = _merge_protected_field(current, incoming, baseline, "body")
        current.pre_actions = _merge_protected_field(current, incoming, baseline, "pre_actions")
        current.post_actions = _merge_protected_field(current, incoming, baseline, "post_actions")
        current.assertions = _merge_protected_field(current, incoming, baseline, "assertions")
        db.add(current)
        updated += 1

    for current in existing_endpoints:
        if _endpoint_key(current) in incoming_keys:
            continue
        tags = list(current.tags or [])
        if REMOVED_FROM_SPEC_TAG not in tags:
            tags.append(REMOVED_FROM_SPEC_TAG)
            current.tags = tags
            db.add(current)
            marked_removed += 1

    project.base_url = detect_base_url(new_spec)
    project.raw_spec = raw_spec
    project.source_type = "url"
    db.add(project)
    db.commit()
    db.refresh(project)

    all_endpoints = db.exec(
        select(ApiEndpoint)
        .where(ApiEndpoint.project_id == project.id)
        .order_by(ApiEndpoint.id)
    ).all()
    return {
        "project": project,
        "endpoints": all_endpoints,
        "created": created,
        "updated": updated,
        "marked_removed": marked_removed,
    }


def _pairs_to_map(items: list[dict] | None, *, include_path: bool = False) -> dict:
    result = {}
    for item in items or []:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        if not include_path and item.get("in") == "path":
            continue
        result[item["key"]] = item.get("value", "")
    return result


def _apply_pre_actions(actions: list[dict] | None, variables: dict) -> dict:
    updates = {}
    for action in actions or []:
        if not isinstance(action, dict) or action.get("type", "set_variable") != "set_variable":
            continue
        key = action.get("key") or action.get("variable")
        if not key:
            continue
        unresolved: set[str] = set()
        value = substitute_variables(str(action.get("value", "")), variables, unresolved)
        updates[key] = value
    variables.update(updates)
    return updates


def _extract_post_actions(actions: list[dict] | None, response_data: Any, variables: dict) -> dict:
    extracted = {}
    if not isinstance(response_data, (dict, list)):
        return extracted
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        if action.get("type", "extract_jsonpath") != "extract_jsonpath":
            continue
        key = action.get("key") or action.get("variable")
        expr = action.get("jsonpath")
        if not key or not expr:
            continue
        matches = parse_jsonpath(expr).find(response_data)
        if not matches:
            continue
        value = matches[0].value
        extracted[key] = str(value) if not isinstance(value, str) else value
    variables.update(extracted)
    return extracted


def _run_assertions(assertions: list[dict] | None, response_data: Any, status_code: int, elapsed_ms: int) -> list[dict]:
    results = []
    for assertion in assertions or []:
        if not isinstance(assertion, dict):
            continue
        kind = assertion.get("type")
        ok = True
        actual = None
        expected = None
        try:
            if kind == "status_code":
                expected = int(assertion.get("value", 200))
                actual = status_code
                ok = actual == expected
            elif kind == "status_code_range":
                lo = int(assertion.get("min", 200))
                hi = int(assertion.get("max", 299))
                expected = f"{lo}-{hi}"
                actual = status_code
                ok = lo <= status_code <= hi
            elif kind == "response_time_lt":
                expected = int(assertion.get("value", 3000))
                actual = elapsed_ms
                ok = elapsed_ms < expected
            elif kind == "jsonpath_exists":
                expected = assertion.get("jsonpath")
                actual = len(parse_jsonpath(expected).find(response_data)) if expected else 0
                ok = actual > 0
            elif kind == "jsonpath_equals":
                expr = assertion.get("jsonpath")
                expected = str(assertion.get("value", ""))
                matches = parse_jsonpath(expr).find(response_data) if expr else []
                actual = str(matches[0].value) if matches else None
                ok = actual == expected
            else:
                continue
        except Exception as exc:
            ok = False
            actual = str(exc)
        results.append({"type": kind, "passed": ok, "expected": expected, "actual": actual})
    return results


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
    variables = build_param_map(db, overrides.get("environment_id"), overrides.get("variables") or [])
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


def build_body_from_schema(schema: dict) -> str:
    if not schema:
        raise ValueError("当前接口没有可用的 request schema")
    example = _schema_example({}, schema)
    return json.dumps(example if example is not None else {}, ensure_ascii=False, indent=2)


def _global_variable_map(db: Session, environment_id: int | None = None) -> dict[str, str]:
    env = db.get(GlobalParameter, environment_id) if environment_id else None
    if env is None:
        env = db.exec(select(GlobalParameter).where(GlobalParameter.is_default == True)).first()
    if env is None:
        env = db.exec(select(GlobalParameter)).first()
    if env is None:
        return {}
    result: dict[str, str] = {}
    for item in env.parameters or []:
        if isinstance(item, dict) and item.get("key"):
            result[str(item["key"])] = str(item.get("value", ""))
    return result


def _normalize_body_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _global_variable_key_map(variables: dict[str, str]) -> dict[str, str]:
    key_map: dict[str, str] = {}
    for key in variables:
        normalized = _normalize_body_key(key)
        if normalized and normalized not in key_map:
            key_map[normalized] = key
    return key_map


def _replace_body_keys_with_global_tokens(value: Any, variables: dict[str, str], variable_keys: dict[str, str]) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            variable_key = str(key) if str(key) in variables else variable_keys.get(_normalize_body_key(key))
            if variable_key:
                result[key] = "{{" + variable_key + "}}"
            else:
                result[key] = _replace_body_keys_with_global_tokens(item, variables, variable_keys)
        return result
    if isinstance(value, list):
        return [_replace_body_keys_with_global_tokens(item, variables, variable_keys) for item in value]
    return value


def _apply_global_tokens_to_body(body_text: str, variables: dict[str, str]) -> str:
    if not variables:
        return body_text
    try:
        body_obj = json.loads(body_text)
    except Exception:
        return body_text
    body_obj = _replace_body_keys_with_global_tokens(body_obj, variables, _global_variable_key_map(variables))
    return json.dumps(body_obj, ensure_ascii=False, indent=2)


def _extract_json_response(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("AI 未返回内容")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
        if match:
            return json.loads(match.group(1).strip())
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", stripped)
        if match:
            return json.loads(match.group(1))
        raise


async def generate_body_from_schema(
    *,
    db: Session | None = None,
    schema: dict,
    current_body: str = "",
    instruction: str = "",
    environment_id: int | None = None,
    model_type: str = "api",
    api_key: str = "",
    api_base_url: str = "",
    api_proxy_url: str = "",
    ollama_url: str = "",
    ollama_model: str = "",
) -> dict:
    rule_body = build_body_from_schema(schema)
    variables = _global_variable_map(db, environment_id) if db is not None else {}
    rule_body = _apply_global_tokens_to_body(rule_body, variables)
    api_key = api_key.strip() if api_key else ""
    api_base_url = api_base_url.strip() if api_base_url else ""
    api_proxy_url = api_proxy_url.strip() if api_proxy_url else ""
    ollama_url = ollama_url.strip() if ollama_url else ""
    ollama_model = ollama_model.strip() if ollama_model else ""

    if model_type == "api" and not api_key:
        return {"body": rule_body, "used_ai": False, "message": "未配置 API Key，已使用 schema 规则生成"}
    if model_type == "ollama" and (not ollama_url or not ollama_model):
        return {"body": rule_body, "used_ai": False, "message": "Ollama 配置不完整，已使用 schema 规则生成"}

    prompt = (
        "你是接口测试数据生成助手。请根据 JSON Schema 生成一个真实、完整、可直接作为请求体使用的 JSON。\n"
        "约束：只返回 JSON，不要 Markdown；不要生成 schema 中不存在的字段；必填字段必须包含；"
        "可选字段如有业务意义可以包含；字段类型必须符合 schema。\n\n"
        f"JSON Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"规则生成示例:\n{rule_body}\n\n"
        f"当前请求体（可参考，可忽略）:\n{current_body or '(空)'}\n\n"
        f"用户补充要求:\n{instruction or '(无)'}"
    )

    try:
        if model_type == "ollama":
            from langchain_ollama import ChatOllama

            model = ChatOllama(base_url=ollama_url, model=ollama_model, temperature=0)
        else:
            from langchain_deepseek import ChatDeepSeek
            from pydantic import SecretStr

            model = ChatDeepSeek(
                model="deepseek-chat",
                temperature=0,
                api_key=SecretStr(api_key),
                base_url=api_base_url or None,
                max_retries=2,
            )
        response = await model.ainvoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        body_obj = _extract_json_response(str(content))
        body = json.dumps(body_obj, ensure_ascii=False, indent=2)
        body = _apply_global_tokens_to_body(body, variables)
        return {"body": body, "used_ai": True, "message": "AI generated successfully"}
        return {"body": json.dumps(body_obj, ensure_ascii=False, indent=2), "used_ai": True, "message": "AI 生成成功"}
    except Exception as exc:
        return {"body": rule_body, "used_ai": False, "message": f"AI 生成失败，已使用 schema 规则生成: {exc}"}


def _endpoint_body_example(endpoint: ApiEndpoint) -> Any:
    schema_example = _schema_example({}, endpoint.request_schema) if endpoint.request_schema else None
    if endpoint.body and str(endpoint.body).strip():
        try:
            parsed = json.loads(endpoint.body)
            if parsed not in ({}, [], None, ""):
                return _merge_body_with_schema_example(parsed, schema_example)
        except Exception:
            return schema_example if schema_example is not None else endpoint.body
    return schema_example


def _merge_body_with_schema_example(current: Any, schema_example: Any) -> Any:
    if schema_example is None:
        return current
    if isinstance(current, dict) and isinstance(schema_example, dict):
        result = deepcopy(current)
        for key, value in schema_example.items():
            if key in result:
                result[key] = _merge_body_with_schema_example(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result
    if isinstance(current, list) and isinstance(schema_example, list):
        if not current:
            return deepcopy(schema_example)
        if schema_example:
            result = deepcopy(current)
            result[0] = _merge_body_with_schema_example(result[0], schema_example[0])
            return result
        return current
    return current


def _body_to_text(body: Any) -> str | None:
    if body is None:
        return None
    if isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False, indent=2)


def _success_assertions() -> list[dict]:
    return deepcopy(DEFAULT_SUCCESS_ASSERTIONS)


def _client_error_assertions(code: int | None = None) -> list[dict]:
    assertions = []
    if code is not None:
        assertions.append({"type": "jsonpath_equals", "jsonpath": "$.code", "value": code})
    return assertions


def _schema_invalid_assertions(reason: str) -> list[dict]:
    return _client_error_assertions(51005 if reason == "类型错误" else None)


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


def _schema_required(schema: dict | None) -> list[str]:
    if not isinstance(schema, dict):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in schema.get("allOf") or []:
        for field in _schema_required(item if isinstance(item, dict) else None):
            if field not in seen:
                result.append(field)
                seen.add(field)
    required = schema.get("required")
    if isinstance(required, list):
        for item in required:
            field = str(item)
            if field not in seen:
                result.append(field)
                seen.add(field)
    return result


def _schema_properties(schema: dict | None) -> dict:
    if not isinstance(schema, dict):
        return {}
    result: dict[str, Any] = {}
    for item in schema.get("allOf") or []:
        result.update(_schema_properties(item if isinstance(item, dict) else None))
    props = schema.get("properties")
    if isinstance(props, dict):
        result.update(props)
    if not result:
        for composite_key in ("oneOf", "anyOf"):
            choices = schema.get(composite_key)
            if isinstance(choices, list) and choices:
                for item in choices:
                    if isinstance(item, dict):
                        result.update(_schema_properties(item))
                        if result:
                            return result
    return result


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
        environment_id=endpoint.environment_id,
        variables=[],
        steps=generate_unit_test_steps(endpoint),
        user_id=user_id,
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario


async def run_scenario(db: Session, scenario: ApiScenario, project: ApiProject) -> dict:
    variables = build_param_map(db, scenario.environment_id, scenario.variables or [])
    base_url = (scenario.base_url or project.base_url or "").rstrip("/")
    results = []
    passed = True

    async with httpx.AsyncClient(limits=API_TEST_HTTP_LIMITS) as client:
        for index, step in enumerate(scenario.steps or [], 1):
            if not isinstance(step, dict) or step.get("enabled", True) is False:
                continue
            endpoint_id = step.get("endpoint_id")
            endpoint = db.get(ApiEndpoint, endpoint_id) if endpoint_id else None
            if not endpoint:
                results.append({"index": index, "status": "error", "detail": "接口步骤不存在"})
                passed = False
                if not step.get("continue_on_failure"):
                    break
                continue

            result, step_passed = await _execute_endpoint_step(
                client,
                project=project,
                endpoint=endpoint,
                step=step,
                variables=variables,
                default_base_url=base_url,
                index=index,
            )
            results.append(result)
            passed = passed and step_passed
            if not step_passed and not step.get("continue_on_failure"):
                break

    return {"passed": passed, "variables": variables, "steps": results}
