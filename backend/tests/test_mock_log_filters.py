"""Tests for Mock 日志路由：来源分类过滤、方法/路径过滤、分页总数与批量清空。

纯单元测试，使用内存 SQLite（conftest.db_engine/db_session），直接调用路由函数。
"""
from sqlmodel import Session, select

import app.routes.mock_log as mock_log_mod
from db.models import MockLog
from utils.base_response import Response


def _seed_logs(session: Session):
    rows = [
        MockLog(config_name="[代理→Mock:用户服务] 获取用户", request_method="GET",
                request_path="/api/mock/users/1", response_status_code=200, matched=True),
        MockLog(config_name="[代理透传]", request_method="GET",
                request_path="/real/other", response_status_code=404, matched=False),
        MockLog(config_name="[代理→透传:用户服务]", request_method="POST",
                request_path="/api/mock/users/1", response_status_code=502, matched=False),
        MockLog(config_name="[项目Mock] 合同服务 GET /users/{id}", request_method="GET",
                request_path="/api/mock/contract/users/7", response_status_code=200, matched=True),
        MockLog(config_name="获取订单", request_method="DELETE",
                request_path="/api/mock/orders/9", response_status_code=200, matched=True),
    ]
    for r in rows:
        session.add(r)
    session.commit()
    return rows


class TestResponsePagination:
    def test_pagination_field_serialized(self):
        resp = Response(data=[1, 2], pagination={"total": 2, "page": 1, "page_size": 20})
        payload = resp.model_dump()
        assert payload["pagination"] == {"total": 2, "page": 1, "page_size": 20}

    def test_pagination_default_none(self):
        assert Response().pagination is None


class TestSourceFilter:
    def test_proxy_mock_only(self, db_session):
        _seed_logs(db_session)
        cond = mock_log_mod._source_condition("proxy_mock")
        rows = db_session.exec(select(MockLog).where(cond)).all()
        assert len(rows) == 1
        assert rows[0].config_name.startswith("[代理→Mock")

    def test_passthrough_includes_both_prefixes(self, db_session):
        _seed_logs(db_session)
        cond = mock_log_mod._source_condition("passthrough")
        rows = db_session.exec(select(MockLog).where(cond)).all()
        assert {r.config_name for r in rows} == {"[代理透传]", "[代理→透传:用户服务]"}

    def test_project_mock_only(self, db_session):
        _seed_logs(db_session)
        cond = mock_log_mod._source_condition("project_mock")
        rows = db_session.exec(select(MockLog).where(cond)).all()
        assert len(rows) == 1
        assert rows[0].config_name.startswith("[项目Mock]")

    def test_missed_excludes_passthrough(self, db_session):
        _seed_logs(db_session)
        cond = mock_log_mod._source_condition("missed")
        rows = db_session.exec(select(MockLog).where(cond)).all()
        assert rows == []

    def test_mock_excludes_proxy_and_project(self, db_session):
        _seed_logs(db_session)
        cond = mock_log_mod._source_condition("mock")
        rows = db_session.exec(select(MockLog).where(cond)).all()
        assert [r.config_name for r in rows] == ["获取订单"]

    def test_unknown_source_returns_none_condition(self):
        assert mock_log_mod._source_condition("whatever") is None


class TestMockLogsRoute:
    def test_list_returns_pagination_total(self, db_session):
        _seed_logs(db_session)
        res = mock_log_mod.get_mock_logs(
            session=db_session, user=None,
            page=1, page_size=2,
            config_id=None, matched=None, method=None, search=None, source=None,
        )
        assert res.code == 200
        assert len(res.data) == 2
        assert res.pagination["total"] == 5

    def test_filter_by_method_and_search(self, db_session):
        _seed_logs(db_session)
        res = mock_log_mod.get_mock_logs(
            session=db_session, user=None,
            page=1, page_size=20,
            config_id=None, matched=None, method="get", search="/users",
            source=None,
        )
        assert res.pagination["total"] == 2
        assert all(r.request_method == "GET" for r in res.data)

    def test_filter_by_source(self, db_session):
        _seed_logs(db_session)
        res = mock_log_mod.get_mock_logs(
            session=db_session, user=None,
            page=1, page_size=20,
            config_id=None, matched=None, method=None, search=None,
            source="project_mock",
        )
        assert res.pagination["total"] == 1
        assert res.data[0].config_name.startswith("[项目Mock]")

    def test_clear_all(self, db_session):
        _seed_logs(db_session)
        res = mock_log_mod.clear_mock_logs(session=db_session, user=None)
        assert res.code == 200
        remaining = db_session.exec(select(MockLog)).all()
        assert remaining == []
