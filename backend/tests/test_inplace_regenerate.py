import asyncio

import pytest
from sqlmodel import SQLModel, Session as SQLSession, create_engine, select
from sqlmodel.pool import StaticPool

from app.pipeline.pipeline_runner import (
    PipelineRunner,
    _cascade_delete_test_point,
)
from db.models import (
    RequirementPoint,
    TestPoint,
    TestCase,
    Session as SessionModel,
    ApiScenario,
)


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def db(engine):
    with SQLSession(engine) as s:
        yield s


def _seed(engine):
    with SQLSession(engine) as s:
        sess = SessionModel(name="t", user_id="u1")
        s.add(sess)
        s.commit()
        s.refresh(sess)
        rp = RequirementPoint(session_id=sess.id, content="登录功能", user_id="u1")
        s.add(rp)
        s.commit()
        s.refresh(rp)
        tp = TestPoint(
            requirement_point_id=rp.id,
            session_id=sess.id,
            content="校验用户名",
            user_id="u1",
        )
        s.add(tp)
        s.commit()
        s.refresh(tp)
        seeded_ids = []
        for i in range(3):
            c = TestCase(
                session_id=sess.id,
                test_point_id=tp.id,
                case_name=f"旧用例{i}",
                user_id="u1",
            )
            s.add(c)
            s.commit()
            s.refresh(c)
            seeded_ids.append(c.id)
        s.commit()
        return sess.id, rp.id, tp.id, seeded_ids


def test_regenerate_dedup_converges(monkeypatch, engine, db):
    """重生成应就地收敛（不追加），并保留首个旧用例 id，杜绝重复。"""
    _sess_id, _rp_id, tp_id, seeded_ids = _seed(engine)

    async def fake_design(*a, **k):
        # design_batch 在单测试点批次下强制返回 1 条用例
        return [{"title": "新用例A", "test_steps": [], "preconditions": [], "case_level": 1}]

    monkeypatch.setattr(
        "app.pipeline.test_case_designer.TestCaseDesigner.design_batch",
        staticmethod(fake_design),
    )

    runner = PipelineRunner(db_session=db, api_key="x", api_base_url="x", api_model="x")
    asyncio.run(
        runner._inplace_update_cases(db.get(TestPoint, tp_id), "ctx", is_api_test=False)
    )

    cases = db.exec(
        select(TestCase).where(TestCase.test_point_id == tp_id).order_by(TestCase.id)
    ).all()
    assert len(cases) == 1, f"期望收敛为 1 条，实际 {len(cases)}"
    assert cases[0].case_name == "新用例A"
    assert cases[0].id == seeded_ids[0]  # id 稳定，未新建


def test_cascade_delete_no_orphan(engine, db):
    _sess_id, _rp_id, tp_id, _seeded_ids = _seed(engine)
    _cascade_delete_test_point(db, db.get(TestPoint, tp_id))
    db.commit()
    remain = db.exec(select(TestCase).where(TestCase.test_point_id == tp_id)).all()
    assert remain == []
    assert db.get(TestPoint, tp_id) is None


def test_cascade_delete_cleans_scenario(engine, db):
    """级联删除测试点时，其用例关联的 ApiScenario 也应被清理（无孤儿场景）。"""
    _sess_id, _rp_id, tp_id, _seeded_ids = _seed(engine)
    # 给该测试点下某用例挂一个场景
    case = db.exec(
        select(TestCase).where(TestCase.test_point_id == tp_id)
    ).all()[0]
    sc = ApiScenario(project_id=1, name="场景X", steps=[], user_id="u1")
    db.add(sc)
    db.commit()
    db.refresh(sc)
    case.scenario_id = sc.id
    db.add(case)
    db.commit()

    _cascade_delete_test_point(db, db.get(TestPoint, tp_id))
    db.commit()
    assert db.get(ApiScenario, sc.id) is None
    assert db.get(TestCase, case.id) is None
