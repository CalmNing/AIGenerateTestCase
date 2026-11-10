"""测试用例列表排序规则：按创建时间倒序（最新创建的排在最前），级别不参与排序。

覆盖：最新在前、级别不影响顺序、同时间戳按 id 倒序、过滤/分页下顺序不变，
以及生产代码中不再残留按 case_level 的 order_by。
"""
import inspect
from datetime import datetime, timedelta
from pathlib import Path

from db.models import TestCase, StatusValue, Session as DbSession
from app.routes.testcase import get_testcases


class _StubUser:
    user_id = "u1"
    username = "u1"
    roles: list = []


BASE_TIME = datetime(2026, 1, 1, 9, 0, 0)


def _list_names(db_session, session_id, **overrides):
    params = dict(
        session_id=session_id,
        session=db_session,
        user=_StubUser(),
        module_id=None,
        offset=0,
        limit=1000,
        case_name=None,
        status=None,
        bug_id=None,
        exist_bug=False,
        test_point_id=None,
    )
    params.update(overrides)
    resp = get_testcases(**params)
    return [item["case_name"] for item in resp.data.items]


def _seed(db_session, with_module=False):
    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    # 创建时间由旧到新；级别故意反向，若仍按 case_level 排序会得到 a,b,c,d
    rows = [
        ("a", 1, BASE_TIME),
        ("b", 2, BASE_TIME + timedelta(hours=1)),
        ("c", 3, BASE_TIME + timedelta(hours=2)),
        ("d", 4, BASE_TIME + timedelta(hours=3)),
    ]
    for name, level, created in rows:
        db_session.add(TestCase(
            case_name=name,
            session_id=sess.id,
            case_level=level,
            created_at=created,
            module_id=7 if with_module and name in ("b", "d") else None,
            status=StatusValue.NOT_RUN.value,
        ))
    db_session.commit()
    return sess


def test_newest_first_and_level_ignored(db_session):
    sess = _seed(db_session)
    assert _list_names(db_session, sess.id) == ["d", "c", "b", "a"]


def test_same_created_at_falls_back_to_id_desc(db_session):
    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    for name in ("a", "b", "c"):
        db_session.add(TestCase(case_name=name, session_id=sess.id, created_at=BASE_TIME))
    db_session.commit()

    assert _list_names(db_session, sess.id) == ["c", "b", "a"]


def test_order_kept_with_filters_and_pagination(db_session):
    sess = _seed(db_session, with_module=True)

    # 过滤后仍是创建时间倒序
    assert _list_names(db_session, sess.id, module_id=7) == ["d", "b"]
    # 分页只截取，不改变顺序
    assert _list_names(db_session, sess.id, offset=1, limit=2) == ["c", "b"]
    # 名称过滤同理
    assert _list_names(db_session, sess.id, case_name="c") == ["c"]


def test_get_testcases_sorts_by_created_at_only():
    src = inspect.getsource(get_testcases)
    assert "desc(TestCase.created_at)" in src
    assert "case_level" not in src


def test_no_case_level_order_by_remains_in_backend():
    backend = Path(__file__).resolve().parents[1]
    offenders = []
    for path in backend.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "order_by" in line and "case_level" in line:
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == [], f"仍存在按级别排序: {offenders}"
