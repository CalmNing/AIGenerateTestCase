"""ui_automation 路由测试(直接调用路由函数,monkeypatch 后台任务;不碰浏览器)。"""
import asyncio
import inspect

import pytest
from fastapi import HTTPException
from sqlmodel import select

import app.routes.ui_automation as route
from db.models import PageObject, UIAutomationRun


@pytest.fixture()
def fake_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester")


@pytest.fixture()
def other_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u2", username="other")


@pytest.fixture()
def page_object(db_session):
    po = PageObject(
        name="Login",
        site_origin="http://a.com",
        page_path="#/login",
        url="http://a.com#/login",
        elements=[{
            "key": "username",
            "name": "用户名输入框",
            "locators": [{"strategy": "testid", "value": "login-username", "stability": 100}],
        }],
        user_id="u1",
    )
    db_session.add(po)
    db_session.commit()
    db_session.refresh(po)
    return po


class TestRouteContract:
    def test_launch_routes_are_async(self):
        """发起后台任务的路由必须是 async def,否则 asyncio.create_task 无事件循环 → 500。"""
        assert inspect.iscoroutinefunction(route.extract_page_object)
        assert inspect.iscoroutinefunction(route.regenerate_page_object)
        assert inspect.iscoroutinefunction(route.validate_page_object)
        assert inspect.iscoroutinefunction(route.run_page_object)
        assert inspect.iscoroutinefunction(route.start_login)


class TestExtract:
    def test_creates_run_and_launches(self, db_session, fake_user, monkeypatch):
        captured = {}

        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None,
                        mark_login_page=False, skip_login=False, environment_id=None, **kwargs):
            captured["run_id"] = run_id
            captured["login"] = login_overrides
            return "task-1"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        body = route.ExtractRequest(site_origin="http://a.com", page_path="#/login", login_username="admin", login_password="pw")
        result = asyncio.run(route.extract_page_object(body, db_session, fake_user))
        assert result.data["task_id"] == "task-1"
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.run_type == "extract"
        assert run.target_url == "http://a.com#/login"
        assert captured["login"]["username"] == "admin"
        # 凭据不落库
        assert "pw" not in str(run.result)

    def test_rejects_empty_url(self, db_session, fake_user):
        body = route.ExtractRequest()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.extract_page_object(body, db_session, fake_user))
        assert exc.value.status_code == 400

    def test_max_depth_defaults_to_three_and_stored_in_spec(self, db_session, fake_user, monkeypatch):
        captured = {}

        def fake_launch(run_id, request_db=None, **kwargs):
            captured["run_id"] = run_id
            return "task-md"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        body = route.ExtractRequest(site_origin="http://a.com", page_path="#/login")
        asyncio.run(route.extract_page_object(body, db_session, fake_user))
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.result["request"]["max_depth"] == 3

    def test_max_depth_clamped_by_request_schema(self, db_session, fake_user):
        with pytest.raises(Exception):
            route.ExtractRequest(max_depth=6)
        with pytest.raises(Exception):
            route.ExtractRequest(max_depth=-1)
        with pytest.raises(Exception):
            route.RegenerateRequest(max_depth=9)

    def test_extract_stores_custom_max_depth(self, db_session, fake_user, monkeypatch):
        captured = {}

        def fake_launch(run_id, request_db=None, **kwargs):
            captured["run_id"] = run_id
            return "task-md2"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        body = route.ExtractRequest(
            site_origin="http://a.com", page_path="#/login", max_depth=1
        )
        asyncio.run(route.extract_page_object(body, db_session, fake_user))
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.result["request"]["max_depth"] == 1

    def test_regenerate_stores_max_depth(self, db_session, fake_user, page_object, monkeypatch):
        captured = {}

        def fake_launch(run_id, request_db=None, **kwargs):
            captured["run_id"] = run_id
            return "task-md3"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        asyncio.run(route.regenerate_page_object(
            page_object.id, route.RegenerateRequest(max_depth=5), db_session, fake_user
        ))
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.result["request"]["max_depth"] == 5
        assert run.result["request"]["merge"] is True

    def test_skip_login_overrides_explicit_selection(self, db_session, fake_user, monkeypatch):
        """没选登录页对象(显式不使用登录态):即便请求里带了登录页对象 id,也不解析、不复用。"""
        login_po = PageObject(
            name="SSO", site_origin="http://a.com", url="http://a.com/sso",
            is_login_page=True, user_id="u1",
        )
        db_session.add(login_po)
        db_session.commit()
        db_session.refresh(login_po)
        captured = {}

        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None,
                        mark_login_page=False, skip_login=False, environment_id=None, **kwargs):
            captured["login_page_object_id"] = login_page_object_id
            captured["skip_login"] = skip_login
            return "task-a"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        body = route.ExtractRequest(
            site_origin="http://a.com",
            page_path="#/target",
            login_page_object_id=login_po.id,
            skip_login=True,
        )
        asyncio.run(route.extract_page_object(body, db_session, fake_user))
        assert captured["skip_login"] is True
        assert captured["login_page_object_id"] is None


