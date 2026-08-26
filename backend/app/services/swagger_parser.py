"""Swagger/OpenAPI parsing, endpoint sync, and body generation.

Extracted verbatim from api_test_tool.py (Phase 2 split).
"""
import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import httpx
import yaml
from sqlmodel import Session, select

from db.models import ApiEndpoint, ApiProject, ApiScenario, GlobalParameter
from app.services.assertion_engine import DEFAULT_SUCCESS_ASSERTIONS
from app.services.schema_utils import (
    _json_equal, _json_fingerprint, _load_json_text, _resolve_ref,
    _resolve_schema_deep, _schema_example,
)


HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
REMOVED_FROM_SPEC_TAG = "__removed_from_spec__"

_SWAGGER_CONFIG_PATH_SUFFIXES = (
    "/v3/api-docs/swagger-config",
    "/v3/api-docs/",
    "/v3/api-docs",
    "/swagger-config",
)


def parse_spec_text(text: str) -> dict:
    spec = yaml.safe_load(text)
    if not isinstance(spec, dict):
        raise ValueError("Swagger/OpenAPI 文档格式不正确")
    if "openapi" not in spec and "swagger" not in spec:
        raise ValueError("未识别到 OpenAPI 或 Swagger 标识")
    return spec


def swagger_config_docs(text: str) -> list[dict]:
    """识别 springdoc swagger-config 响应，返回其中的文档列表；非 config 返回空列表。"""
    try:
        data = json.loads(text)
    except Exception:
        return []
    urls = data.get("urls") if isinstance(data, dict) else None
    if not isinstance(urls, list):
        return []
    docs: list[dict] = []
    for item in urls:
        if isinstance(item, dict) and str(item.get("url") or "").strip():
            docs.append({
                "name": str(item.get("name") or "").strip(),
                "url": str(item["url"]).strip(),
            })
    return docs


def merge_specs(specs: list[dict]) -> dict:
    """合并多个 OpenAPI 文档（如 springdoc 分组文档），路径/方法重复时保留先出现的。"""
    if not specs:
        raise ValueError("没有可合并的接口文档")
    merged = deepcopy(specs[0])
    merged["paths"] = dict(merged.get("paths") or {})

    for spec in specs[1:]:
        for path, path_item in (spec.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            target = merged["paths"].get(path)
            if not isinstance(target, dict):
                merged["paths"][path] = deepcopy(path_item)
                continue
            for method, operation in path_item.items():
                target.setdefault(method, deepcopy(operation))

        existing_tags = {
            tag.get("name"): tag
            for tag in merged.get("tags") or []
            if isinstance(tag, dict) and tag.get("name")
        }
        for tag in spec.get("tags") or []:
            name = tag.get("name") if isinstance(tag, dict) else None
            if name and name not in existing_tags:
                merged.setdefault("tags", []).append(deepcopy(tag))
                existing_tags[name] = tag

        for section_key in ("components", "definitions"):
            incoming_section = spec.get(section_key)
            if not isinstance(incoming_section, dict):
                continue
            section = merged.setdefault(section_key, {})
            for subsection_key, subsection in incoming_section.items():
                if isinstance(subsection, dict) and isinstance(section.get(subsection_key), dict):
                    for name, value in subsection.items():
                        section[subsection_key].setdefault(name, deepcopy(value))
                elif subsection_key not in section:
                    section[subsection_key] = deepcopy(subsection)

        existing_servers = {
            server.get("url") for server in merged.get("servers") or [] if isinstance(server, dict)
        }
        for server in spec.get("servers") or []:
            url = server.get("url") if isinstance(server, dict) else None
            if url and url not in existing_servers:
                merged.setdefault("servers", []).append(deepcopy(server))
                existing_servers.add(url)

    return merged


async def load_openapi_from_url(client: httpx.AsyncClient, url: str) -> tuple[str, list[dict]]:
    """拉取 OpenAPI URL；若为 swagger-config，则抓取其下全部分组文档并合并。

    返回 (合并后的 spec 文本, swagger-config 文档列表)；普通 URL 时文档列表为空。
    """
    resp = await client.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    text = resp.text

    docs = swagger_config_docs(text)
    if not docs:
        return text, []

    specs: list[dict] = []
    for doc in docs:
        doc_resp = await client.get(urljoin(url, doc["url"]), timeout=30.0, follow_redirects=True)
        doc_resp.raise_for_status()
        specs.append(parse_spec_text(doc_resp.text))

    merged = merge_specs(specs)
    return json.dumps(merged, ensure_ascii=False), docs


def suggest_project_name(url: str, from_config: bool) -> str:
    """根据 URL 推断项目名：swagger-config 地址剥掉已知后缀后取最后一段。"""
    parts = urlsplit(url)
    path = parts.path
    if from_config:
        for suffix in _SWAGGER_CONFIG_PATH_SUFFIXES:
            if path.rstrip("/").endswith(suffix):
                path = path.rstrip("/")[: -len(suffix)]
                break
    segment = unquote(path.rstrip("/").rsplit("/", 1)[-1]) if path.strip("/") else ""
    return segment or (parts.hostname or "导入接口项目")

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
    removed = 0
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

    # 文档中已删除的接口：直接从数据库移除（同时清理关联的场景步骤引用）
    for current in existing_endpoints:
        if _endpoint_key(current) in incoming_keys:
            continue
        session_scenarios = db.exec(
            select(ApiScenario).where(ApiScenario.project_id == project.id)
        ).all()
        for scenario in session_scenarios:
            steps = [s for s in (scenario.steps or []) if s.get("endpoint_id") != current.id]
            if len(steps) != len(scenario.steps or []):
                scenario.steps = steps
                db.add(scenario)
        db.delete(current)
        removed += 1

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
        "removed": removed,
    }

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
    api_model: str = "deepseek-v4-flash",
    ollama_url: str = "",
    ollama_model: str = "",
) -> dict:
    rule_body = build_body_from_schema(schema)
    variables = _global_variable_map(db, environment_id) if db is not None else {}
    rule_body = _apply_global_tokens_to_body(rule_body, variables)
    api_key = api_key.strip() if api_key else ""
    api_base_url = api_base_url.strip() if api_base_url else ""
    api_proxy_url = api_proxy_url.strip() if api_proxy_url else ""
    api_model = api_model.strip() if api_model else "deepseek-v4-flash"
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
            from langchain_openai import ChatOpenAI
            from pydantic import SecretStr

            extra_kwargs = {}
            if api_proxy_url:
                import httpx
                extra_kwargs["http_async_client"] = httpx.AsyncClient(
                    proxy=api_proxy_url,
                    timeout=httpx.Timeout(None, connect=30.0, read=None, write=None, pool=None),
                )
            model = ChatOpenAI(
                model=api_model,
                temperature=0,
                api_key=SecretStr(api_key),
                base_url=api_base_url or "https://api.deepseek.com",
                max_retries=2,
                **extra_kwargs,
            )
        response = await model.ainvoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        body_obj = _extract_json_response(str(content))
        body = json.dumps(body_obj, ensure_ascii=False, indent=2)
        body = _apply_global_tokens_to_body(body, variables)
        return {"body": body, "used_ai": True, "message": "AI generated successfully"}
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
