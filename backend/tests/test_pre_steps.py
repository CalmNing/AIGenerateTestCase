"""Pre-step executor tests: set_variable / request / chaining / failure isolation."""

import asyncio

import httpx

from app.services.pre_steps import run_pre_steps


def _run(steps, variables=None):
    return asyncio.run(run_pre_steps(steps, dict(variables or {})))




class TestParamsStep:
    """params 类型步骤：一次性注入一组参数变量（参数统一在参数步骤设置）。"""

    def test_injects_multiple_variables_without_request(self, monkeypatch):
        def handler(request):
            raise AssertionError("params step must not send a request")

        _install_mock(monkeypatch, handler)

        variables, results = _run([
            {"type": "params", "params": {"tenantId": "t-1", "region": "cn-north"}},
        ])

        assert variables == {"tenantId": "t-1", "region": "cn-north"}
        assert results[0]["ok"] is True
        assert results[0]["params"] == {"tenantId": "t-1", "region": "cn-north"}

    def test_kv_list_shape_with_enabled_flag(self):
        variables, results = _run([
            {"type": "params", "params": [
                {"key": "a", "value": "1", "enabled": True},
                {"key": "b", "value": "2", "enabled": False},
                {"key": "", "value": "3"},
            ]},
        ])

        assert variables == {"a": "1"}
        assert results[0]["params"] == {"a": "1"}

    def test_values_can_reference_earlier_variables(self):
        variables, _ = _run([
            {"type": "set_variable", "key": "host", "value": "example.com"},
            {"type": "params", "params": {"url": "http://{{host}}/api"}},
        ])

        assert variables["url"] == "http://example.com/api"

    def test_empty_params_step_is_skipped(self):
        variables, results = _run([{"type": "params", "params": {}}])

        assert variables == {}
        assert results == []


class TestKvListRequestStep:
    """request 步骤的 headers/params 支持 [{key,value,enabled}] 列表形态。"""

    def test_headers_as_kv_list(self, monkeypatch):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"ok": True})

        _install_mock(monkeypatch, handler)

        variables, _ = _run([{
            "type": "request",
            "url": "http://biz.test/api",
            "headers": [
                {"key": "Authorization", "value": "Bearer tok", "enabled": True},
                {"key": "X-Skip", "value": "no", "enabled": False},
            ],
            "params": [{"key": "page", "value": "1"}],
        }])

        assert seen[0].headers["authorization"] == "Bearer tok"
        assert "x-skip" not in seen[0].headers
        assert str(seen[0].url) == "http://biz.test/api?page=1"


class TestSavedRequestSource:
    """request 步骤 source=saved：执行时按 id 现取已保存请求的最新配置。"""

    def _make_session(self, db_session, headers=None, params=None, body=None):
        from db.models import SavedRequest

        row = SavedRequest(
            name="登录接口",
            method="POST",
            url="http://biz.test/login",
            headers=headers or [],
            parameters=params or [],
            body=body,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    def test_saved_request_is_executed(self, monkeypatch, db_session):
        import asyncio

        from app.services.pre_steps import run_pre_steps

        self._make_session(
            db_session,
            headers=[{"key": "Authorization", "value": "Bearer x", "enabled": True}],
            params=[{"key": "app", "value": "iot", "enabled": True}],
            body='{"u": "admin"}',
        )
        # 给该请求补上后置提取规则，验证关联请求的提取规则也被沿用
        from db.models import SavedRequest

        row = db_session.get(SavedRequest, 1)
        row.post_extractions = [{"variable": "token", "jsonpath": "$.data.token"}]
        db_session.add(row)
        db_session.commit()

        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {"token": "tok-8"}})

        _install_mock(monkeypatch, handler)

        variables, results = asyncio.run(run_pre_steps(
            [{"type": "request", "source": "saved", "saved_request_id": 1}],
            {},
            db=db_session,
        ))

        assert str(seen[0].url) == "http://biz.test/login?app=iot"
        assert seen[0].headers["authorization"] == "Bearer x"
        assert seen[0].content == b'{"u": "admin"}'
        assert variables["token"] == "tok-8"
        assert results[0]["ok"] is True
        assert results[0]["name"] == "登录接口"

    def test_user_extracts_override_saved_post_extractions(self, monkeypatch, db_session):
        import asyncio

        from app.services.pre_steps import run_pre_steps

        # 关联请求自身带 post_extractions: access-token
        row = self._make_session(db_session)
        row.post_extractions = [{"variable": "access-token", "jsonpath": "$.data.token"}]
        db_session.add(row)
        db_session.commit()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"token": "tok-x"}})

        _install_mock(monkeypatch, handler)

        # 用户在前置步骤里显式配置了 token1 —— 必须以它为准
        variables, results = asyncio.run(run_pre_steps(
            [{
                "type": "request", "source": "saved", "saved_request_id": 1,
                "extracts": [{"key": "token1", "jsonpath": "$.data.token"}],
            }],
            {},
            db=db_session,
        ))

        assert variables["token1"] == "tok-x"
        assert "access-token" not in variables

    def test_saved_post_extractions_used_when_user_none(self, monkeypatch, db_session):
        import asyncio

        from app.services.pre_steps import run_pre_steps

        row = self._make_session(db_session)
        row.post_extractions = [{"variable": "access-token", "jsonpath": "$.data.token"}]
        db_session.add(row)
        db_session.commit()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"token": "tok-y"}})

        _install_mock(monkeypatch, handler)

        variables, _ = asyncio.run(run_pre_steps(
            [{"type": "request", "source": "saved", "saved_request_id": 1}],
            {},
            db=db_session,
        ))

        assert variables["access-token"] == "tok-y"

    def test_missing_saved_request_reports_error(self, db_session):
        import asyncio

        from app.services.pre_steps import run_pre_steps

        variables, results = asyncio.run(run_pre_steps(
            [{"type": "request", "source": "saved", "saved_request_id": 999}],
            {},
            db=db_session,
        ))

        assert variables == {}
        assert results[0]["ok"] is False
        assert "不存在" in results[0]["error"]

    def test_saved_step_without_db_reports_error(self):
        import asyncio

        from app.services.pre_steps import run_pre_steps

        variables, results = asyncio.run(run_pre_steps(
            [{"type": "request", "source": "saved", "saved_request_id": 1}],
            {},
        ))

        assert results[0]["ok"] is False


