"""
单元：需求点模块内去重（LLM 语义比对）
- _resolve_keep_indices / _assign_requirement_order 为纯函数，直接测
- _dedup_requirement_points_by_llm 用 mock 模型验证去重与兜底逻辑，无需真实 LLM

运行：PYTHONPATH=backend backend/.venv/Scripts/python backend/tests/test_requirement_point_dedup.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, MagicMock

from app.pipeline.pipeline_runner import (
    _resolve_keep_indices,
    _assign_requirement_order,
    _dedup_requirement_points_by_llm,
)
from app.pipeline.schemas import RequirementDedupResult


class _FakeRP:
    """模拟 RequirementPoint ORM 对象，仅暴露 content / order_index。"""

    def __init__(self, content, order_index=0):
        self.content = content
        self.order_index = order_index


def _make_model(keep_indices):
    """构造一个 mock 模型：with_structured_output().ainvoke 返回给定 keep_indices。"""
    result = RequirementDedupResult(keep_indices=keep_indices)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=result)
    model = MagicMock()
    model.with_structured_output = MagicMock(return_value=structured)
    return model


def test_resolve_keep_indices():
    # 0-based 正常
    assert _resolve_keep_indices([0, 2], 3) == [0, 2]
    # 超出 0-based 范围的值忽略（模型按 0-based 返回，提示词已约定）
    assert _resolve_keep_indices([1, 2, 3], 3) == [1, 2]
    # 去重 + 排序
    assert _resolve_keep_indices([0, 0, 1], 3) == [0, 1]
    # 越界忽略
    assert _resolve_keep_indices([5, -1], 3) == []
    # 空 / None
    assert _resolve_keep_indices([], 3) == []
    assert _resolve_keep_indices(None, 3) == []


def test_assign_order():
    existing = [_FakeRP("a", 2), _FakeRP("b", 5)]
    new = [{"content": "c", "priority": "high"}, {"content": "d", "priority": "low"}]
    out = _assign_requirement_order(new, existing, module_id=7)
    assert [o["order_index"] for o in out] == [6, 7]
    assert out[0]["module_id"] == 7
    assert out[0]["priority"] == "high"
    assert out[1]["priority"] == "low"

    # 无已有需求点 → 从 0 开始
    out2 = _assign_requirement_order(new, [], module_id=None)
    assert [o["order_index"] for o in out2] == [0, 1]
    assert out2[0]["module_id"] is None


def test_llm_dedup_keeps_only_new():
    existing = [_FakeRP("用户可登录系统", 0), _FakeRP("用户可退出登录", 1)]
    new = [
        {"content": "用户可以使用账号密码登录系统", "priority": "high"},  # 与已有语义重复
        {"content": "管理员可以冻结用户账号", "priority": "medium"},       # 全新
    ]
    model = _make_model(keep_indices=[1])  # 只保留索引 1
    out = asyncio.run(
        _dedup_requirement_points_by_llm(new, existing, model, module_id=1)
    )
    assert len(out) == 1
    assert out[0]["content"] == "管理员可以冻结用户账号"
    assert out[0]["order_index"] == 2  # 已有最大 order_index(1) + 1


def test_llm_dedup_no_existing_calls_no_llm():
    new = [{"content": "a"}, {"content": "b"}]
    model = MagicMock()  # 不应被调用
    out = asyncio.run(
        _dedup_requirement_points_by_llm(new, [], model, module_id=1)
    )
    assert len(out) == 2
    model.with_structured_output.assert_not_called()


def test_llm_dedup_empty_candidates():
    existing = [_FakeRP("x", 0)]
    new = [{"content": ""}, {"content": None}]
    model = MagicMock()
    out = asyncio.run(
        _dedup_requirement_points_by_llm(new, existing, model, module_id=1)
    )
    assert out == []


def test_llm_dedup_fallback_on_exception():
    existing = [_FakeRP("登录", 0)]
    new = [{"content": "x"}, {"content": "y"}]
    model = MagicMock()
    model.with_structured_output = MagicMock(side_effect=RuntimeError("boom"))
    out = asyncio.run(
        _dedup_requirement_points_by_llm(new, existing, model, module_id=1)
    )
    # 异常时保守保留全部，不误删
    assert len(out) == 2


def test_llm_dedup_fallback_on_all_out_of_range():
    existing = [_FakeRP("登录", 0)]
    new = [{"content": "x"}, {"content": "y"}]
    model = _make_model(keep_indices=[9, 12])  # 全部越界
    out = asyncio.run(
        _dedup_requirement_points_by_llm(new, existing, model, module_id=1)
    )
    # 索引全部越界视为异常，保守保留全部
    assert len(out) == 2


def _run_all():
    test_resolve_keep_indices()
    test_assign_order()
    test_llm_dedup_keeps_only_new()
    test_llm_dedup_no_existing_calls_no_llm()
    test_llm_dedup_empty_candidates()
    test_llm_dedup_fallback_on_exception()
    test_llm_dedup_fallback_on_all_out_of_range()
    print("ALL LLM DEDUP TESTS PASSED")


if __name__ == "__main__":
    _run_all()
