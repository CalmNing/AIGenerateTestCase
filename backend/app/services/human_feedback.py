"""B7：人工反馈信号回流——让生成器"看到"人工审阅的结果。

数据来源（两条通道）：
- 正样本：**执行通过的用例**（status=PASSED / validation_status=passed，
  被真实接口验证过，客观证据最强）优先，人工编辑过的用例（human_edited）次之
  （外部证据 CLAST/ASE'25：高质量 few-shot 使通过率 +28%）；
- 负样本：reject_reason 非空的用例 —— 驳回理由构成"生成时规避清单"。

两个入口：
1. get_positive_examples()：挑同模块（或同会话）的人工正样本，注入生成 prompt
   （few-shot：人工改过的用例比 LLM 自产的更能代表"好"的标准）；
2. get_rejection_guidance()：汇总近期驳回理由的分布，注入生成 prompt
   （"以下问题在本项目中出现过，请特别避免"）。

设计原则：
- 数据不足时返回空串（不硬凑）；
- 任何失败静默降级，绝不影响生成主流程；
- 示例做字符截断，防止正样本本身过长撑爆预算。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# few-shot 示例最多几条（太多会挤占预算，且边际收益递减）
MAX_EXAMPLES = 3
# 单条示例的最大字符数
MAX_EXAMPLE_CHARS = 500
# 驳回清单最多展示几类
MAX_REJECT_TYPES = 5

# 正样本数量阈值：低于此值时相关性检索只会引入噪声，退回来源优先级
POSITIVE_FALLBACK_THRESHOLD = 8

# 驳回理由的可读文案（与 routes/testcase.py 的 REJECT_REASONS 对齐）
REJECT_LABELS = {
    "duplicate": "与已有用例重复",
    "vague_steps": "步骤笼统不可执行",
    "unverifiable_expect": "预期结果不可验证",
    "missing_branch": "遗漏分支/场景",
    "fact_error": "事实/业务错误",
}


def get_positive_examples(db, *, module_id=None, session_id=None, limit=MAX_EXAMPLES):
    """取高质量用例做 few-shot 正样本。

    正样本的优先级（用户 2026-11 确认的口径）：
      1. **执行通过的用例**（status=PASSED / validation_status=passed）
         —— 被（用户确认）真实接口/系统验证过，是客观证据最强的"好"；
      2. 人工编辑过的用例（human_edited=True）—— 人工判断的"好"，次之。

    优先同 module，不足时放宽到同 session。
    返回 [{case_name, preset_conditions, steps, expected_results, case_level}]
    """
    if db is None:
        return []
    try:
        from db.models import TestCase
        from sqlmodel import select, or_, desc

        def _query(extra):
            stmt = select(TestCase).where(
                or_(
                    TestCase.status == "PASSED",
                    TestCase.validation_status == "passed",
                    TestCase.human_edited.is_(True),
                )
            )
            if extra:
                stmt = stmt.where(extra)
            return stmt

        results: list = []
        # 1) 同模块：执行通过优先，其次人工编辑
        if module_id is not None:
            rows = db.exec(
                _query(TestCase.module_id == module_id)
                .order_by(desc(TestCase.status), desc(TestCase.reviewed_at))
                .limit(limit * 2)
            ).all()
            results = [
                r for r in rows
                if r.status == "PASSED" or r.validation_status == "passed"
            ][:limit]
            if len(results) < limit:
                seen = {r.id for r in results}
                results.extend(
                    r for r in rows
                    if r.id not in seen and r.human_edited
                )
                results = results[:limit]
        # 2) 兜底：放宽到同会话
        if len(results) < limit and session_id is not None:
            seen = {r.id for r in results}
            rows = db.exec(
                _query(TestCase.session_id == session_id)
                .order_by(desc(TestCase.status), desc(TestCase.reviewed_at))
                .limit(limit * 2)
            ).all()
            for r in rows:
                if r.id in seen:
                    continue
                results.append(r)
                if len(results) >= limit:
                    break

        return [
            {
                "case_name": r.case_name,
                "preset_conditions": r.preset_conditions or [],
                "steps": r.steps or [],
                "expected_results": r.expected_results or [],
                "case_level": r.case_level,
            }
            for r in results
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("获取正样本失败（已忽略）: %s", exc)
        return []


def get_rejection_guidance(db, *, module_id=None, session_id=None):
    """汇总近期驳回理由 → "规避清单"文本（空数据返回空串）。"""
    if db is None:
        return ""
    try:
        from db.models import TestCase
        from sqlmodel import select

        rows = []
        if module_id is not None:
            rows = db.exec(
                select(TestCase.reject_reason)
                .where(
                    TestCase.reject_reason.is_not(None),
                    TestCase.module_id == module_id,
                )
                .limit(50)
            ).all()
        if not rows and session_id is not None:
            rows = db.exec(
                select(TestCase.reject_reason)
                .where(
                    TestCase.reject_reason.is_not(None),
                    TestCase.session_id == session_id,
                )
                .limit(50)
            ).all()
        if not rows:
            return ""

        from collections import Counter

        counter = Counter()
        for raw in rows:
            if not raw:
                continue
            # reject_reason 形如 "duplicate" 或 "duplicate: 与xx重复"
            code = str(raw).split(":", 1)[0].strip()
            counter[code] += 1

        if not counter:
            return ""
        parts = [
            "- %s（%d 次）" % (REJECT_LABELS.get(code, code), n)
            for code, n in counter.most_common(MAX_REJECT_TYPES)
        ]
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("汇总驳回理由失败（已忽略）: %s", exc)
        return ""


def get_relevant_positive_examples(
    db,
    *,
    query: str,
    module_id=None,
    session_id=None,
    limit=MAX_EXAMPLES,
) -> list:
    """按需求相关性召回高质量正样本（few-shot 语义检索）。

    与 get_positive_examples 的区别：
    - get_positive_examples：按"来源优先级"取最新的 N 条（无相关性概念）；
    - 本函数：把正样本索引进 FTS5，按与本次需求的**文本相关性**召回——
      正样本多于阈值（POSITIVE_FALLBACK_THRESHOLD）时用语义检索，
      少时退回来源优先级（此时检索只会引入噪声）。

    正样本判定：status=PASSED / validation_status=passed / human_edited=True。
    """
    if db is None or not (query or "").strip():
        return []
    try:
        from db.models import TestCase
        from sqlmodel import select, or_

        rows = db.exec(
            select(TestCase).where(
                or_(
                    TestCase.status == "PASSED",
                    TestCase.validation_status == "passed",
                    TestCase.human_edited.is_(True),
                )
            )
        ).all()
        if not rows:
            return []

        # 样本少：直接用来源优先级取最新（检索只引入噪声）
        if len(rows) <= POSITIVE_FALLBACK_THRESHOLD:
            return get_positive_examples(
                db, module_id=module_id, session_id=session_id, limit=limit,
            )

        # 样本充足：FTS5 相关性召回
        from utils.retrieval import get_index

        idx = get_index()
        if idx is None:
            return get_positive_examples(
                db, module_id=module_id, session_id=session_id, limit=limit,
            )

        # 正样本在**内存中**临时建倒排做相关性匹配，不写入共享索引——
        # 否则 few-shot 文档（大 rowid）会占据「最近索引的文档」列表，
        # 且污染历史需求的索引统计（用户在 RAG 观测页发现过此问题）。
        from utils.token_budget import estimate_tokens  # noqa: F401 - 保持导入一致性
        import re as _re

        def _tokens(text: str) -> list:
            lowered = (text or "").lower()
            out: list = []
            for run in _re.findall(r"[\u4e00-\u9fff]+", lowered):
                if len(run) == 1:
                    out.append(run)
                else:
                    out.extend(run[i:i + 2] for i in range(len(run) - 1))
            out.extend(_re.findall(r"[a-z0-9_]{2,}", lowered))
            return out

        query_tokens = set(_tokens(query))
        if not query_tokens:
            return get_positive_examples(
                db, module_id=module_id, session_id=session_id, limit=limit,
            )

        scored = []
        for r in rows:
            text = " ".join([
                str(r.case_name or ""),
                " ".join(str(x) for x in (r.expected_results or [])[:3]),
                " ".join(str(x) for x in (r.steps or [])[:2] if isinstance(x, (str, dict))),
            ])
            toks = set(_tokens(text[:2000]))
            overlap = len(query_tokens & toks)
            if overlap > 0:
                scored.append((overlap, r))

        scored.sort(key=lambda x: -x[0])
        hits_rows = [r for _, r in scored[:limit]]
        if not hits_rows:
            return get_positive_examples(
                db, module_id=module_id, session_id=session_id, limit=limit,
            )

        return [
            {
                "case_name": r.case_name,
                "preset_conditions": r.preset_conditions or [],
                "steps": r.steps or [],
                "expected_results": r.expected_results or [],
                "case_level": r.case_level,
            }
            for r in hits_rows
        ]
        if not hits:
            return get_positive_examples(
                db, module_id=module_id, session_id=session_id, limit=limit,
            )

        by_id = {int(r.id): r for r in rows}
        out: list = []
        seen: set = set()
        for h in hits:
            tc_id = h.row_id - EXAMPLE_ID_OFFSET
            if tc_id in seen or tc_id not in by_id:
                continue
            seen.add(tc_id)
            r = by_id[tc_id]
            out.append({
                "case_name": r.case_name,
                "preset_conditions": r.preset_conditions or [],
                "steps": r.steps or [],
                "expected_results": r.expected_results or [],
                "case_level": r.case_level,
            })
            if len(out) >= limit:
                break
        # 检索无命中时兜底
        if not out:
            return get_positive_examples(
                db, module_id=module_id, session_id=session_id, limit=limit,
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("相关性示例召回失败（降级为来源优先级）: %s", exc)
        return get_positive_examples(
            db, module_id=module_id, session_id=session_id, limit=limit,
        )


def format_examples_for_prompt(examples, *, max_chars=MAX_EXAMPLE_CHARS):
    """把正样本格式化为 prompt 注入文本（空列表返回空串）。"""
    if not examples:
        return ""
    lines = [
        "## 高质量示例（以下用例经真实执行验证通过或人工审阅修正，代表本项目的质量标准，请对齐其风格与粒度）",
        "",
    ]
    for i, ex in enumerate(examples, 1):
        try:
            body = json.dumps(ex, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            continue
        if len(body) > max_chars:
            body = body[:max_chars] + "…"
        lines.append("### 示例 %d" % i)
        lines.append("```json")
        lines.append(body)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def format_rejection_for_prompt(guidance):
    """把驳回规避清单格式化为 prompt 注入文本（空输入返回空串）。"""
    if not guidance or not guidance.strip():
        return ""
    return (
        "## 本项目已出现过的用例质量问题（请特别避免）\n"
        + guidance
        + "\n"
    )