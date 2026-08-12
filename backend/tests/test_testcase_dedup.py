"""
单元：跨用例冗余审查（LLM 语义比对）
- _dedup_test_cases_by_llm 用 mock 模型验证去重与兜底逻辑，无需真实 LLM

运行：PYTHONPATH=backend backend/.venv/Scripts/python backend/tests/test_testcase_dedup.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, MagicMock

from app.pipeline.pipeline_runner import _dedup_test_cases_by_llm
from app.pipeline.schemas import TestCaseDedupResult


class _FakeTC:
    """模拟 TestCase ORM 对象（仅暴露去重用到的字段）。"""

    def __init__(self, id, case_name, expected_results=None,
                 test_type="functional", design_method="scenario", case_level=1):
        self.id = id
        self.case_name = case_name
        self.expected_results = expected_results or []
        self.test_type = test_type
        self.design_method = design_method
        self.case_level = case_level


def _make_model(remove_ids):
    result = TestCaseDedupResult(remove_ids=remove_ids)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=result)
    model = MagicMock()
    model.with_structured_output = MagicMock(return_value=structured)
    return model


def test_dedup_skips_llm_for_single():
    cases = [_FakeTC(1, "a")]
    model = MagicMock()
    out = asyncio.run(_dedup_test_cases_by_llm(cases, "req", model))
    assert out == []
    model.with_structured_output.assert_not_called()


def test_dedup_returns_remove_ids():
    cases = [
        _FakeTC(1, "详情页字段展示", ["头部字段完整", "申请详情字段"]),
        _FakeTC(2, "详情页统一布局（含字段）", ["头部字段完整", "申请详情字段", "底部统一"]),
    ]
    model = _make_model(remove_ids=[1])  # 删除被完全覆盖的用例 1
    out = asyncio.run(_dedup_test_cases_by_llm(cases, "req", model))
    assert out == [1]


def test_dedup_filters_invalid_ids():
    cases = [_FakeTC(1, "A"), _FakeTC(2, "B"), _FakeTC(3, "C")]
    model = _make_model(remove_ids=[2, 99, -1])  # 越界/非法 id 被过滤
    out = asyncio.run(_dedup_test_cases_by_llm(cases, "req", model))
    assert out == [2]


def test_dedup_dedups_and_preserves_order():
    cases = [_FakeTC(1, "A"), _FakeTC(2, "B"), _FakeTC(3, "C")]
    model = _make_model(remove_ids=[3, 3, 2])  # 重复 + 乱序
    out = asyncio.run(_dedup_test_cases_by_llm(cases, "req", model))
    assert out == [3, 2]


def test_dedup_fallback_on_empty():
    cases = [_FakeTC(1, "A"), _FakeTC(2, "B")]
    model = _make_model(remove_ids=[])  # 无冗余判定
    out = asyncio.run(_dedup_test_cases_by_llm(cases, "req", model))
    assert out == []


def test_dedup_fallback_on_exception():
    cases = [_FakeTC(1, "A"), _FakeTC(2, "B")]
    model = MagicMock()
    model.with_structured_output = MagicMock(side_effect=RuntimeError("boom"))
    out = asyncio.run(_dedup_test_cases_by_llm(cases, "req", model))
    assert out == []


def test_dedup_guards_delete_all():
    cases = [_FakeTC(1, "A"), _FakeTC(2, "B")]
    model = _make_model(remove_ids=[1, 2])  # 全部删除被拦截
    out = asyncio.run(_dedup_test_cases_by_llm(cases, "req", model))
    assert out == []


def _run_all():
    test_dedup_skips_llm_for_single()
    test_dedup_returns_remove_ids()
    test_dedup_filters_invalid_ids()
    test_dedup_dedups_and_preserves_order()
    test_dedup_fallback_on_empty()
    test_dedup_fallback_on_exception()
    test_dedup_guards_delete_all()
    print("ALL TESTCASE DEDUP TESTS PASSED")


if __name__ == "__main__":
    _run_all()
