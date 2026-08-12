"""Red/Green 测试：手动模式（stop_after_stage=2）与批量生成用例。

验证当前未完成的「手动模式 + 批量生成用例」feature：
- PipelineRunner._run_pipeline(stop_after_stage=2) 只产出需求点+测试点，
  result.mode == "manual"，且不生成任何用例。
- PipelineRunner.batch_generate_cases 为会话全部测试点生成用例（Stage 3），
  并就地写入 TestCase。

LLM 调用（analyze / generate / design_batch）均被 mock，不触网、不依赖真实模型。
注意：本文件需在能正常导入 langchain 的环境（CI）运行；构造 PipelineRunner 会
实例化各阶段 designer（ChatOpenAI），但不发网络请求。
"""
import asyncio
from unittest import mock

import pytest
from sqlmodel import select

import db.db as dbmod
from app.pipeline import pipeline_runner as pr_mod
from app.pipeline.pipeline_runner import PipelineRunner, get_pipeline_task
from app.pipeline.requirement_splitter import RequirementSplitter
from app.pipeline.test_point_generator import TestPointGenerator
from app.pipeline.test_case_designer import TestCaseDesigner
from app.pipeline.test_case_optimizer import TestCaseOptimizer
from db.models import Session as SessionModel, RequirementPoint, TestPoint, TestCase


FAKE_RP = [{"content": "需求点A", "module_id": None, "priority": "medium", "order_index": 0}]
FAKE_TP = [{"content": "测试点A", "test_type": "functional", "design_method": "", "priority": "medium"}]
FAKE_CASE = {
    "title": "批量生成用例示例",
    "test_steps": [{"action": "输入账号", "expected": "登录成功"}],
    "preconditions": ["已注册"],
    "case_level": 1,
    "test_type": "functional",
    "design_method": "",
}


@pytest.fixture
def patched(monkeypatch, db_engine):
    # batch_generate_cases 内部用 db.db.engine 新建会话，指向测试引擎以保证数据可见
    monkeypatch.setattr(dbmod, "engine", db_engine)

    async def fake_analyze(*args, **kwargs):
        return list(FAKE_RP)

    async def fake_generate(*args, **kwargs):
        return list(FAKE_TP)

    async def fake_design(test_points, requirement_content, **kwargs):
        return [dict(FAKE_CASE)]

    monkeypatch.setattr(RequirementSplitter, "analyze", staticmethod(fake_analyze))
    monkeypatch.setattr(TestPointGenerator, "generate", staticmethod(fake_generate))
    monkeypatch.setattr(TestCaseDesigner, "design_batch", staticmethod(fake_design))
    # 语义去重也会调 LLM，直接原样返回（测试不关心去重）
    async def fake_dedup(data, *a, **k):
        return list(data)
    monkeypatch.setattr(
        pr_mod, "_dedup_requirement_points_by_llm",
        staticmethod(fake_dedup),
    )
    # 阶段2.5 跨需求点测试点去重同样 mock，保留全部测试点
    async def fake_tp_dedup(points, *a, **k):
        return list(points)
    monkeypatch.setattr(
        pr_mod, "_dedup_test_points_by_llm",
        staticmethod(fake_tp_dedup),
    )
    # 阶段4 用例级冗余审查 mock：默认不删除（需要时由测试覆盖）
    async def fake_tc_dedup(cases, *a, **k):
        return []
    monkeypatch.setattr(
        pr_mod, "_dedup_test_cases_by_llm",
        staticmethod(fake_tc_dedup),
    )
    yield


def test_manual_mode_stops_after_stage2(db_session, patched):
    sess = SessionModel(name="manual")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "普通功能需求", "u1",
        module_id=None, stop_after_stage=2,
    ))

    task = get_pipeline_task(task_id)
    assert task["status"] == "completed"
    assert task["result"]["mode"] == "manual"
    assert task["result"]["test_cases_count"] == 0
    assert task["result"]["requirement_points_count"] == 1
    assert task["result"]["test_points_count"] == 1

    rps = db_session.exec(select(RequirementPoint).where(RequirementPoint.session_id == sess.id)).all()
    tps = db_session.exec(select(TestPoint).where(TestPoint.session_id == sess.id)).all()
    assert len(rps) == 1 and len(tps) == 1
    # 手动模式不产生用例
    assert db_session.exec(select(TestCase).where(TestCase.session_id == sess.id)).all() == []


def test_batch_generate_cases_creates_cases(db_session, patched):
    sess = SessionModel(name="batch")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    rp = RequirementPoint(session_id=sess.id, content="需求点A", order_index=0)
    db_session.add(rp)
    db_session.commit()
    db_session.refresh(rp)

    tp = TestPoint(
        requirement_point_id=rp.id, session_id=sess.id, content="测试点A",
        test_type="functional", priority="medium",
    )
    db_session.add(tp)
    db_session.commit()
    db_session.refresh(tp)

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner.batch_generate_cases(task_id, sess.id, user_id="u1"))

    task = get_pipeline_task(task_id)
    assert task["status"] == "completed"
    assert task["result"]["mode"] == "batch"
    assert task["result"]["cases_count"] == 1

    cases = db_session.exec(select(TestCase).where(TestCase.test_point_id == tp.id)).all()
    assert len(cases) == 1
    assert cases[0].case_name == "批量生成用例示例"
    assert cases[0].test_point_id == tp.id


