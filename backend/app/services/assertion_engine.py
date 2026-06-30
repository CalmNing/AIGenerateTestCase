"""Assertion execution and pre/post action helpers.

Extracted verbatim from api_test_tool.py (Phase 2 split). Leaf-dependency
module: depends only on jsonpath + proxy variable substitution.
"""
from copy import deepcopy
from typing import Any

from jsonpath_ng import parse as parse_jsonpath

from app.routes.proxy import substitute_variables


DEFAULT_SUCCESS_ASSERTIONS = [{"type": "jsonpath_equals", "value": 200, "jsonpath": "$.code"}]


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

def _run_assertions(assertions: list[dict] | None, response_data: Any, status_code: int, elapsed_ms: int, variables: dict | None = None) -> list[dict]:
    results = []
    for assertion in assertions or []:
        if not isinstance(assertion, dict):
            continue
        kind = assertion.get("type")
        ok = True
        actual = None
        expected = None
        try:
            # 对断言的 value 字段进行变量替换
            raw_value = assertion.get("value")
            if isinstance(raw_value, str) and "{{" in raw_value and variables:
                unresolved: set[str] = set()
                expected_str = substitute_variables(raw_value, variables, unresolved)
                # 尝试转为数字（如果原始值是数字）
                try:
                    expected = int(expected_str)
                except (ValueError, TypeError):
                    try:
                        expected = float(expected_str)
                    except (ValueError, TypeError):
                        expected = expected_str
            else:
                expected = raw_value

            if kind == "status_code":
                expected = int(expected if expected is not None else 200)
                actual = status_code
                ok = actual == expected
            elif kind == "status_code_range":
                lo = int(assertion.get("min", 200))
                hi = int(assertion.get("max", 299))
                expected = f"{lo}-{hi}"
                actual = status_code
                ok = lo <= status_code <= hi
            elif kind == "response_time_lt":
                expected = int(expected if expected is not None else 3000)
                actual = elapsed_ms
                ok = elapsed_ms < expected
            elif kind == "jsonpath_exists":
                expected = assertion.get("jsonpath")
                actual = len(parse_jsonpath(expected).find(response_data)) if expected else 0
                ok = actual > 0
            elif kind == "jsonpath_equals":
                expr = assertion.get("jsonpath")
                expected = str(expected) if expected is not None else ""
                matches = parse_jsonpath(expr).find(response_data) if expr else []
                actual = str(matches[0].value) if matches else None
                ok = actual == expected
            elif kind == "jsonpath_not_equals":
                expr = assertion.get("jsonpath")
                expected = str(expected) if expected is not None else ""
                matches = parse_jsonpath(expr).find(response_data) if expr else []
                if not matches:
                    actual = "字段不存在"
                    ok = True  # 字段不存在视为"不等于"
                else:
                    actual = str(matches[0].value)
                    ok = actual != expected
            else:
                continue
        except Exception as exc:
            ok = False
            actual = str(exc)
        results.append({"type": kind, "passed": ok, "expected": expected, "actual": actual})
    return results

def _success_assertions() -> list[dict]:
    return deepcopy(DEFAULT_SUCCESS_ASSERTIONS)

def _client_error_assertions(code: int | None = None) -> list[dict]:
    assertions = []
    if code is not None:
        assertions.append({"type": "jsonpath_equals", "jsonpath": "$.code", "value": code})
    return assertions

def _schema_invalid_assertions(reason: str) -> list[dict]:
    return _client_error_assertions(51005 if reason == "类型错误" else None)