class TestCrud:
    def test_list_shared_across_users(self, db_session, fake_user, other_user, page_object):
        db_session.add(PageObject(name="Other", user_id="u2"))
        db_session.commit()
        rows = route.list_page_objects(db_session, fake_user).data
        assert {r.name for r in rows} == {"Login", "Other"}

    def test_get_other_user_ok(self, db_session, other_user, page_object):
        result = route.get_page_object(page_object.id, db_session, other_user)
        assert result.data.id == page_object.id

    def test_create_and_update(self, db_session, fake_user):
        created = route.create_page_object(
            route.PageObjectPayload(name="Search", site_origin="http://b.com", elements=[]),
            db_session, fake_user,
        ).data
        assert created.url == "http://b.com"
        updated = route.update_page_object(
            created.id,
            route.PageObjectPayload(name="Search2", elements=[{"key": "q"}]),
            db_session, fake_user,
        ).data
        assert updated.name == "Search2"
        assert updated.elements == [{"key": "q"}]

    def test_delete(self, db_session, fake_user, page_object):
        route.delete_page_object(page_object.id, db_session, fake_user)
        assert db_session.get(PageObject, page_object.id) is None


class TestValidateAndRun:
    def test_validate_launches(self, db_session, fake_user, page_object, monkeypatch):
        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None,
                        mark_login_page=False, skip_login=False, environment_id=None, **kwargs):
            asyncio.get_running_loop()  # 路由必须是 async,否则后台任务无事件循环可挂
            return "task-v"

        monkeypatch.setattr(route, "launch_validation", fake_launch)
        result = asyncio.run(route.validate_page_object(page_object.id, db_session, fake_user))
        assert result.data["task_id"] == "task-v"

    def test_validate_missing_url(self, db_session, fake_user, page_object):
        page_object.url = ""
        db_session.add(page_object)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.validate_page_object(page_object.id, db_session, fake_user))
        assert exc.value.status_code == 400

    def test_validate_passes_login_overrides(self, db_session, fake_user, page_object, monkeypatch):
        captured = {}

        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None,
                        mark_login_page=False, skip_login=False, environment_id=None, **kwargs):
            asyncio.get_running_loop()
            captured["login"] = login_overrides
            return "task-v"

        monkeypatch.setattr(route, "launch_validation", fake_launch)
        body = route.ValidateRequest(login_username="admin", login_password="pw")
        result = asyncio.run(route.validate_page_object(page_object.id, db_session, fake_user, body))
        assert result.data["task_id"] == "task-v"
        assert captured["login"]["username"] == "admin"
        assert captured["login"]["password"] == "pw"

    def test_run_rejects_empty_steps(self, db_session, fake_user, page_object):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.run_page_object(page_object.id, route.RunRequest(steps=[]), db_session, fake_user))
        assert exc.value.status_code == 400

    def test_run_launches(self, db_session, fake_user, page_object, monkeypatch):
        captured = {}

        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None,
                        mark_login_page=False, skip_login=False, environment_id=None, **kwargs):
            asyncio.get_running_loop()
            captured["run_id"] = run_id
            return "task-e"

        monkeypatch.setattr(route, "launch_execution", fake_launch)
        result = asyncio.run(route.run_page_object(
            page_object.id,
            route.RunRequest(steps=[{"element": "username", "action": "fill", "value": "admin"}]),
            db_session, fake_user,
        ))
        assert result.data["task_id"] == "task-e"
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.run_type == "execute"
        assert run.steps[0]["action"] == "fill"

    def test_run_rejects_disabled_elements(self, db_session, fake_user, page_object, monkeypatch):
        page_object.elements = [{
            "key": "username",
            "name": "用户名输入框",
            "enabled": False,
            "locators": [{"strategy": "testid", "value": "login-username", "stability": 100}],
        }]
        db_session.add(page_object)
        db_session.commit()

        def fail_launch(*args, **kwargs):
            raise AssertionError("disabled elements must not launch")

        monkeypatch.setattr(route, "launch_execution", fail_launch)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.run_page_object(
                page_object.id,
                route.RunRequest(steps=[{"element": "username", "action": "fill", "value": "admin"}]),
                db_session, fake_user,
            ))
        assert exc.value.status_code == 400
        assert "步骤中存在无效的元素" in str(exc.value.detail)
        assert "username" in str(exc.value.detail)

    def test_run_allows_disabled_elements_not_referenced(self, db_session, fake_user, page_object, monkeypatch):
        page_object.elements = [
            {
                "key": "username",
                "name": "用户名输入框",
                "enabled": True,
                "locators": [{"strategy": "testid", "value": "login-username", "stability": 100}],
            },
            {
                "key": "unused",
                "name": "未使用的无效元素",
                "enabled": False,
                "locators": [{"strategy": "css", "value": "#unused", "stability": 50}],
            },
        ]
        db_session.add(page_object)
        db_session.commit()
        captured = {}

        def fake_launch(run_id, **kwargs):
            asyncio.get_running_loop()
            captured["run_id"] = run_id
            return "task-e2"

        monkeypatch.setattr(route, "launch_execution", fake_launch)
        result = asyncio.run(route.run_page_object(
            page_object.id,
            route.RunRequest(steps=[{"element": "username", "action": "fill", "value": "admin"}]),
            db_session, fake_user,
        ))
        assert result.data["task_id"] == "task-e2"
        assert captured["run_id"]


