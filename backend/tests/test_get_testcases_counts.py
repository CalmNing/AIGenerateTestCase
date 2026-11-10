"""Verify get_testcases aggregate counts are correct after query collapse.

Calls the route function directly with the in-memory db_session, bypassing
auth (the count path does not use the user).
"""
from datetime import datetime, timedelta

from db.models import TestCase, StatusValue, Session as DbSession
from app.routes.testcase import get_testcases


class _StubUser:
    user_id = "u1"
    username = "u1"
    roles: list = []


def _seed(db_session):
    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    # 创建时间由旧到新，级别故意反向：列表必须按创建时间倒序，而不是按级别
    base = datetime(2026, 1, 1, 9, 0, 0)
    rows = [
        TestCase(case_name="a", session_id=sess.id, status=StatusValue.PASSED.value,
                 case_level=4, created_at=base),
        TestCase(case_name="b", session_id=sess.id, status=StatusValue.PASSED.value,
                 case_level=3, created_at=base + timedelta(minutes=1)),
        TestCase(case_name="c", session_id=sess.id, status=StatusValue.FAILED.value,
                 case_level=2, created_at=base + timedelta(minutes=2)),
        TestCase(case_name="d", session_id=sess.id, status=StatusValue.NOT_RUN.value,
                 case_level=1, created_at=base + timedelta(minutes=3)),
        TestCase(case_name="e", session_id=sess.id, status=StatusValue.NOT_RUN.value,
                 bug_id=42, case_level=4, created_at=base + timedelta(minutes=4)),
    ]
    for r in rows:
        db_session.add(r)
    db_session.commit()
    return sess


def test_counts_aggregate_correctly(db_session):
    sess = _seed(db_session)
    resp = get_testcases(
        session_id=sess.id, session=db_session, user=_StubUser(),
        module_id=None, offset=0, limit=1000,
        case_name=None, status=None, bug_id=None, exist_bug=False,
    )
    page = resp.data
    assert page.totalNumber == 5
    assert page.passed == 2
    assert page.failed == 1
    assert page.not_run == 2
    assert page.totalBugs == 1
    # 列表按创建时间倒序（最新在最前），级别不参与排序
    assert [item["case_name"] for item in page.items] == ["e", "d", "c", "b", "a"]


def test_counts_respect_module_filter(db_session):
    sess = _seed(db_session)
    # add two more rows in module 7
    base = datetime(2026, 1, 1, 9, 0, 0)
    db_session.add(TestCase(case_name="m1", session_id=sess.id, module_id=7,
                            status=StatusValue.PASSED.value,
                            created_at=base + timedelta(minutes=5)))
    db_session.add(TestCase(case_name="m2", session_id=sess.id, module_id=7,
                            status=StatusValue.FAILED.value,
                            created_at=base + timedelta(minutes=6)))
    db_session.commit()

    resp = get_testcases(
        session_id=sess.id, session=db_session, user=_StubUser(),
        module_id=7, offset=0, limit=1000,
        case_name=None, status=None, bug_id=None, exist_bug=False,
    )
    page = resp.data
    assert page.totalNumber == 2
    assert page.passed == 1
    assert page.failed == 1
    assert page.not_run == 0
    # 过滤后仍按创建时间倒序
    assert [item["case_name"] for item in page.items] == ["m2", "m1"]
