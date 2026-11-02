"""pipeline 执行效率优化（B6-优化）的回归测试。

三件事：
1. 阶段4 优化批次并行化后，失败批次仍正确计数并登记 warning；
2. 批次失败自动重试 1 次（第一次失败、重试成功 → 不计失败）；
3. 并发上限可配置（_pipeline_concurrency 读取 config）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.pipeline.pipeline_runner as pr_mod
from app.pipeline.pipeline_runner import PipelineRunner, _pipeline_concurrency, get_pipeline_task
from app.pipeline.requirement_splitter import RequirementSplitter
from app.pipeline.test_point_generator import TestPointGenerator
from app.pipeline.test_case_designer import TestCaseDesigner
from db.models import Session as SessionModel


@pytest.fixture()
def patched(monkeypatch, db_engine):
    import db.db as dbmod

    monkeypatch.setattr(dbmod, "engine", db_engine)

    async def fake_analyze(*a, **k):
        return [{"content": "需求点A", "module_id": None, "priority": "medium", "order_index": 0}]

    async def fake_generate(*a, **k):
        return [{"content": "测试点A", "test_type": "functional",
                 "design_method": "", "priority": "medium"}]

    async def fake_dedup(data, *a, **k):
        return list(data)

    async def fake_tp_dedup(points, *a, **k):
        return list(points)

    async def fake_tc_dedup(cases, *a, **k):
        return []

    # 12 条用例 → 2 个优化批次（OPT_BATCH_SIZE=10）
    async def fake_design(test_points, requirement_content, **k):
        cases = []
        for tp in test_points:
            for i in range(12):
                cases.append({
                    "title": f"用例{tp[chr(105)+chr(100)]}-{i}",
                    "test_steps": [{"action": "步骤", "expected": "预期"}],
                    "preconditions": [],
                    "case_level": 1,
                    "test_type": tp["test_type"],
                    "design_method": "",
                    "test_point_id": tp["id"],
                })
        return cases

    monkeypatch.setattr(RequirementSplitter, "analyze", staticmethod(fake_analyze))
    monkeypatch.setattr(TestPointGenerator, "generate", staticmethod(fake_generate))
    monkeypatch.setattr(TestCaseDesigner, "design_batch", staticmethod(fake_design))
    monkeypatch.setattr(pr_mod, "_dedup_requirement_points_by_llm", staticmethod(fake_dedup))
    monkeypatch.setattr(pr_mod, "_dedup_test_points_by_llm", staticmethod(fake_tp_dedup))
    monkeypatch.setattr(pr_mod, "_dedup_test_cases_by_llm", staticmethod(fake_tc_dedup))
    yield


def test_optimize_batches_run_concurrently(db_session, patched, monkeypatch):
    """并行化：批次应重叠执行（max_in_flight > 1），且全部成功。"""
    state = {"in_flight": 0, "max_in_flight": 0, "calls": 0}

    async def fake_optimize(cases_for_opt, **kwargs):
        state["in_flight"] += 1
        state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        state["calls"] += 1
        await asyncio.sleep(0.05)
        state["in_flight"] -= 1
        return list(cases_for_opt)

    monkeypatch.setattr(pr_mod.TestCaseOptimizer, "optimize", staticmethod(fake_optimize))

    sess = SessionModel(name="par")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "功能需求", "u1", module_id=None, stop_after_stage=4,
    ))

    task = get_pipeline_task(task_id)
    assert state["calls"] >= 2, "应有多个优化批次"
    assert state["max_in_flight"] > 1, (
        f"批次应并行执行（max_in_flight={state[chr(109)+chr(97)+chr(120)+chr(95)+chr(105)+chr(110)+chr(95)+chr(102)+chr(108)+chr(105)+chr(103)+chr(104)+chr(116)]}）"
    )
    assert task["result"]["optimization_failed_batches"] == 0


def test_optimize_batch_retry_then_success(db_session, patched, monkeypatch):
    """第一次失败、重试成功 → 不计失败批次。"""
    fail_once_for = {"seen": set()}

    async def flaky_optimize(cases_for_opt, **kwargs):
        # 以第一批的首个用例 id 为指纹：该批首次调用失败，重试成功
        fingerprint = cases_for_opt[0]["id"]
        if fingerprint not in fail_once_for["seen"]:
            fail_once_for["seen"].add(fingerprint)
            raise RuntimeError("transient 503")
        return list(cases_for_opt)

    monkeypatch.setattr(pr_mod.TestCaseOptimizer, "optimize", staticmethod(flaky_optimize))

    sess = SessionModel(name="retry")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "功能需求", "u1", module_id=None, stop_after_stage=4,
    ))

    task = get_pipeline_task(task_id)
    assert task["result"]["optimization_failed_batches"] == 0
    codes = [w["code"] for w in task.get("warnings", [])]
    assert "optimization_batch_failed" not in codes


def test_optimize_batch_retry_exhausted_counts_failure(db_session, patched, monkeypatch):
    """重试后仍失败 → 计 1 个失败批次（不是 2），且重试恰好发生 2 次（初次+重试）。"""
    attempts_by_batch = {}

    async def always_fail(cases_for_opt, **kwargs):
        key = cases_for_opt[0]["id"]
        attempts_by_batch[key] = attempts_by_batch.get(key, 0) + 1
        raise RuntimeError("down")

    monkeypatch.setattr(pr_mod.TestCaseOptimizer, "optimize", staticmethod(always_fail))

    sess = SessionModel(name="fail")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "功能需求", "u1", module_id=None, stop_after_stage=4,
    ))

    task = get_pipeline_task(task_id)
    # 12 条用例 → 2 批，全部失败 → 2 个失败批次（每批独立计数）
    assert task["result"]["optimization_failed_batches"] == len(attempts_by_batch)
    assert all(n == 2 for n in attempts_by_batch.values()), (
        f"每个失败批次应恰好 初次+重试=2 次，实际 {attempts_by_batch}"
    )
    warns = [w for w in task["warnings"] if w["code"] == "optimization_batch_failed"]
    assert len(warns) == len(attempts_by_batch)
    assert all("已重试" in w["message"] for w in warns)


def test_pipeline_concurrency_configurable(monkeypatch):
    from config import config_manager

    monkeypatch.setattr(config_manager, "get_pipeline_concurrency", lambda: 8)
    assert _pipeline_concurrency() == 8

    monkeypatch.setattr(config_manager, "get_pipeline_concurrency", lambda: 0)
    assert _pipeline_concurrency() == 1, "下限 1"

    monkeypatch.setattr(config_manager, "get_pipeline_concurrency", lambda: 99)
    assert _pipeline_concurrency() == 16, "上限 16"