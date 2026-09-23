"""登录 = 「配了执行步骤的页面对象」:其他页面对象关联它复用登录态的完整链路。

覆盖:登录态复用/自动登录/步骤回退/状态回写/会话落盘/CRUD 关联。
全部走 fake agent 与 fake 浏览器阶段,不碰真 Playwright。
"""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.routes.ui_automation as route
import app.services.browser_agent as ba
import app.services.page_login as pl
import app.services.page_object_runner as runner_mod
from app.services.page_object_runner import PageObjectRunner
from db.models import PageObject, UIAutomationRun


@pytest.fixture()
def fake_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester")


@pytest.fixture()
def other_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u2", username="other")


def _write_state(path, *, cookies=1, origins=0):
    payload = {
        "cookies": [{"name": f"c{i}", "value": "v"} for i in range(cookies)],
        "origins": [{"origin": "http://a.com", "localStorage": []} for _ in range(origins)],
    }
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=True)
    return str(path)


def _make_page(db, *, name="页面", url="http://a.com#/home", user_id="u1", **kwargs):
    row = PageObject(name=name, url=url, site_origin="http://a.com", user_id=user_id, **kwargs)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


LOGIN_STEPS = [
    {"element": "username", "action": "fill", "value": "admin"},
    {"element": "password", "action": "fill", "value": "pw"},
    {"element": "submit", "action": "click", "value": ""},
]
LOGIN_ELEMENTS = [
    {"key": "username", "locators": [{"strategy": "css", "value": "#u", "stability": 90}]},
    {"key": "password", "locators": [{"strategy": "css", "value": "#p", "stability": 90}]},
    {"key": "submit", "locators": [{"strategy": "css", "value": "#s", "stability": 90}]},
]


class FakeLoginAgent:
    """模拟 browser-use Agent:把登录态写进 storage_state 文件。"""

    def __init__(self, llm, prompt, storage_state=None, *, success=True, write_state=True):
        self._success = success
        self._storage_state = storage_state
        self._write_state = write_state
        self.history_callback = None

    async def run(self, max_steps=30):
        if self._write_state and self._storage_state:
            _write_state(self._storage_state)
        return SimpleNamespace(success=self._success, final_result=lambda: "登录完成")


def _agent_factory(*, success=True, write_state=True):
    def build(llm, prompt, storage_state=None):
        return FakeLoginAgent(llm, prompt, storage_state, success=success, write_state=write_state)

    return build


@pytest.fixture()
def login_page(db_session, monkeypatch, tmp_path):
    """一个典型的登录页对象:抽好了元素、配好了登录步骤。"""
    monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
    row = _make_page(
        db_session,
        name="登录页",
        url="http://a.com/sso/",
        is_login_page=True,
        steps=LOGIN_STEPS,
        elements=LOGIN_ELEMENTS,
    )
    pl.ensure_session_path(row)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# 登录态文件与状态视图
# ---------------------------------------------------------------------------

