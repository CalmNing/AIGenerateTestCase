"""必填字段确定性补全（B6 基线发现的头号质量问题）。

背景
----
用质量基线（`scripts/quality_baseline.py`）在 1574 条存量用例上实测：
- 330 个步骤带 `request_schema.required`；
- 其中 **442 处必填字段缺失**（`cmpId` 214 次、`id` 47 次、`amount` 30 次…）；
- 直接后果：接口用例与 Schema 的一致性只有 **76.2%**，运行时必然请求失败。

根因：`api_context` 里已经注入了"[请求骨架-硬约束] + 必填字段列表"，
但 LLM 并不保证遵守——**这类约束更适合用确定性代码兜底，而不是靠提示词**。

本模块提供
----------
1. `fill_missing_required(schema, body)`：按 `schema.required` 补齐缺失字段，
   值由 `swagger_parser._schema_example` 从 Schema 派生（复用既有实现，
   保证生成的示例是**类型合法、枚举合法、格式合法**的）；
2. `fill_step_body(step, schema)`：作用于用例步骤（`body` 可能是 JSON 字符串）；
3. `fill_testcase_required_fields(db, testcase, ...)`：数据库感知的批量补全；
4. `dependency_candidates`：把补出来、且形如 `xxxId` 的字段标注为
   **依赖候选**——它们理想的值应来自上游接口响应（`{{var}}`），
   而不是伪造值。这里先保证结构合法，依赖推断仍由既有机制负责。

设计原则
--------
- **幂等**：已有字段绝不覆盖（只补缺失）；
- **可解释**：返回补了哪些字段，调用方可记录/展示；
- **不抛异常**：任何失败都返回原值 + 空列表。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 形如 xxxId / xxx_id / id 的字段，理想值来自上游接口
_ID_LIKE_RE = re.compile(r"(?:^id$|id$|^.*_id$)", re.IGNORECASE)

# _schema_example 返回 None 时的类型兜底
_TYPE_DEFAULTS: dict[str, Any] = {
    "string": "",
    "integer": 0,
    "number": 0,
    "boolean": False,
    "array": [],
    "object": {},
}


def is_id_like(field_name: str) -> bool:
    """字段名是否形如"引用某实体的 ID"（其值通常来自上游接口）。"""
    return bool(_ID_LIKE_RE.search(str(field_name or "")))


def _derive_value(prop_schema: Any) -> Any:
    """从字段 Schema 派生一个类型合法的占位值。

    优先复用 `swagger_parser._schema_example`（它已处理 example/default/enum/
    format/min/max 等），失败或返回 None 时用类型默认值兜底。
    """
    if not isinstance(prop_schema, dict):
        return None

    try:
        from app.services.swagger_parser import _schema_example

        value = _schema_example({}, prop_schema)
        if value is not None:
            return value
    except Exception:  # noqa: BLE001 - 派生失败走兜底
        logger.debug("schema 示例派生失败，使用类型默认值", exc_info=True)

    schema_type = prop_schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((t for t in schema_type if t != "null"), None)
    if schema_type in _TYPE_DEFAULTS:
        return _TYPE_DEFAULTS[schema_type]

    # 有 properties 但没写 type → 当对象处理
    if "properties" in prop_schema:
        return {}
    return ""


def fill_missing_required(
    schema: Any,
    body: Any,
    *,
    depth: int = 0,
    max_depth: int = 4,
) -> tuple[Any, list[str]]:
    """为 body 补齐 `schema.required` 中缺失的字段（递归，幂等）。

    返回 `(新的 body, 本次补齐的字段路径列表)`。
    只补缺失，**绝不覆盖已有值**。

    例：
        schema = {"required": ["cmpId", "amount"],
                  "properties": {"cmpId": {"type": "string"},
                                 "amount": {"type": "number"}}}
        body   = {"name": "合同A"}
        -> ({"name": "合同A", "cmpId": "", "amount": 0}, ["cmpId", "amount"])
    """
    if not isinstance(schema, dict) or depth > max_depth:
        return body, []

    filled: list[str] = []

    if isinstance(body, dict):
        props = schema.get("properties")
        props = props if isinstance(props, dict) else {}
        required = schema.get("required")
        required = required if isinstance(required, list) else []

        for name in required:
            if not isinstance(name, str) or not name:
                continue
            if name in body:
                continue
            prop_schema = props.get(name)
            if prop_schema is None:
                # schema 未定义该字段的类型：按"未知"处理，给空字符串
                # （宁可结构齐全后由一致性检查继续提示，也不要漏字段）
                value = ""
            else:
                value = _derive_value(prop_schema)
            body[name] = value
            filled.append(name)

        # 递归：对已是对象的字段继续补它自己的 required；
        # 数组字段则按 items 的 schema 补每个元素（此前只处理 dict，数组元素被漏掉）
        for key, value in list(body.items()):
            prop_schema = props.get(key)
            if not isinstance(prop_schema, dict):
                continue
            if isinstance(value, dict):
                new_value, sub_filled = fill_missing_required(
                    prop_schema, value, depth=depth + 1, max_depth=max_depth,
                )
                if sub_filled:
                    body[key] = new_value
                    filled.extend(f"{key}.{sub}" for sub in sub_filled)
            elif isinstance(value, list):
                new_value, sub_filled = fill_missing_required(
                    prop_schema, value, depth=depth + 1, max_depth=max_depth,
                )
                if sub_filled:
                    body[key] = new_value
                    filled.extend(f"{key}{sub}" for sub in sub_filled)

    elif isinstance(body, list):
        # 数组：按 items 的 schema 逐个补
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for i, item in enumerate(body):
                new_item, sub_filled = fill_missing_required(
                    items_schema, item, depth=depth + 1, max_depth=max_depth,
                )
                if sub_filled:
                    body[i] = new_item
                    filled.extend(f"[{i}].{sub}" for sub in sub_filled)

    return body, filled


def _load_body(raw: Any) -> tuple[Any, bool]:
    """把步骤的 body 解析成对象，返回 (body, 是否为字符串)。"""
    if isinstance(raw, (dict, list)):
        return raw, False
    if not isinstance(raw, str):
        return None, False
    text = raw.strip()
    if not text:
        return None, False
    try:
        return json.loads(text), True
    except Exception:  # noqa: BLE001
        return None, True


def fill_step_body(step: Any, schema: Any) -> tuple[Any, list[str]]:
    """补全单个步骤 body 里的必填字段。

    返回 `(新的 step, 补齐的字段列表)`；不改动原对象（浅拷贝后返回新 dict）。
    """
    if not isinstance(step, dict) or not isinstance(schema, dict):
        return step, []

    body, was_string = _load_body(step.get("body"))
    if body is None:
        # 没有 body 但 schema 有 required → 从零构造一个合法 body
        required = schema.get("required")
        if not isinstance(required, list) or not required:
            return step, []
        body = {}
    elif not isinstance(body, (dict, list)):
        return step, []

    new_body, filled = fill_missing_required(schema, body)
    if not filled:
        return step, []

    new_step = dict(step)
    original = step.get("body")
    if isinstance(original, str) or original is None:
        # 与存量数据格式保持一致：body 统一存 JSON 字符串
        # （原有就是字符串，或原本缺失、本次新建）
        new_step["body"] = json.dumps(new_body, ensure_ascii=False, indent=2)
    else:
        new_step["body"] = new_body
    return new_step, filled


def dependency_candidates(filled_fields: list[str]) -> list[str]:
    """从补齐的字段里挑出"本应是上游引用"的候选（形如 xxxId）。

    这些字段当前被填了占位值（结构合法），但更理想的值来自上游接口响应。
    返回给调用方用于提示/后续依赖推断。
    """
    out = []
    for path in filled_fields or []:
        leaf = str(path).split(".")[-1].split("]")[-1].lstrip(".")
        if is_id_like(leaf):
            out.append(path)
    return out


def fill_testcase_steps(
    steps: Any,
    endpoint_schema_by_id: dict,
    *,
    endpoint_id_by_ordinal: Optional[dict] = None,
) -> tuple[Any, list[str]]:
    """补全一条用例里所有接口步骤的必填字段。

    参数：
        steps: 用例的 steps（list）
        endpoint_schema_by_id: {endpoint_id: request_schema}
        endpoint_id_by_ordinal: {序号(1-based): endpoint_id}，用于解析 endpoint_ref

    返回 `(新 steps, 所有补齐的字段路径)`。
    """
    if not isinstance(steps, list):
        return steps, []

    ordinal_map = endpoint_id_by_ordinal or {}
    new_steps = []
    all_filled: list[str] = []

    for step in steps:
        if not isinstance(step, dict):
            new_steps.append(step)
            continue

        eid = step.get("endpoint_id")
        if eid is None:
            ref = step.get("endpoint_ref")
            if isinstance(ref, int):
                eid = ordinal_map.get(ref)

        schema = endpoint_schema_by_id.get(eid) if eid is not None else None
        if not isinstance(schema, dict):
            new_steps.append(step)
            continue

        new_step, filled = fill_step_body(step, schema)
        new_steps.append(new_step)
        if filled:
            all_filled.append(
                f"endpoint#{eid}: " + ", ".join(filled)
            )

    return new_steps, all_filled
