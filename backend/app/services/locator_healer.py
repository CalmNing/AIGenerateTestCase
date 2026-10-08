"""定位器解析与自愈。

自愈分两级:
  1. 多候选降级:按稳定性依次尝试元素预置的多个定位器;若命中的不是主定位器,
     则提升为主定位器(回写)。
  2. DOM 相似度重匹配:全部候选失败时,对实时 DOM 重扫得到的元素按指纹相似度
     打分,命中则合并新定位器并提升为主定位器(回写 PageObject)。

`similarity`/`best_match`/`merge_locators` 为纯函数,可直接单测;运行时的
Playwright 调用收敛在 `resolve_locator`/`validate_element`。
"""
import re
from datetime import datetime
from typing import Any, Optional

from app.services.locator_strategies import (
    js_count_elements,
    js_first_element,
    playwright_locator,
)
from app.services.page_extractor import build_fingerprint, build_locators
from db.models import cn_tz

# 指纹字段权重(仅对源指纹中非空字段归一化)
_FINGERPRINT_WEIGHTS = {
    "tag": 0.15,
    "input_type": 0.15,
    "name": 0.15,
    "aria_label": 0.12,
    "label": 0.12,
    "placeholder": 0.10,
    "role": 0.08,
    "text": 0.08,
    "id": 0.05,
}

HEAL_THRESHOLD = 0.6
MAX_LOCATORS = 6


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def similarity(source: dict, candidate: dict) -> float:
    """指纹相似度:按源指纹已知字段的加权命中率,范围 0~1。"""
    total = 0.0
    matched = 0.0
    for field, weight in _FINGERPRINT_WEIGHTS.items():
        source_value = _norm(source.get(field))
        if not source_value:
            continue
        total += weight
        if source_value == _norm(candidate.get(field)):
            matched += weight
    if total <= 0:
        return 0.0
    return round(matched / total, 4)


def best_match(
    fingerprint: dict,
    candidates: list[dict],
    threshold: float = HEAL_THRESHOLD,
) -> tuple[Optional[int], float, float]:
    """在候选原始元素中找最相似者。

    Returns:
        (候选下标或 None, 最高分, 次高分)。
    """
    scored: list[tuple[int, float]] = []
    for index, attrs in enumerate(candidates or []):
        if not isinstance(attrs, dict):
            continue
        scored.append((index, similarity(fingerprint, build_fingerprint(attrs))))
    if not scored:
        return None, 0.0, 0.0
    scored.sort(key=lambda item: item[1], reverse=True)
    best_index, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0
    if best_score < threshold:
        return None, best_score, second_score
    return best_index, best_score, second_score


def merge_locators(existing: list[dict], candidate_attrs: dict) -> list[dict]:
    """把候选元素的新定位器合并进原定位器列表,按稳定性降序、去重、截断。"""
    merged: list[dict] = []
    for loc in list(build_locators(candidate_attrs)) + list(existing or []):
        if not isinstance(loc, dict) or not loc.get("value"):
            continue
        if any(m["strategy"] == loc.get("strategy") and m["value"] == loc.get("value") for m in merged):
            continue
        merged.append({
            "strategy": loc.get("strategy"),
            "value": loc.get("value"),
            "stability": int(loc.get("stability") or 0),
        })
    merged.sort(key=lambda item: item["stability"], reverse=True)
    return merged[:MAX_LOCATORS]


def apply_heal(
    element: dict,
    candidate_attrs: dict,
    score: float,
    reason: str = "rescan",
) -> dict:
    """把愈合结果写回元素(dict 原地修改),返回愈合事件。"""
    old_primary = (element.get("locators") or [{}])[0].get("value", "")
    element["locators"] = merge_locators(element.get("locators") or [], candidate_attrs)
    new_primary = (element.get("locators") or [{}])[0].get("value", "")
    element["heal_count"] = int(element.get("heal_count") or 0) + 1
    element["last_healed_at"] = datetime.now(tz=cn_tz).isoformat()
    return {
        "element_key": element.get("key"),
        "element_name": element.get("name"),
        "reason": reason,
        "from": old_primary,
        "to": new_primary,
        "similarity": score,
    }


