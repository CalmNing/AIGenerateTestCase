"""
Pipeline 编排器 — 串联 4 个阶段，管理进度和错误处理
支持功能测试和接口测试两种模式
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable

from sqlmodel import Session, select

from app.pipeline.requirement_splitter import RequirementSplitter
from app.pipeline.test_point_generator import TestPointGenerator
from app.pipeline.test_case_designer import TestCaseDesigner
from app.pipeline.test_case_optimizer import TestCaseOptimizer
from app.routes.skills import get_skill_body
from app.pipeline.schemas import (
    RequirementDedupResult,
    TestPointDedupResult,
    TestCaseDedupResult,
)
from utils.helpers import _build_coverage_summary, _retrieve_related_history, _TokenUsageCallback
from utils.document_prefetch import (
    FEISHU_URL_RE as _FEISHU_URL_RE,
    LANHU_URL_RE as _LANHU_URL_RE,
    prefetch_document,
)

logger = logging.getLogger(__name__)

from app.services.api_consistency import check_testcase_consistency, score_confidence
from utils.helpers import _upsert_history_prompt
from utils.model_utils import _structured_output_robust


def _compute_accuracy(case_data: dict, endpoint_index_to_id: dict, session) -> "tuple[dict, float, str]":
    """计算用例一致性与置信度，返回 (report_dict, score, level)。无 LLM。"""
    from db.models import ApiEndpoint
    endpoints = {}
    for ordinal, eid in endpoint_index_to_id.items():
        ep = session.get(ApiEndpoint, eid)
        if ep is not None:
            endpoints[eid] = ep
    report = check_testcase_consistency(case_data, endpoint_index_to_id, endpoints)
    score, level = score_confidence(report, case_data)
    return report, score, level


def _resolve_keep_indices(keep_indices: Any, n: int) -> List[int]:
    """把 LLM 返回的保留编号解析为合法的 0-based 去重索引列表（自动去重、升序）。

    约定模型以 0-based 返回（提示词已显式标注编号从 0 开始），此处仅做范围校验。
    """
    if not keep_indices:
        return []
    resolved: set = set()
    for i in keep_indices:
        if isinstance(i, int) and 0 <= i < n:
            resolved.add(i)
    return sorted(resolved)


def _assign_requirement_order(
    new_points: List[dict],
    existing_points: List[Any],
    module_id: Optional[int],
) -> List[dict]:
    """为待新建需求点分配 order_index（在已有最大 order_index 基础上续接）。"""
    max_order = -1
    for p in existing_points:
        oi = getattr(p, "order_index", None)
        if oi is not None and oi > max_order:
            max_order = oi
    result: List[dict] = []
    for idx, rp in enumerate(new_points):
        result.append({
            "content": rp.get("content", ""),
            "priority": rp.get("priority", "medium"),
            "order_index": max_order + 1 + idx,
            "module_id": module_id,
        })
    return result


async def _dedup_requirement_points_by_llm(
    new_points: List[dict],
    existing_points: List[Any],
    model: Any,
    module_id: Optional[int],
    *,
    on_fallback: Optional[Callable[[str, str], None]] = None,
) -> List[dict]:
    """基于 LLM 语义比对，返回当前模块下需要新建的需求点列表。

    - 范围：同一 module_id（调用方已按 session + module 过滤 existing_points）
    - 由 LLM 判断新候选是否与已有需求点语义重复，仅保留未覆盖的
    - LLM 异常/返回空时保守保留全部新候选，避免误删导致覆盖丢失；
      此时通过 `on_fallback(code, message)` 回调上报，让调用方登记为警告
      （P0 失败可见性：保守保留不再静默）。
    """
    # 无已有需求点则全部保留
    if not existing_points:
        return _assign_requirement_order(new_points, existing_points, module_id)

    existing_contents = [
        getattr(p, "content", "") for p in existing_points
        if getattr(p, "content", None)
    ]
    candidates = [rp.get("content", "") for rp in new_points]
    # 候选全部为空则无需创建
    if not any(candidates):
        return []

    existing_text = "\n".join(
        f"{i + 1}. {c}" for i, c in enumerate(existing_contents)
    ) or "（无，当前模块暂无需求点）"
    candidates_text = "\n".join(
        f"[{i}] {c}" for i, c in enumerate(candidates)
    )

    system_prompt = _REQ_DEDUP_SYSTEM
    user_prompt = (
        f"## 当前模块已有需求点（共 {len(existing_contents)} 条，已存在，无需再次创建）\n"
        f"{existing_text}\n\n"
        f"## 本次新生成的需求点候选（共 {len(candidates)} 条，编号从 0 开始）\n"
        f"{candidates_text}\n\n"
        "请返回需要【保留】的候选编号列表（0-based，即上面 [0]、[1] 对应的数字）。"
        "若候选全部都是已有需求点的重复，返回空列表。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    def _fallback(code: str, message: str):
        if on_fallback:
            try:
                on_fallback(code, message)
            except Exception:  # noqa: BLE001 - 回调绝不能影响主流程
                logger.debug("去重回调异常（已忽略）", exc_info=True)

    try:
        # P5：补埋点——此前去重调用没接 callbacks，缓存命中率是盲区
        _dedup_cb = _TokenUsageCallback()
        result = await _structured_output_robust(
            model, RequirementDedupResult, messages,
            config={"callbacks": [_dedup_cb]},
        )
    except Exception as e:
        logger.warning("需求点 LLM 去重异常，保守保留全部新需求点: %s", e)
        _fallback("requirement_dedup_failed", f"需求点去重调用失败，已保留全部候选：{type(e).__name__}: {e}")
        return _assign_requirement_order(new_points, existing_points, module_id)

    if result is None:
        logger.warning("需求点 LLM 去重返回空，保守保留全部新需求点")
        _fallback("requirement_dedup_empty", "需求点去重返回空结果，已保留全部候选")
        return _assign_requirement_order(new_points, existing_points, module_id)

    keep_indices = getattr(result, "keep_indices", None) or []
    valid = _resolve_keep_indices(keep_indices, len(new_points))
    if not valid and keep_indices:
        # 模型返回了索引但全部越界（异常），保守保留全部
        logger.warning("需求点 LLM 去重：返回索引全部越界，保守保留全部新需求点")
        _fallback("requirement_dedup_invalid_indices", "需求点去重返回的索引全部越界，已保留全部候选")
        return _assign_requirement_order(new_points, existing_points, module_id)

    to_keep = [new_points[i] for i in valid]
    return _assign_requirement_order(to_keep, existing_points, module_id)


_TP_DEDUP_SYSTEM = """你是一名资深的测试用例测试点去重专家。给定同一功能模块下、由不同需求点生成的【全部测试点】，请判断哪些测试点在语义上重复、或其验证维度已被其他测试点完全覆盖。

判定标准：
- 若某测试点的全部验证维度都被另一个更综合的测试点覆盖，则应删除该测试点，只保留每个重复簇中最综合、覆盖最全的一个。
- 若测试点之间仅侧重点不同、各自拥有独有的验证维度，则全部保留，不要删除。
- 宁可多保留也不要漏删：只有确认某测试点完全被另一测试点覆盖时才删除。
- 只依据给定文本判断，不要臆造。

返回需要保留的测试点 id 列表（每个重复簇只保留最综合的一个；若互不重复则返回全部 id）。"""


async def _dedup_test_points_by_llm(
    points: List[Any],
    requirement_content: str,
    model: Any,
    session: Any,
    *,
    on_fallback: Optional[Callable[[str, str], None]] = None,
) -> List[Any]:
    """跨需求点测试点去重：仅保留语义上未被覆盖的测试点。

    - 范围：本轮同 session+module 新生成的测试点（含各自需求点内容）
    - 由 LLM 判断语义重复/被完全覆盖的测试点，只保留每个重复簇中最综合的一个
    - LLM 异常/返回空/全删时保守保留全部，避免覆盖丢失
    """
    if len(points) <= 1:
        return points

    # 收集测试点及其所属需求点内容，帮助 LLM 判断归属与覆盖范围
    tp_ids = [tp.id for tp in points]
    rp_map: Dict[int, str] = {}
    for tp in points:
        rp_id = getattr(tp, "requirement_point_id", None)
        if rp_id and rp_id not in rp_map:
            from db.models import RequirementPoint

            rp = session.get(RequirementPoint, rp_id)
            rp_map[rp_id] = getattr(rp, "content", "") if rp else ""

    candidates_text = "\n".join(
        f"[{tp.id}] 所属需求点: {rp_map.get(getattr(tp, 'requirement_point_id', None), '')}\n"
        f"    测试点: {tp.content} (test_type={tp.test_type}, design_method={tp.design_method})"
        for tp in points
    )

    messages = [
        {"role": "system", "content": _TP_DEDUP_SYSTEM},
        {
            "role": "user",
            "content": (
                f"## 本次生成的测试点（共 {len(points)} 个，括号内为其 DB id）\n"
                f"{candidates_text}\n\n"
                f"## 原始需求文档（参考，帮助判断哪些验证维度属于真实需求）\n"
                f"{requirement_content[:3000]}\n\n"
                "请返回需要【保留】的测试点 id 列表。"
            ),
        },
    ]

    def _fallback(code: str, message: str):
        if on_fallback:
            try:
                on_fallback(code, message)
            except Exception:  # noqa: BLE001
                logger.debug("去重回调异常（已忽略）", exc_info=True)

    try:
        _dedup_cb = _TokenUsageCallback()
        result = await _structured_output_robust(
            model, TestPointDedupResult, messages,
            config={"callbacks": [_dedup_cb]},
        )
    except Exception as e:
        logger.warning("测试点 LLM 去重异常，保守保留全部测试点: %s", e)
        _fallback("test_point_dedup_failed", f"测试点去重调用失败，已保留全部测试点：{type(e).__name__}: {e}")
        return points

    if result is None:
        logger.warning("测试点 LLM 去重返回空，保守保留全部测试点")
        _fallback("test_point_dedup_empty", "测试点去重返回空结果，已保留全部测试点")
        return points

    keep_ids = getattr(result, "keep_ids", None) or []
    keep_set: set = set()
    for i in keep_ids:
        try:
            keep_set.add(int(i))
        except (TypeError, ValueError):
            continue

    valid_ids = set(tp_ids)
    keep_set &= valid_ids
    if not keep_set:
        logger.info("测试点 LLM 去重：无重复判定，保留全部 %d 个测试点", len(points))
        return points
    if len(keep_set) == len(points):
        logger.info("测试点 LLM 去重：全部保留 %d 个测试点", len(points))
        return points

    kept = [tp for tp in points if tp.id in keep_set]
    dropped = [tp for tp in points if tp.id not in keep_set]
    for tp in dropped:
        session.delete(tp)
    session.commit()
    for tp in kept:
        session.refresh(tp)
    logger.info(
        "测试点跨需求点去重：%d 个 → %d 个（删除 %d 个重复）",
        len(points), len(kept), len(dropped),
    )
    return kept


_TC_DEDUP_SYSTEM = """你是一名资深的测试用例冗余审查专家。给定同一功能模块下生成的全部测试用例，请判断哪些用例的验证维度被其他用例完全覆盖、应被删除。

判定标准：
- 若某用例的全部验证维度都被另一条更综合的用例覆盖，则应删除该用例，只保留每个重复簇中最综合的一个。
- 若用例之间仅侧重点不同、各自拥有独有的验证维度，则全部保留，不要删除。
- 宁可不删，不要误删：只有确认某用例完全被另一条用例覆盖时才标记删除。
- 不要删除全部用例（至少保留一条）。

