"""
单元：跨需求点测试点去重（LLM 语义比对）
- _dedup_test_points_by_llm 用 mock 模型验证去重与兜底逻辑，无需真实 LLM

运行：PYTHONPATH=backend backend/.venv/Scripts/python backend/tests/test_testpoint_dedup.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, MagicMock

from app.pipeline.pipeline_runner import _dedup_test_points_by_llm
from app.pipeline.schemas import TestPointDedupResult


class _FakeTP:
    """模拟 TestPoint ORM 对象。"""

    def __init__(self, id, content, requirement_point_id=None,
                 test_type="functional", design_method="equivalence_partitioning"):
        self.id = id
        self.content = content
        self.requirement_point_id = requirement_point_id
        self.test_type = test_type
        self.design_method = design_method


class _FakeRPContent:
    content = "需求点内容"


class _FakeSession:
    """记录 delete/commit 的假 Session（无需真实 DB）。"""

    def __init__(self):
        self.deleted = []
        self.committed = 0

    def get(self, model, rp_id):
        return _FakeRPContent()

    def delete(self, tp):
        self.deleted.append(tp.id)

    def commit(self):
        self.committed += 1

    def refresh(self, tp):
        return tp


def _make_model(keep_ids):
    result = TestPointDedupResult(keep_ids=keep_ids)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=result)
    model = MagicMock()
    model.with_structured_output = MagicMock(return_value=structured)
    return model


def test_dedup_skips_llm_for_single():
    pts = [_FakeTP(1, "a")]
    model = MagicMock()
    session = _FakeSession()
    out = asyncio.run(_dedup_test_points_by_llm(pts, "req", model, session))
    assert out == pts
    model.with_structured_output.assert_not_called()
    assert session.deleted == []


def test_dedup_keeps_most_comprehensive():
    pts = [
        _FakeTP(1, "详情页字段展示验证", requirement_point_id=10),
        _FakeTP(2, "详情页头部与底部统一布局验证（含字段）", requirement_point_id=11),
    ]
    model = _make_model(keep_ids=[2])  # 保留更综合的一个
    session = _FakeSession()
    out = asyncio.run(_dedup_test_points_by_llm(pts, "req", model, session))
    assert [tp.id for tp in out] == [2]
    assert session.deleted == [1]
    assert session.committed >= 1


def test_dedup_keeps_all_when_no_dup():
    pts = [_FakeTP(1, "A"), _FakeTP(2, "B")]
    model = _make_model(keep_ids=[1, 2])  # 全部保留
    session = _FakeSession()
    out = asyncio.run(_dedup_test_points_by_llm(pts, "req", model, session))
    assert len(out) == 2
    assert session.deleted == []


def test_dedup_fallback_on_empty_keep():
    pts = [_FakeTP(1, "A"), _FakeTP(2, "B")]
    model = _make_model(keep_ids=[])  # 无重复判定
    session = _FakeSession()
    out = asyncio.run(_dedup_test_points_by_llm(pts, "req", model, session))
    assert len(out) == 2
    assert session.deleted == []


def test_dedup_fallback_on_exception():
    pts = [_FakeTP(1, "A"), _FakeTP(2, "B")]
    model = MagicMock()
    model.with_structured_output = MagicMock(side_effect=RuntimeError("boom"))
    session = _FakeSession()
    out = asyncio.run(_dedup_test_points_by_llm(pts, "req", model, session))
    assert len(out) == 2
    assert session.deleted == []


def test_dedup_fallback_on_invalid_ids():
    pts = [_FakeTP(1, "A"), _FakeTP(2, "B")]
    model = _make_model(keep_ids=[9, 12])  # 全部为不存在的 id
    session = _FakeSession()
    out = asyncio.run(_dedup_test_points_by_llm(pts, "req", model, session))
    assert len(out) == 2
    assert session.deleted == []


def _run_all():
    test_dedup_skips_llm_for_single()
    test_dedup_keeps_most_comprehensive()
    test_dedup_keeps_all_when_no_dup()
    test_dedup_fallback_on_empty_keep()
    test_dedup_fallback_on_exception()
    test_dedup_fallback_on_invalid_ids()
    print("ALL TESTPOINT DEDUP TESTS PASSED")


if __name__ == "__main__":
    _run_all()
