"""Multi-step scenario execution and cross-step dependency inference.

Extracted verbatim from api_test_tool.py (Phase 2 split). Top of the DAG.
"""
import json
import re
from copy import deepcopy
from typing import Any

import httpx
from jsonpath_ng import parse as parse_jsonpath
from sqlmodel import Session

from db.models import ApiEndpoint, ApiProject, ApiScenario
from app.routes.proxy import build_param_map
from app.services.schema_utils import _schema_example, _schema_properties
from app.services.step_runner import API_TEST_HTTP_LIMITS, _execute_endpoint_step


GENERIC_DEPENDENCY_FIELD_NAMES = {
    "code",
    "msg",
    "message",
    "success",
    "data",
    "result",
    "results",
    "list",
    "items",
    "rows",
    "total",
    "page",
    "size",
    "status",
    "timestamp",
}

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

def _dependency_name_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

def _dependency_aliases(name: str) -> set[str]:
    key = _dependency_name_key(name)
    aliases = {key} if key else set()
    if key.endswith("id") and len(key) > 2:
        aliases.add(key[:-2])
    else:
        aliases.add(key + "id")
    return {item for item in aliases if item}

def _dependency_variable_name(name: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "")).strip("_")
    if not raw:
        return "value"
    if raw[0].isdigit():
        raw = "v_" + raw
    return raw[0].lower() + raw[1:]

def _is_dependency_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_RE.fullmatch(value.strip()))

def _is_dependency_fillable(value: Any) -> bool:
    if _is_dependency_placeholder(value):
        return False
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in {"", "string", "null", "undefined", "0"}
    if isinstance(value, (int, float)) and value == 0:
        return True
    return False

def _iter_response_schema_fields(schema: Any, path: str = "$") -> list[dict]:
    if not isinstance(schema, dict):
        return []
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        return _iter_response_schema_fields(schema["items"], path + "[0]")
    props = _schema_properties(schema)
    if not props:
        # 兼容：response_schema 为空或仅为 {"type":"string"} 时，
        # 提供默认提取候选（$.data / $.data.id），使新增接口的依赖推断能正常工作
        if not schema.get("type") or schema.get("type") == "string":
            return list(_DEFAULT_RESPONSE_FIELDS)
        return []
    fields: list[dict] = []
    for key, prop in props.items():
        child_path = f"{path}.{key}"
        child_fields = _iter_response_schema_fields(prop, child_path)
        if child_fields:
            fields.extend(child_fields)
        elif _dependency_name_key(key) not in GENERIC_DEPENDENCY_FIELD_NAMES:
            fields.append({
                "name": key,
                "variable": _dependency_variable_name(key),
                "jsonpath": child_path,
                "aliases": _dependency_aliases(key),
            })
    return fields

def _body_from_step_for_inference(step: dict) -> Any:
    body = step.get("body")
    if not body:
        return _schema_example({}, step.get("request_schema") or {})
    try:
        return json.loads(body) if isinstance(body, str) else deepcopy(body)
    except Exception:
        return _schema_example({}, step.get("request_schema") or {})

def _infer_body_dependencies(value: Any, candidates: dict[str, list[dict]], summary: dict) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            matched = None
            if _is_dependency_fillable(item):
                for alias in _dependency_aliases(key):
                    matches = candidates.get(alias) or []
                    if matches:
                        matched = matches[-1]
                        break
            if matched:
                result[key] = "{{" + matched["variable"] + "}}"
                matched["used"] = True
                summary["replaced_fields"] += 1
            else:
                result[key] = _infer_body_dependencies(item, candidates, summary)
        return result
    if isinstance(value, list):
        return [_infer_body_dependencies(item, candidates, summary) for item in value]
    return value

def _infer_pair_dependencies(items: list[dict] | None, candidates: dict[str, list[dict]], summary: dict) -> list[dict]:
    result = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        row = deepcopy(item)
        key = row.get("key")
        if key and _is_dependency_fillable(row.get("value")):
            for alias in _dependency_aliases(key):
                matches = candidates.get(alias) or []
                if matches:
                    matched = matches[-1]
                    row["value"] = "{{" + matched["variable"] + "}}"
                    matched["used"] = True
                    summary["replaced_fields"] += 1
                    break
        result.append(row)
    return result

def _has_post_action(actions: list[dict] | None, variable: str, jsonpath: str | None) -> bool:
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        key = action.get("key") or action.get("variable")
        if key == variable:
            return True
        if jsonpath is not None and action.get("jsonpath") == jsonpath:
            return True
    return False