返回需要删除的用例 id 列表（remove_ids；若互不重复则为空列表）。"""


async def _dedup_test_cases_by_llm(
    cases: List[Any],
    requirement_content: str,
    model: Any,
    *,
    on_fallback: Optional[Callable[[str, str], None]] = None,
) -> List[int]:
    """跨用例冗余审查：返回验证维度完全被其他用例覆盖、应删除的用例 id 列表。

    - 输入为同 session+module 的完整用例集（用标题+验证维度摘要，轻量小输入）
    - 由 LLM 判定语义重复/被完全覆盖的用例，返回 remove_ids
    - 异常/返回空/全删时保守返回空列表，避免覆盖丢失
    """
    if len(cases) <= 1:
        return []

    case_ids = [tc.id for tc in cases]
    lines = []
    for tc in cases:
        er = tc.expected_results or []
        if not isinstance(er, list):
            er = []
        lines.append(
            f"[{tc.id}] {tc.case_name}\n"
            f"    类型={tc.test_type or ''} 方法={tc.design_method or ''} 等级={tc.case_level or 1}\n"
            f"    验证要点: {'；'.join(str(x) for x in er[:6])}"
        )
    summaries_text = "\n".join(lines)

    messages = [
        {"role": "system", "content": _TC_DEDUP_SYSTEM},
        {
            "role": "user",
            "content": (
                f"## 本次生成的全部用例（共 {len(cases)} 个，括号内为用例 DB id）\n"
                f"{summaries_text}\n\n"
                f"## 原始需求文档（参考，帮助判断哪些验证维度属于真实需求）\n"
                f"{requirement_content[:2000]}\n\n"
                "请返回需要【删除】的用例 id 列表（remove_ids）。"
            ),
        },
    ]

    def _fallback(code: str, message: str):
        if on_fallback:
            try:
                on_fallback(code, message)
            except Exception:  # noqa: BLE001
                logger.debug("去重回调异常（已忽略）", exc_info=True)

    try:
        _dedup_cb = _TokenUsageCallback()
        result = await _structured_output_robust(
            model, TestCaseDedupResult, messages,
            config={"callbacks": [_dedup_cb]},
        )
    except Exception as e:
        logger.warning("用例冗余审查异常，保守不删除任何用例: %s", e)
        _fallback("test_case_dedup_failed", f"用例冗余审查失败，未删除任何用例：{type(e).__name__}: {e}")
        return []

    if result is None:
        logger.warning("用例冗余审查返回空，保守不删除任何用例")
        _fallback("test_case_dedup_empty", "用例冗余审查返回空结果，未删除任何用例")
        return []

    remove_ids = getattr(result, "remove_ids", None) or []
    valid = set(case_ids)
    out: List[int] = []
    seen: set = set()
    for i in remove_ids:
        try:
            val = int(i)
        except (TypeError, ValueError):
            continue
        if val in valid and val not in seen:
            seen.add(val)
            out.append(val)
    # 防御：不删除全部用例
    if len(out) >= len(cases):
        logger.info("用例冗余审查：删除全部被拦截，保守不删除")
        _fallback("test_case_dedup_delete_all_blocked", f"用例冗余审查试图删除全部 {len(cases)} 个用例，已拦截并保留")
        return []
    if out:
        logger.info("用例冗余审查：标记 %d 个冗余用例待移除", len(out))
    return out


_REQ_DEDUP_SYSTEM = """你是一名资深的测试用例需求去重专家。给定【当前模块已有需求点】与【本次新生成的需求点候选】，请判断每个候选需求点是否在语义上与已有需求点重复。

判定标准：
- 若候选与任一已有需求点含义相同、或只是换了一种说法、或已被已有需求点完全覆盖，则视为重复，不应保留。
- 若候选是已有需求点未覆盖的全新内容，则应保留。
- 候选之间若彼此语义重复，也只保留其中一个（取最先出现的编号）。