class TestLogin:
    def test_creates_run_and_launches(self, db_session, fake_user, monkeypatch):
        captured = {}

        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None,
                        mark_login_page=False, skip_login=False, environment_id=None, **kwargs):
            asyncio.get_running_loop()
            captured["run_id"] = run_id
            captured["login"] = login_overrides
            return "task-l"

        monkeypatch.setattr(route, "launch_login", fake_launch)
        body = route.LoginRequest(url="http://a.com#/login", login_username="admin", login_password="pw")
        result = asyncio.run(route.start_login(body, db_session, fake_user))
        assert result.data["task_id"] == "task-l"
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.run_type == "login"
        assert run.target_url == "http://a.com#/login"
        assert captured["login"]["username"] == "admin"
        assert "pw" not in str(run.result or "")

    def test_rejects_empty_url(self, db_session, fake_user):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.start_login(route.LoginRequest(), db_session, fake_user))
        assert exc.value.status_code == 400


class TestExport:
    def test_export_returns_code(self, db_session, fake_user, page_object):
        response = route.export_page_object(page_object.id, db_session, fake_user)
        body = response.body.decode("utf-8")
        assert "class LoginPage" in body
        assert "self.page.get_by_test_id('login-username')" in body

    def test_regenerate_launches_and_links_page_object(self, db_session, fake_user, page_object, monkeypatch):
        captured = {}

        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None,
                        mark_login_page=False, skip_login=False, environment_id=None, **kwargs):
            asyncio.get_running_loop()
            captured["run_id"] = run_id
            return "task-r"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        result = asyncio.run(route.regenerate_page_object(
            page_object.id, route.RegenerateRequest(merge=False), db_session, fake_user
        ))
        assert result.data["task_id"] == "task-r"
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.page_object_id == page_object.id
        assert run.run_type == "extract"
        assert run.task_id == "task-r"
        assert run.result["request"]["merge"] is False

    def test_regenerate_skip_login_ignores_existing_link(
        self, db_session, fake_user, page_object, monkeypatch
    ):
        """重新生成时没选登录页对象:该对象已关联的登录页对象也不回退复用。"""
        login_po = PageObject(
            name="SSO", site_origin="http://a.com", url="http://a.com/sso",
            is_login_page=True, user_id="u1",
        )
        db_session.add(login_po)
        db_session.commit()
        db_session.refresh(login_po)
        page_object.login_page_object_id = login_po.id
        db_session.add(page_object)
        db_session.commit()
        captured = {}

        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None,
                        mark_login_page=False, skip_login=False, environment_id=None, **kwargs):
            captured["login_page_object_id"] = login_page_object_id
            captured["skip_login"] = skip_login
            return "task-s"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        asyncio.run(route.regenerate_page_object(
            page_object.id, route.RegenerateRequest(skip_login=True), db_session, fake_user
        ))
        assert captured["skip_login"] is True
        assert captured["login_page_object_id"] is None

    def test_regenerate_still_falls_back_to_existing_link(
        self, db_session, fake_user, page_object, monkeypatch
    ):
        """对照:不传 skip_login 时保持原行为 —— 回退用对象已关联的登录页对象。"""
        login_po = PageObject(
            name="SSO", site_origin="http://a.com", url="http://a.com/sso",
            is_login_page=True, user_id="u1",
        )
        db_session.add(login_po)
        db_session.commit()
        db_session.refresh(login_po)
        page_object.login_page_object_id = login_po.id
        db_session.add(page_object)
        db_session.commit()
        captured = {}

        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None,
                        mark_login_page=False, skip_login=False, environment_id=None, **kwargs):
            captured["login_page_object_id"] = login_page_object_id
            captured["skip_login"] = skip_login
            return "task-f"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        asyncio.run(route.regenerate_page_object(
            page_object.id, route.RegenerateRequest(), db_session, fake_user
        ))
        assert captured["skip_login"] is False
        assert captured["login_page_object_id"] == login_po.id

    def test_regenerate_missing_url(self, db_session, fake_user, page_object):
        page_object.url = ""
        db_session.add(page_object)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.regenerate_page_object(page_object.id, route.RegenerateRequest(), db_session, fake_user))
        assert exc.value.status_code == 400

    def test_task_falls_back_to_db_after_restart(self, db_session, fake_user, page_object):
        run = UIAutomationRun(
            page_object_id=page_object.id,
            user_id="u1",
            run_type="extract",
            task_id="stale-task",
            status="PASSED",
            summary="已完成",
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        result = route.get_automation_task("stale-task", db_session, fake_user)
        assert result.data["status"] == "done"
        assert result.data["result_status"] == "PASSED"
        assert result.data["record_id"] == run.id

    def test_task_fallback_shared(self, db_session, other_user, page_object):
        run = UIAutomationRun(user_id="u1", run_type="extract", task_id="stale-task", status="ERROR")
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        result = route.get_automation_task("stale-task", db_session, other_user)
        assert result.data["record_id"] == run.id

    def test_fail_orphan_runs_marks_interrupted(self, db_session, page_object):
        from app.services.page_object_runner import fail_orphan_runs

        db_session.add(UIAutomationRun(user_id="u1", run_type="execute", task_id="t1", status="RUNNING"))
        db_session.add(UIAutomationRun(user_id="u1", run_type="execute", task_id="t2", status="PASSED"))
        db_session.commit()
        assert fail_orphan_runs(db_session) == 1
        db_session.commit()
        running = db_session.exec(select(UIAutomationRun).where(UIAutomationRun.task_id == "t1")).first()
        done = db_session.exec(select(UIAutomationRun).where(UIAutomationRun.task_id == "t2")).first()
        assert running.status == "ERROR"
        assert done.status == "PASSED"

    def test_get_run_shared(self, db_session, other_user, page_object):
        run = UIAutomationRun(page_object_id=page_object.id, user_id="u1", run_type="validate")
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        result = route.get_run(run.id, db_session, other_user)
        assert result.data.id == run.id


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """monkeypatch 目标:拦截 httpx.AsyncClient,记录请求并返回固定响应。"""

    last_request: dict = {}
    payload: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None, params=None):
        _FakeAsyncClient.last_request = {"url": url, "headers": headers, "params": params}
        return _FakeResponse(_FakeAsyncClient.payload)