class TestSessionHelpers:
    def test_session_path_isolated_per_page_object(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        first = _make_page(db_session, name="A", url="http://a.com/a")
        second = _make_page(db_session, name="B", url="http://a.com/b")
        assert pl.ensure_session_path(first) != pl.ensure_session_path(second)
        assert str(pl.page_session_path(first.id)).endswith(f"po{first.id}.json")

    def test_session_view_reports_counts_without_secrets(self, login_page, db_session):
        _write_state(pl.ensure_session_path(login_page), cookies=3, origins=1)
        pl.refresh_status(db_session, login_page)
        db_session.refresh(login_page)
        view = pl.session_view(login_page)
        assert view["session_ready"] is True
        assert view["session_status"] == "READY"
        assert view["session_cookie_count"] == 3
        assert view["session_origin_count"] == 1
        assert not any("value" in str(v) for v in view.values())

    def test_clear_session_resets_status(self, login_page, db_session):
        path = _write_state(pl.ensure_session_path(login_page))
        pl.refresh_status(db_session, login_page)
        view = pl.clear_session(db_session, login_page)
        assert view["session_status"] == "NONE"
        assert view["session_ready"] is False
        assert not os.path.exists(path)


# ---------------------------------------------------------------------------
# Runner:复用登录态 / 自动登录 / 无步骤回退
# ---------------------------------------------------------------------------

class TestEnsureSessionViaLoginPage:
    def test_reuses_existing_session_without_login(
        self, db_session, login_page, monkeypatch
    ):
        state = _write_state(pl.ensure_session_path(login_page))
        called = []

        async def fake_execute(*args, **kwargs):  # pragma: no cover - 不该被调用
            called.append(args)
            return True

        monkeypatch.setattr(runner_mod, "_execute_in_browser", fake_execute)
        target = _make_page(db_session, name="目标页", url="http://a.com#/target")
        target.login_page_object_id = login_page.id
        db_session.add(target)
        db_session.commit()

        session = PageObjectRunner(db_session, agent_factory=_agent_factory())
        result = asyncio.run(session._ensure_session(db_session, target.url, None, target, "u1"))
        assert result == state
        assert called == []  # 有登录态 → 绝不重复登录

    def test_skip_login_ignores_linked_login_page_and_its_session(
        self, db_session, login_page, monkeypatch
    ):
        """显式不使用登录态:即便关联了登录页对象、它也有可用登录态,也不复用、不执行登录步骤。"""
        _write_state(pl.ensure_session_path(login_page))
        called = []

        async def fake_execute(*args, **kwargs):  # pragma: no cover - 不该被调用
            called.append(args)
            return True

        monkeypatch.setattr(runner_mod, "_execute_in_browser", fake_execute)
        target = _make_page(db_session, name="目标页", url="http://a.com#/target")
        target.login_page_object_id = login_page.id
        db_session.add(target)
        db_session.commit()

        session = PageObjectRunner(db_session, agent_factory=_agent_factory(), skip_login=True)
        result = asyncio.run(session._ensure_session(db_session, target.url, None, target, "u1"))
        assert result is None  # 匿名:一个登录态都不带
        assert called == []  # 也不去跑登录页对象的登录步骤

    def test_runs_login_steps_and_keeps_session(
        self, db_session, login_page, monkeypatch, tmp_path
    ):
        """没有登录态时:执行登录页对象的步骤,并把会话落盘到它自己的文件。"""
        target = _make_page(db_session, name="目标页", url="http://a.com#/target")
        target.login_page_object_id = login_page.id
        db_session.add(target)
        db_session.commit()

        calls = []

        async def fake_execute(
            url, elements, steps, storage_state, trace, heal_events, task_id, save_state_path=None
        ):
            calls.append(
                {"url": url, "steps": steps, "save_state_path": save_state_path,
                 "storage_state": storage_state}
            )
            trace.append({"step_no": 1, "element": "submit", "status": "passed"})
            if save_state_path:
                _write_state(save_state_path, cookies=2, origins=1)
            return True

        monkeypatch.setattr(runner_mod, "_execute_in_browser", fake_execute)
        session = PageObjectRunner(db_session, agent_factory=_agent_factory())
        result = asyncio.run(session._ensure_session(db_session, target.url, None, target, "u1"))

        assert result == str(pl.ensure_session_path(login_page))
        assert calls[0]["url"] == login_page.url          # 登录在登录页上完成
        assert calls[0]["steps"] == LOGIN_STEPS           # 用的是登录页对象保存的步骤
        assert calls[0]["save_state_path"] == result      # 会话落盘到登录页对象
        db_session.refresh(login_page)
        assert login_page.session_status == "READY"
        assert login_page.last_login_at is not None
        # 顺带同步到 origin 通用缓存,未关联登录页对象的动作也能复用
        assert ba.has_valid_state_file(ba._cache_path("http://a.com"))

    def test_bootstrap_resolves_template_url_before_goto(
        self, db_session, monkeypatch, tmp_path
    ):
        """登录页 url 存 {{web_url}} 原文时,执行登录步骤前必须解析成真实地址。

        回归:PO514 的 url 是 `{{web_url}}/`,直接 page.goto 会失败,重登录永远
        建不出登录态,业务页抽取只能停在登录页元素。
        """
        from db.models import GlobalParameter

        db_session.add(GlobalParameter(
            name="默认环境", is_default=True,
            parameters=[{"key": "web_url", "value": "http://env-a.com"}],
        ))
        db_session.commit()
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        login_page = _make_page(
            db_session,
            name="登录(模板)",
            url="{{web_url}}/",
            is_login_page=True,
            steps=LOGIN_STEPS,
            elements=LOGIN_ELEMENTS,
        )
        pl.ensure_session_path(login_page)
        db_session.add(login_page)
        db_session.commit()
        target = _make_page(db_session, name="目标页", url="http://env-a.com#/target")
        target.login_page_object_id = login_page.id
        db_session.add(target)
        db_session.commit()

        calls = []

        async def fake_execute(
            url, elements, steps, storage_state, trace, heal_events, task_id, save_state_path=None
        ):
            calls.append({"url": url, "save_state_path": save_state_path})
            trace.append({"step_no": 1, "element": "submit", "status": "passed"})
            if save_state_path:
                _write_state(save_state_path, cookies=2, origins=1)
            return True

        monkeypatch.setattr(runner_mod, "_execute_in_browser", fake_execute)
        session = PageObjectRunner(db_session, agent_factory=_agent_factory())
        result = asyncio.run(session._ensure_session(db_session, target.url, None, target, "u1"))

        assert result == str(pl.ensure_session_path(login_page))
        assert calls[0]["url"] == "http://env-a.com/"
        assert "{{" not in calls[0]["url"]

    def test_falls_back_to_ai_login_without_steps(
        self, db_session, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        login_page = _make_page(
            db_session, name="登录页(无步骤)", url="http://a.com/sso/", is_login_page=True
        )
        login_page.login_page_object_id = None
        db_session.add(login_page)
        db_session.commit()
        target = _make_page(db_session, name="目标页", url="http://a.com#/target")
        target.login_page_object_id = login_page.id
        db_session.add(target)
        db_session.commit()

        session = PageObjectRunner(
            db_session,
            login_overrides={"login_url": "http://a.com/sso/", "username": "admin", "password": "pw"},
            agent_factory=_agent_factory(),
        )
        result = asyncio.run(session._ensure_session(db_session, target.url, None, target, "u1"))
        assert result == str(pl.ensure_session_path(login_page))
        db_session.refresh(login_page)
        assert login_page.session_status == "READY"

    def test_login_failure_raises_and_marks_failed(self, db_session, login_page, monkeypatch):
        async def fake_execute(*args, **kwargs):
            return False

        monkeypatch.setattr(runner_mod, "_execute_in_browser", fake_execute)
        target = _make_page(db_session, name="目标页", url="http://a.com#/target")
        target.login_page_object_id = login_page.id
        db_session.add(target)
        db_session.commit()

        session = PageObjectRunner(db_session, agent_factory=_agent_factory())
        with pytest.raises(RuntimeError):
            asyncio.run(session._ensure_session(db_session, target.url, None, target, "u1"))
        db_session.refresh(login_page)
        assert login_page.session_status == "FAILED"

    def test_page_object_marked_as_login_page_runs_anonymously(
        self, db_session, login_page, monkeypatch
    ):
        """登录页对象自己执行/抽取时必须匿名打开。

        带上历史登录态,站点会直接跳进业务页 —— 登录页元素一个都不在,步骤必然全
        失效。实测鲁班平台:带缓存 → #/home/workbench/...(工作台,input/button 全空);
        匿名 → /sso/?redirect=...(登录页,元素全命中)。所以即便本地已有可用登录态,
        这里也要返回 None;也不在此触发登录步骤 —— 本次动作本身就是登录步骤
        (反向链路见 test_runs_login_steps_and_keeps_session)。
        """
        _write_state(pl.ensure_session_path(login_page))
        called = []

        async def fake_execute(*args, **kwargs):  # pragma: no cover - 不该被调用
            called.append(args)
            return True

        monkeypatch.setattr(runner_mod, "_execute_in_browser", fake_execute)
        session = PageObjectRunner(db_session)
        result = asyncio.run(
            session._ensure_session(db_session, login_page.url, None, login_page, "u1")
        )
        assert result is None
        assert called == []

    def test_unrelated_page_object_falls_back_to_origin_cache(
        self, db_session, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        cache = _write_state(ba._cache_path("http://a.com"))
        page_object = _make_page(db_session, name="普通页", url="http://a.com#/x")
        session = PageObjectRunner(db_session)
        result = asyncio.run(
            session._ensure_session(db_session, page_object.url, None, page_object, "u1")
        )
        assert result == cache


class TestExecuteSavePath:
    def test_saves_to_login_page_object(self, db_session, login_page):
        target = _make_page(db_session, name="目标页", url="http://a.com#/target")
        target.login_page_object_id = login_page.id
        db_session.add(target)
        db_session.commit()
        session = PageObjectRunner(db_session)
        assert session._state_save_path(db_session, target, "u1") == str(
            pl.ensure_session_path(login_page)
        )

    def test_saves_to_own_file_when_self_is_login_page(self, db_session, login_page):
        session = PageObjectRunner(db_session)
        assert session._state_save_path(db_session, login_page, "u1") == str(
            pl.ensure_session_path(login_page)
        )

    def test_saves_to_origin_cache_without_login_page(
        self, db_session, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        page_object = _make_page(db_session, name="普通页", url="http://a.com#/x")
        session = PageObjectRunner(db_session)
        assert session._state_save_path(db_session, page_object, "u1") == str(
            ba._cache_path("http://a.com")
        )


class TestExecuteStepsFallback:
    def test_uses_page_object_saved_steps(self, db_session, login_page, monkeypatch):
        """执行时不传步骤 → 用页面对象上保存的步骤(登录页对象就是靠这个被其他对象调用)。"""
        login_page.steps = LOGIN_STEPS
        db_session.add(login_page)
        db_session.commit()
        run = UIAutomationRun(
            page_object_id=login_page.id,
            user_id="u1",
            run_type="execute",
            target_url=login_page.url,
            status="RUNNING",
            steps=[],
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)

        captured = {}

        async def fake_execute(
            url, elements, steps, storage_state, trace, heal_events, task_id, save_state_path=None
        ):
            captured["steps"] = steps
            captured["save_state_path"] = save_state_path
            trace.append({"step_no": 1, "status": "passed"})
            return True

        monkeypatch.setattr(runner_mod, "_execute_in_browser", fake_execute)
        session = PageObjectRunner(db_session)
        asyncio.run(session.run(run.id))
        db_session.refresh(run)
        assert run.status == "PASSED"
        assert captured["steps"] == LOGIN_STEPS
        assert captured["save_state_path"] == str(pl.ensure_session_path(login_page))


# ---------------------------------------------------------------------------
# 路由:关联 / 状态 / 清除 / 捕获
# ---------------------------------------------------------------------------

class TestPageObjectRoutes:
    def test_create_persists_login_link_and_steps(self, db_session, fake_user, login_page):
        body = route.PageObjectPayload(
            name="目标页", site_origin="http://a.com", page_path="#/t",
            steps=[{"element": "x", "action": "click", "value": ""}],
            is_login_page=False,
            login_page_object_id=login_page.id,
        )
        data = route.create_page_object(body, db_session, fake_user).data
        assert data.login_page_object_id == login_page.id
        assert data.steps[0]["element"] == "x"

    def test_update_can_unlink(self, db_session, fake_user, login_page):
        target = _make_page(db_session, name="目标页", url="http://a.com#/t", login_page_object_id=login_page.id)
        body = route.PageObjectPayload(
            name=target.name, site_origin=target.site_origin, page_path=target.page_path,
            url=target.url, login_page_object_id=0,
        )
        route.update_page_object(target.id, body, db_session, fake_user)
        db_session.refresh(target)
        assert target.login_page_object_id is None

    def test_create_rejects_foreign_login_page(self, db_session, fake_user, login_page):
        body = route.PageObjectPayload(name="x", url="http://a.com/x", login_page_object_id=99999)
        with pytest.raises(HTTPException) as exc:
            route.create_page_object(body, db_session, fake_user)
        assert exc.value.status_code == 404

    def test_session_endpoint_reports_state(self, db_session, fake_user, login_page):
        _write_state(pl.ensure_session_path(login_page), cookies=2)
        pl.refresh_status(db_session, login_page)
        data = route.get_page_object_session(login_page.id, db_session, fake_user).data
        assert data["session_ready"] is True and data["session_cookie_count"] == 2

    def test_session_clear_endpoint(self, db_session, fake_user, login_page):
        _write_state(pl.ensure_session_path(login_page))
        pl.refresh_status(db_session, login_page)
        data = route.clear_page_object_session(login_page.id, db_session, fake_user).data
        assert data["session_status"] == "NONE"

    def test_capture_from_origin_cache(self, db_session, fake_user, login_page):
        _write_state(ba._cache_path("http://a.com"), cookies=4, origins=1)
        data = route.capture_page_object_session(
            login_page.id,
            route.PageObjectCaptureRequest(site_origin="http://a.com"),
            db_session,
            fake_user,
        ).data
        assert data["session_ready"] is True
        assert data["session_cookie_count"] == 4

    def test_capture_without_cache_returns_400(self, db_session, fake_user, login_page):
        with pytest.raises(HTTPException) as exc:
            route.capture_page_object_session(
                login_page.id,
                route.PageObjectCaptureRequest(site_origin="http://a.com"),
                db_session,
                fake_user,
            )
        assert exc.value.status_code == 400

    def test_login_route_launches_with_page_object(self, db_session, fake_user, login_page, monkeypatch):
        captured = {}

        def fake_launch(run_id, request_db=None, login_overrides=None, login_page_object_id=None):
            captured["login_page_object_id"] = login_page_object_id
            return "task-l"

        monkeypatch.setattr(route, "launch_login", fake_launch)
        result = asyncio.run(
            route.login_page_object(login_page.id, route.PageObjectLoginRequest(), db_session, fake_user)
        )
        assert result.data["task_id"] == "task-l"
        assert captured["login_page_object_id"] == login_page.id
