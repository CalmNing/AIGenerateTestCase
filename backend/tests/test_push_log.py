"""Tests for IoT 推送请求记录路由：分页、过滤、详情与清空。

纯单元测试，使用内存 SQLite（conftest.db_engine/db_session），直接调用路由函数。
"""
from sqlmodel import Session, select

import app.routes.push_log as push_log_mod
from db.models import PushLog
from utils.base_response import Response


def _seed_logs(session: Session):
    rows = [
        PushLog(request_method="POST", source_url="https://iot.example.com/api/push?token={{token}}",
                final_url="https://iot.example.com/api/push?token=abc123",
                request_headers={"Content-Type": "application/json"},
                final_headers={"Content-Type": "application/json"},
                request_params={"token": "{{token}}"}, final_params={"token": "abc123"},
                request_body='{"device":"sensor-1"}', final_body='{"device":"sensor-1"}',
                response_status=200, duration_ms=120, user_id="u1"),
        PushLog(request_method="GET", source_url="https://iot.example.com/api/status",
                final_url="https://iot.example.com/api/status",
                response_status=500, duration_ms=800, user_id="u1"),
        PushLog(request_method="POST", source_url="https://mock.example.com/api/data",
                final_url="https://mock.example.com/api/data",
                response_status=200, mocked=True, duration_ms=1, user_id="u2"),
        PushLog(request_method="PUT", source_url="not-a-valid-url",
                final_url="not-a-valid-url",
                response_status=0, error="Invalid URL", duration_ms=5, user_id="u1"),
        PushLog(request_method="DELETE", source_url="https://iot.example.com/api/device/9",
                final_url="https://iot.example.com/api/device/9",
                response_status=204, duration_ms=45, user_id="u2"),
    ]
    for r in rows:
        session.add(r)
    session.commit()
    return rows


class TestPushLogsList:
    def test_pagination_total(self, db_session):
        _seed_logs(db_session)
        res = push_log_mod.get_push_logs(
            session=db_session, user=None,
            page=1, page_size=2,
            method=None, search=None, mocked=None, failed=None,
        )
        assert res.code == 200
        assert len(res.data) == 2
        assert res.pagination["total"] == 5

    def test_filter_by_method(self, db_session):
        _seed_logs(db_session)
        res = push_log_mod.get_push_logs(
            session=db_session, user=None,
            page=1, page_size=20,
            method="post", search=None, mocked=None, failed=None,
        )
        assert res.pagination["total"] == 2
        assert all(r.request_method == "POST" for r in res.data)

    def test_filter_by_search_matches_source_and_final_url(self, db_session):
        _seed_logs(db_session)
        res = push_log_mod.get_push_logs(
            session=db_session, user=None,
            page=1, page_size=20,
            method=None, search="iot.example.com", mocked=None, failed=None,
        )
        assert res.pagination["total"] == 3

    def test_filter_mocked(self, db_session):
        _seed_logs(db_session)
        res = push_log_mod.get_push_logs(
            session=db_session, user=None,
            page=1, page_size=20,
            method=None, search=None, mocked=True, failed=None,
        )
        assert res.pagination["total"] == 1
        assert res.data[0].mocked is True

    def test_filter_failed_includes_error_and_http_4xx_5xx(self, db_session):
        _seed_logs(db_session)
        res = push_log_mod.get_push_logs(
            session=db_session, user=None,
            page=1, page_size=20,
            method=None, search=None, mocked=None, failed=True,
        )
        statuses = {(r.response_status, r.error) for r in res.data}
        assert (0, "Invalid URL") in statuses
        assert (500, None) in statuses
        assert res.pagination["total"] == 2

    def test_filter_success_excludes_error_and_http_4xx_5xx(self, db_session):
        _seed_logs(db_session)
        res = push_log_mod.get_push_logs(
            session=db_session, user=None,
            page=1, page_size=20,
            method=None, search=None, mocked=None, failed=False,
        )
        assert res.pagination["total"] == 3
        assert all(r.error is None and r.response_status < 400 for r in res.data)

    def test_order_newest_first(self, db_session):
        rows = _seed_logs(db_session)
        res = push_log_mod.get_push_logs(
            session=db_session, user=None,
            page=1, page_size=20,
            method=None, search=None, mocked=None, failed=None,
        )
        ids = [r.id for r in res.data]
        assert ids == sorted(ids, reverse=True)
        assert len(rows) == 5


