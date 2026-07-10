"""TestCase 准确性保证字段：存在性 + 自动迁移写入验证。"""
from sqlmodel import Session, select
from sqlalchemy import inspect

from db.models import TestCase


def test_testcase_has_accuracy_fields(db_session: Session):
    cols = {c["name"] for c in inspect(db_session.get_bind()).get_columns("testcase")}
    for field in ("consistency_report", "confidence_score", "confidence_level",
                  "validation_status", "validation_detail", "validation_at"):
        assert field in cols, f"缺少字段 {field}"

    tc = TestCase(case_name="acc", confidence_score=0.9, confidence_level="high",
                  validation_status="pending")
    db_session.add(tc)
    db_session.commit()
    db_session.refresh(tc)
    assert tc.id is not None
    assert tc.confidence_level == "high"