只依据给出的文本判断，不要臆造。返回需要保留的候选编号（0-based）。"""


# 内存中的 pipeline 任务状态存储
_pipeline_tasks: Dict[str, Dict[str, Any]] = {}

# 并发度改为可配置：见 _pipeline_concurrency()（配置键 pipeline_concurrency，默认 4）

# 降级（warning）级别的文案：用于 task["message"] 汇总，避免前端显示"完全成功"
_WARNING_PHASE_LABELS = {1: "需求拆分", 2: "测试点生成", 3: "用例设计", 4: "用例优化"}


def _fill_required_fields(
    db,
    steps: list,
    endpoint_index_to_id: Dict[int, int],
) -> tuple[list, list[str]]:
    """按接口 request_schema.required 补齐用例步骤缺失的必填字段（B6）。

    实测存量 1574 条用例中有 442 处必填字段缺失（cmpId 214 次等），
    导致与 Schema 的一致性只有 76.2%。这类"结构化约束"用确定性代码兜底
    比靠提示词可靠得多。

    返回 (新 steps, 补齐的字段说明列表)。
    """
    if not steps:
        return steps, []
    try:
        from app.services.required_field_filler import fill_testcase_steps
        from db.models import ApiEndpoint

        endpoint_ids = set()
        for s in steps:
            if not isinstance(s, dict):
                continue
            eid = s.get("endpoint_id")
            if eid is None and isinstance(s.get("endpoint_ref"), int):
                eid = endpoint_index_to_id.get(s["endpoint_ref"])
            if eid:
                endpoint_ids.add(int(eid))

        if not endpoint_ids:
            return steps, []

        rows = db.exec(
            select(ApiEndpoint).where(ApiEndpoint.id.in_(list(endpoint_ids)))
        ).all()
        schema_by_id = {
            e.id: (e.request_schema if isinstance(e.request_schema, dict) else {})
            for e in rows
        }
        if not any(schema_by_id.values()):
            return steps, []

        return fill_testcase_steps(
            steps, schema_by_id, endpoint_id_by_ordinal=endpoint_index_to_id,
        )
    except Exception as exc:  # noqa: BLE001 - 补全失败不得影响生成
        logger.warning("必填字段补全失败（已忽略）: %s", exc)
        return steps, []


def _pipeline_concurrency() -> int:
    """pipeline 内部 LLM 调用的并发上限（B6-优化：从写死 4 改为可配置）。

    配置键 `pipeline_concurrency`，默认 4（与历史行为一致）。
    DeepSeek 无硬限流、且希望缩短大任务等待时可调大（如 6~8）；
    被测服务或代理不稳时调小。
    """
    try:
        from config import config_manager

        return max(1, min(16, int(config_manager.get_pipeline_concurrency())))
    except Exception:  # noqa: BLE001
        return 4


def _api_context_char_allowance() -> int:
    """api_context 的字符额度（按 token 预算换算）。

    预算口径与 utils/token_budget.py 一致：中文 1 字≈1 token，ASCII 4 字符≈1 token；
    这里按"字符数 ≈ token 数 × 1.6"（中文为主的保守换算）给出上限。
    """
    try:
        from config import config_manager

        window = config_manager.get_model_window()
        reserved = config_manager.get_reserved_output_tokens()
    except Exception:  # noqa: BLE001
        window, reserved = 32768, 8192
    usable = max(0, int(window * 0.85) - reserved)
    # api_context 最多占可用预算的一半，剩下的留给需求/历史/输出
    return max(2000, int(usable * 0.5 * 1.6))


def _add_warning(
    task_id: str,
    phase: int,
    code: str,
    message: str,
    detail: Any = "",
    severity: str = "warning",
) -> None:
    """记录一条警告到任务状态（P0 失败可见性）。

    背景：改造前阶段内批次失败只是 `logger.error` 后返回空列表，任务仍被标记
    `completed`，前端看到的是"生成完成"——部分失败被伪装成完全成功。
    现在所有可继续的失败都会落到 `task["warnings"]`，由前端与 `result` 一并展示。

    两档严重度，区分"结果确实缺内容"与"仅仅是个质量提示"：
    - `warning`：降级导致部分结果缺失（批次失败、去重降级、历史保存失败等）
      → 任务状态降为 `partial`；
    - `quality`：结果完整但质量可能不达标（等级分布异常等） → 仅提示，不改状态。

    任何异常都不得影响主流程（例如 task 已被清理时不写）。
    """
    task = _pipeline_tasks.get(task_id)
    if task is None:
        return
    warnings = task.setdefault("warnings", [])
    entry: Dict[str, Any] = {
        "phase": phase,
        "phase_name": _WARNING_PHASE_LABELS.get(phase, ""),
        "code": code,
        "message": message,
        "severity": severity if severity in ("warning", "quality") else "warning",
    }
    if detail:
        entry["detail"] = str(detail)[:400]
    warnings.append(entry)
    log = logger.warning if entry["severity"] == "warning" else logger.info
    log(
        "[Pipeline %s] %s(%s, 阶段%s): %s | %s",
        task_id[:8],
        "降级警告" if entry["severity"] == "warning" else "质量提示",
        code, phase, message, str(detail)[:200],
    )


def compute_level_distribution(cases: List[Any]) -> Dict[str, int]:
    """统计用例等级分布（1=功能 2=边界 3=异常 4=场景），用于质量校验。"""
    dist = {"1": 0, "2": 0, "3": 0, "4": 0, "unknown": 0}
    for tc in cases:
        level = getattr(tc, "case_level", None) if not isinstance(tc, dict) else tc.get("case_level")
        try:
            key = str(int(level))
        except (TypeError, ValueError):
            key = "unknown"
        if key not in dist:
            key = "unknown"
        dist[key] += 1
    return dist


def check_level_distribution(task_id: str, phase: int, cases: List[Any]) -> Dict[str, int]:
    """校验等级分布并把明显异常登记为警告（P0）。返回分布供 result 使用。

    判定规则（宽松，避免误报）：
    - 用例数 >= 4 且全部落在同一等级 → 警告（提示词明确要求级别分布合理）；
    - 存在非法等级（非 1-4）→ 警告。
    """
    dist = compute_level_distribution(cases)
    total = len(cases)
    if total == 0:
        return dist

    if dist.get("unknown"):
        _add_warning(
            task_id, phase, "invalid_case_level",
            f"{dist['unknown']} 个用例的 case_level 不在 1-4 范围内",
            severity="quality",
        )

    valid_levels = {k: v for k, v in dist.items() if k != "unknown"}
    used = [k for k, v in valid_levels.items() if v]
    if total >= 4 and len(used) <= 1 and used:
        _add_warning(
            task_id, phase, "uniform_case_level",
            f"{total} 个用例全部为等级 {used[0]}，级别分布异常（提示词要求覆盖功能/边界/异常/场景）",
            detail=f"level_distribution={dist}",
            severity="quality",
        )
    return dist


def get_pipeline_task(task_id: str) -> Optional[Dict[str, Any]]:
    """获取 pipeline 任务状态"""
    return _pipeline_tasks.get(task_id)


def _normalize_key_value_aliases(items: Any) -> Any:
    """Normalize key/value rows so the executor can read parameter locations."""
    if not isinstance(items, list):
        return items
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        row = dict(item)
        if "in" not in row and row.get("in_field") is not None:
            row["in"] = row.get("in_field")
        row.pop("in_field", None)
        normalized.append(row)
    return normalized


def _resolve_pipeline_endpoint_id(step: dict, endpoint_index_to_id: Dict[int, int]) -> int | None:
    """Resolve endpoint_id from either endpoint_id or pipeline endpoint_ref."""
    eid = step.get("endpoint_id")
    if eid:
        return int(eid)
    ref = step.get("endpoint_ref")
    if ref and endpoint_index_to_id:
        return endpoint_index_to_id.get(int(ref))
    return None


def _is_pipeline_api_call_step(step: Any) -> bool:
    if not isinstance(step, dict):
        return False
    return (
        step.get("type") == "api_call"
        or step.get("endpoint_id") is not None
        or step.get("endpoint_ref") is not None
    )


def _normalize_pipeline_api_step(step: dict, endpoint_index_to_id: Dict[int, int]) -> dict:
    """Normalize an LLM-produced pipeline API step before persistence/execution."""
    normalized = dict(step)
    if normalized.get("endpoint_id") or normalized.get("endpoint_ref"):
        normalized.setdefault("type", "api_call")
    eid = _resolve_pipeline_endpoint_id(normalized, endpoint_index_to_id)
    if eid:
        normalized["endpoint_id"] = eid
        normalized.pop("endpoint_ref", None)
    for key in ("headers", "parameters", "variables"):
        normalized[key] = _normalize_key_value_aliases(normalized.get(key))
    return normalized


def _normalize_pipeline_api_steps(steps: Any, endpoint_index_to_id: Dict[int, int]) -> Any:
    if not isinstance(steps, list):
        return steps
    return [
        _normalize_pipeline_api_step(step, endpoint_index_to_id)
        if isinstance(step, dict) else step
        for step in steps
    ]


def _collect_pipeline_endpoint_ids(test_cases: list, endpoint_index_to_id: Dict[int, int]) -> set[int]:
    endpoint_ids: set[int] = set()
    for tc in test_cases:
        for step in (tc.steps or []):
            if _is_pipeline_api_call_step(step):
                eid = _resolve_pipeline_endpoint_id(step, endpoint_index_to_id)
                if eid:
                    endpoint_ids.add(eid)
        for pc in (tc.preset_conditions or []):
            if _is_pipeline_api_call_step(pc):
                eid = _resolve_pipeline_endpoint_id(pc, endpoint_index_to_id)
                if eid:
                    endpoint_ids.add(eid)
    return endpoint_ids


def _build_pipeline_scenario_steps(testcase, endpoint_map: dict, endpoint_index_to_id: Dict[int, int]) -> list[dict]:
    """Build executable ApiScenario steps from a pipeline-generated testcase."""
    api_steps = []
    for pc in (testcase.preset_conditions or []):
        if _is_pipeline_api_call_step(pc) and _resolve_pipeline_endpoint_id(pc, endpoint_index_to_id):
            api_steps.append(("preset", pc))
    for step in (testcase.steps or []):
        if _is_pipeline_api_call_step(step) and _resolve_pipeline_endpoint_id(step, endpoint_index_to_id):
            api_steps.append(("step", step))

    scenario_steps = []
    for step_type, raw_step in api_steps:
        step_copy = _normalize_pipeline_api_step(raw_step, endpoint_index_to_id)
        eid = step_copy.get("endpoint_id")
        ep = endpoint_map.get(eid) if eid else None
        if ep:
            step_copy.setdefault("method", ep.method)
            step_copy.setdefault("path", ep.path)
            step_copy.setdefault("url", ep.url or ep.path)
            step_copy.setdefault("endpoint_name", ep.name)
            step_copy.setdefault("enabled", True)
            step_copy.setdefault("continue_on_failure", True)
        if step_type == "preset":
            step_copy["is_preset"] = True
        scenario_steps.append(step_copy)
    return scenario_steps


def merge_entities_by_position(
    existing: list,
    generated: list,
    *,
    max_keep: int | None = None,
) -> tuple[list, list, list]:
    """
    按位置顺序匹配，计算就地更新动作。

    Args:
        existing: 已存在的实体列表（按稳定顺序，如按 id 升序）。
        generated: 新生成的实体数据列表（可能为含空 dict 的占位）。
        max_keep: 最多保留的生成数量（None 表示全部保留）。

    Returns:
        (to_update, to_create, to_delete)
        - to_update: [(existing[i], generated[i])] 需要更新的 (实体, 新数据) 对
        - to_create: [generated[i]] 需要新建的数据（i >= len(existing)）
        - to_delete: [existing[i]] 多余的旧实体（i >= 生成数量）
    保留 generated 中位置 < len(existing) 但内容为空的占位项（跳过，不更新也不删）。
    """
    if max_keep is not None:
        generated = generated[:max_keep]

    to_update: list = []
    to_create: list = []
    to_delete: list = []

    for i, gen in enumerate(generated):
        is_empty = (gen is None) or (isinstance(gen, dict) and not gen)
        if i < len(existing):
            if is_empty:
                # 占位空项：既不更新也不删除已有实体（保留现状）
                continue
            to_update.append((existing[i], gen))
        else:
            if is_empty:
                continue
            to_create.append(gen)

    # 多余旧实体删除
    for i in range(len(generated), len(existing)):
        to_delete.append(existing[i])

    return to_update, to_create, to_delete


def _is_empty_case(tc: "TestCase") -> bool:
    return not (tc.case_name or tc.steps)


def _is_empty_tp(tp: "TestPoint") -> bool:
    return not tp.content


def _cascade_delete_test_point(session, tp):
    """级联删除测试点及其下所有用例 + 孤儿场景（提交由调用方统一处理）。"""
    from db.models import TestCase, ApiScenario
    cases = session.exec(select(TestCase).where(TestCase.test_point_id == tp.id)).all()
    for c in cases:
        if c.scenario_id:
            sc = session.get(ApiScenario, c.scenario_id)
            if sc:
                session.delete(sc)
        session.delete(c)
    session.delete(tp)


class PipelineRunner:
    """4阶段 Pipeline 编排器（支持功能测试和接口测试）"""

    def __init__(
        self,
        db_session: Session,
        api_key: str,
        api_base_url: str = "https://api.deepseek.com",
        api_model: str = "deepseek-v4-flash",
        api_proxy_url: Optional[str] = None,
        mcp_configs: Optional[List[Dict[str, Any]]] = None,
    ):
        self.db = db_session
        self.api_key = api_key
        self.api_base_url = api_base_url
        self.api_model = api_model
        self.api_proxy_url = api_proxy_url
        self.mcp_configs = mcp_configs or []

        # 初始化 4 个 agent（共享同一模型配置）
        model_kwargs = dict(
            api_key=api_key,
            api_base_url=api_base_url,
            api_model=api_model,
            api_proxy_url=api_proxy_url,
        )
        self.splitter = RequirementSplitter(**model_kwargs)
        self.tp_generator = TestPointGenerator(**model_kwargs)
        self.tc_designer = TestCaseDesigner(**model_kwargs)
        self.optimizer = TestCaseOptimizer(**model_kwargs)

    def _create_task(self) -> str:
        """创建 pipeline 任务，返回 task_id"""
        task_id = str(uuid.uuid4())
        _pipeline_tasks[task_id] = {
            "task_id": task_id,
            "status": "running",
            "current_phase": 0,
            "total_phases": 4,
            "phase_name": "初始化",
            "progress": 0,
            "message": "正在初始化...",
            "result": None,
            "error": None,
            # P0 失败可见性：可继续的失败（批次失败/去重降级/等级异常）都登记在这里，
            # 前端据此提示"部分成功"，不再把降级伪装成完全成功。
            "warnings": [],
            "created_at": datetime.now().isoformat(),
        }
        return task_id

    def _update_progress(
        self,
        task_id: str,
        phase: int,
        phase_name: str,
        progress: int,
        message: str,
    ):
        """更新任务进度"""
        if task_id in _pipeline_tasks:
            _pipeline_tasks[task_id].update({
                "current_phase": phase,
                "phase_name": phase_name,
                "progress": min(progress, 100),
                "message": message,
            })
            logger.info(f"[Pipeline {task_id[:8]}] 阶段{phase} {phase_name}: {progress}% - {message}")

    def _complete_task(self, task_id: str, result: Dict[str, Any]):
        """标记任务完成（有 warnings 时把状态显式降级为 partial，并写入 message）。"""
        if task_id in _pipeline_tasks:
            task = _pipeline_tasks[task_id]
            warnings = task.get("warnings") or []
            # 只有 severity=warning 的条目才算"结果确实缺内容"；quality 仅作提示
            degradations = [w for w in warnings if w.get("severity") != "quality"]
            # warnings 同时挂到 result 上，方便前端只读 result 的场景（如批量生成回调）
            result = dict(result or {})
            result["warnings"] = list(warnings)
            result["partial"] = bool(degradations)
            status = "partial" if degradations else "completed"
            if degradations:
                message = f"生成完成，但有 {len(degradations)} 项降级（部分结果可能缺失）"
            elif warnings:
                message = f"生成完成（{len(warnings)} 项质量提示）"
            else:
                message = "生成完成！"
            task.update({
                "status": status,
                "progress": 100,
                "phase_name": "完成",
                "message": message,
                "result": result,
            })

    def _fail_task(self, task_id: str, error: str):
        """标记任务失败（保留已产生的 warnings，便于定位"失败前已降级"的部分）"""
        if task_id in _pipeline_tasks:
            task = _pipeline_tasks[task_id]
            _pipeline_tasks[task_id].update({
                "status": "failed",
                "message": f"生成失败：{error}",
                "error": error,
                "warnings": list(task.get("warnings") or []),
            })

    def _save_history_prompt(
        self,
        task_id: str,
        session_id: int,
        module_id: Optional[int],
        user_id: Optional[str],
        requirement_text: str,
        original_requirement: str,
    ):
        """保存本次生成的需求描述到历史提示词记录。

        仅保存用户原始需求（或文档链接预取后的正文），不保存技能注入前缀与
        接口 schema 上下文，避免历史记录被无关内容污染。
        """
        # 剥离技能注入前缀（"## 激活的测试方法论技能 ... ## 用户需求\\n\\n"）
        content = requirement_text
        if content.startswith("## 激活的测试方法论技能"):
            idx = content.find("## 用户需求")
            if idx != -1:
                content = content[idx + len("## 用户需求"):].lstrip("\n")

        # 若需求文本与原始输入不同（说明含文档链接且已被预取为文档正文），
        # 优先保存预取后的文档正文；否则保存用户原始输入
        has_doc_url = bool(
            _FEISHU_URL_RE.search(original_requirement)
            or _LANHU_URL_RE.search(original_requirement)
        )
        if has_doc_url and content.strip() != original_requirement.strip():
            save_content = content
        else:
            save_content = original_requirement

        _upsert_history_prompt(
            self.db,
            content=save_content,
            module_id=module_id,
            session_id=session_id,
            user_id=user_id,
        )
        logger.info(f"[Pipeline {task_id[:8]}] 已保存历史提示词")

    async def run(
        self,
        session_id: int,
        requirement_text: str,
        user_id: Optional[str] = None,
        selected_skills: Optional[List[str]] = None,
        module_id: Optional[int] = None,
        *,
        api_endpoint_ids: Optional[List[int]] = None,
        api_project_id: Optional[int] = None,
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_context: str = "",
        original_requirement: str = "",
        stop_after_stage: int = 4,
        feishu_section: Optional[str] = None,
        core_endpoint_ids: Optional[set] = None,
    ) -> str:
        """
        启动 4 阶段 pipeline（异步执行）

        Args:
            session_id: 会话ID
            requirement_text: 需求文本
            user_id: 用户ID
            selected_skills: 选中的技能名称列表
            module_id: 模块ID
            api_endpoint_ids: API 接口 ID 列表
            api_project_id: API 项目 ID
            endpoint_index_to_id: 接口序号→数据库ID映射
            api_context: API 接口信息文本
            stop_after_stage: 执行到哪个阶段后停止（1-4），后续阶段跳过

        Returns:
            task_id: 任务ID，可用于查询进度
        """
        task_id = self._create_task()

        # 保留用户原始需求（注入技能前），用于保存历史提示词
        original_requirement = requirement_text

        # 注入技能内容到需求文本
        if selected_skills:
            skill_context = ""
            for name in selected_skills:
                body = get_skill_body(name)
                if body:
                    skill_context += f"\n\n{body}"
            if skill_context:
                requirement_text = (
                    "## 激活的测试方法论技能\n\n"
                    + skill_context.strip()
                    + "\n\n---\n\n## 用户需求\n\n"
                    + requirement_text
                )
                logger.info(f"已注入 {len(selected_skills)} 个技能到 pipeline")

        # 在后台执行 pipeline
        asyncio.create_task(
            self._run_pipeline(
                task_id, session_id, requirement_text, user_id,
                module_id=module_id,
                api_endpoint_ids=api_endpoint_ids,
                api_project_id=api_project_id,
                endpoint_index_to_id=endpoint_index_to_id or {},
                api_context=api_context,
                original_requirement=original_requirement,
                stop_after_stage=stop_after_stage,
                feishu_section=feishu_section,
                core_endpoint_ids=core_endpoint_ids,
            )
        )

        return task_id

    async def _prefetch_document(self, requirement_text: str, feishu_section: str | None = None) -> str:
        """预取飞书/蓝湖文档内容，替换需求文本中的链接为实际内容"""
        # 图片解析沿用当前 pipeline 的模型配置；不支持图片的模型会自动跳过。
        vision_config = {
            "api_key": self.api_key,
            "api_base_url": self.api_base_url,
            "api_model": self.api_model,
            "api_proxy_url": self.api_proxy_url or "",
        }
        feishu_url = _FEISHU_URL_RE.search(requirement_text or "")
        if feishu_url and feishu_section:
            from utils.document_prefetch import fetch_feishu_document
            content = await fetch_feishu_document(
                requirement_text, self.mcp_configs,
                section=feishu_section, vision_config=vision_config,
            )
            if not content:
                raise ValueError(f"飞书文档目录读取失败：{feishu_section}")
            return content
        return await prefetch_document(
            requirement_text, self.mcp_configs, vision_config=vision_config)

    async def _run_pipeline(
        self,
        task_id: str,
        session_id: int,
        requirement_text: str,
        user_id: Optional[str] = None,
        *,
        module_id: Optional[int] = None,
        api_endpoint_ids: Optional[List[int]] = None,
        api_project_id: Optional[int] = None,
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_context: str = "",
        original_requirement: str = "",
        stop_after_stage: int = 4,
        feishu_section: Optional[str] = None,
        core_endpoint_ids: Optional[set] = None,
    ):
        """执行完整的 4 阶段 pipeline"""
        from db.models import RequirementPoint, TestPoint, TestCase

        is_api_test = bool(api_context)

        try:
            # 预取飞书/蓝湖文档内容
            has_feishu = bool(_FEISHU_URL_RE.search(requirement_text))
            has_lanhu = bool(_LANHU_URL_RE.search(requirement_text))
            if has_feishu or has_lanhu:
                self._update_progress(task_id, 1, "需求拆分", 2, "正在预取文档内容...")
                prefetched = await self._prefetch_document(requirement_text, feishu_section)
                if prefetched != requirement_text:
                    requirement_text = prefetched
                    logger.info(f"Pipeline: 文档预取成功，内容长度 {len(requirement_text)}")
                elif has_feishu:
                    raise ValueError(
                        "飞书文档读取失败：已检测到飞书链接，但未能通过 MCP 获取文档正文。"
                        "请检查 MCP 服务器配置、飞书应用权限或 access token 是否有效后重试。"
                    )

            # P5：token 预算裁剪 api_context（核心接口保全文，辅助接口压骨架）
            if api_context:
                try:
                    from utils.token_budget import compact_api_context, estimate_tokens

                    allowance = _api_context_char_allowance()
                    if estimate_tokens(api_context) > estimate_tokens("x" * allowance):
                        api_context, api_report = compact_api_context(
                            api_context,
                            core_endpoint_ids=core_endpoint_ids,
                            max_chars=allowance,
                        )
                        if api_report.get("truncated"):
                            _add_warning(
                                task_id, 1, "context_truncated",
                                (
                                    f"接口上下文超出 token 预算，已裁剪（节省约 "
                                    f"{api_report.get('saved_tokens', 0)} token），"
                                    "部分接口规格可能不完整"
                                ),
                                detail=json.dumps(
                                    api_report.get("items", []), ensure_ascii=False
                                )[:400],
                            )
                except Exception as ctx_err:  # noqa: BLE001
                    logger.warning("接口上下文裁剪失败（不影响生成）: %s", ctx_err)

            # ========== 阶段1: 需求拆分 (0-25%) ==========
            self._update_progress(task_id, 1, "需求拆分", 5, "正在分析需求文档...")

            # P2：把「已覆盖清单」注入阶段 1（拆分阶段就避开已覆盖内容），
            # 并把相关历史需求作为参考注入（pipeline 此前完全不读历史上下文）。
            coverage_summary = _build_coverage_summary(self.db, module_id)
            # B7：人工反馈回流（正样本 few-shot + 驳回规避清单）
            try:
                from app.services.human_feedback import (
                    format_examples_for_prompt,
                    format_rejection_for_prompt,
                    get_relevant_positive_examples,
                    get_rejection_guidance,
                )

                pos_examples = get_relevant_positive_examples(
                    self.db,
                    query=requirement_text[:500],
                    module_id=module_id,
                    session_id=session_id,
                )
                if pos_examples:
                    coverage_summary += "\n\n" + format_examples_for_prompt(pos_examples)
                    logger.info("B7：已注入 %d 条高质量示例（相关性召回）", len(pos_examples))
                reject_guide = get_rejection_guidance(
                    self.db, module_id=module_id, session_id=session_id,
                )
                if reject_guide:
                    coverage_summary += "\n\n" + format_rejection_for_prompt(reject_guide)
            except Exception as fb_err:  # noqa: BLE001
                logger.warning("人工反馈回流失败（已忽略）: %s", fb_err)
            related_history = _retrieve_related_history(
                self.db,
                query=requirement_text,
                module_id=module_id,
                user_id=user_id,
                session_id=session_id,
            )
            if related_history:
                coverage_summary = related_history + coverage_summary

            requirement_points_data = await self.splitter.analyze(
                requirement_text,
                is_api_test=is_api_test,
                api_context=api_context,
                coverage_summary=coverage_summary,
            )

            if not requirement_points_data:
                raise ValueError("未生成任何需求点")

            # 查询当前模块下已有需求点，用于去重（范围：同一 session + 同一 module）
            existing_stmt = select(RequirementPoint).where(
                RequirementPoint.session_id == session_id
            )
            if module_id is not None:
                existing_stmt = existing_stmt.where(
                    RequirementPoint.module_id == module_id
                )
            else:
                # 未选择模块时，按 module_id IS NULL 过滤同一会话下的无模块需求点
                existing_stmt = existing_stmt.where(
                    RequirementPoint.module_id.is_(None)
                )
            existing_points = self.db.exec(existing_stmt).all()

            # 去重：基于 LLM 语义比对，已存在的相同需求点不再创建，仅新建未覆盖到的
            def _req_dedup_warn(code: str, message: str):
                _add_warning(task_id, 1, code, message)

            to_create = await _dedup_requirement_points_by_llm(
                requirement_points_data, existing_points, self.splitter.model, module_id,
                on_fallback=_req_dedup_warn,
            )
            skipped = len(requirement_points_data) - len(to_create)

            # 保存需求点到数据库
            req_points = []
            for rp_data in to_create:
                rp = RequirementPoint(
                    session_id=session_id,
                    content=rp_data["content"],
                    module_id=rp_data["module_id"],
                    priority=rp_data["priority"],
                    order_index=rp_data["order_index"],
                    user_id=user_id,
                )
                self.db.add(rp)
                req_points.append(rp)

            self.db.commit()
            for rp in req_points:
                self.db.refresh(rp)

            self._update_progress(
                task_id, 1, "需求拆分", 25,
                f"需求拆分完成，新建 {len(req_points)} 个需求点"
                + (f"（已跳过 {skipped} 个重复）" if skipped else "")
            )

            # 阶段1模式：只完成需求拆分
            if stop_after_stage <= 1:
                self._update_progress(
                    task_id, 0, "完成", 100,
                    f"需求拆分完成：{len(req_points)} 个需求点"
                )
                self._complete_task(task_id, {
                    "requirement_points_count": len(req_points),
                    "test_points_count": 0,
                    "test_cases_count": 0,
                    "mode": "stage1",
                    "level_distribution": compute_level_distribution([]),
                })
                return

            # ========== 阶段2: 测试点生成 (25-50%) ==========
            all_test_points = []
            sem = asyncio.Semaphore(_pipeline_concurrency())
            total_rp = len(req_points)

            async def _gen_for_point(rp):
                async with sem:
                    try:
                        return rp, await self.tp_generator.generate(
                            rp.content,
                            is_api_test=is_api_test,
                            api_context=api_context,
                        )
                    except Exception as e:
                        # P0：单点失败不再静默——登记 warning，让前端知道该需求点无测试点
                        logger.error(f"需求点 {rp.id} 测试点生成失败: {e}")
                        _add_warning(
                            task_id, 2, "test_point_generation_failed",
                            f"需求点「{str(rp.content)[:40]}」的测试点生成失败，该需求点未产出测试点",
                            detail=f"{type(e).__name__}: {e}",
                        )
                        return rp, []

            self._update_progress(
                task_id, 2, "测试点生成", 25,
                f"正在为 {total_rp} 个需求点并发生成测试点..."
            )
            tp_results = await asyncio.gather(*[_gen_for_point(rp) for rp in req_points])

            # 收集后统一写库（Session 非协程安全，写操作不并发）
            for rp, tp_data_list in tp_results:
                for tp_data in tp_data_list:
                    tp = TestPoint(
                        requirement_point_id=rp.id,
                        session_id=session_id,
                        content=tp_data.get("content", ""),
                        test_type=tp_data.get("test_type", "functional"),
                        design_method=tp_data.get("design_method", ""),
                        priority=tp_data.get("priority", "medium"),
                        user_id=user_id,
                    )
                    self.db.add(tp)
                    all_test_points.append(tp)

            self.db.commit()
            for tp in all_test_points:
                self.db.refresh(tp)

            self._update_progress(
                task_id, 2, "测试点生成", 50,
                f"测试点生成完成，共 {len(all_test_points)} 个测试点"
            )

            # ========== 阶段2.5: 跨需求点测试点去重 ==========
            # 阶段2 按需求点独立并发生成，彼此不可见，同一功能区域可能被重复建测试点。
            # 此处 LLM 语义比对，删除被其他测试点完全覆盖的重复测试点（1:1 约束下
            # 后续每个测试点只生成 1 条用例，去重从源头消灭用例级重复）。
            if len(all_test_points) > 1:
                self._update_progress(
                    task_id, 2, "测试点生成", 50,
                    f"正在跨需求点去重测试点（共 {len(all_test_points)} 个）..."
                )
                def _tp_dedup_warn(code: str, message: str):
                    _add_warning(task_id, 2, code, message)

                all_test_points = await _dedup_test_points_by_llm(
                    all_test_points, requirement_text, self.tp_generator.model, self.db,
                    on_fallback=_tp_dedup_warn,
                )

            # 手动模式：只执行前两阶段，到此结束
            if stop_after_stage <= 2:
                self._update_progress(
                    task_id, 0, "完成", 100,
                    f"手动模式完成：{len(req_points)} 个需求点，{len(all_test_points)} 个测试点"
                )
                self._complete_task(task_id, {
                    "requirement_points_count": len(req_points),
                    "test_points_count": len(all_test_points),
                    "test_cases_count": 0,
                    "mode": "manual",
                })
                return

            # ========== 阶段3: 用例设计 (50-85%) ==========
            all_test_cases = []
            # B6：本次生成中自动补齐的必填字段总数（用于质量提示）
            required_filled_total = 0
            BATCH_SIZE = 3
            batches = [
                all_test_points[i:i + BATCH_SIZE]
                for i in range(0, len(all_test_points), BATCH_SIZE)
            ]

            # 加载原始需求文档作为上下文
            requirement_content = requirement_text
            design_sem = asyncio.Semaphore(_pipeline_concurrency())

            async def _design_batch(batch):
                async with design_sem:
                    test_points_for_batch = [
                        {
                            "id": tp.id,
                            "content": tp.content,
                            "test_type": tp.test_type,
                            "design_method": tp.design_method,
                            "priority": tp.priority,
                        }
                        for tp in batch
                    ]
                    try:
                        return await self.tc_designer.design_batch(
                            test_points_for_batch, requirement_content,
                            is_api_test=is_api_test,
                            api_context=api_context,
                        )
                    except Exception as e:
                        # P0：整批失败登记 warning（含受影响的测试点 id）
                        logger.error(f"批次用例设计失败: {e}")
                        _add_warning(
                            task_id, 3, "case_design_batch_failed",
                            f"一批 {len(batch)} 个测试点的用例设计失败，对应测试点无用例",
                            detail=(
                                f"test_point_ids={[getattr(tp, 'id', None) for tp in batch]}; "
                                f"{type(e).__name__}: {e}"
                            ),
                        )
                        return []

            self._update_progress(
                task_id, 3, "用例设计", 50,
                f"正在并发设计{'接口' if is_api_test else '功能'}测试用例... 共 {len(batches)} 批次"
            )
            batch_results = await asyncio.gather(*[_design_batch(b) for b in batches])

            # 收集后统一写库（Session 非协程安全，写操作不并发）
            for cases_data in batch_results:
                for case_data in cases_data:
                    steps = case_data.get("test_steps", [])

                    # 接口测试：将 endpoint_ref 转为 endpoint_id（与单次生成路径一致）
                    if is_api_test and isinstance(steps, list):
                        converted_steps = []
                        for s in steps:
                            if isinstance(s, dict):
                                s = dict(s)
                                # 补全 type 字段（LLM 有时不设置）
                                if s.get("endpoint_id") or s.get("endpoint_ref"):
                                    if not s.get("type"):
                                        s["type"] = "api_call"
                                # 转换 endpoint_ref → endpoint_id
                                if s.get("endpoint_ref") and endpoint_index_to_id:
                                    ref = s.pop("endpoint_ref", None)
                                    eid = endpoint_index_to_id.get(int(ref)) if ref else None
                                    if eid:
                                        s["endpoint_id"] = eid
                            converted_steps.append(s)
                        steps = converted_steps

                    # 根据测试类型设置不同的字段
                    if is_api_test:
                        steps = _normalize_pipeline_api_steps(steps, endpoint_index_to_id)
                        # B6 头号质量问题修复：按 request_schema.required 确定性补齐
                        # 缺失的必填字段（LLM 不保证遵守"请求骨架-硬约束"）。
                        steps, filled = _fill_required_fields(
                            self.db, steps, endpoint_index_to_id,
                        )
                        if filled:
                            required_filled_total += len(filled)

                    tc_kwargs = dict(
                        case_name=case_data.get("title", ""),
                        steps=steps if isinstance(steps, list) else [],
                        preset_conditions=case_data.get("preconditions", []) or [],
                        case_level=case_data.get("case_level", 1),
                        session_id=session_id,
                        module_id=module_id,
                        test_point_id=case_data.get("test_point_id"),
                        test_type=case_data.get("test_type"),
                        design_method=case_data.get("design_method"),
                        user_id=user_id,
                    )

                    if is_api_test:
                        # 接口测试：设置 API 相关字段
                        tc_kwargs["expected_results"] = [
                            s.get("description", "") if isinstance(s, dict) else str(s)
                            for s in steps
                        ] if steps else []
                        if api_endpoint_ids:
                            tc_kwargs["api_endpoint_id"] = ",".join(str(eid) for eid in api_endpoint_ids)
                        if api_project_id:
                            tc_kwargs["api_project_id"] = api_project_id
                        # 保存断言规则
                        assertions = case_data.get("assertions")
                        if assertions:
                            tc_kwargs["assertions"] = assertions
                    else:
                        # 功能测试：纯文本步骤
                        tc_kwargs["expected_results"] = [
                            s.get("expected", "") for s in steps
                        ] if steps else []

                    # 准确性后处理（一致性 + 置信度），仅接口测试
                    if is_api_test and api_endpoint_ids and endpoint_index_to_id:
                        try:
                            report, score, level = _compute_accuracy(case_data, endpoint_index_to_id, self.db)
                            tc_kwargs["consistency_report"] = json.dumps(report, ensure_ascii=False)
                            tc_kwargs["confidence_score"] = score
                            tc_kwargs["confidence_level"] = level
                        except Exception as _acc_err:
                            logger.warning("[Pipeline] 准确性计算失败: %s", _acc_err)

                    tc = TestCase(**tc_kwargs)
                    self.db.add(tc)
                    all_test_cases.append(tc)

            self.db.commit()
            # 刷新获取 ID
            for tc in all_test_cases:
                self.db.refresh(tc)

            # 为接口测试用例创建 ApiScenario 并关联 scenario_id
            logger.info(
                f"[Pipeline] 场景创建检查: is_api_test={is_api_test}, "
                f"api_project_id={api_project_id}, test_cases={len(all_test_cases)}, "
                f"endpoint_index_to_id={endpoint_index_to_id}"
            )
            if is_api_test and api_project_id and all_test_cases:
                from db.models import ApiScenario, ApiEndpoint as ApiEndpointModel

                def _resolve_endpoint_id(step: dict) -> int | None:
                    """从步骤中提取 endpoint_id，支持 endpoint_ref（pipeline）和 endpoint_id（单次）两种格式。"""
                    eid = step.get("endpoint_id")
                    if eid:
                        return int(eid)
                    ref = step.get("endpoint_ref")
                    if ref and endpoint_index_to_id:
                        return endpoint_index_to_id.get(int(ref))
                    return None

                # 预加载接口信息
                # 识别 API 调用步骤：type == "api_call" 或 有 endpoint_id/endpoint_ref
                def _is_api_call_step(step: dict) -> bool:
                    if not isinstance(step, dict):
                        return False
                    return (
                        step.get("type") == "api_call"
                        or step.get("endpoint_id") is not None
                        or step.get("endpoint_ref") is not None
                    )

                all_endpoint_ids = set()
                for tc in all_test_cases:
                    for step in (tc.steps or []):
                        if _is_api_call_step(step):
                            eid = _resolve_endpoint_id(step)
                            if eid:
                                all_endpoint_ids.add(eid)
                    for pc in (tc.preset_conditions or []):
                        if _is_api_call_step(pc):
                            eid = _resolve_endpoint_id(pc)
                            if eid:
                                all_endpoint_ids.add(eid)

                logger.info(f"[Pipeline] 发现 {len(all_endpoint_ids)} 个关联接口: {all_endpoint_ids}")

                endpoint_map: dict[int, ApiEndpointModel] = {}
                if all_endpoint_ids:
                    rows = self.db.exec(
                        select(ApiEndpointModel).where(ApiEndpointModel.id.in_(list(all_endpoint_ids)))
                    ).all()
                    endpoint_map = {e.id: e for e in rows}

                scenarios_created = 0
                for tc in all_test_cases:
                    # 收集 API 调用步骤（前置条件 + 主步骤）
                    api_steps = []
                    for pc in (tc.preset_conditions or []):
                        if _is_api_call_step(pc) and _resolve_endpoint_id(pc):
                            api_steps.append(("preset", pc))
                    for step in (tc.steps or []):
                        if _is_api_call_step(step) and _resolve_endpoint_id(step):
                            api_steps.append(("step", step))

                    if not api_steps:
                        continue

                    scenario_steps = []
                    for step_type, s in api_steps:
                        step_copy = _normalize_pipeline_api_step(s, endpoint_index_to_id)
                        # 统一转为 endpoint_id 格式
                        eid = _resolve_endpoint_id(s)
                        if eid:
                            step_copy["endpoint_id"] = eid
                            step_copy.pop("endpoint_ref", None)
                        ep = endpoint_map.get(eid) if eid else None
                        if ep:
                            step_copy.setdefault("method", ep.method)
                            step_copy.setdefault("path", ep.path)
                            step_copy.setdefault("url", ep.url or ep.path)
                            step_copy.setdefault("endpoint_name", ep.name)
                            step_copy.setdefault("enabled", True)
                            step_copy.setdefault("continue_on_failure", True)
                        if step_type == "preset":
                            step_copy["is_preset"] = True
                        scenario_steps.append(step_copy)

                    scenario = ApiScenario(
                        project_id=api_project_id,
                        name=f"{tc.case_name}_场景",
                        description=f"测试用例 {tc.case_name} 的接口场景",
                        steps=scenario_steps,
                        user_id=user_id,
                    )
                    self.db.add(scenario)
                    self.db.flush()
                    tc.scenario_id = scenario.id
                    scenarios_created += 1

                self.db.commit()
                logger.info(f"[Pipeline] 已创建 {scenarios_created} 个场景，关联到 {scenarios_created} 个用例")

            self._update_progress(
                task_id, 3, "用例设计", 85,
                f"用例设计完成，共 {len(all_test_cases)} 个用例"
            )

            if stop_after_stage <= 3:
                self._update_progress(
                    task_id, 0, "完成", 100,
                    f"用例设计完成：{len(req_points)} 个需求点，{len(all_test_points)} 个测试点，{len(all_test_cases)} 个测试用例"
                )
                stage3_level_dist = check_level_distribution(task_id, 3, all_test_cases)
                self._complete_task(task_id, {
                    "requirement_points_count": len(req_points),
                    "test_points_count": len(all_test_points),
                    "test_cases_count": len(all_test_cases),
                    "mode": "stage3",
                    "level_distribution": stage3_level_dist,
                })
                return

            # ========== 阶段4: 用例优化 (85-100%) ==========
            if all_test_cases:
                self._update_progress(task_id, 4, "用例优化", 88, "正在优化测试用例...")

                try:
                    cases_for_opt = []
                    for tc in all_test_cases:
                        item = {
                            "id": tc.id,
                            "title": tc.case_name,
                            "description": "",
                            "preconditions": tc.preset_conditions or [],
                            "test_steps": tc.steps if tc.steps else [],
                        }
                        if is_api_test:
                            item["assertions"] = tc.assertions or []
                        cases_for_opt.append(item)

                    # 大批量下单次调用输出会被 max_tokens 截断，故按块分批优化
                    # （用例级冗余审查已独立为轻量调用，质量优化是逐用例的，可安全分片）
                    #
                    # B6-优化：批次并行化（对齐阶段3的 gather+Semaphore 写法）。
                    # 此前是 for 循环逐批串行 await：100 条用例 = 10 批串行等待，
                    # 与阶段3（同样场景已并发）不一致，纯属遗漏。
                    # 批次失败自动重试 1 次（瞬时 5xx/超时常见，重试可减少无产出）。
                    OPT_BATCH_SIZE = 10
                    OPT_MAX_RETRIES = 1
                    opt_map = {}
                    opt_failed_batches = 0
                    chunks = [
                        (i // OPT_BATCH_SIZE + 1, cases_for_opt[i:i + OPT_BATCH_SIZE])
                        for i in range(0, len(cases_for_opt), OPT_BATCH_SIZE)
                    ]
                    opt_sem = asyncio.Semaphore(_pipeline_concurrency())

                    async def _optimize_batch(chunk: list):
                        async with opt_sem:
                            return await self.optimizer.optimize(
                                chunk, is_api_test=is_api_test, api_context=api_context,
                            )

                    async def _run_batch(batch_no: int, chunk: list):
                        """执行一批，失败自动重试一次（瞬时 5xx/超时常见）。"""
                        try:
                            return await _optimize_batch(chunk)
                        except Exception as first_err:
                            if OPT_MAX_RETRIES < 1:
                                raise
                            logger.warning(
                                f"优化批次 {batch_no} 失败，自动重试 1 次: {first_err}"
                            )
                            try:
                                return await _optimize_batch(chunk)
                            except Exception as e:
                                raise e from first_err

                    async def _safe_run_batch(batch_no: int, chunk: list):
                        nonlocal opt_failed_batches
                        try:
                            chunk_optimized = await _run_batch(batch_no, chunk)
                        except Exception as e:
                            # P0：优化批次失败不再只是日志——登记 warning 并计数
                            logger.warning(
                                f"优化批次 {batch_no} 失败（含重试），跳过该批: {e}"
                            )
                            opt_failed_batches += 1
                            _add_warning(
                                task_id, 4, "optimization_batch_failed",
                                f"第 {batch_no} 批用例优化失败（已重试），该批用例保留优化前内容",
                                detail=(
                                    f"case_ids={[c.get('id') for c in chunk]}; "
                                    f"{type(e).__name__}: {e}"
                                ),
                            )
                            return
                        for opt in chunk_optimized:
                            if opt.get("id"):
                                opt_map[opt["id"]] = opt

                    await asyncio.gather(*[_safe_run_batch(no, ch) for no, ch in chunks])

                    # 应用优化结果
                    optimized_count = 0
                    for tc in all_test_cases:
                        opt = opt_map.get(tc.id)
                        if opt and opt.get("title"):
                            tc.case_name = opt["title"]
                            if opt.get("test_steps"):
                                opt_steps = opt["test_steps"]
                                # 优化器输出也是 endpoint_ref 格式，需转为 endpoint_id
                                if is_api_test and endpoint_index_to_id:
                                    converted_opt_steps = []
                                    for s in opt_steps:
                                        if isinstance(s, dict) and s.get("endpoint_ref"):
                                            s = dict(s)
                                            ref = s.pop("endpoint_ref", None)
                                            eid = endpoint_index_to_id.get(int(ref)) if ref else None
                                            if eid:
                                                s["endpoint_id"] = eid
                                        if isinstance(s, dict):
                                            s = _normalize_pipeline_api_step(s, endpoint_index_to_id)
                                        converted_opt_steps.append(s)
                                    opt_steps = converted_opt_steps
                                elif is_api_test:
                                    opt_steps = _normalize_pipeline_api_steps(opt_steps, endpoint_index_to_id)
                                tc.steps = opt_steps
                                if is_api_test:
                                    tc.expected_results = [
                                        s.get("description", "") for s in opt_steps
                                    ]
                                else:
                                    tc.expected_results = [
                                        s.get("expected", "") for s in opt_steps
                                    ]
                            if opt.get("preconditions"):
                                tc.preset_conditions = opt["preconditions"]
                            if is_api_test and opt.get("assertions"):
                                tc.assertions = opt["assertions"]
                            optimized_count += 1

                    if is_api_test and api_project_id and all_test_cases:
                        from db.models import ApiEndpoint as ApiEndpointModel, ApiScenario

                        all_endpoint_ids = _collect_pipeline_endpoint_ids(all_test_cases, endpoint_index_to_id)
                        endpoint_map: dict[int, ApiEndpointModel] = {}
                        if all_endpoint_ids:
                            rows = self.db.exec(
                                select(ApiEndpointModel).where(ApiEndpointModel.id.in_(list(all_endpoint_ids)))
                            ).all()
                            endpoint_map = {e.id: e for e in rows}

                        for tc in all_test_cases:
                            if not tc.scenario_id:
                                continue
                            scenario = self.db.get(ApiScenario, tc.scenario_id)
                            if not scenario:
                                continue
                            scenario.steps = _build_pipeline_scenario_steps(tc, endpoint_map, endpoint_index_to_id)
                            self.db.add(scenario)

                    self.db.commit()
                    logger.info(f"优化完成，成功优化 {optimized_count} 个用例")
                    if opt_failed_batches:
                        logger.warning(f"优化有 {opt_failed_batches} 个批次失败（已登记 warnings）")

                except Exception as e:
                    # P0：整阶段优化失败要可见（此前只记日志，任务照常显示"生成完成"）
                    logger.error(f"用例优化失败: {e}")
                    _add_warning(
                        task_id, 4, "optimization_stage_failed",
                        "用例优化阶段整体失败，所有用例保留优化前内容",
                        detail=f"{type(e).__name__}: {e}",
                    )

                # 用例级冗余审查（独立轻量 LLM 调用，可靠）：删除验证维度完全被其他用例覆盖的用例及其测试点（1:1 级联）
                if len(all_test_cases) > 1:
                    try:
                        def _tc_dedup_warn(code: str, message: str):
                            _add_warning(task_id, 4, code, message)

                        remove_ids = await _dedup_test_cases_by_llm(
                            all_test_cases, requirement_text, self.optimizer.model,
                            on_fallback=_tc_dedup_warn,
                        )
                    except Exception as e:
                        logger.warning("用例冗余审查异常，跳过删除: %s", e)
                        remove_ids = []
                    valid_ids = {tc.id for tc in all_test_cases}
                    to_remove = set(remove_ids) & valid_ids
                    # 防御：不删除全部用例（至少保留一条）
                    if to_remove and len(to_remove) < len(all_test_cases):
                        from db.models import TestPoint as _TP
                        for tc in all_test_cases:
                            if tc.id in to_remove and tc.test_point_id:
                                tp = self.db.get(_TP, tc.test_point_id)
                                if tp:
                                    _cascade_delete_test_point(self.db, tp)
                        self.db.commit()
                        all_test_cases = [
                            tc for tc in all_test_cases if tc.id not in to_remove
                        ]
                        logger.info(
                            f"用例级冗余审查：已移除 {len(to_remove)} 个重复用例"
                            f"（含其测试点），剩余 {len(all_test_cases)} 个用例"
                        )

            # ========== P3：执行验证闭环（生成 → 执行 → 按失败修复） ==========
            # 这是把"执行结果"从只有写侧变成可回灌的反馈信号的关键一步。
            # 仅接口测试有意义（需要 ApiScenario + ApiProject），且默认关闭（配置开关），
            # 避免在未配置执行环境时误发真实请求。
            verification_summary = None
            if is_api_test and api_project_id and all_test_cases:
                verification_summary = await self._run_verification_loop(
                    task_id,
                    all_test_cases,
                    api_project_id=api_project_id,
                    api_context=api_context,
                    endpoint_index_to_id=endpoint_index_to_id,
                    user_id=user_id,
                    session_id=session_id,
                )

            # P0：等级分布校验（提示词要求覆盖功能/边界/异常/场景，此前无任何代码校验）
            level_distribution = check_level_distribution(task_id, 3, all_test_cases)

            # B6：自动补齐必填字段的提示（让用户知道哪些值是占位符）
            if required_filled_total:
                _add_warning(
                    task_id, 3, "required_fields_autofilled",
                    f"自动补齐了 {required_filled_total} 处缺失的接口必填字段"
                    "（值为 Schema 派生占位值，建议核对或替换为真实数据）",
                    detail="由 request_schema.required 驱动，避免请求因缺字段直接失败",
                    severity="quality",
                )

            # B6：本次生成的质量指标（纯规则、零成本），随结果返回并登记低质量警告
            quality_metrics = self._evaluate_quality(task_id, all_test_cases)

            # B6-C：覆盖度审计——链路断裂（需求点无测试点/无用例）与方法覆盖不足
            # 让"漏分支"第一次变成机器可检测的问题
            coverage_summary_dict = None
            try:
                from app.services.coverage_audit import audit_requirement_coverage

                audit = audit_requirement_coverage(self.db, session_id=session_id)
                coverage_summary_dict = audit.to_dict()
                # 链路断裂（warning 级）：本次生成覆盖的需求点缺测试点/用例
                for gap in audit.gaps:
                    if gap.gap_type == "no_testpoints":
                        _add_warning(
                            task_id, 1, "coverage_no_testpoints",
                            f"需求点「{gap.requirement_content}」没有拆分出任何测试点",
                            severity="warning",
                        )
                    elif gap.gap_type == "no_testcases":
                        _add_warning(
                            task_id, 3, "coverage_no_testcases",
                            f"需求点「{gap.requirement_content}」的测试点均无用例",
                            severity="warning",
                        )
                    elif gap.gap_type == "method_gap":
                        missing_labels = ",".join(
                            m.replace("_", " ") for m in gap.detail.split(",") if m
                        )
                        _add_warning(
                            task_id, 2, "coverage_method_gap",
                            f"需求点「{gap.requirement_content}」缺少设计方法：{missing_labels}",
                            severity="quality",
                        )
            except Exception as cov_err:  # noqa: BLE001
                logger.warning("覆盖度审计失败（已忽略）: %s", cov_err)

            # 保存历史提示词（需求描述），仅整体生成成功时记录
            try:
                self._save_history_prompt(
                    task_id, session_id, module_id, user_id,
                    requirement_text, original_requirement,
                )
            except Exception as e:
                logger.warning(f"保存历史提示词失败（不影响主流程）: {e}")

            # 完成
            self._complete_task(task_id, {
                "requirement_points_count": len(req_points),
                "test_points_count": len(all_test_points),
                "test_cases_count": len(all_test_cases),
                "is_api_test": is_api_test,
                "mode": "auto",
                # P0 可见性指标：等级分布 + 优化失败批次数（前端可直接展示）
                "level_distribution": level_distribution,
                "optimization_failed_batches": opt_failed_batches if all_test_cases else 0,
                # P3：执行验证闭环的统计（None = 未启用或非接口测试）
                "verification": verification_summary,
                # B6：质量指标（零成本规则计算，可与基线快照对比）
                "quality": quality_metrics,
                "required_fields_autofilled": required_filled_total,
                # B6-C：覆盖度审计（链路完整率与方法缺口，None = 审计失败）
                "coverage": coverage_summary_dict,
            })

            logger.info(
                f"Pipeline 完成: {len(req_points)} 需求点, "
                f"{len(all_test_points)} 测试点, {len(all_test_cases)} 测试用例"
                f"{'(接口测试)' if is_api_test else '(功能测试)'}"
            )

        except Exception as e:
            logger.error(f"Pipeline 执行失败: {e}", exc_info=True)
            self._fail_task(task_id, str(e))

    # ===================== B6：质量指标 =====================

    def _evaluate_quality(self, task_id: str, all_test_cases: list) -> Optional[dict]:
        """计算本次生成的质量指标，并在明显不达标时登记警告（B6）。

        纯规则计算，零 API 成本；失败时返回 None，绝不影响主流程。
        """
        if not all_test_cases:
            return None
        try:
            from app.services.quality_metrics import (
                evaluate_testcases,
                summarize_metrics,
            )

            results = evaluate_testcases(self.db, all_test_cases)
            summary = summarize_metrics(results)
            logger.info(
                "质量指标：完整率 %.1f%%、结构规范率 %.1f%%、断言覆盖 %.1f%%、重复率 %.1f%%",
                summary["completeness_rate"] * 100,
                summary["structure_ok_rate"] * 100,
                summary["assertion_coverage_rate"] * 100,
                summary["duplicate_rate"] * 100,
            )

            # 明显不达标时登记质量提示（severity=quality，不改变任务状态）
            if summary["structure_ok_rate"] < 0.8:
                _add_warning(
                    task_id, 4, "low_structure_quality",
                    f"结构规范率仅 {summary['structure_ok_rate'] * 100:.0f}%（"
                    f"{len(results) - round(summary['structure_ok_rate'] * len(results))} 条用例"
                    "存在步骤缺描述/缺接口引用等结构问题）",
                    severity="quality",
                )
            if summary["duplicate_rate"] > 0.1:
                _add_warning(
                    task_id, 4, "high_duplicate_rate",
                    f"重复率 {summary['duplicate_rate'] * 100:.0f}%"
                    f"（{summary['duplicate_groups']} 组重复），建议检查去重效果",
                    severity="quality",
                )
            checked_count = summary.get("consistency_checked") or 0
            if checked_count and summary["consistency_pass_rate"] < 0.7:
                _add_warning(
                    task_id, 4, "low_schema_consistency",
                    f"接口用例与 Schema 一致性仅 "
                    f"{summary['consistency_pass_rate'] * 100:.0f}%"
                    f"（{checked_count} 条已检），可能存在必填字段缺失或字段越界",
                    severity="quality",
                )
            return summary
        except Exception as exc:  # noqa: BLE001 - 指标计算失败不得影响生成
            logger.warning("质量指标计算失败（已忽略）: %s", exc)
            return None

    # ===================== P3：执行验证闭环 =====================

    async def _run_verification_loop(
        self,
        task_id: str,
        all_test_cases: list,
        *,
        api_project_id: int,
        api_context: str,
        endpoint_index_to_id: Dict[int, int],
        user_id: Optional[str],
        session_id: Optional[int] = None,
        max_rounds: int = 1,
    ) -> Optional[dict]:
        """执行 → 失败归因 → 带失败信息重修复（P3 闭环）。

        流程：
          1. 对有 ApiScenario 的接口用例真实执行（`case_verifier.verify_batch`）；
          2. 把失败归因成结构化修复指令（只含"用例写得不对"的失败，环境类失败排除）；
          3. 把失败指令喂回优化器重修一次；
          4. 重修后再次执行，确认是否修复；
          5. 全部过程写入 `validation_status` / 执行日志，并在失败时登记 warnings。

        返回统计摘要；未启用（功能测试 / 无项目 / 配置关闭）时返回 None。
        """
        try:
            from app.services.case_verifier import (
                build_failure_feedback,
                summarize_verification,
                verify_batch,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("加载用例验证器失败（跳过 P3 闭环）: %s", exc)
            return None

        # 配置开关：默认关闭，避免在未配置执行环境时误发真实请求
        try:
            from config import config_manager

            if not config_manager.get_verification_enabled():
                logger.info("执行验证闭环未启用（配置开关关闭），跳过")
                return None
            max_rounds = max(0, min(3, config_manager.get_verification_max_rounds()))
            read_only = config_manager.get_verification_read_only()
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取执行验证配置失败（跳过 P3 闭环）: %s", exc)
            return None

        # 记录 session_id（取会话级请求头用）
        self._verification_session_id = session_id

        # 只验证有场景的用例
        candidates = [tc for tc in all_test_cases if getattr(tc, "scenario_id", None)]
        if not candidates:
            logger.info("没有关联接口场景的用例，跳过执行验证闭环")
            return None

        self._update_progress(
            task_id, 4, "执行验证", 92,
            f"正在执行 {len(candidates)} 条接口用例进行验证...",
        )

        session_headers = self._resolve_session_headers()
        results = await verify_batch(
            self.db, candidates, session_headers=session_headers,
            read_only=read_only,
        )
        summary = summarize_verification(results)
        logger.info(
            "执行验证完成：通过 %d / 执行 %d（跳过 %d，失败 %d）",
            summary["passed"], summary["executed"], summary["skipped"], summary["failed"],
        )

        # 回写验证状态与执行日志（让"执行结果"真正落到用例上，可被前端查看）
        self._persist_verification_results(results, candidates)

        # 失败登记 warning（部分失败不能被伪装成完全成功）
        if summary["failed"] or summary["error"]:
            _add_warning(
                task_id, 4, "verification_failed",
                f"{summary['failed'] + summary['error']} 条接口用例在真实执行中失败",
                detail=(
                    f"passed={summary['passed']}/{summary['executed']}; "
                    f"causes={sorted({r.cause for r in results if not r.passed})}"
                ),
            )

        if summary["failed"] == 0 and summary["error"] == 0:
            return summary
        if max_rounds <= 0:
            return summary

        # ---- 重修复一轮 ----
        names = {tc.id: tc.case_name for tc in candidates}
        feedback = build_failure_feedback(results, case_names=names)
        if not feedback:
            logger.info("失败均为环境类问题（连不上/超时），不做自动重修复")
            return summary

        self._update_progress(
            task_id, 4, "执行验证", 95,
            f"正在依据 {summary['fixable']} 条执行失败修正用例...",
        )

        try:
            fixed = await self._repair_failed_cases(
                task_id,
                all_test_cases,
                feedback,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("执行失败重修复异常（已忽略）: %s", exc)
            fixed = 0

        if fixed:
            # 重新执行，确认是否真的修复
            self._update_progress(
                task_id, 4, "执行验证", 97, "重新执行验证修复结果...",
            )
            recheck_candidates = [tc for tc in all_test_cases if getattr(tc, "scenario_id", None)]
            recheck = await verify_batch(
                self.db, recheck_candidates, session_headers=session_headers,
                read_only=read_only,
            )
            self._persist_verification_results(recheck, recheck_candidates)
            recheck_summary = summarize_verification(recheck)
            summary["repair_rounds"] = 1
            summary["repaired_cases"] = fixed
            summary["after_repair"] = recheck_summary
            logger.info(
                "重修复后：通过 %d / 执行 %d（原通过 %d）",
                recheck_summary["passed"], recheck_summary["executed"], summary["passed"],
            )

        return summary

    def _resolve_session_headers(self) -> list:
        """取会话级请求头（执行接口用例时需要携带鉴权头）。"""
        try:
            session_id = getattr(self, "_verification_session_id", None)
            if not session_id:
                return []
            from app.routes.testcase import _session_api_overrides

            overrides = _session_api_overrides(self.db, session_id)
            return overrides.get("headers") or []
        except Exception:  # noqa: BLE001
            return []

    def _persist_verification_results(self, results: list, testcases: list) -> None:
        """把验证结果写回用例（validation_status / status）与执行日志。"""
        by_id = {getattr(tc, "id", None): tc for tc in testcases or []}
        for r in results or []:
            tc = by_id.get(r.testcase_id)
            if tc is None:
                continue
            try:
                if r.status == "skipped":
                    # 跳过的用例不覆盖已有状态
                    continue
                tc.validation_status = "passed" if r.passed else "failed"
                tc.validation_detail = json.dumps(r.to_dict(), ensure_ascii=False)
                tc.validation_at = datetime.now()
                tc.status = "PASSED" if r.passed else "FAILED"
                self.db.add(tc)
            except Exception as exc:  # noqa: BLE001
                logger.debug("回写验证结果失败（已忽略）: %s", exc)
        try:
            self.db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("提交验证结果失败（已忽略）: %s", exc)

    async def _repair_failed_cases(
        self,
        task_id: str,
        all_test_cases: list,
        feedback: str,
        *,
        api_context: str,
        endpoint_index_to_id: Dict[int, int],
    ) -> int:
        """把执行失败明细喂回优化器，重修这些用例。返回重修的用例数。"""
        items = []
        target_ids = set()
        for tc in all_test_cases:
            if not getattr(tc, "scenario_id", None):
                continue
            item = {
                "id": tc.id,
                "title": tc.case_name,
                "description": "",
                "preconditions": tc.preset_conditions or [],
                "test_steps": tc.steps if tc.steps else [],
                "assertions": tc.assertions or [],
            }
            items.append(item)
            target_ids.add(tc.id)

        if not items:
            return 0

        OPT_BATCH_SIZE = 10
        repaired = 0
        for i in range(0, len(items), OPT_BATCH_SIZE):
            chunk = items[i:i + OPT_BATCH_SIZE]
            try:
                optimized = await self.optimizer.optimize(
                    chunk,
                    is_api_test=True,
                    api_context=api_context,
                    failure_feedback=feedback,
                )
            except Exception as e:
                logger.warning("执行失败重修复批次失败（已忽略）: %s", e)
                _add_warning(
                    task_id, 4, "verification_repair_failed",
                    f"依据执行失败重修复第 {i // OPT_BATCH_SIZE + 1} 批失败",
                    detail=f"{type(e).__name__}: {e}",
                )
                continue

            opt_map = {o.get("id"): o for o in optimized if o.get("id")}
            for tc in all_test_cases:
                opt = opt_map.get(tc.id)
                if not opt:
                    continue
                if opt.get("title"):
                    tc.case_name = opt["title"]
                if opt.get("test_steps"):
                    tc.steps = _normalize_pipeline_api_steps(
                        opt["test_steps"], endpoint_index_to_id
                    )
                    tc.expected_results = [
                        s.get("description", "") if isinstance(s, dict) else str(s)
                        for s in tc.steps
                    ]
                if opt.get("preconditions"):
                    tc.preset_conditions = opt["preconditions"]
                if opt.get("assertions"):
                    tc.assertions = opt["assertions"]
                self.db.add(tc)
                repaired += 1

        try:
            self.db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("提交重修复结果失败（已忽略）: %s", exc)
        return repaired

    # ===================== 复用：用例构造 + 场景同步 =====================

    def _build_testcase_kwargs(
        self,
        case_data: dict,
        *,
        test_point_id: int,
        session_id: int,
        requirement_content: str,
        is_api_test: bool,
        api_context: str,
        endpoint_index_to_id: Dict[int, int],
        api_project_id: Optional[int],
        api_endpoint_ids: Optional[List[int]],
        module_id: Optional[int],
        user_id: Optional[str],
        test_type: Optional[str] = None,
        design_method: Optional[str] = None,
        priority: Optional[str] = "medium",
    ) -> dict:
        """从 designer 输出的单条 case_data 构造 TestCase 构造参数（不含 id）。"""
        steps = case_data.get("test_steps", [])

        if is_api_test and isinstance(steps, list):
            converted = []
            for s in steps:
                if isinstance(s, dict):
                    s = dict(s)
                    if s.get("endpoint_id") or s.get("endpoint_ref"):
                        if not s.get("type"):
                            s["type"] = "api_call"
                    if s.get("endpoint_ref") and endpoint_index_to_id:
                        ref = s.pop("endpoint_ref", None)
                        eid = endpoint_index_to_id.get(int(ref)) if ref else None
                        if eid:
                            s["endpoint_id"] = eid
                converted.append(s)
            steps = converted
            steps = _normalize_pipeline_api_steps(steps, endpoint_index_to_id)

        tc_kwargs = dict(
            case_name=case_data.get("title", ""),
            steps=steps if isinstance(steps, list) else [],
            preset_conditions=case_data.get("preconditions", []) or [],
            case_level=case_data.get("case_level", 1),
            session_id=session_id,
            module_id=module_id,
            test_point_id=test_point_id,
            test_type=case_data.get("test_type") or test_type,
            design_method=case_data.get("design_method") or design_method,
            user_id=user_id,
        )

        if is_api_test:
            tc_kwargs["expected_results"] = [
                s.get("description", "") if isinstance(s, dict) else str(s)
                for s in steps
            ] if steps else []
            if api_endpoint_ids:
                tc_kwargs["api_endpoint_id"] = ",".join(str(eid) for eid in api_endpoint_ids)
            if api_project_id:
                tc_kwargs["api_project_id"] = api_project_id
            assertions = case_data.get("assertions")
            if assertions:
                tc_kwargs["assertions"] = assertions
        else:
            tc_kwargs["expected_results"] = [
                s.get("expected", "") for s in steps
            ] if steps else []

        return tc_kwargs

    def _sync_api_scenarios(
        self,
        test_cases: list,
        *,
        api_project_id: int,
        endpoint_index_to_id: Dict[int, int],
        user_id: Optional[str],
    ) -> None:
        """为 test_cases 中带 API 调用步骤的用例创建/更新 ApiScenario，并回写 scenario_id。"""
        from db.models import ApiScenario, ApiEndpoint as ApiEndpointModel

        all_endpoint_ids = _collect_pipeline_endpoint_ids(test_cases, endpoint_index_to_id)
        endpoint_map: dict[int, ApiEndpointModel] = {}
        if all_endpoint_ids:
            rows = self.db.exec(
                select(ApiEndpointModel).where(ApiEndpointModel.id.in_(list(all_endpoint_ids)))
            ).all()
            endpoint_map = {e.id: e for e in rows}

        for tc in test_cases:
            api_steps = []
            for pc in (tc.preset_conditions or []):
                if _is_pipeline_api_call_step(pc) and _resolve_pipeline_endpoint_id(pc, endpoint_index_to_id):
                    api_steps.append(("preset", pc))
            for step in (tc.steps or []):
                if _is_pipeline_api_call_step(step) and _resolve_pipeline_endpoint_id(step, endpoint_index_to_id):
                    api_steps.append(("step", step))
            if not api_steps:
                continue

            scenario_steps = []
            for step_type, s in api_steps:
                step_copy = _normalize_pipeline_api_step(s, endpoint_index_to_id)
                eid = _resolve_pipeline_endpoint_id(s, endpoint_index_to_id)
                if eid:
                    step_copy["endpoint_id"] = eid
                    step_copy.pop("endpoint_ref", None)
                ep = endpoint_map.get(eid) if eid else None
                if ep:
                    step_copy.setdefault("method", ep.method)
                    step_copy.setdefault("path", ep.path)
                    step_copy.setdefault("url", ep.url or ep.path)
                    step_copy.setdefault("endpoint_name", ep.name)
                    step_copy.setdefault("enabled", True)
                    step_copy.setdefault("continue_on_failure", True)
                if step_type == "preset":
                    step_copy["is_preset"] = True
                scenario_steps.append(step_copy)

            if tc.scenario_id:
                scenario = self.db.get(ApiScenario, tc.scenario_id)
                if scenario:
                    scenario.steps = scenario_steps
                    scenario.name = f"{tc.case_name}_场景"
                    self.db.add(scenario)
                    continue
            scenario = ApiScenario(
                project_id=api_project_id,
                name=f"{tc.case_name}_场景",
                description=f"测试用例 {tc.case_name} 的接口场景",
                steps=scenario_steps,
                user_id=user_id,
            )
            self.db.add(scenario)
            self.db.flush()
            tc.scenario_id = scenario.id

    # ===================== 就地更新：用例 =====================

    async def _inplace_update_cases(
        self,
        test_point,
        requirement_content: str,
        *,
        is_api_test: bool,
        api_context: str = "",
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_project_id: Optional[int] = None,
        api_endpoint_ids: Optional[List[int]] = None,
        module_id: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """
        用 LLM 为一个测试点重设计用例，并就地更新其下已有关联用例（去重、不重复）。

        Returns: 该测试点下最终用例数量。
        """
        from db.models import TestCase

        endpoint_index_to_id = endpoint_index_to_id or {}

        # 1) 调 LLM 生成（design_batch 强制 len == 1，缺失返回 {}）
        cases_data = await self.tc_designer.design_batch(
            [{
                "id": test_point.id,
                "content": test_point.content,
                "test_type": test_point.test_type,
                "design_method": test_point.design_method,
                "priority": test_point.priority,
            }],
            requirement_content,
            is_api_test=is_api_test,
            api_context=api_context,
        )
        generated = cases_data[0] if cases_data else {}

        # 2) 读取该测试点下已有关联用例（按 id 稳定顺序）
        existing: list = self.db.exec(
            select(TestCase).where(TestCase.test_point_id == test_point.id).order_by(TestCase.id)
        ).all()

        # 3) 按位置匹配
        to_update, to_create, to_delete = merge_entities_by_position(existing, [generated])

        # 4) 删除多余旧用例（同时清理其孤儿场景）
        for old in to_delete:
            if old.scenario_id:
                from db.models import ApiScenario
                sc = self.db.get(ApiScenario, old.scenario_id)
                if sc:
                    self.db.delete(sc)
            self.db.delete(old)

        # 5) 更新已有（保留 id）
        for old, gen in to_update:
            kwargs = self._build_testcase_kwargs(
                gen,
                test_point_id=test_point.id,
                session_id=test_point.session_id,
                requirement_content=requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
                test_type=test_point.test_type,
                design_method=test_point.design_method,
                priority=test_point.priority,
            )
            for k, v in kwargs.items():
                setattr(old, k, v)
            self.db.add(old)

        # 6) 新建缺失
        created: list = []
        for gen in to_create:
            kwargs = self._build_testcase_kwargs(
                gen,
                test_point_id=test_point.id,
                session_id=test_point.session_id,
                requirement_content=requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
                test_type=test_point.test_type,
                design_method=test_point.design_method,
                priority=test_point.priority,
            )
            tc = TestCase(**kwargs)
            self.db.add(tc)
            created.append(tc)

        kept_existing = [old for old, _ in to_update]
        self.db.commit()
        for tc in (kept_existing + created):
            self.db.refresh(tc)

        # 7) 接口测试：为所有（更新+新建）用例同步 ApiScenario
        # 仅包含保留下来的用例，避免为已删除用例生成孤儿场景
        all_cases = [t for t in (kept_existing + created) if not _is_empty_case(t)]
        if is_api_test and api_project_id and all_cases:
            self._sync_api_scenarios(
                all_cases,
                api_project_id=api_project_id,
                endpoint_index_to_id=endpoint_index_to_id,
                user_id=user_id,
            )
            self.db.commit()

        return len(all_cases)

    # ===================== 就地更新：测试点（级联到用例） =====================

    async def _inplace_update_test_points(
        self,
        requirement_point,
        requirement_content: str,
        *,
        is_api_test: bool,
        api_context: str = "",
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_project_id: Optional[int] = None,
        api_endpoint_ids: Optional[List[int]] = None,
        module_id: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> list:
        """
        用 LLM 为需求点重生成测试点，并就地更新其下已有关联测试点；
        每个（更新/新建的）测试点再就地重生成用例（一步到位）。
        返回最终测试点列表。
        """
        from db.models import TestPoint

        endpoint_index_to_id = endpoint_index_to_id or {}

        tp_data_list = await self.tp_generator.generate(
            requirement_point.content,
            is_api_test=is_api_test,
            api_context=api_context,
        )

        existing: list = self.db.exec(
            select(TestPoint)
            .where(TestPoint.requirement_point_id == requirement_point.id)
            .order_by(TestPoint.id)
        ).all()

        to_update, to_create, to_delete = merge_entities_by_position(existing, tp_data_list)

        # 删除多余旧测试点（级联删其用例 + 孤儿场景）
        for old in to_delete:
            _cascade_delete_test_point(self.db, old)

        # 更新已有测试点（保留 id）
        for old, gen in to_update:
            old.content = gen.get("content", old.content)
            old.test_type = gen.get("test_type", old.test_type)
            old.design_method = gen.get("design_method", old.design_method)
            old.priority = gen.get("priority", old.priority)
            old.api_project_id = api_project_id
            old.api_endpoint_ids = ",".join(str(e) for e in api_endpoint_ids) if api_endpoint_ids else None
            self.db.add(old)

        # 新建缺失测试点
        created: list = []
        for gen in to_create:
            tp = TestPoint(
                requirement_point_id=requirement_point.id,
                session_id=requirement_point.session_id,
                content=gen.get("content", ""),
                test_type=gen.get("test_type", "functional"),
                design_method=gen.get("design_method", ""),
                priority=gen.get("priority", "medium"),
                user_id=user_id,
                api_project_id=api_project_id,
                api_endpoint_ids=",".join(str(e) for e in api_endpoint_ids) if api_endpoint_ids else None,
            )
            self.db.add(tp)
            created.append(tp)

        kept_existing = [old for old, _ in to_update]
        self.db.commit()
        for tp in (kept_existing + created):
            self.db.refresh(tp)

        # 一步到位：为每个测试点就地重生成用例
        # 仅对保留下来的测试点执行，避免为已删除测试点生成孤儿用例
        kept_existing = [old for old, _ in to_update]
        final_tps = [t for t in (kept_existing + created) if not _is_empty_tp(t)]
        for tp in final_tps:
            await self._inplace_update_cases(
                tp,
                requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
            )
        return final_tps

    # ===================== 重生成入口（异步任务包装） =====================

    def _resolve_api_context(self, api_project_id, api_endpoint_ids_str):
        """根据测试点/需求点上的 api 字段重建接口上下文。"""
        from app.services.api_context import build_api_context, parse_endpoint_ids
        from app.routes.testcase import infer_endpoint_dependencies

        api_endpoint_ids = parse_endpoint_ids(api_endpoint_ids_str) if api_endpoint_ids_str else []
        is_api_test = bool(api_endpoint_ids and api_project_id)
        endpoint_index_to_id: Dict[int, int] = {}
        api_context = ""
        if is_api_test:
            extra = infer_endpoint_dependencies(self.db, api_endpoint_ids, api_project_id)
            for eid in extra:
                if eid not in api_endpoint_ids:
                    api_endpoint_ids.append(eid)
            api_context, endpoint_index_to_id = build_api_context(
                self.db, api_endpoint_ids, api_project_id, {},
            )
        return is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids

    async def regenerate_test_point(
        self,
        task_id: str,
        test_point_id: int,
        user_id: Optional[str] = None,
        *,
        module_id: Optional[int] = None,
    ) -> None:
        """重生成单个测试点的用例（就地更新）。"""
        from db.db import engine
        from sqlmodel import Session as DBSession
        from db.models import TestPoint
        try:
            with DBSession(engine) as session:
                self.db = session
                tp = session.get(TestPoint, test_point_id)
                if not tp:
                    raise ValueError("测试点不存在")

                is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids = \
                    self._resolve_api_context(tp.api_project_id, tp.api_endpoint_ids)

                requirement_content = tp.content  # 测试点级用测试点内容作为上下文
                count = await self._inplace_update_cases(
                    tp,
                    requirement_content,
                    is_api_test=is_api_test,
                    api_context=api_context,
                    endpoint_index_to_id=endpoint_index_to_id,
                    api_project_id=api_project_id,
                    api_endpoint_ids=api_endpoint_ids,
                    module_id=module_id,
                    user_id=user_id,
                )
                self._complete_task(task_id, {
                    "test_point_id": test_point_id,
                    "cases_count": count,
                    "is_api_test": is_api_test,
                })
        except Exception as e:
            logger.error(f"重生成测试点用例失败: {e}", exc_info=True)
            self._fail_task(task_id, str(e))

    async def regenerate_requirement_point(
        self,
        task_id: str,
        requirement_point_id: int,
        user_id: Optional[str] = None,
        *,
        module_id: Optional[int] = None,
    ) -> None:
        """重生成需求点下的测试点（一步到位级联到用例）。"""
        from db.db import engine
        from sqlmodel import Session as DBSession
        from db.models import RequirementPoint
        try:
            with DBSession(engine) as session:
                self.db = session
                rp = session.get(RequirementPoint, requirement_point_id)
                if not rp:
                    raise ValueError("需求点不存在")

                # 未显式传入时继承需求点已有的 module_id
                if module_id is None:
                    module_id = rp.module_id

                is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids = \
                    self._resolve_api_context(rp.api_project_id, rp.api_endpoint_ids)

                final_tps = await self._inplace_update_test_points(
                    rp,
                    rp.content,
                    is_api_test=is_api_test,
                    api_context=api_context,
                    endpoint_index_to_id=endpoint_index_to_id,
                    api_project_id=api_project_id,
                    api_endpoint_ids=api_endpoint_ids,
                    module_id=module_id,
                    user_id=user_id,
                )
                self._complete_task(task_id, {
                    "requirement_point_id": requirement_point_id,
                    "test_points_count": len(final_tps),
                    "is_api_test": is_api_test,
                })
        except Exception as e:
            logger.error(f"重生成需求点测试点失败: {e}", exc_info=True)
            self._fail_task(task_id, str(e))

    async def batch_generate_cases(
        self,
        task_id: str,
        session_id: int,
        user_id: Optional[str] = None,
        *,
        module_id: Optional[int] = None,
    ) -> None:
        """批量为所有测试点生成用例（Stage 3）。"""
        from db.db import engine
        from sqlmodel import Session as DBSession
        from db.models import TestPoint, RequirementPoint
        try:
            with DBSession(engine) as session:
                self.db = session
                # 查询所有测试点（按需求点排序）
                stmt = (
                    select(TestPoint)
                    .join(RequirementPoint, TestPoint.requirement_point_id == RequirementPoint.id)
                    .where(RequirementPoint.session_id == session_id)
                )
                if module_id is not None:
                    stmt = stmt.where(RequirementPoint.module_id == module_id)
                test_points = list(self.db.exec(stmt).all())

                if not test_points:
                    self._complete_task(task_id, {
                        "test_points_count": 0,
                        "cases_count": 0,
                        "mode": "batch",
                    })
                    return

                total = len(test_points)
                total_cases = 0
                for i, tp in enumerate(test_points):
                    self._update_progress(
                        task_id, 3, "用例设计",
                        int(50 + (i / total) * 35),
                        f"正在为测试点生成用例... ({i + 1}/{total})"
                    )
                    is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids = \
                        self._resolve_api_context(tp.api_project_id, tp.api_endpoint_ids)
                    count = await self._inplace_update_cases(
                        tp,
                        tp.content,
                        is_api_test=is_api_test,
                        api_context=api_context,
                        endpoint_index_to_id=endpoint_index_to_id,
                        api_project_id=api_project_id,
                        api_endpoint_ids=api_endpoint_ids,
                        module_id=module_id,
                        user_id=user_id,
                    )
                    total_cases += count

                self._update_progress(
                    task_id, 3, "用例设计", 85,
                    f"用例生成完成，共 {total_cases} 个测试用例"
                )
                self._complete_task(task_id, {
                    "test_points_count": total,
                    "cases_count": total_cases,
                    "mode": "batch",
                })
        except Exception as e:
            logger.error(f"批量生成用例失败: {e}", exc_info=True)
            self._fail_task(task_id, str(e))
