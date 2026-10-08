"""Hybrid 执行器:按绑定计划确定性执行 L1/L2,LLM 步进兜底 L3。

设计(spec):
  - 单页面:一次 Playwright 会话内跑完全部绑定步 + 结构化断言
  - L1: 直接 resolve_target + _perform(复用 page_object_runner 原语)
  - L2: 先校验候选定位器(validate_element),再执行;失败走自愈
  - L3: 同一页面上的步进 LLM(非 browser-use 整例);失败才降级整例 agent
  - PASS = 所有操作步成功 + 所有已结构化断言通过;未结构化预期默认跳过
    (final_judgement=True 时才做 LLM 终判,写入 agent_summary)

执行不在中途把会话交给 browser-use:SPA 表单态无法靠 storage_state 跨会话保存。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from app.services.element_tree import build_execution_index
from app.services.locator_healer import (
    heal_from_candidates,
    promote_locator,
    resolve_target,
    validate_element,
)
from app.services.page_object_runner import (
    _frame_for,
    _heal_candidates_current_page,
    _perform,
    _wait_for_page_ready,
    ensure_overlay_path,
)
from app.services.ui_binding_schemas import (
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_L3,
    UIBoundAssert,
    UIBoundStep,
)

logger = logging.getLogger(__name__)

OnTrace = Callable[[dict], None]


@dataclass
class HybridResult:
    """hybrid 执行结果;success 即终判 PASS/FAIL。"""

    success: bool
    steps_trace: List[dict] = field(default_factory=list)
    heal_events: List[dict] = field(default_factory=list)
    agent_summary: str = ""
    # L3 步进 LLM 失败(而非业务断言失败)时为 True → 调用方降级整例 agent
    needs_agent_fallback: bool = False
    agent_fallback_reason: str = ""


def _element_index(po_elements: list) -> tuple[dict[str, dict], dict[str, list[str]]]:
    by_key, path_by_key = build_execution_index(po_elements or [])
    for e in po_elements or []:
        if isinstance(e, dict) and e.get("key") and e["key"] not in by_key:
            by_key[e["key"]] = {k: v for k, v in e.items() if k != "children"}
            path_by_key.setdefault(e["key"], [])
    return by_key, path_by_key


async def _resolve_with_heal(
    page,
    element: dict,
    by_key: dict[str, dict],
    *,
    storage_state: Optional[str],
    url: str,
    heal_events: list[dict],
    step_no: int,
) -> Optional[dict]:
    """定位 + 多候选提升 + DOM 重扫自愈;返回 resolved 或 None。"""
    resolved = await resolve_target(_frame_for(page, element), element)
    if resolved:
        event = promote_locator(element, resolved["strategy"], resolved["value"])
        if event:
            event["step"] = step_no
            heal_events.append(event)
        return resolved

    raw = await _heal_candidates_current_page(page, storage_state, url)
    event = heal_from_candidates(element, raw)
    if event:
        event["step"] = step_no
        heal_events.append(event)
        resolved = await resolve_target(_frame_for(page, element), element)
    return resolved


async def _run_l1_l2_step(
    page,
    step: UIBoundStep,
    by_key: dict[str, dict],
    path_by_key: dict[str, list[str]],
    *,
    storage_state: Optional[str],
    url: str,
    heal_events: list[dict],
) -> dict:
    """执行一条 L1/L2 操作步;返回 trace entry。"""
    entry = {
        "step_no": step.step_no,
        "action": step.action,
        "element_key": step.element_key,
        "level": step.level,
        "nl": step.nl,
        "status": "failed",
        "healed": False,
        "error": None,
    }

    if step.action == "navigate":
        try:
            if step.value:
                await page.goto(str(step.value), wait_until="domcontentloaded", timeout=30000)
            await _wait_for_page_ready(page)
            entry["status"] = "passed"
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)[:300]
        return entry

    key = str(step.element_key or "")
    element = by_key.get(key)
    if element is None:
        entry["error"] = f"元素不存在: {key}"
        return entry

    try:
        # L2: 先校验候选定位器(失败仍继续,交给 resolve/heal)
        if step.level == LEVEL_L2:
            try:
                await validate_element(_frame_for(page, element), element)
            except Exception:  # noqa: BLE001
                pass

        desired = list(path_by_key.get(key) or [])
        # ensure_overlay_path 需要 opened 栈 — 由调用方维护更复杂,此处简化:
        # 首次执行时由外层统一开浮层(见 run_hybrid)。

        resolved = await _resolve_with_heal(
            page, element, by_key,
            storage_state=storage_state, url=url,
            heal_events=heal_events, step_no=step.step_no,
        )
        if resolved is None:
            entry["error"] = "定位失败(所有候选均未命中)"
            return entry
        entry["healed"] = bool(heal_events and heal_events[-1].get("step") == step.step_no)
        resolved["trigger"] = element.get("trigger")

        await _perform(resolved, {"action": step.action, "value": step.value}, page=page)
        entry["status"] = "passed"
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)[:300]
    return entry


async def _run_assert(
    page,
    a: UIBoundAssert,
    by_key: dict[str, dict],
    *,
    storage_state: Optional[str],
    url: str,
    heal_events: list[dict],
) -> dict:
    """执行一条结构化断言(L1/L2);skip 不进结果。"""
    entry = {
        "assert_index": a.index,
        "action": a.action,
        "element_key": a.element_key,
        "level": a.level,
        "nl": a.nl,
        "status": "failed",
        "healed": False,
        "error": None,
    }
    if a.action == "skip" or a.level == LEVEL_L3:
        entry["status"] = "skipped"
        return entry

    # 页面级 assert_text(无元素):用 page.content 包含匹配
    if not a.element_key and a.action == "assert_text":
        try:
            content = await page.content()
            if str(a.value or "") not in content:
                entry["error"] = f"页面文本未包含「{a.value}」"
                return entry
            entry["status"] = "passed"
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)[:300]
        return entry

    key = str(a.element_key or "")
    element = by_key.get(key)
    if element is None:
        entry["error"] = f"元素不存在: {key}"
        return entry

    try:
        resolved = await _resolve_with_heal(
            page, element, by_key,
            storage_state=storage_state, url=url,
            heal_events=heal_events, step_no=a.index,
        )
        if resolved is None:
            entry["error"] = "定位失败"
            return entry
        entry["healed"] = True
        resolved["trigger"] = element.get("trigger")
        await _perform(resolved, {"action": a.action, "value": a.value}, page=page)
        entry["status"] = "passed"
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)[:300]
    return entry


async def _run_l3_step(
    page,
    step: UIBoundStep,
    *,
    by_key: dict[str, dict],
    llm=None,
    step_agent=None,
) -> dict:
    """L3 步进兜底:同一页面上的轻量 LLM 解析 + 就地执行。

    step_agent 可注入(测试);默认走 ui_step_agent.step_agent_llm。
    llm 为 build_chat_model 实例;None 时尝试延迟构建。
    """
    entry = {
        "step_no": step.step_no,
        "action": step.action,
        "element_key": step.element_key,
        "level": step.level,
        "nl": step.nl,
        "status": "failed",
        "healed": False,
        "error": None,
        "agent": True,
    }
    try:
        if step_agent is None:
            from app.services.ui_step_agent import step_agent_llm as step_agent  # 延迟导入
        plan = await step_agent(page, step, by_key=by_key, llm=llm)
        if plan is None or not isinstance(plan, dict):
            entry["error"] = "L3 步进 LLM 未返回可执行计划"
            return entry
        action = str(plan.get("action") or "agent").strip().lower()
        ekey = str(plan.get("element_key") or "")
        value = plan.get("value")
        if action == "agent" or not ekey:
            entry["error"] = "L3 步进 LLM 仍无法绑定元素"
            return entry
        element = by_key.get(ekey)
        if element is None:
            entry["error"] = f"L3 绑定的元素不存在: {ekey}"
            return entry
        resolved = await resolve_target(_frame_for(page, element), element)
        if resolved is None:
            entry["error"] = "L3 定位失败"
            return entry
        resolved["trigger"] = element.get("trigger")
        await _perform(resolved, {"action": action, "value": value}, page=page)
        entry["element_key"] = ekey
        entry["action"] = action
        entry["status"] = "passed"
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)[:300]
    return entry


async def run_hybrid(
    *,
    url: str,
    page_elements: list[dict],
    steps: list[UIBoundStep],
    asserts: list[UIBoundAssert],
    storage_state: Optional[str],
    task_id: Optional[str] = None,
    final_judgement: bool = False,
    expected_results: Optional[list] = None,
    llm=None,
    step_agent=None,
    on_trace: Optional[OnTrace] = None,
) -> HybridResult:
    """打开目标页,顺序执行绑定步 + 结构化断言,返回 HybridResult。

    - L1/L2 确定性;L3 走同页步进 LLM;任一操作步失败 → success=False
    - 结构化断言(L1/L2)失败 → success=False;skip 不影响
    - L3 步进 LLM 连续失败且无 L1/L2 可执行 → needs_agent_fallback=True
    """
    from playwright.async_api import async_playwright

    by_key, path_by_key = _element_index(page_elements)
    steps_trace: list[dict] = []
    heal_events: list[dict] = []
    opened: list[str] = []
    all_ops_ok = True
    all_asserts_ok = True
    l3_failures = 0
    l12_ran = 0

    async def _emit(entry: dict) -> None:
        steps_trace.append(entry)
        if on_trace:
            try:
                on_trace(entry)
            except Exception:  # noqa: BLE001
                pass

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = None
        try:
            kwargs: dict = {}
            if storage_state:
                kwargs["storage_state"] = storage_state
            context = await browser.new_context(**kwargs)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await _wait_for_page_ready(page)

            for step in sorted(steps, key=lambda s: s.step_no):
                if step.level in (LEVEL_L1, LEVEL_L2) or (
                    step.level == LEVEL_L3 and step.action == "navigate"
                ):
                    # 浮层:按元素祖先链开
                    if step.element_key:
                        desired = list(path_by_key.get(str(step.element_key)) or [])
                        opened = await ensure_overlay_path(page, desired, opened, by_key, path_by_key)
                    entry = await _run_l1_l2_step(
                        page, step, by_key, path_by_key,
                        storage_state=storage_state, url=url, heal_events=heal_events,
                    )
                    l12_ran += 1
                else:
                    entry = await _run_l3_step(
                        page, step, by_key=by_key, llm=llm, step_agent=step_agent,
                    )
                    if entry.get("status") != "passed":
                        l3_failures += 1

                await _emit(entry)
                if entry.get("status") not in ("passed", "skipped"):
                    all_ops_ok = False
                    # 单步失败不 break:继续跑完以便轨迹完整(与 agent 多步一致)

            # 结构化断言
            for a in sorted(asserts, key=lambda x: x.index):
                if a.action == "skip" or a.level == LEVEL_L3:
                    entry = {
                        "assert_index": a.index, "action": a.action, "level": a.level,
                        "nl": a.nl, "status": "skipped", "error": a.reason or "默认跳过",
                    }
                else:
                    if a.element_key:
                        desired = list(path_by_key.get(str(a.element_key)) or [])
                        opened = await ensure_overlay_path(page, desired, opened, by_key, path_by_key)
                    entry = await _run_assert(
                        page, a, by_key,
                        storage_state=storage_state, url=url, heal_events=heal_events,
                    )
                await _emit(entry)
                if entry.get("status") == "failed":
                    all_asserts_ok = False

            # 未结构化预期终判(默认跳过)
            unstructured = [
                str(e) for e in (expected_results or [])
                if str(e).strip() and not any(
                    a.nl and str(e).strip() == a.nl.strip() for a in asserts
                )
            ]
            agent_summary = ""
            if unstructured:
                if final_judgement:
                    try:
                        from app.services.ui_step_agent import judge_unstructured
                        agent_summary = await judge_unstructured(page, unstructured, llm=llm) or ""
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[Hybrid] 未结构化终判失败: {exc}")
                        agent_summary = f"(终判失败: {exc})"
                else:
                    agent_summary = "未结构化预期默认跳过: " + "; ".join(unstructured[:5])

            success = all_ops_ok and all_asserts_ok
            # 全是 L3 且 L3 全失败,又没有任何 L1/L2 成功 → 需整例 agent 兜底
            needs_fallback = (
                l12_ran == 0
                and l3_failures > 0
                and not any(t.get("status") == "passed" for t in steps_trace)
            )
            return HybridResult(
                success=success,
                steps_trace=steps_trace,
                heal_events=heal_events,
                agent_summary=agent_summary,
                needs_agent_fallback=needs_fallback,
                agent_fallback_reason="所有 L3 步进均失败且无 L1/L2 成功" if needs_fallback else "",
            )
        finally:
            if context is not None:
                try:
                    # 落盘登录态到给定路径(非空时);不依赖 state_meaningful 类型签名
                    if storage_state:
                        await context.storage_state(path=str(storage_state))
                except Exception:  # noqa: BLE001
                    pass
            await browser.close()