class TestCreatePageObject:
    def test_create_idempotent_by_url_same_user(self, db_session, fake_user, page_object):
        """同用户同 URL 重复创建应返回已有记录,不产生重复行。"""
        body = route.PageObjectPayload(
            name="Login-copy",
            site_origin="http://a.com",
            page_path="#/login",
            url="http://a.com#/login",
        )
        result = route.create_page_object(body, db_session, fake_user)
        assert result.data.id == page_object.id
        rows = db_session.exec(select(PageObject).where(PageObject.url == "http://a.com#/login")).all()
        assert len(rows) == 1

    def test_create_different_user_deduped(self, db_session, fake_user, other_user, page_object):
        body = route.PageObjectPayload(name="Other", url="http://a.com#/login")
        result = route.create_page_object(body, db_session, other_user)
        assert result.data.id == page_object.id


class TestLubanProxy:
    def test_rejects_missing_token_or_base(self, db_session, fake_user):
        body = route.LubanProxyRequest(base_url="http://a.com", access_token="")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.luban_modules(body, db_session, fake_user))
        assert exc.value.status_code == 400
        body = route.LubanProxyRequest(base_url="", access_token="tok")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.luban_modules(body, db_session, fake_user))
        assert exc.value.status_code == 400

    def test_modules_forwards_headers_and_params(self, db_session, fake_user, monkeypatch):
        _FakeAsyncClient.payload = {"success": True, "data": [{"id": "1", "code": "project", "url": "/builder", "name": "项目管理"}]}
        monkeypatch.setattr(route.httpx, "AsyncClient", _FakeAsyncClient)
        body = route.LubanProxyRequest(base_url="http://a.com/", access_token=" tok ", res_id="42")
        result = asyncio.run(route.luban_modules(body, db_session, fake_user))
        req = _FakeAsyncClient.last_request
        assert req["url"] == "http://a.com/service/acl/biz/modules"
        assert req["params"] == {"type": "web"}
        assert req["headers"]["access-token"] == "tok"
        assert req["headers"]["resId"] == "42"
        assert result.data[0]["code"] == "project"

    def test_menus_requires_module_code(self, db_session, fake_user):
        body = route.LubanMenusRequest(base_url="http://a.com", access_token="tok")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.luban_menus(body, db_session, fake_user))
        assert exc.value.status_code == 400

    def test_menus_forwards_sys_module_code(self, db_session, fake_user, monkeypatch):
        _FakeAsyncClient.payload = {
            "success": True,
            "data": [{"id": "9", "parentId": "-1", "name": "WBS模板", "type": "menu", "url": "/configuration/wbsTemplate"}],
        }
        monkeypatch.setattr(route.httpx, "AsyncClient", _FakeAsyncClient)
        body = route.LubanMenusRequest(base_url="http://a.com", access_token="tok", sys_module_code="project")
        result = asyncio.run(route.luban_menus(body, db_session, fake_user))
        assert _FakeAsyncClient.last_request["params"] == {"sysModuleCode": "project"}
        assert result.data[0]["name"] == "WBS模板"

    def test_upstream_error_maps_to_502(self, db_session, fake_user, monkeypatch):
        _FakeAsyncClient.payload = {"success": False, "code": 401, "msg": "unauthorized"}
        monkeypatch.setattr(route.httpx, "AsyncClient", _FakeAsyncClient)
        body = route.LubanProxyRequest(base_url="http://a.com", access_token="tok")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.luban_modules(body, db_session, fake_user))
        assert exc.value.status_code == 502


