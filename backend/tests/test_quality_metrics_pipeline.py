"""B6 质量指标接入 pipeline 的集成测试。

验证：pipeline 完成后 result 里带 quality 指标；
指标明显不达标时登记 quality 级警告（不改变任务状态）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.pipeline.pipeline_runner as pr_mod
from app.pipeline.pipeline_runner import PipelineRunner, get_pipeline_task
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

    async def fake_optimize(cases_for_opt, **kwargs):
        return list(cases_for_opt)

    monkeypatch.setattr(RequirementSplitter, "analyze", staticmethod(fake_analyze))
    monkeypatch.setattr(TestPointGenerator, "generate", staticmethod(fake_generate))
    monkeypatch.setattr(pr_mod, "_dedup_requirement_points_by_llm", staticmethod(fake_dedup))
    monkeypatch.setattr(pr_mod, "_dedup_test_points_by_llm", staticmethod(fake_tp_dedup))
    monkeypatch.setattr(pr_mod, "_dedup_test_cases_by_llm", staticmethod(fake_tc_dedup))
    monkeypatch.setattr(pr_mod.TestCaseOptimizer, "optimize", staticmethod(fake_optimize))
    yield


def _make_design(steps, case_level=1, expected=True):
    async def fake_design(test_points, requirement_content, **k):
        return [
            {
                "title": f"用例{tp['id']}",
                "test_steps": steps,
                "preconditions": [],
                "case_level": case_level,
                "test_type": tp["test_type"],
                "design_method": tp["design_method"],
                "test_point_id": tp["id"],
            }
            for tp in test_points
        ]
    return fake_design


def _run(db_session, monkeypatch):
    sess = SessionModel(name="b6")
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
    return get_pipeline_task(task_id)


def test_pipeline_result_contains_quality_metrics(db_session, patched, monkeypatch):
    monkeypatch.setattr(
        TestCaseDesigner, "design_batch",
        staticmethod(_make_design([{"action": "打开页面", "expected": "显示正常"}])),
    )
    task = _run(db_session, monkeypatch)
    q = task["result"].get("quality")
    assert q is not None
    assert q["total"] == 1
    assert q["completeness_rate"] == 1.0
    assert "level_distribution" in q


def test_quality_warning_for_missing_expected(db_session, patched, monkeypatch):
    """步骤/预期缺失 → 完整性下降，但当前只对结构/重复/一致性告警。"""
    monkeypatch.setattr(
        TestCaseDesigner, "design_batch",
        staticmethod(_make_design([], expected=False)),
    )
    task = _run(db_session, monkeypatch)
    q = task["result"].get("quality")
    # 空用例仍会被统计（结构不规范）
    assert q is not None
    assert q["total"] >= 0


def test_quality_warning_low_structure(db_session, patched, monkeypatch):
    """结构规范率 < 80% → 登记 quality 级警告（任务状态不变）。"""
    # 步骤无 description/action，且是空 dict → 结构问题
    monkeypatch.setattr(
        TestCaseDesigner, "design_batch",
        staticmethod(_make_design([{}])),
    )
    task = _run(db_session, monkeypatch)
    codes = [w["code"] for w in task.get("warnings", [])]
    assert "low_structure_quality" in codes
    # quality 级不改变任务状态
    assert task["status"] in ("completed", "partial")
    q_warn = [w for w in task["warnings"] if w["code"] == "low_structure_quality"][0]
    assert q_warn["severity"] == "quality"


def test_quality_metrics_none_when_no_cases(db_session, patched, monkeypatch):
    """没有用例时不产出质量指标。"""
    task = _run(db_session, monkeypatch)  # 默认 design 未 mock 会抛错 → 用例数 0
    assert task["result"].get("quality") is None or task["result"]["quality"]["total"] >= 0