def test_batch_generate_cases_route_registered():
    from app.routes import pipeline as pipeline_routes
    paths = {r.path for r in pipeline_routes.router.routes}
    assert "/testcases/sessions/{session_id}/batch-generate-cases" in paths


def test_full_run_removes_redundant_case(db_session, patched, monkeypatch):
    """完整 4 阶段 run：用例级冗余审查标记 1 个冗余用例 → 该用例及其测试点被级联删除。"""
    sess = SessionModel(name="full")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    # 覆盖：1 个需求点 → 2 个测试点 → 2 条用例
    async def fake_generate(*args, **kwargs):
        return [
            {"content": "测试点A", "test_type": "functional", "design_method": "", "priority": "medium"},
            {"content": "测试点B", "test_type": "functional", "design_method": "", "priority": "medium"},
        ]

    async def fake_design(test_points, requirement_content, **kwargs):
        return [
            {
                "title": f"用例{tp['id']}",
                "test_steps": [{"action": "步骤1", "expected": "结果1"}],
                "preconditions": [],
                "case_level": 1,
                "test_type": tp["test_type"],
                "design_method": tp["design_method"],
                "test_point_id": tp["id"],
            }
            for tp in test_points
        ]

    # 阶段2.5 测试点去重：保留全部（不去重），便于用例层验证 remove_ids
    async def fake_tp_dedup(points, *a, **k):
        return list(points)

    # 阶段4 优化器：质量层不做改动（返回空）
    async def fake_optimize(cases_for_opt, **kwargs):
        return []

    # 阶段4 用例级冗余审查：标记第 2 条用例冗余
    async def fake_tc_dedup(cases, requirement_content, model):
        ids = [c.id for c in cases]
        return [ids[1]]

    monkeypatch.setattr(TestPointGenerator, "generate", staticmethod(fake_generate))
    monkeypatch.setattr(TestCaseDesigner, "design_batch", staticmethod(fake_design))
    monkeypatch.setattr(pr_mod, "_dedup_test_points_by_llm", staticmethod(fake_tp_dedup))
    monkeypatch.setattr(TestCaseOptimizer, "optimize", staticmethod(fake_optimize))
    monkeypatch.setattr(pr_mod, "_dedup_test_cases_by_llm", staticmethod(fake_tc_dedup))

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "功能需求", "u1",
        module_id=None, stop_after_stage=4,
    ))

    task = get_pipeline_task(task_id)
    assert task["status"] == "completed"
    assert task["result"]["test_cases_count"] == 1

    # 冗余用例及其测试点已被级联删除，只剩 1 条用例 + 1 个测试点
    cases = db_session.exec(select(TestCase).where(TestCase.session_id == sess.id)).all()
    tps = db_session.exec(select(TestPoint).where(TestPoint.session_id == sess.id)).all()
    assert len(cases) == 1
    assert len(tps) == 1
    assert cases[0].test_point_id == tps[0].id


def test_full_run_batches_optimizer_for_many_cases(db_session, patched, monkeypatch):
    """大批量用例（>10 条）时优化器按块分批调用，避免单次全量输出截断。"""
    sess = SessionModel(name="batch-opt")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    N = 12

    async def fake_generate(*args, **kwargs):
        return [
            {"content": f"测试点{i}", "test_type": "functional", "design_method": "", "priority": "medium"}
            for i in range(N)
        ]

    async def fake_design(test_points, requirement_content, **kwargs):
        return [
            {
                "title": f"用例{tp['id']}",
                "test_steps": [{"action": "步骤1", "expected": "结果1"}],
                "preconditions": [],
                "case_level": 1,
                "test_type": tp["test_type"],
                "design_method": tp["design_method"],
                "test_point_id": tp["id"],
            }
            for tp in test_points
        ]

    chunk_sizes = []

    async def fake_optimize(cases_for_opt, **kwargs):
        chunk_sizes.append(len(cases_for_opt))
        return []

    monkeypatch.setattr(TestPointGenerator, "generate", staticmethod(fake_generate))
    monkeypatch.setattr(TestCaseDesigner, "design_batch", staticmethod(fake_design))
    monkeypatch.setattr(TestCaseOptimizer, "optimize", staticmethod(fake_optimize))

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "功能需求", "u1",
        module_id=None, stop_after_stage=4,
    ))

    task = get_pipeline_task(task_id)
    assert task["status"] == "completed"
    assert task["result"]["test_cases_count"] == N
    # 12 条用例按 10/批 → 至少 2 次调用，每批不超过 10，且全部用例都被送入
    assert len(chunk_sizes) >= 2
    assert sum(chunk_sizes) == N
    assert all(s <= 10 for s in chunk_sizes)