class TestClearElements:
    """「清空元素」复用 PUT /page-objects/{id}:必须确认空数组能真的落库。

    后端是 `if body.elements is not None`(PageObjectPayload.elements 默认 `[]`),
    若哪天改成真值判断,清空会静默失效 —— 界面提示成功但元素还在。
    """

    def _payload(self, po, elements):
        return route.PageObjectPayload(
            name=po.name,
            site_origin=po.site_origin or "",
            page_path=po.page_path or "",
            url=po.url or "",
            description=po.description or "",
            elements=elements,
        )

    def test_empty_elements_are_persisted(self, db_session, fake_user, page_object):
        assert len(page_object.elements) == 1
        route.update_page_object(page_object.id, self._payload(page_object, []), db_session, fake_user)
        db_session.refresh(page_object)
        assert page_object.elements == []
        # 必须落成空列表而不是 None:前端按数组渲染,None 会让元素表崩
        assert page_object.elements is not None

    def test_clear_does_not_touch_other_fields(self, db_session, fake_user, page_object):
        route.update_page_object(page_object.id, self._payload(page_object, []), db_session, fake_user)
        db_session.refresh(page_object)
        assert page_object.name == "Login"
        assert page_object.url == "http://a.com#/login"

    def test_other_users_can_clear(self, db_session, other_user, page_object):
        route.update_page_object(page_object.id, self._payload(page_object, []), db_session, other_user)
        db_session.refresh(page_object)
        assert page_object.elements == []