def promote_locator(element: dict, strategy: str, value: str) -> Optional[dict]:
    """命中非主定位器时提升为主定位器(dict 原地修改),返回愈合事件或 None。"""
    locators = element.get("locators") or []
    if not locators:
        return None
    primary = locators[0]
    if primary.get("strategy") == strategy and primary.get("value") == value:
        return None
    winner = next(
        (loc for loc in locators if loc.get("strategy") == strategy and loc.get("value") == value),
        None,
    )
    if winner is None:
        return None
    old_primary = primary.get("value", "")
    locators.remove(winner)
    locators.insert(0, winner)
    element["locators"] = locators
    element["heal_count"] = int(element.get("heal_count") or 0) + 1
    element["last_healed_at"] = datetime.now(tz=cn_tz).isoformat()
    return {
        "element_key": element.get("key"),
        "element_name": element.get("name"),
        "reason": "fallback",
        "from": old_primary,
        "to": value,
        "similarity": None,
    }


def heal_from_candidates(element: dict, candidates: list[dict]) -> Optional[dict]:
    """对实时 DOM 候选做相似度重匹配并回写;无足够相似候选时返回 None。"""
    index, score, _second = best_match(element.get("fingerprint") or {}, candidates)
    if index is None:
        return None
    return apply_heal(element, candidates[index], score)


def _primary_value(element: dict) -> str:
    locators = element.get("locators") or []
    return locators[0].get("value", "") if locators else ""


def _looks_like_tree(elements: list) -> bool:
    """列表里是否存在嵌套 children(树形态)。"""
    return any(
        isinstance(e, dict) and isinstance(e.get("children"), list) and e.get("children")
        for e in elements or []
    )


def merge_elements(
    old_elements: list[dict],
    new_elements: list[dict],
    threshold: float = 0.8,
) -> list[dict]:
    """重新生成时合并新旧元素:保留用户编辑的名称/key/描述/启停与自愈历史,刷新定位器与指纹。

    匹配优先级:key → 主定位器值 → 指纹相似度(≥threshold)。未匹配的新元素直接采用,
    未匹配的旧元素视为页面已移除而丢弃。

    兼容树/扁平两种输入:
      - 任一侧含嵌套 children 时,先扫平再匹配,再以 new 的树形结构为骨架回填
        (结构跟随本次抽取;旧树里本次没抽到的分支丢弃);
      - 两侧皆扁平时保持原扁平合并行为。
    """
    from app.services.element_tree import build_element_tree, flatten_elements

    old_is_tree = _looks_like_tree(old_elements)
    new_is_tree = _looks_like_tree(new_elements)
    if not old_is_tree and not new_is_tree:
        return _merge_flat(old_elements, new_elements, threshold)

    old_flat = flatten_elements(old_elements if old_is_tree else old_elements)
    new_flat = flatten_elements(new_elements if new_is_tree else new_elements)
    # 先按扁平规则合并(保留 old 的用户字段),得到 key → merged 元素
    merged_flat = _merge_flat(old_flat, new_flat, threshold)
    by_key = {e.get("key"): e for e in merged_flat if isinstance(e, dict) and e.get("key")}

    # 以 new 树为骨架重建:结构是本次抽取的,节点数据用 merged(含 old 用户编辑)
    def _rebuild(nodes: list) -> list:
        out: list = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            key = node.get("key")
            source = by_key.get(key) if key else None
            clone = dict(source if source is not None else node)
            # probePath/children 结构来自 new
            clone["probePath"] = list(node.get("probePath") or clone.get("probePath") or [])
            if node.get("probeTrigger") is not None:
                clone["probeTrigger"] = node.get("probeTrigger")
            children = node.get("children") or []
            clone["children"] = _rebuild(children)
            out.append(clone)
        return out

    new_tree = new_elements if new_is_tree else build_element_tree(new_flat)
    return _rebuild(new_tree)


def _merge_flat(
    old_elements: list[dict],
    new_elements: list[dict],
    threshold: float = 0.8,
) -> list[dict]:
    """扁平合并(原 merge_elements 主体)。"""
    old = [e for e in (old_elements or []) if isinstance(e, dict)]
    used: set = set()
    merged: list[dict] = []
    for new in new_elements or []:
        if not isinstance(new, dict):
            continue
        match_index: Optional[int] = None
        # 1) key 相同
        for i, old_el in enumerate(old):
            if i in used:
                continue
            if old_el.get("key") and old_el.get("key") == new.get("key"):
                match_index = i
                break
        # 2) 主定位器值相同
        if match_index is None and _primary_value(new):
            for i, old_el in enumerate(old):
                if i in used:
                    continue
                if _primary_value(old_el) == _primary_value(new):
                    match_index = i
                    break
        # 3) 指纹相似度
        if match_index is None:
            best_index: Optional[int] = None
            best_score = 0.0
            for i, old_el in enumerate(old):
                if i in used:
                    continue
                score = similarity(old_el.get("fingerprint") or {}, new.get("fingerprint") or {})
                if score > best_score:
                    best_index, best_score = i, score
            if best_index is not None and best_score >= threshold:
                match_index = best_index

        if match_index is None:
            merged.append(new)
            continue
        used.add(match_index)
        old_el = old[match_index]
        merged.append({
            **new,
            "key": old_el.get("key") or new.get("key"),
            "name": old_el.get("name") or new.get("name"),
            "description": old_el.get("description") or new.get("description"),
            "enabled": old_el.get("enabled", True),
            "heal_count": old_el.get("heal_count", 0),
            "last_healed_at": old_el.get("last_healed_at"),
        })
    return merged


