"""P3 闭环的 pipeline 集成测试。

用 mock 的 run_scenario 模拟"首轮失败 → 重修 → 复验通过"，验证：
- 开启配置开关后，pipeline 会执行用例并把失败信息回灌给优化器；
- 重修后重新执行，验证结果写回用例（validation_status / status）；
- 失败会登记 warning（部分失败不能被伪装成完全成功）；
- 开关关闭时完全不执行（不会误发真实请求）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlmodel import select

import app.services.scenario_runner as scenario_mod
from app.pipeline import pipeline_runner as pr_mod
from app.pipeline.pipeline_runner import PipelineRunner, get_pipeline_task
from app.pipeline.requirement_splitter import RequirementSplitter
from app.pipeline.test_point_generator import TestPointGenerator
from app.pipeline.test_case_designer import TestCaseDesigner
from db.models import ApiEndpoint, ApiProject, Session as SessionModel, TestCase


FAKE_RP = [{"content": "需求点A", "module_id": None, "priority": "medium", "order_index": 0}]
FAKE_TP = [{"content": "测试点A", "test_type": "functional", "design_method": "", "priority": "medium"}]

# fake_design 需要知道引用哪个接口（由 api_env fixture 填充）
ENDPOINT_REF = {"id": 1}


@pytest.fixture()
def api_env(db_session):
    """造一个带 base_url 的 API 项目与端点（不会真的发请求，执行器被 mock）。"""
    sess = SessionModel(name="p3")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    project = ApiProject(name="demo", base_url="http://demo.local", user_id="u1")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # 默认用 GET（只读），这样只读保护不会跳过它；
    # 需要写方法的用例由单独用例显式覆盖。
    endpoint = ApiEndpoint(
        project_id=project.id, name="查询", method="GET", path="/items",
        user_id="u1",
    )
    db_session.add(endpoint)
    db_session.commit()
    db_session.refresh(endpoint)
    ENDPOINT_REF["id"] = endpoint.id
    return sess, project, endpoint


@pytest.fixture()
def patched(monkeypatch, db_engine):
    import db.db as dbmod

    monkeypatch.setattr(dbmod, "engine", db_engine)

    async def fake_analyze(*a, **k):
        return list(FAKE_RP)

    async def fake_generate(*a, **k):
        return list(FAKE_TP)

    async def fake_design(test_points, requirement_content, **k):
        # 接口测试模式下，步骤必须带 endpoint 引用，pipeline 才会为用例创建 ApiScenario
        return [
            {
                "title": f"用例{tp['id']}",
                "test_steps": [{
                    "type": "api_call",
                    "endpoint_id": ENDPOINT_REF["id"],
                    "description": "调用查询接口",
                    "expected": "成功",
                }],
                "preconditions": [],
                "case_level": 1,
                "test_type": tp["test_type"],
                "design_method": tp["design_method"],
                "test_point_id": tp["id"],
                "assertions": [{"type": "status_code", "value": 200}],
            }
            for tp in test_points
        ]

    async def fake_dedup(data, *a, **k):
        return list(data)

    async def fake_tp_dedup(points, *a, **k):
        return list(points)

    async def fake_tc_dedup(cases, *a, **k):
        return []

    monkeypatch.setattr(RequirementSplitter, "analyze", staticmethod(fake_analyze))
    monkeypatch.setattr(TestPointGenerator, "generate", staticmethod(fake_generate))
    monkeypatch.setattr(TestCaseDesigner, "design_batch", staticmethod(fake_design))
    monkeypatch.setattr(pr_mod, "_dedup_requirement_points_by_llm", staticmethod(fake_dedup))
    monkeypatch.setattr(pr_mod, "_dedup_test_points_by_llm", staticmethod(fake_tp_dedup))
    monkeypatch.setattr(pr_mod, "_dedup_test_cases_by_llm", staticmethod(fake_tc_dedup))
    yield


def _enable_verification(monkeypatch, enabled=True, rounds=1):
    from config import config_manager

    monkeypatch.setattr(
        config_manager, "get_verification_enabled", lambda: enabled,
    )
    monkeypatch.setattr(
        config_manager, "get_verification_max_rounds", lambda: rounds,
    )


def test_verification_disabled_by_default(monkeypatch):
    from config import config_manager

    # 默认配置下开关是关闭的（避免误发真实请求）
    assert config_manager.get_verification_enabled() is False


def test_pipeline_skips_verification_when_disabled(
    db_session, patched, api_env, monkeypatch
):
    sess, project, endpoint = api_env
    _enable_verification(monkeypatch, enabled=False)

    called = {"count": 0}

    async def fake_run_scenario(*a, **k):
        called["count"] += 1
        return {"passed": True, "variables": {}, "steps": []}

    monkeypatch.setattr(scenario_mod, "run_scenario", fake_run_scenario)

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "接口需求", "u1",
        module_id=None, stop_after_stage=4,
        api_endpoint_ids=[endpoint.id],
        api_project_id=project.id,
        endpoint_index_to_id={1: endpoint.id},
        api_context="## 接口上下文\nPOST /login",
    ))

    task = get_pipeline_task(task_id)
    assert called["count"] == 0, "开关关闭时不得执行任何用例"
    assert task["result"].get("verification") is None


def test_pipeline_verification_failure_is_visible(
    db_session, patched, api_env, monkeypatch
):
    """执行失败必须登记 warning（不能被伪装成完全成功）。"""
    sess, project, endpoint = api_env
    _enable_verification(monkeypatch, enabled=True, rounds=0)

    async def fake_run_scenario(*a, **k):
        return {
            "passed": False, "variables": {},
            "steps": [{
                "index": 1, "status": "failed", "status_code": 200,
                "assertions": [
                    {"type": "jsonpath_equals", "passed": False,
                     "expected": "0", "actual": "500"},
                ],
            }],
        }

    monkeypatch.setattr(scenario_mod, "run_scenario", fake_run_scenario)

    # 优化器会真实调 API（401），这里 mock 掉，只关注验证闭环本身
    async def fake_optimize(cases_for_opt, **kwargs):
        return list(cases_for_opt)

    monkeypatch.setattr(pr_mod.TestCaseOptimizer, "optimize", staticmethod(fake_optimize))

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "接口需求", "u1",
        module_id=None, stop_after_stage=4,
        api_endpoint_ids=[endpoint.id],
        api_project_id=project.id,
        endpoint_index_to_id={1: endpoint.id},
        api_context="## 接口上下文\nPOST /login",
    ))

    task = get_pipeline_task(task_id)
    codes = [w["code"] for w in task["warnings"]]
    assert "verification_failed" in codes
    assert task["status"] == "partial"
    assert task["result"]["partial"] is True

    v = task["result"]["verification"]
    assert v is not None
    assert v["failed"] == 1
    assert v["passed"] == 0


def test_pipeline_verification_repairs_and_rechecks(
    db_session, patched, api_env, monkeypatch
):
    """首轮失败 → 重修 → 复验通过，验证结果写回用例。"""
    sess, project, endpoint = api_env
    _enable_verification(monkeypatch, enabled=True, rounds=1)

    state = {"calls": 0}

    async def fake_run_scenario(*a, **k):
        state["calls"] += 1
        if state["calls"] == 1:
            return {
                "passed": False, "variables": {},
                "steps": [{
                    "index": 1, "status": "failed", "status_code": 200,
                    "assertions": [
                        {"type": "jsonpath_equals", "passed": False,
                         "expected": "0", "actual": "500"},
                    ],
                }],
            }
        return {"passed": True, "variables": {}, "steps": [
            {"index": 1, "status": "passed", "assertions": [
                {"type": "status_code", "passed": True, "expected": 200, "actual": 200},
            ]},
        ]}

    monkeypatch.setattr(scenario_mod, "run_scenario", fake_run_scenario)

    # 记录优化器收到的 failure_feedback，验证"失败信息被回灌"
    seen_feedback = []

    async def fake_optimize(cases_for_opt, **kwargs):
        fb = kwargs.get("failure_feedback", "")
        if fb:
            seen_feedback.append(fb)
        # 原样返回输入，保证优化阶段不报错
        return list(cases_for_opt)

    monkeypatch.setattr(pr_mod.TestCaseOptimizer, "optimize", staticmethod(fake_optimize))

    runner = PipelineRunner(
        db_session=db_session, api_key="x",
        api_base_url="https://api.deepseek.com", api_model="deepseek-v4-flash",
    )
    task_id = runner._create_task()
    asyncio.run(runner._run_pipeline(
        task_id, sess.id, "接口需求", "u1",
        module_id=None, stop_after_stage=4,
        api_endpoint_ids=[endpoint.id],
        api_project_id=project.id,
        endpoint_index_to_id={1: endpoint.id},
        api_context="## 接口上下文\nPOST /login",
    ))

    task = get_pipeline_task(task_id)
    v = task["result"]["verification"]
    assert v["failed"] == 1
    assert v.get("repair_rounds") == 1
    assert v.get("after_repair", {}).get("passed") == 1

    # 失败信息确实被回灌给优化器
    assert seen_feedback, "失败明细应回灌给优化器"
    assert "执行验证失败明细" in seen_feedback[0]

    # 验证结果写回用例
    cases = db_session.exec(select(TestCase).where(TestCase.session_id == sess.id)).all()
    assert cases
    assert cases[0].validation_status == "passed"
    assert cases[0].status == "PASSED"
    assert cases[0].validation_detail