class TestSiteOriginVariables:
    """站点地址支持 {{var}} 变量:页面对象存原文,执行时按默认环境参数解析。

    规则:换环境只需改全局参数,页面对象无需逐个重填;变量未定义时 400 快速失败。
    """

    @pytest.fixture()
    def env(self, db_session):
        from db.models import GlobalParameter
        db_session.add(GlobalParameter(
            name="默认环境", is_default=True,
            parameters=[{"key": "web_url", "value": "http://env-a.com"}],
        ))
        db_session.commit()

    def test_extract_resolves_target_and_keeps_raw(self, db_session, fake_user, env, monkeypatch):
        """执行 URL 用解析后的值;request spec 存原文(新建页面对象按原文落库)。"""
        captured = {}

        def fake_launch(run_id, **kwargs):
            captured["run_id"] = run_id
            return "task-var-1"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        body = route.ExtractRequest(site_origin="{{web_url}}", page_path="#/login")
        asyncio.run(route.extract_page_object(body, db_session, fake_user))
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.target_url == "http://env-a.com#/login"
        assert run.result["request"]["site_origin"] == "{{web_url}}"
        assert run.result["request"]["url"] == "{{web_url}}#/login"

    def test_extract_rejects_undefined_variable(self, db_session, fake_user, env):
        body = route.ExtractRequest(site_origin="{{nope}}", page_path="#/login")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.extract_page_object(body, db_session, fake_user))
        assert exc.value.status_code == 400
        assert "nope" in exc.value.detail

    def test_validate_resolves_variable_keeps_po_raw(self, db_session, fake_user, env, monkeypatch):
        """验证时解析变量,页面对象本身保持原文。"""
        po = PageObject(
            name="Home", site_origin="{{web_url}}", page_path="#/home",
            url="{{web_url}}#/home", user_id="u1",
        )
        db_session.add(po)
        db_session.commit()
        db_session.refresh(po)
        monkeypatch.setattr(route, "launch_validation", lambda run_id, **kw: "task-var-2")
        result = asyncio.run(route.validate_page_object(po.id, db_session, fake_user, None))
        run = db_session.get(UIAutomationRun, result.data["run_id"])
        assert run.target_url == "http://env-a.com#/home"
        db_session.refresh(po)
        assert po.url == "{{web_url}}#/home"

    def test_run_rejects_undefined_variable(self, db_session, fake_user, env):
        po = PageObject(
            name="Home", url="{{missing}}#/x", steps=[{"action": "点击"}], user_id="u1",
        )
        db_session.add(po)
        db_session.commit()
        db_session.refresh(po)
        body = route.RunRequest(steps=[{"action": "点击"}])
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.run_page_object(po.id, body, db_session, fake_user))
        assert exc.value.status_code == 400
        assert "missing" in exc.value.detail

    def test_luban_base_url_supports_variable(self, db_session, fake_user, env, monkeypatch):
        """抽取表单站点地址填变量时,鲁班菜单代理也要能用。"""
        _FakeAsyncClient.payload = {"success": True, "data": []}
        monkeypatch.setattr(route.httpx, "AsyncClient", _FakeAsyncClient)
        body = route.LubanProxyRequest(base_url="{{web_url}}/", access_token="tok")
        asyncio.run(route.luban_modules(body, db_session, fake_user))
        assert _FakeAsyncClient.last_request["url"] == "http://env-a.com/service/acl/biz/modules"

    def test_extract_uses_selected_environment_id(self, db_session, fake_user, env, monkeypatch):
        """前端传当前选中环境时,{{var}} 按该环境解析,不用默认环境。"""
        from db.models import GlobalParameter
        other = GlobalParameter(
            name="环境B", is_default=False,
            parameters=[{"key": "web_url", "value": "http://env-b.com"}],
        )
        db_session.add(other)
        db_session.commit()
        db_session.refresh(other)

        captured = {}

        def fake_launch(run_id, **kwargs):
            captured["run_id"] = run_id
            captured["environment_id"] = kwargs.get("environment_id")
            return "task-env-b"

        monkeypatch.setattr(route, "launch_extraction", fake_launch)
        body = route.ExtractRequest(
            site_origin="{{web_url}}", page_path="#/login", environment_id=other.id
        )
        asyncio.run(route.extract_page_object(body, db_session, fake_user))
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.target_url == "http://env-b.com#/login"
        assert captured["environment_id"] == other.id
        assert run.result["request"]["site_origin"] == "{{web_url}}"

    def test_luban_uses_selected_environment_id(self, db_session, fake_user, env, monkeypatch):
        """鲁班代理 base_url 解析也要按选中环境。"""
        from db.models import GlobalParameter
        other = GlobalParameter(
            name="环境B", is_default=False,
            parameters=[{"key": "web_url", "value": "http://env-b.com"}],
        )
        db_session.add(other)
        db_session.commit()
        db_session.refresh(other)
        _FakeAsyncClient.payload = {"success": True, "data": []}
        monkeypatch.setattr(route.httpx, "AsyncClient", _FakeAsyncClient)
        body = route.LubanProxyRequest(
            base_url="{{web_url}}/", access_token="tok", environment_id=other.id
        )
        asyncio.run(route.luban_modules(body, db_session, fake_user))
        assert _FakeAsyncClient.last_request["url"] == "http://env-b.com/service/acl/biz/modules"