def _install_mock(monkeypatch, handler):
    """Replace outbound httpx client with MockTransport (no real network)."""
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("app.services.pre_steps.httpx.AsyncClient", _factory)


class TestSetVariable:
    def test_sets_variable_without_request(self, monkeypatch):
        def handler(request):
            raise AssertionError("set_variable must not send a request")

        _install_mock(monkeypatch, handler)

        variables, results = _run([
            {"type": "set_variable", "key": "tenantId", "value": "t-1"},
        ])

        assert variables["tenantId"] == "t-1"
        assert results[0]["ok"] is True
        assert results[0]["key"] == "tenantId"

    def test_value_can_reference_existing_variable(self, monkeypatch):
        variables, _ = _run(
            [{"type": "set_variable", "key": "url", "value": "http://{{host}}/api"}],
            {"host": "example.com"},
        )

        assert variables["url"] == "http://example.com/api"

    def test_step_without_key_is_skipped(self):
        variables, results = _run([{"type": "set_variable", "value": "x"}])

        assert variables == {}
        assert results == []


class TestRequestStep:
    def test_extracts_variable_from_response(self, monkeypatch):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {"token": "tok-1", "id": 7}})

        _install_mock(monkeypatch, handler)

        variables, results = _run([{
            "type": "request",
            "name": "login",
            "method": "POST",
            "url": "http://biz.test/login",
            "extracts": [
                {"key": "token", "jsonpath": "$.data.token"},
                {"key": "uid", "jsonpath": "$.data.id"},
            ],
        }])

        assert variables["token"] == "tok-1"
        assert variables["uid"] == "7"
        assert results[0]["ok"] is True
        assert results[0]["status_code"] == 200
        assert results[0]["extracted"] == {"token": "tok-1", "uid": "7"}
        assert len(seen) == 1
        assert str(seen[0].url) == "http://biz.test/login"

    def test_missing_jsonpath_is_skipped(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"token": "tok-1"}})

        _install_mock(monkeypatch, handler)

        variables, results = _run([{
            "type": "request",
            "url": "http://biz.test/login",
            "extracts": [{"key": "nope", "jsonpath": "$.data.missing"}],
        }])

        assert "nope" not in variables
        assert results[0]["extracted"] == {}

    def test_request_failure_does_not_raise(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        _install_mock(monkeypatch, handler)

        variables, results = _run([{
            "type": "request",
            "url": "http://biz.test/login",
            "extracts": [{"key": "token", "jsonpath": "$.token"}],
        }])

        assert variables == {}
        assert results[0]["ok"] is False
        assert results[0]["status_code"] == 500

    def test_transport_error_is_captured(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        _install_mock(monkeypatch, handler)

        variables, results = _run([{
            "type": "request",
            "url": "http://biz.test/login",
        }])

        assert variables == {}
        assert results[0]["ok"] is False
        assert results[0]["error"]


class TestChaining:
    def test_later_step_uses_earlier_extracted_variable(self, monkeypatch):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if "login" in str(request.url):
                return httpx.Response(200, json={"data": {"token": "tok-9"}})
            return httpx.Response(200, json={"ok": True})

        _install_mock(monkeypatch, handler)

        variables, results = _run([
            {
                "type": "request",
                "url": "http://biz.test/login",
                "extracts": [{"key": "token", "jsonpath": "$.data.token"}],
            },
            {
                "type": "request",
                "url": "http://biz.test/orders",
                "headers": {"Authorization": "Bearer {{token}}"},
            },
        ])

        assert variables["token"] == "tok-9"
        assert len(seen) == 2
        assert seen[1].headers["authorization"] == "Bearer tok-9"
        assert len(results) == 2

    def test_disabled_step_is_skipped(self, monkeypatch):
        def handler(request):
            raise AssertionError("disabled step must not send a request")

        _install_mock(monkeypatch, handler)

        variables, results = _run([{
            "type": "request",
            "url": "http://biz.test/login",
            "enabled": False,
        }])

        assert variables == {}
        assert results == []

    def test_empty_steps_returns_unchanged(self):
        variables, results = _run([], {"a": "1"})

        assert variables == {"a": "1"}
        assert results == []
