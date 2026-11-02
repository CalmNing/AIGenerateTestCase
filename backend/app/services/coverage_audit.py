"""覆盖度审计（B6-C：让「漏分支」从人眼发现变成机器检测）。

核心思想
--------
功能用例的测试设计方法（等价类/边界值/决策表/状态迁移/场景）在测试点层级
已经是**结构化字段**（design_method），需求点→测试点→用例的三级链路也完整
落库。因此「覆盖完整性」可以变成一张可对账的矩阵：

    需求点 × 设计方法 → 每格 = 该需求点在该方法下有没有测试点/用例

缺口的三种形态（全部可自动检测）：
1. 链路断裂：需求点没有测试点（拆分遗漏）、测试点没有用例（设计遗漏）；
2. 方法缺失：某需求点只覆盖了少数设计方法（提示词要求"综合运用多种方法"，
   此前无校验）——只做 quality 级提示，不做硬性失败（不是每个需求点都需要全部方法）；
3. 悬空产物：测试点/用例存在但上游缺失（孤儿，删除需求点时未级联）。

对外接口：
- audit_requirement_coverage(db, session_id, ...)：审计并返回结构化缺口；
- format_coverage_report()：可读报告。

零 LLM 成本，纯规则计算。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# 测试设计方法的完整集合（与 app/pipeline/prompts.py SHARED_CACHE_PREFIX 对齐）
DESIGN_METHODS = (
    "equivalence_partitioning",   # 等价类划分
    "boundary_value",             # 边界值分析
    "decision_table",             # 决策表
    "state_transition",           # 状态转换
    "scenario",                   # 场景法
)

# 一个需求点至少应覆盖的方法数下限（低于此提示"方法覆盖不足"）
MIN_METHODS_PER_REQUIREMENT = 3


@dataclass
class CoverageGap:
    """一个覆盖缺口。"""
    gap_type: str            # no_testpoints / no_testcases / method_gap / orphan_testpoint / orphan_testcase
    severity: str            # warning（结果确实缺失） / quality（质量提示）
    requirement_point_id: Optional[int]
    requirement_content: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "gap_type": self.gap_type,
            "severity": self.severity,
            "requirement_point_id": self.requirement_point_id,
            "requirement_content": self.requirement_content[:60],
            "detail": self.detail,
        }


@dataclass
class CoverageAuditResult:
    """审计结果汇总。"""
    requirement_total: int = 0
    requirement_with_testpoints: int = 0
    requirement_with_cases: int = 0
    testpoint_total: int = 0
    testpoint_with_cases: int = 0
    gaps: list = field(default_factory=list)

    @property
    def link_completeness(self) -> float:
        """链路完整率：有测试点的需求点占比。"""
        if not self.requirement_total:
            return 0.0
        return round(self.requirement_with_testpoints / self.requirement_total, 4)

    def gaps_by_type(self) -> dict:
        out: dict = defaultdict(list)
        for g in self.gaps:
            out[g.gap_type].append(g)
        return dict(out)

    def to_dict(self) -> dict:
        return {
            "requirement_total": self.requirement_total,
            "requirement_with_testpoints": self.requirement_with_testpoints,
            "requirement_with_cases": self.requirement_with_cases,
            "testpoint_total": self.testpoint_total,
            "testpoint_with_cases": self.testpoint_with_cases,
            "link_completeness": self.link_completeness,
            "method_coverage_avg": self._method_coverage_avg(),
            "gaps": [g.to_dict() for g in self.gaps[:50]],
            "gap_counts": {k: len(v) for k, v in self.gaps_by_type().items()},
        }

    def _method_coverage_avg(self) -> float:
        """平均每个需求点覆盖的设计方法数（有测试点的需求点中）。"""
        method_counts = [
            len(g.detail.split(",")) for g in self.gaps
            if g.gap_type == "method_gap"
        ]
        # method_gap 的 detail 记录的是"缺失的方法"，覆盖率 = 全集-缺失
        if not method_counts:
            return float(len(DESIGN_METHODS))
        covered = [len(DESIGN_METHODS) - len(g.detail.split(",")) for g in self.gaps if g.gap_type == "method_gap"]
        return round(sum(covered) / len(covered), 2) if covered else float(len(DESIGN_METHODS))


@dataclass
class _RPRow:
    """内部：一个需求点及其下测试点/用例的聚合。"""
    id: int
    content: str
    testpoints: list = field(default_factory=list)   # [{id, design_method, has_cases}]

    @property
    def methods_covered(self) -> set:
        out = set()
        for tp in self.testpoints:
            m = (tp.get("design_method") or "").strip()
            if m:
                out.add(m)
        return out

    @property
    def has_cases(self) -> bool:
        return any(tp.get("has_cases") for tp in self.testpoints)


def audit_requirement_coverage(db, *, session_id: Optional[int] = None) -> CoverageAuditResult:
    """审计覆盖度（B6-C）。纯 SQL 聚合 + 规则判定，零 LLM 成本。

    参数：session_id 为空时审计全部会话（离线巡检用）。
    """
    from db.models import RequirementPoint, TestCase, TestPoint
    from sqlmodel import select

    result = CoverageAuditResult()
    try:
        rp_stmt = select(RequirementPoint)
        if session_id is not None:
            rp_stmt = rp_stmt.where(RequirementPoint.session_id == session_id)
        rps = db.exec(rp_stmt).all()

        tp_stmt = select(TestPoint)
        if session_id is not None:
            tp_stmt = tp_stmt.where(TestPoint.session_id == session_id)
        tps = db.exec(tp_stmt).all()

        tc_stmt = select(
            TestCase.test_point_id,
        ).where(TestCase.test_point_id.is_not(None))
        if session_id is not None:
            tc_stmt = tc_stmt.where(TestCase.session_id == session_id)
        tc_tp_ids = set(db.exec(tc_stmt).all())

        # 悬空检测：测试点/用例引用了不存在的上游
        rp_ids = {r.id for r in rps}
        orphan_tps = [tp for tp in tps if tp.requirement_point_id not in rp_ids]
    except Exception as exc:  # noqa: BLE001
        logger.warning("覆盖审计查询失败（返回空结果）: %s", exc)
        return result

    # 聚合：需求点 -> 测试点（含是否有用例）
    tp_by_rp: dict = defaultdict(list)
    for tp in tps:
        tp_by_rp[tp.requirement_point_id].append({
            "id": tp.id,
            "design_method": (tp.design_method or "").strip(),
            "has_cases": tp.id in tc_tp_ids,
        })

    result.requirement_total = len(rps)
    result.testpoint_total = len(tps)
    result.testpoint_with_cases = len(tc_tp_ids & set(tp.id for tp in tps))

    # 悬空测试点
    for tp in orphan_tps:
        result.gaps.append(CoverageGap(
            gap_type="orphan_testpoint",
            severity="warning",
            requirement_point_id=tp.requirement_point_id,
            detail=f"测试点 {tp.id} 引用的需求点 {tp.requirement_point_id} 不存在",
        ))

    for rp in rps:
        agg = _RPRow(id=rp.id, content=rp.content or "", testpoints=tp_by_rp.get(rp.id, []))
        name = agg.content[:40]

        # 缺口1：需求点完全没有测试点
        if not agg.testpoints:
            result.gaps.append(CoverageGap(
                gap_type="no_testpoints",
                severity="warning",
                requirement_point_id=rp.id,
                requirement_content=name,
                detail="该需求点没有拆分出任何测试点",
            ))
            continue

        result.requirement_with_testpoints += 1

        # 缺口2：有测试点但全都没有用例
        if not agg.has_cases:
            result.gaps.append(CoverageGap(
                gap_type="no_testcases",
                severity="warning",
                requirement_point_id=rp.id,
                requirement_content=name,
                detail=f"{len(agg.testpoints)} 个测试点均无用例",
            ))

        # 缺口3：设计方法覆盖不足（quality 级，非每个需求点都需要全部方法）
        covered = agg.methods_covered & set(DESIGN_METHODS)
        missing = [m for m in DESIGN_METHODS if m not in covered]
        if len(covered) < MIN_METHODS_PER_REQUIREMENT:
            result.gaps.append(CoverageGap(
                gap_type="method_gap",
                severity="quality",
                requirement_point_id=rp.id,
                requirement_content=name,
                detail=",".join(missing),
            ))

    # 悬空用例：test_point_id 指向不存在的测试点
    tp_ids = {tp.id for tp in tps}
    orphan_tc_ids = tc_tp_ids - tp_ids
    for tid in list(orphan_tc_ids)[:20]:
        result.gaps.append(CoverageGap(
            gap_type="orphan_testcase",
            severity="warning",
            requirement_point_id=None,
            detail=f"用例引用的测试点 {tid} 不存在",
        ))

    result.requirement_with_cases = sum(
        1 for rp in rps if _RPRow(
            id=rp.id, content="", testpoints=tp_by_rp.get(rp.id, []),
        ).has_cases
    )
    return result


def format_coverage_report(audit: CoverageAuditResult) -> str:
    """可读报告（供脚本/日志输出）。"""
    lines = [
        "# 覆盖度审计报告",
        "",
        f"- 需求点：{audit.requirement_total}（有测试点 {audit.requirement_with_testpoints}，链路完整率 {audit.link_completeness * 100:.1f}%）",
        f"- 测试点：{audit.testpoint_total}（有用例 {audit.testpoint_with_cases}）",
        "",
        "## 缺口统计",
        "",
    ]
    counts = audit.gaps_by_type()
    if not counts:
        lines.append("（无缺口）")
        return "\n".join(lines)
    labels = {
        "no_testpoints": "需求点无测试点（拆分遗漏）",
        "no_testcases": "测试点无用例（设计遗漏）",
        "method_gap": "设计方法覆盖不足（quality）",
        "orphan_testpoint": "悬空测试点",
        "orphan_testcase": "悬空用例",
    }
    for k, v in counts.items():
        lines.append(f"- {labels.get(k, k)}：{len(v)}")
    return "\n".join(lines)