class TestTrailingSlashTargetUrl:
    """web_url 带尾斜杠 + 页面路径以 / 开头时不得拼出双斜杠。

    双斜杠 path(//)会让 SPA 路由异常/重定向到登录页,抽取到的是登录页元素。
    """

    @pytest.fixture()
    def env_slash(self, db_session):
        from db.models import GlobalParameter
        db_session.add(GlobalParameter(
            name="默认环境", is_default=True,
            parameters=[{"key": "web_url", "value": "http://env-a.com/"}],
        ))
        db_session.commit()

    @pytest.fixture()
    def po_double(self, db_session):
        po = PageObject(
            name="Duty", site_origin="{{web_url}}",
            page_path="/#/home/builder/goal",
            url="{{web_url}}/#/home/builder/goal",
            user_id="u1",
        )
        db_session.add(po)
        db_session.commit()
        db_session.refresh(po)
        return po

    def test_regenerate_target_url_no_double_slash(self, db_session, fake_user, env_slash, po_double, monkeypatch):
        monkeypatch.setattr(route, "launch_extraction", lambda run_id, **kw: "task-slash")
        result = asyncio.run(route.regenerate_page_object(po_double.id, route.RegenerateRequest(), db_session, fake_user))
        run = db_session.get(UIAutomationRun, result.data["run_id"])
        assert run.target_url == "http://env-a.com/#/home/builder/goal"
        assert "//" not in run.target_url.split("://", 1)[1]

    def test_validate_target_url_no_double_slash(self, db_session, fake_user, env_slash, po_double, monkeypatch):
        monkeypatch.setattr(route, "launch_validation", lambda run_id, **kw: "task-slash-v")
        result = asyncio.run(route.validate_page_object(po_double.id, db_session, fake_user, None))
        run = db_session.get(UIAutomationRun, result.data["run_id"])
        assert run.target_url == "http://env-a.com/#/home/builder/goal"

    def test_run_target_url_no_double_slash(self, db_session, fake_user, env_slash, po_double, monkeypatch):
        po_double.steps = [{"action": "点击"}]
        db_session.add(po_double)
        db_session.commit()
        monkeypatch.setattr(route, "launch_execution", lambda run_id, **kw: "task-slash-x")
        result = asyncio.run(route.run_page_object(po_double.id, route.RunRequest(), db_session, fake_user))
        run = db_session.get(UIAutomationRun, result.data["run_id"])
        assert run.target_url == "http://env-a.com/#/home/builder/goal"

    def test_extract_with_slash_origin_and_slash_path(self, db_session, fake_user, env_slash, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            route, "launch_extraction",
            lambda run_id, **kw: captured.setdefault("run_id", run_id) or "task-slash-e",
        )
        body = route.ExtractRequest(site_origin="{{web_url}}", page_path="/#/home/x")
        asyncio.run(route.extract_page_object(body, db_session, fake_user))
        run = db_session.get(UIAutomationRun, captured["run_id"])
        assert run.target_url == "http://env-a.com/#/home/x"
