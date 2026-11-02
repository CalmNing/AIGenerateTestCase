"""覆盖度审计（B6-C）测试。

核心场景：链路断裂（需求点无测试点/无用例）、方法覆盖不足、悬空产物。
全部零 LLM 成本。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlmodel import select

from app.services.coverage_audit import (
    DESIGN_METHODS,
    MIN_METHODS_PER_REQUIREMENT,
    CoverageAuditResult,
    CoverageGap,
    audit_requirement_coverage,
    format_coverage_report,
)
from db.models import RequirementPoint, Session as DbSession, TestCase, TestPoint


@pytest.fixture()
def seeded(db_session):
    """构造覆盖完整的会话：1 个需求点 → 2 个测试点（各带用例）。"""
    sess = DbSession(name="cov")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    rp = RequirementPoint(session_id=sess.id, content="完整需求点", order_index=0)
    db_session.add(rp)
    db_session.commit()
    db_session.refresh(rp)

    methods = DESIGN_METHODS[:MIN_METHODS_PER_REQUIREMENT]
    for i, m in enumerate(methods):
        tp = TestPoint(
            requirement_point_id=rp.id, session_id=sess.id,
            content=f"测试点{i}", test_type="functional",
            design_method=m, priority="medium",
        )
        db_session.add(tp)
        db_session.commit()
        db_session.refresh(tp)
        db_session.add(TestCase(
            case_name=f"用例{i}", steps=[{"action": "x"}],
            expected_results=["y"], session_id=sess.id,
            test_point_id=tp.id,
        ))
    db_session.commit()
    return sess, rp


def test_full_coverage_no_gaps(db_session, seeded):
    """链路完整 + 方法覆盖达标 → 无 warning 级缺口。"""
    sess, rp = seeded
    audit = audit_requirement_coverage(db_session, session_id=sess.id)
    assert audit.requirement_total == 1
    assert audit.requirement_with_testpoints == 1
    warns = [g for g in audit.gaps if g.severity == "warning"]
    assert warns == []
    assert audit.link_completeness == 1.0


def test_gap_no_testpoints(db_session):
    """需求点没拆出测试点 → warning 级缺口。"""
    sess = DbSession(name="cov2")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    rp = RequirementPoint(session_id=sess.id, content="孤儿需求点", order_index=0)
    db_session.add(rp)
    db_session.commit()

    audit = audit_requirement_coverage(db_session, session_id=sess.id)
    gaps = [g for g in audit.gaps if g.gap_type == "no_testpoints"]
    assert len(gaps) == 1
    assert gaps[0].severity == "warning"
    assert gaps[0].requirement_point_id == rp.id
    assert audit.link_completeness == 0.0


def test_gap_no_testcases(db_session, seeded):
    """有测试点但无用例 → warning 级缺口。"""
    sess, rp = seeded
    # 删掉所有用例
    for tc in db_session.exec(
        select(TestCase).where(TestCase.session_id == sess.id)
    ).all():
        db_session.delete(tc)
    db_session.commit()

    audit = audit_requirement_coverage(db_session, session_id=sess.id)
    gaps = [g for g in audit.gaps if g.gap_type == "no_testcases"]
    assert len(gaps) == 1
    assert gaps[0].severity == "warning"


def test_gap_method_insufficient(db_session):
    """只覆盖 1 类设计方法 → quality 级提示。"""
    sess = DbSession(name="cov3")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    rp = RequirementPoint(session_id=sess.id, content="单一方法需求点", order_index=0)
    db_session.add(rp)
    db_session.commit()
    db_session.refresh(rp)
    tp = TestPoint(
        requirement_point_id=rp.id, session_id=sess.id,
        content="tp", test_type="functional",
        design_method="equivalence_partitioning", priority="medium",
    )
    db_session.add(tp)
    db_session.commit()
    db_session.refresh(tp)
    db_session.add(TestCase(
        case_name="c", steps=[{"action": "x"}], expected_results=["y"],
        session_id=sess.id, test_point_id=tp.id,
    ))
    db_session.commit()

    audit = audit_requirement_coverage(db_session, session_id=sess.id)
    m_gaps = [g for g in audit.gaps if g.gap_type == "method_gap"]
    assert len(m_gaps) == 1
    assert m_gaps[0].severity == "quality"
    # 缺失方法列表不应包含已覆盖的
    assert "equivalence_partitioning" not in m_gaps[0].detail.split(",")


def test_orphan_testpoint(db_session, seeded):
    """测试点引用的需求点不存在 → 悬空缺口。"""
    sess, rp = seeded
    orphan = TestPoint(
        requirement_point_id=99999, session_id=sess.id,
        content="孤儿", test_type="functional", priority="medium",
    )
    db_session.add(orphan)
    db_session.commit()

    audit = audit_requirement_coverage(db_session, session_id=sess.id)
    gaps = [g for g in audit.gaps if g.gap_type == "orphan_testpoint"]
    assert gaps, "应检测到悬空测试点"
    assert gaps[0].severity == "warning"


def test_session_isolation(db_session, seeded):
    """审计按 session 隔离：另一会话的需求点不影响本会话结果。"""
    sess, rp = seeded
    other = DbSession(name="other")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    rp2 = RequirementPoint(session_id=other.id, content="其他会话需求点", order_index=0)
    db_session.add(rp2)
    db_session.commit()

    audit = audit_requirement_coverage(db_session, session_id=sess.id)
    assert audit.requirement_total == 1, "不应包含其他会话的需求点"


def test_format_report():
    audit = CoverageAuditResult(
        requirement_total=2,
        requirement_with_testpoints=1,
        requirement_with_cases=1,
        testpoint_total=2,
        testpoint_with_cases=2,
        gaps=[CoverageGap(
            gap_type="no_testpoints", severity="warning",
            requirement_point_id=2, requirement_content="x", detail="y",
        )],
    )
    text = format_coverage_report(audit)
    assert "覆盖度审计报告" in text
    assert "36.1%" in text or "50.0%" in text


def test_empty_session():
    audit = CoverageAuditResult()
    assert audit.link_completeness == 0.0
    assert audit.gaps == []