class TestPushLogDetailAndDelete:
    def test_detail_returns_record(self, db_session):
        rows = _seed_logs(db_session)
        res = push_log_mod.get_push_log(session=db_session, user=None, log_id=rows[0].id)
        assert res.code == 200
        assert res.data.final_url.endswith("token=abc123")
        assert res.data.request_body == '{"device":"sensor-1"}'

    def test_detail_missing_returns_404_code(self, db_session):
        res = push_log_mod.get_push_log(session=db_session, user=None, log_id=99999)
        assert res.code == 404

    def test_delete_one(self, db_session):
        rows = _seed_logs(db_session)
        res = push_log_mod.delete_push_log(session=db_session, user=None, log_id=rows[0].id)
        assert res.code == 200
        remaining = db_session.exec(select(PushLog)).all()
        assert len(remaining) == 4

    def test_clear_all(self, db_session):
        _seed_logs(db_session)
        res = push_log_mod.clear_push_logs(session=db_session, user=None)
        assert res.code == 200
        assert db_session.exec(select(PushLog)).all() == []


class TestSavePushLog:
    def test_save_persists_raw_and_final(self, db_session):
        from app.auth import UserInfo
        from app.routes.proxy import ProxyRequest, _save_push_log

        user = UserInfo(user_id="u1", username="alice", roles=[])
        req = ProxyRequest(
            url="https://iot.example.com/api/push?token={{token}}",
            method="post",
            headers={"Content-Type": "application/json", "X-Token": "{{token}}"},
            params={"token": "{{token}}"},
            data={"hello": "world"},
        )
        _save_push_log(
            db_session, user=user, request=req, environment_id=7,
            final_url="https://iot.example.com/api/push?token=abc",
            final_headers={"Content-Type": "application/json", "X-Token": "abc"},
            final_params={"token": "abc"},
            final_data={"hello": "world"},
            duration_ms=42,
            response_status=200,
            response_headers={"Content-Type": "application/json"},
            response_body={"ok": True},
        )
        log = db_session.exec(select(PushLog)).one()
        assert log.request_method == "POST"
        assert log.source_url == "https://iot.example.com/api/push?token={{token}}"
        assert log.final_url == "https://iot.example.com/api/push?token=abc"
        assert log.request_headers["X-Token"] == "{{token}}"
        assert log.final_headers["X-Token"] == "abc"
        assert log.final_body == '{"hello": "world"}'
        assert log.response_body == '{"ok": true}'
        assert log.response_status == 200
        assert log.duration_ms == 42
        assert log.environment_id == 7
        assert log.user_id == "u1"
        assert log.error is None
        assert log.mocked is False

    def test_save_error_record(self, db_session):
        from app.auth import UserInfo
        from app.routes.proxy import ProxyRequest, _save_push_log

        user = UserInfo(user_id="u2", username="bob", roles=[])
        req = ProxyRequest(url="https://down.example.com/x", method="GET")
        _save_push_log(
            db_session, user=user, request=req, environment_id=None,
            final_url="https://down.example.com/x",
            final_headers=None, final_params=None, final_data=None,
            duration_ms=10, error="Proxy error: connect timeout",
        )
        log = db_session.exec(select(PushLog)).one()
        assert log.response_status == 0
        assert log.error == "Proxy error: connect timeout"
        assert log.user_id == "u2"
        assert log.request_headers == {}


class TestBodyToText:
    def test_none(self):
        from app.routes.proxy import _body_to_text
        assert _body_to_text(None) is None

    def test_str_passthrough(self):
        from app.routes.proxy import _body_to_text
        assert _body_to_text("raw") == "raw"

    def test_dict_serialized(self):
        from app.routes.proxy import _body_to_text
        assert _body_to_text({"a": 1}) == '{"a": 1}'

    def test_list_serialized(self):
        from app.routes.proxy import _body_to_text
        assert _body_to_text([1, 2]) == '[1, 2]'
