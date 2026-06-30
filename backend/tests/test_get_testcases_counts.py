"""Verify get_testcases aggregate counts are correct after query collapse.

Calls the route function directly with the in-memory db_session, bypassing
auth (the count path does not use the user).
"""
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
    rows = [
        TestCase(case_name="a", session_id=sess.id, status=StatusValue.PASSED.value),
        TestCase(case_name="b", session_id=sess.id, status=StatusValue.PASSED.value),
        TestCase(case_name="c", session_id=sess.id, status=StatusValue.FAILED.value),
        TestCase(case_name="d", session_id=sess.id, status=StatusValue.NOT_RUN.value),
        TestCase(case_name="e", session_id=sess.id, status=StatusValue.NOT_RUN.value, bug_id=42),
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


def test_counts_respect_module_filter(db_session):
    sess = _seed(db_session)
    # add two more rows in module 7
    db_session.add(TestCase(case_name="m1", session_id=sess.id, module_id=7,
                            status=StatusValue.PASSED.value))
    db_session.add(TestCase(case_name="m2", session_id=sess.id, module_id=7,
                            status=StatusValue.FAILED.value))
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