def infer_step_dependencies(steps: list[dict]) -> dict:
    candidates: dict[str, list[dict]] = {}
    candidate_order: list[dict] = []
    summary = {
        "steps": len(steps),
        "added_post_actions": 0,
        "replaced_fields": 0,
        "skipped_existing": 0,
    }

    for index, step in enumerate(steps):
        body = _body_from_step_for_inference(step)
        inferred_body = _infer_body_dependencies(body, candidates, summary)
        if inferred_body != body:
            step["body"] = json.dumps(inferred_body, ensure_ascii=False, indent=2)

        headers = _infer_pair_dependencies(step.get("headers"), candidates, summary)
        if headers != (step.get("headers") or []):
            step["headers"] = headers
        parameters = _infer_pair_dependencies(step.get("parameters"), candidates, summary)
        if parameters != (step.get("parameters") or []):
            step["parameters"] = parameters

        used_before = {id(item) for item in candidate_order if item.get("used")}
        for candidate in candidate_order:
            if id(candidate) not in used_before:
                continue
            producer = steps[candidate["producer_index"]]
            actions = list(producer.get("post_actions") or [])
            if _has_post_action(actions, candidate["variable"], candidate["jsonpath"]):
                summary["skipped_existing"] += 1
                candidate["used"] = False
                continue
            actions.append({
                "type": "extract_jsonpath",
                "key": candidate["variable"],
                "jsonpath": candidate["jsonpath"],
            })
            producer["post_actions"] = actions
            summary["added_post_actions"] += 1
            candidate["used"] = False

        for field in _iter_response_schema_fields(step.get("response_schema") or {}):
            candidate = {
                **field,
                "producer_index": index,
                "used": False,
            }
            candidate_order.append(candidate)
            for alias in field["aliases"]:
                candidates.setdefault(alias, []).append(candidate)

    # --- 第二遍：处理已含 {{placeholder}} 但无对应提取的变量 ---
    # 当 AI 生成测试用例时带了 {{contract_id}} 等变量名，但前置创建接口
    # 没有 response_schema 时，依赖推断无法自动建立提取链。
    # 此处主动扫描步骤中的 {{var}} 引用，回溯到前序 POST 创建接口补全 post_actions。
    _PLACEHOLDER_IN_BODY_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
    for index, step in enumerate(steps):
        if index == 0:
            continue

        # 收集本步骤中所有 {{var}} 引用
        vars_needed: set[str] = set()
        body_text = step.get("body")
        if isinstance(body_text, str):
            vars_needed.update(_PLACEHOLDER_IN_BODY_RE.findall(body_text))
        for pair_list in [step.get("parameters", []), step.get("headers", [])]:
            if isinstance(pair_list, list):
                for pair in pair_list:
                    if isinstance(pair, dict):
                        vars_needed.update(_PLACEHOLDER_IN_BODY_RE.findall(str(pair.get("value", ""))))

        if not vars_needed:
            continue

        for var_name in vars_needed:
            # 检查是否已有候选（已有提取来源）
            has_candidate = any(
                c.get("variable") == var_name and c["producer_index"] < index
                for c in candidate_order
            )
            if has_candidate:
                continue

            # 检查是否已有前置步骤提供了该变量的提取
            already_extracted = any(
                _has_post_action(
                    steps[i].get("post_actions"),
                    var_name,
                    None,  # 只按 key 匹配，不检查 jsonpath
                )
                for i in range(index)
            )
            if already_extracted:
                continue

            # 无候选 → 回溯前序 POST 创建接口，自动补全提取
            for prev_idx in range(index - 1, -1, -1):
                prev_step = steps[prev_idx]
                method = (prev_step.get("method") or "").upper()
                path = prev_step.get("path") or ""
                if method != "POST" or "/{" in path or "/:" in path:
                    continue

                actions = list(prev_step.get("post_actions") or [])
                # 尝试多种 jsonpath 猜测
                jsonpath_candidates = [
                    f"$.data.{var_name}",
                    f"$.{var_name}",
                    "$.data",
                ]
                already_has = any(
                    _has_post_action(actions, var_name, jp)
                    for jp in jsonpath_candidates
                )
                if already_has:
                    break

                jsonpath = jsonpath_candidates[0]
                actions.append({
                    "type": "extract_jsonpath",
                    "key": var_name,
                    "jsonpath": jsonpath,
                })
                prev_step["post_actions"] = actions
                summary["added_post_actions"] += 1
                break

    return summary

async def run_scenario(db: Session, scenario: ApiScenario, project: ApiProject) -> dict:
    env_id = scenario.environment_id or project.environment_id
    variables = build_param_map(db, env_id, scenario.variables or [])
    base_url = (project.base_url or "").rstrip("/")
    results = []
    passed = True

    # 收集需要从响应中提取的变量（值以 $. 开头的视为 jsonpath 表达式）
    pending_extractions: dict[str, str] = {}  # variable_name -> jsonpath_expression
    for var in (scenario.variables or []):
        if isinstance(var, dict) and var.get("key") and var.get("value", "").startswith("$."):
            pending_extractions[var["key"]] = var["value"]
            # 临时移除，等提取后再赋值
            variables.pop(var["key"], None)

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

            # 从当前步骤的响应中提取待提取的变量
            if step_passed and pending_extractions:
                response_data = result.get("response", {}).get("body")
                if isinstance(response_data, (dict, list)):
                    extracted_keys = []
                    for var_name, jsonpath_expr in pending_extractions.items():
                        try:
                            matches = parse_jsonpath(jsonpath_expr).find(response_data)
                            if matches:
                                value = matches[0].value
                                variables[var_name] = str(value) if not isinstance(value, str) else value
                                extracted_keys.append(var_name)
                        except Exception:
                            pass
                    # 移除已提取的变量
                    for key in extracted_keys:
                        del pending_extractions[key]

            if not step_passed and not step.get("continue_on_failure"):
                break

    return {"passed": passed, "variables": variables, "steps": results}

_DEFAULT_RESPONSE_FIELDS = [
    {
        "name": "data",
        "variable": "data",
        "jsonpath": "$.data",
        "aliases": _dependency_aliases("data"),
    },
    {
        "name": "id",
        "variable": "id",
        "jsonpath": "$.data.id",
        "aliases": _dependency_aliases("id"),
    },
]
