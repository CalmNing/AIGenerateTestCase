"""P0 失败可见性 / P1 记忆接线：新增行为的回归测试。

覆盖点：
1. `build_thread_id` —— thread_id 必须是稳定键（旧实现每次 uuid4，等于没记忆）。
2. `agent_memory_context` —— 能产出可用 checkpointer/store，且不影响主流程。
3. `_add_warning` / `_complete_task` —— 降级会让任务状态变 partial，
   而 quality 级别的提示不会把任务误标成 partial。
4. `compute_level_distribution` / `check_level_distribution` —— 等级分布统计与异常提示。
5. 去重函数的 `on_fallback` —— 保守保留时必须上报原因，不再静默。
6. pipeline 批次失败 —— 会写入 warnings（而不是只记日志）。

全部用例都不触网、不调真实 LLM。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.pipeline import pipeline_runner as pr_mod
from app.pipeline.pipeline_runner import (
    PipelineRunner,
    _add_warning,
    check_level_distribution,
    compute_level_distribution,
    get_pipeline_task,
)
from app.pipeline.schemas import TestCaseDedupResult
from utils import agent_memory


# ---------------------------------------------------------------- P1：thread_id

def test_thread_id_is_stable_and_normalized():
    a = agent_memory.build_thread_id(
        scope="testcase", user_id="u1", session_id=7, module_id=3)
    b = agent_memory.build_thread_id(
        scope="testcase", user_id="u1", session_id=7, module_id=3)
    assert a == b, "thread_id 必须稳定，否则 checkpointer 永远读不到历史"
    assert a == "testcase:u1:7:3"


def test_thread_id_handles_none_and_unsafe_chars():
    tid = agent_memory.build_thread_id(
        scope="testcase", user_id="abc/def ghi", session_id=None, module_id=None)
    assert tid == "testcase:abc_def_ghi:none:none"
    assert "/" not in tid and " " not in tid


def test_thread_id_differs_by_module():
    a = agent_memory.build_thread_id(scope="testcase", user_id="u1", session_id=7, module_id=3)
    b = agent_memory.build_thread_id(scope="testcase", user_id="u1", session_id=7, module_id=4)
    assert a != b


def test_agent_memory_context_yields_checkpointer_and_store():
    async def _run():
        async with agent_memory.agent_memory_context() as (cp, store):
            assert cp is not None
            # store 允许为 None（极端降级），但正常路径下应有值
            assert hasattr(cp, "aget_tuple")
            return cp, store

    cp, _ = asyncio.run(_run())
    assert cp is not None


def test_agent_memory_disabled_falls_back_to_memory(monkeypatch):
    """配置关闭记忆时，仍必须产出可用的 checkpointer（降级而非崩溃）。"""
    from config import config_manager

    monkeypatch.setattr(config_manager, "get_agent_memory_enabled", lambda: False)

    async def _run():
        async with agent_memory.agent_memory_context() as (cp, store):
            return type(cp).__name__

    name = asyncio.run(_run())
    # 关闭落盘后应是内存实现（或最终降级），但不能抛异常
    assert name in ("InMemorySaver", "NullCheckpointer")


# ---------------------------------------------------------- P0：warnings 语义

def test_add_warning_records_entry_and_severity():
    task_id = "task-warn"
    pr_mod._pipeline_tasks[task_id] = {"warnings": []}
    _add_warning(task_id, 2, "boom", "生成失败", detail="TypeError: x")
    entry = pr_mod._pipeline_tasks[task_id]["warnings"][0]
    assert entry["code"] == "boom"
    assert entry["severity"] == "warning"
    assert entry["phase_name"] == "测试点生成"
    assert "TypeError" in entry["detail"]


def test_add_warning_on_missing_task_is_noop():
    # task 已被清理时不得抛异常（后台任务与轮询存在竞态）
    _add_warning("not-exist", 1, "x", "y")


def test_quality_only_warnings_keep_task_completed():
    task_id = "task-quality"
    pr_mod._pipeline_tasks[task_id] = {"warnings": []}
    _add_warning(task_id, 3, "uniform_case_level", "全部同一等级", severity="quality")
    runner = PipelineRunner.__new__(PipelineRunner)  # 不触发 __init__ 的模型构造
    runner._complete_task(task_id, {"test_cases_count": 5})
    task = get_pipeline_task(task_id)
    assert task["status"] == "completed"
    assert task["result"]["partial"] is False
    assert "质量提示" in task["message"]


def test_degradation_warnings_mark_task_partial():
    task_id = "task-partial"
    pr_mod._pipeline_tasks[task_id] = {"warnings": []}
    _add_warning(task_id, 3, "case_design_batch_failed", "一批用例设计失败")
    runner = PipelineRunner.__new__(PipelineRunner)
    runner._complete_task(task_id, {"test_cases_count": 1})
    task = get_pipeline_task(task_id)
    assert task["status"] == "partial"
    assert task["result"]["partial"] is True
    assert task["result"]["warnings"][0]["code"] == "case_design_batch_failed"


def test_fail_task_keeps_warnings():
    task_id = "task-fail"
    pr_mod._pipeline_tasks[task_id] = {"warnings": []}
    _add_warning(task_id, 1, "boom", "x")
    runner = PipelineRunner.__new__(PipelineRunner)
    runner._fail_task(task_id, "整体失败")
    task = get_pipeline_task(task_id)
    assert task["status"] == "failed"
    assert task["warnings"][0]["code"] == "boom"


# ------------------------------------------------------- P0：等级分布校验

class _FakeCase:
    def __init__(self, level):
        self.case_level = level


def test_compute_level_distribution():
    cases = [_FakeCase(1), _FakeCase(1), _FakeCase(2), _FakeCase(3),
             _FakeCase(4), _FakeCase(None), _FakeCase(9)]
    dist = compute_level_distribution(cases)
    assert dist["1"] == 2 and dist["2"] == 1 and dist["3"] == 1 and dist["4"] == 1
    assert dist["unknown"] == 2


def test_compute_level_distribution_empty():
    assert compute_level_distribution([]) == {"1": 0, "2": 0, "3": 0, "4": 0, "unknown": 0}


def test_uniform_level_registers_quality_warning():
    task_id = "task-level"
    pr_mod._pipeline_tasks[task_id] = {"warnings": []}
    cases = [_FakeCase(1) for _ in range(6)]
    check_level_distribution(task_id, 3, cases)
    codes = [w["code"] for w in get_pipeline_task(task_id)["warnings"]]
    assert "uniform_case_level" in codes


def test_mixed_levels_do_not_warn():
    task_id = "task-level-ok"
    pr_mod._pipeline_tasks[task_id] = {"warnings": []}
    cases = [_FakeCase(1), _FakeCase(2), _FakeCase(3), _FakeCase(4)]
    check_level_distribution(task_id, 3, cases)
    assert get_pipeline_task(task_id)["warnings"] == []


def test_small_case_set_does_not_warn_uniform():
    """少于 4 条时"全部同一等级"是正常的，不误报。"""
    task_id = "task-level-small"
    pr_mod._pipeline_tasks[task_id] = {"warnings": []}
    check_level_distribution(task_id, 3, [_FakeCase(1), _FakeCase(1)])
    assert get_pipeline_task(task_id)["warnings"] == []


# ---------------------------------------------------- P0：去重降级可见性

class _FakeTC:
    def __init__(self, id, case_name, expected_results=None):
        self.id = id
        self.case_name = case_name
        self.expected_results = expected_results or []
        self.test_type = "functional"
        self.design_method = "scenario"
        self.case_level = 1


def test_test_case_dedup_reports_fallback_on_empty():
    """模型返回空（含内部异常被吞的情况）→ 保守不删除并上报。"""
    calls = []
    model = MagicMock()
    model.with_structured_output = MagicMock(side_effect=RuntimeError("boom"))
    out = asyncio.run(pr_mod._dedup_test_cases_by_llm(
        [_FakeTC(1, "A"), _FakeTC(2, "B")], "req", model,
        on_fallback=lambda code, msg: calls.append((code, msg)),
    ))
    assert out == [], "异常/空返回时必须保守不删除"
    assert calls and calls[0][0] == "test_case_dedup_empty"


def test_test_case_dedup_reports_fallback_on_exception(monkeypatch):
    """_structured_output_robust 直接抛异常时，必须上报 failed 而非静默。"""
    async def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(pr_mod, "_structured_output_robust", boom)
    calls = []
    out = asyncio.run(pr_mod._dedup_test_cases_by_llm(
        [_FakeTC(1, "A"), _FakeTC(2, "B")], "req", MagicMock(),
        on_fallback=lambda code, msg: calls.append((code, msg)),
    ))
    assert out == []
    assert calls and calls[0][0] == "test_case_dedup_failed"


def test_test_case_dedup_reports_blocked_delete_all():
    result = TestCaseDedupResult(remove_ids=[1, 2])
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=result)
    model = MagicMock()
    model.with_structured_output = MagicMock(return_value=structured)
    calls = []
    out = asyncio.run(pr_mod._dedup_test_cases_by_llm(
        [_FakeTC(1, "A"), _FakeTC(2, "B")], "req", model,
        on_fallback=lambda code, msg: calls.append((code, msg)),
    ))
    assert out == []
    assert calls and calls[0][0] == "test_case_dedup_delete_all_blocked"


def test_dedup_fallback_callback_exception_is_swallowed():
    """回调本身抛异常也不能影响去重结果。"""
    def bad_cb(code, msg):
        raise RuntimeError("callback boom")

    model = MagicMock()
    model.with_structured_output = MagicMock(side_effect=RuntimeError("boom"))
    out = asyncio.run(pr_mod._dedup_test_cases_by_llm(
        [_FakeTC(1, "A"), _FakeTC(2, "B")], "req", model, on_fallback=bad_cb,
    ))
    assert out == []


def test_dedup_without_callback_still_works():
    """不传回调时行为与改造前一致（向后兼容）。"""
    model = MagicMock()
    model.with_structured_output = MagicMock(side_effect=RuntimeError("boom"))
    out = asyncio.run(pr_mod._dedup_test_cases_by_llm(
        [_FakeTC(1, "A"), _FakeTC(2, "B")], "req", model,
    ))
    assert out == []


# ------------------------------------------------ P0：pipeline 端到端可见性

@pytest.fixture()
def patched(monkeypatch, db_engine):
    import db.db as dbmod
    from app.pipeline.requirement_splitter import RequirementSplitter
    from app.pipeline.test_point_generator import TestPointGenerator

    monkeypatch.setattr(dbmod, "engine", db_engine)

    async def fake_analyze(*a, **k):
        return [{"content": "需求点A", "module_id": None, "priority": "medium", "order_index": 0}]

    async def fake_generate_fail(*a, **k):
        raise RuntimeError("LLM 挂了")

    async def fake_dedup(data, *a, **k):
        return list(data)

    async def fake_tp_dedup(points, *a, **k):
        return list(points)

    monkeypatch.setattr(RequirementSplitter, "analyze", staticmethod(fake_analyze))
    monkeypatch.setattr(TestPointGenerator, "generate", staticmethod(fake_generate_fail))
    monkeypatch.setattr(pr_mod, "_dedup_requirement_points_by_llm", staticmethod(fake_dedup))
    monkeypatch.setattr(pr_mod, "_dedup_test_points_by_llm", staticmethod(fake_tp_dedup))


def test_pipeline_test_point_failure_is_visible(db_session, patched):
    """阶段2 单点失败 → 任务必须带 warnings，而不是静默"完成"。"""
    from db.models import Session as SessionModel

    sess = SessionModel(name="p0")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "功能需求", "u1", module_id=None, stop_after_stage=2,
    ))

    task = get_pipeline_task(task_id)
    codes = [w["code"] for w in task["warnings"]]
    assert "test_point_generation_failed" in codes
    assert task["result"]["partial"] is True
    assert task["status"] == "partial"
