"""B7：人工反馈信号回流——让生成器"看到"人工审阅的结果。

数据来源（两条通道）：
- 正样本：human_edited=True 的用例 —— 人工修过的内容是高质量示例
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

# 驳回理由的可读文案（与 routes/testcase.py 的 REJECT_REASONS 对齐）
REJECT_LABELS = {
    "duplicate": "与已有用例重复",
    "vague_steps": "步骤笼统不可执行",
    "unverifiable_expect": "预期结果不可验证",
    "missing_branch": "遗漏分支/场景",
    "fact_error": "事实/业务错误",
}


def get_positive_examples(db, *, module_id=None, session_id=None, limit=MAX_EXAMPLES):
    """取人工编辑过的高质量用例（few-shot 正样本）。

    优先同 module，不足时放宽到同 session。
    返回 [{case_name, preset_conditions, steps, expected_results, case_level}]
    """
    if db is None:
        return []
    try:
        from db.models import TestCase
        from sqlmodel import select

        results = []
        if module_id is not None:
            results = db.exec(
                select(TestCase)
                .where(
                    TestCase.human_edited.is_(True),
                    TestCase.module_id == module_id,
                )
                .order_by(TestCase.reviewed_at.desc())
                .limit(limit)
            ).all()
        if len(results) < limit and session_id is not None:
            exclude = {r.id for r in results}
            rows = db.exec(
                select(TestCase)
                .where(
                    TestCase.human_edited.is_(True),
                    TestCase.session_id == session_id,
                )
                .order_by(TestCase.reviewed_at.desc())
                .limit(limit * 2)
            ).all()
            for r in rows:
                if r.id in exclude:
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
        logger.warning("获取人工正样本失败（已忽略）: %s", exc)
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


def format_examples_for_prompt(examples, *, max_chars=MAX_EXAMPLE_CHARS):
    """把正样本格式化为 prompt 注入文本（空列表返回空串）。"""
    if not examples:
        return ""
    lines = [
        "## 人工认可的高质量示例（以下用例经人工审阅修改，代表本项目的质量标准，请对齐其风格与粒度）",
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