def _sorted_locators(element: dict) -> list[dict]:
    return sorted(
        [loc for loc in (element.get("locators") or []) if isinstance(loc, dict)],
        key=lambda item: int(item.get("stability") or 0),
        reverse=True,
    )


async def resolve_locator(page, element: dict) -> Optional[dict]:
    """按稳定性依次尝试定位器,返回首个命中(可为 fake page,count 需 await)。"""
    for index, loc in enumerate(_sorted_locators(element)):
        strategy = loc.get("strategy")
        value = loc.get("value")
        if not strategy or not value:
            continue
        try:
            locator = playwright_locator(page, strategy, value)
            count = await locator.count()
        except Exception:
            continue
        if count and count > 0:
            return {"locator": locator, "index": index, "strategy": strategy, "value": value}
    return None


# ---------------------------------------------------------------------------
# JS 兜底(closed shadow root 内嵌 iframe 等场景:locator 恒 0 但 evaluate 可用)
# ---------------------------------------------------------------------------

def _supports_js(container) -> bool:
    return (
        hasattr(container, "evaluate")
        and callable(getattr(container, "evaluate", None))
        and hasattr(container, "evaluate_handle")
        and callable(getattr(container, "evaluate_handle", None))
    )


async def count_via_js(container, strategy: str, value: str) -> Optional[int]:
    """用 JS 在 container(通常为 Frame)里统计策略命中数;不可用/失败返回 None。"""
    if not _supports_js(container):
        return None
    try:
        result = await container.evaluate(js_count_elements(strategy, value))
        return int(result) if result is not None else None
    except Exception:
        return None


async def query_first_element(container, strategy: str, value: str):
    """用 JS 取策略命中的第一个元素(ElementHandle);无命中/失败返回 None。"""
    if not _supports_js(container):
        return None
    try:
        handle = await container.evaluate_handle(js_first_element(strategy, value))
        return handle.as_element() if handle is not None else None
    except Exception:
        return None


async def resolve_target(container, element: dict) -> Optional[dict]:
    """resolve_locator 的增强版:locator 全部 0 命中时退 JS 兜底。

    返回 {"kind": "locator"|"handle", "locator"/"handle", "index", "strategy", "value"}。
    """
    resolved = await resolve_locator(container, element)
    if resolved:
        return {"kind": "locator", **resolved}
    for index, loc in enumerate(_sorted_locators(element)):
        strategy = loc.get("strategy")
        value = loc.get("value")
        if not strategy or not value:
            continue
        count = await count_via_js(container, strategy, value)
        if not count:
            continue
        handle = await query_first_element(container, strategy, value)
        if handle is not None:
            return {
                "kind": "handle",
                "handle": handle,
                "index": index,
                "strategy": strategy,
                "value": value,
            }
    return None


async def validate_element(page, element: dict) -> dict:
    """逐个定位器验证命中数,返回结构化结果。

    locator 全部 0 命中且容器支持 evaluate 时,退 JS 计数(closed shadow
    root 内嵌 iframe 等场景 Playwright 选择器引擎不可用但 evaluate 可用)。
    """
    results = []
    for loc in element.get("locators") or []:
        strategy = loc.get("strategy")
        value = loc.get("value")
        entry = {"strategy": strategy, "value": value, "stability": loc.get("stability"), "count": 0, "matched": False}
        try:
            locator = playwright_locator(page, strategy, value)
            entry["count"] = int(await locator.count())
            entry["matched"] = entry["count"] > 0
        except Exception as exc:  # noqa: BLE001 - 单个定位器失败不影响整体
            entry["error"] = str(exc)[:200]
        results.append(entry)
    if not any(item["matched"] for item in results):
        for entry in results:
            if entry.get("matched") or not entry.get("strategy") or not entry.get("value"):
                continue
            count = await count_via_js(page, entry["strategy"], entry["value"])
            if count:
                entry["count"] = count
                entry["matched"] = True
                entry["via_js"] = True
    return {
        "key": element.get("key"),
        "name": element.get("name"),
        "matched": any(item["matched"] for item in results),
        "locators": results,
    }
