"""PageObjectRunner 登录态编排测试:注入 fake agent,不碰真浏览器/真 LLM。"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.browser_agent as ba
from app.services.page_object_runner import PageObjectRunner
from db.models import PageObject, UIAutomationRun


class FakeLoginAgent:
    """模拟 browser-use Agent:可选写入 storage_state 文件模拟登录成功。"""

    def __init__(self, llm, prompt, storage_state=None, *, success=True, write_state=True):
        self.prompt = prompt
        self._success = success
        self._storage_state = storage_state
        self._write_state = write_state
        self.history_callback = None

    async def run(self, max_steps=30):
        if self._write_state and self._storage_state:
            with open(self._storage_state, "w", encoding="utf-8") as fh:
                json.dump(
                    {"cookies": [{"name": "sid", "value": "x"}], "origins": []},
                    fh,
                    ensure_ascii=True,
                )
        return SimpleNamespace(success=self._success, final_result=lambda: "登录完成")


def _factory(*, success=True, write_state=True, calls=None):
    def build(llm, prompt, storage_state=None):
        if calls is not None:
            calls.append({"prompt": prompt, "storage_state": storage_state})
        return FakeLoginAgent(llm, prompt, storage_state, success=success, write_state=write_state)

    return build


LOGIN_OVERRIDES = {
    "login_url": "http://a.com/login",
    "username": "admin",
    "password": "pw",
    "login_method": "用户名登录",
}


class TestLoginOnlyPrompt:
    def test_forbids_relogin_and_target_recheck(self):
        ctx = ba.LoginContext(
            logged_in_cache=False,
            username="anning",
            password="pw",
            login_url="http://a.com/sso/",
            login_method="用户名登录",
            company="建设集团公司",
        )
        prompt = ba._login_only_prompt(ctx, "http://a.com/#/home")
        assert "http://a.com/sso/" in prompt
        assert "anning" in prompt and "pw" in prompt
        assert "建设集团公司" in prompt
        # 必须只登录一次
        assert "只提交一次登录表单" in prompt
        # 不得要求重新访问目标页/重新登录(该站点裸根地址恒重定向到登录页)
        assert "重新执行登录" not in prompt
        assert "不要访问目标页面地址做二次校验" in prompt


class TestEnsureSession:
    def test_logs_in_when_cache_missing(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        calls = []
        runner = PageObjectRunner(
            db_session, login_overrides=LOGIN_OVERRIDES, agent_factory=_factory(calls=calls)
        )
        path = asyncio.run(runner._ensure_session(db_session, "http://a.com#/home", None))
        assert path == str(tmp_path / "http_a.com.json")
        assert ba.has_valid_session("http://a.com") is True
        assert len(calls) == 1
        assert "admin" in calls[0]["prompt"] and "pw" in calls[0]["prompt"]

    def test_reuses_valid_cache_without_login(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        cache = tmp_path / "http_a.com.json"
        cache.write_text('{"cookies":[{"name":"sid","value":"x"}],"origins":[]}', encoding="utf-8")
        calls = []
        runner = PageObjectRunner(
            db_session, login_overrides=LOGIN_OVERRIDES, agent_factory=_factory(calls=calls)
        )
        path = asyncio.run(runner._ensure_session(db_session, "http://a.com#/home", None))
        assert path == str(cache)
        assert calls == []

    def test_does_not_relogin_after_session_established(self, db_session, tmp_path, monkeypatch):
        """同一进程内已登录过的 origin,即使缓存文件随后不可读也不重复登录。"""
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        calls = []
        runner = PageObjectRunner(
            db_session, login_overrides=LOGIN_OVERRIDES, agent_factory=_factory(calls=calls)
        )
        asyncio.run(runner._ensure_session(db_session, "http://a.com", None))
        assert len(calls) == 1
        # 模拟缓存被清空(并发写空),但本进程已确认登录过
        (tmp_path / "http_a.com.json").unlink()
        asyncio.run(runner._ensure_session(db_session, "http://a.com", None))
        assert len(calls) == 1

    def test_no_login_config_skips_agent(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        calls = []
        runner = PageObjectRunner(db_session, agent_factory=_factory(calls=calls))
        # 缓存文件不存在:匿名访问返回 None(避免 Playwright ENOENT),且不触发登录
        path = asyncio.run(runner._ensure_session(db_session, "http://a.com", None))
        assert path is None
        assert calls == []

    def test_no_login_config_reuses_existing_cache(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        cache = tmp_path / "http_a.com.json"
        cache.write_text('{"cookies":[{"name":"sid","value":"x"}],"origins":[]}', encoding="utf-8")
        calls = []
        runner = PageObjectRunner(db_session, agent_factory=_factory(calls=calls))
        path = asyncio.run(runner._ensure_session(db_session, "http://a.com", None))
        assert path == str(cache)
        assert calls == []

    def test_skip_login_ignores_existing_cache(self, db_session, tmp_path, monkeypatch):
        """显式不使用登录态:该站点有有效缓存也不读,也不触发登录,直接匿名。"""
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        cache = tmp_path / "http_a.com.json"
        cache.write_text('{"cookies":[{"name":"sid","value":"x"}],"origins":[]}', encoding="utf-8")
        calls = []
        runner = PageObjectRunner(
            db_session,
            login_overrides=LOGIN_OVERRIDES,
            agent_factory=_factory(calls=calls),
            skip_login=True,
        )
        path = asyncio.run(runner._ensure_session(db_session, "http://a.com", None))
        assert path is None
        assert calls == []
        assert cache.exists()  # 只是本次不用,缓存文件本身不动

    def test_raises_when_login_fails(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        runner = PageObjectRunner(
            db_session,
            login_overrides=LOGIN_OVERRIDES,
            agent_factory=_factory(success=False, write_state=False),
        )
        with pytest.raises(RuntimeError):
            asyncio.run(runner._ensure_session(db_session, "http://a.com", None))


class TestEstablishLoginSession:
    def test_unconfigured_returns_reason(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        result = asyncio.run(ba.establish_login_session(db_session, "http://a.com", {}))
        assert result["ok"] is False
        assert "未配置" in result["reason"]


class TestRunLogin:
    """login 任务 = 为「登录页对象」建立/刷新登录态。"""

    @staticmethod
    def _make(db_session, *, steps=None):
        page_object = PageObject(
            name="登录页", url="http://a.com/login", is_login_page=True,
            elements=[], steps=steps or [], user_id="u1",
        )
        db_session.add(page_object)
        db_session.commit()
        db_session.refresh(page_object)
        run = UIAutomationRun(
            page_object_id=page_object.id,
            user_id="u1",
            run_type="login",
            target_url="http://a.com/login",
            status="RUNNING",
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        return page_object, run

    def test_run_login_persists_success(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        page_object, run = self._make(db_session)
        runner = PageObjectRunner(
            db_session, login_overrides=LOGIN_OVERRIDES, agent_factory=_factory()
        )
        asyncio.run(runner.run(run.id))
        db_session.refresh(run)
        db_session.refresh(page_object)
        assert run.status == "PASSED"
        assert run.finished_at is not None
        assert "登录成功" in run.summary
        # 登录态落在该页面对象的专属文件,并记录到对象上供其他页面对象复用
        assert page_object.session_status == "READY"
        assert page_object.session_path.endswith(f"po{page_object.id}.json")
        assert Path(page_object.session_path).exists()

    def test_run_login_persists_failure(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        page_object, run = self._make(db_session)
        runner = PageObjectRunner(
            db_session,
            login_overrides=LOGIN_OVERRIDES,
            agent_factory=_factory(success=False, write_state=False),
        )
        asyncio.run(runner.run(run.id))
        db_session.refresh(run)
        db_session.refresh(page_object)
        assert run.status == "FAILED"
        assert "登录失败" in run.summary
        assert page_object.session_status == "FAILED"

    def test_run_login_without_login_page_object_fails(self, db_session, tmp_path, monkeypatch):
        """未标记为登录页对象 / 未关联登录页对象时,错误可见而不是静默失败。"""
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        page_object = PageObject(name="普通页面", url="http://a.com/x", user_id="u1")
        db_session.add(page_object)
        db_session.commit()
        db_session.refresh(page_object)
        run = UIAutomationRun(
            page_object_id=page_object.id, user_id="u1", run_type="login",
            target_url="http://a.com/x", status="RUNNING",
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        runner = PageObjectRunner(db_session, login_overrides=LOGIN_OVERRIDES, agent_factory=_factory())
        asyncio.run(runner.run(run.id))
        db_session.refresh(run)
        assert run.status == "ERROR"
        assert "登录页对象" in (run.error or "")


def _looks_like_login_page():
    from app.services.page_object_runner import _looks_like_login_page as fn
    return fn


class TestExtractReloginOnExpiredSession:
    """登录态过期探测:抽到密码框 → 强制重登录 → 重抽一次。"""

    @staticmethod
    def _make_extract_run(db_session) -> UIAutomationRun:
        run = UIAutomationRun(
            user_id="u1",
            run_type="extract",
            target_url="http://a.com#/home",
            status="RUNNING",
            result={"request": {"use_llm_naming": False}},
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        return run

    def _patch_browser_phase(self, monkeypatch):
        async def fake_phase(coro):
            return await coro

        monkeypatch.setattr(ba, "_run_browser_phase", fake_phase)

    def test_expired_session_triggers_relogin_and_reextract(self, db_session, tmp_path, monkeypatch):
        import app.services.page_object_runner as por

        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        # 缓存文件非空 → _ensure_session 直接复用(模拟服务端会话已过期的本地缓存)
        (tmp_path / "http_a.com.json").write_text(
            '{"cookies":[{"name":"sid","value":"old"}],"origins":[]}', encoding="utf-8"
        )

        calls = {"extract": 0, "storage": [], "relogin": 0}

        async def fake_extract(url, *, storage_state=None, timeout_ms=30000, **kwargs):
            calls["extract"] += 1
            calls["storage"].append(storage_state)
            if calls["extract"] == 1:
                return [{"tag": "input", "inputType": "password", "id": "pwd"}]  # 登录页
            return [{"tag": "button", "text": "新增"}]  # 重抽拿到正式页面

        async def fake_relogin(db, url, overrides, **kwargs):
            calls["relogin"] += 1
            return {"ok": True, "cache_path": str(tmp_path / "http_a.com.json")}

        monkeypatch.setattr(por, "extract_elements", fake_extract)
        monkeypatch.setattr(ba, "establish_login_session", fake_relogin)
        self._patch_browser_phase(monkeypatch)

        runner = PageObjectRunner(db_session, login_overrides=LOGIN_OVERRIDES)
        run = self._make_extract_run(db_session)
        asyncio.run(runner.run(run.id))
        db_session.refresh(run)

        assert run.status == "PASSED"
        assert calls["extract"] == 2
        assert calls["relogin"] == 1
        assert run.result["raw_count"] == 1
        assert run.summary.startswith("共采集 1 个交互元素")

    def test_skip_login_does_not_probe_or_relogin(self, db_session, tmp_path, monkeypatch):
        """显式不使用登录态:抽到密码框也不当成「登录态过期」,不重登、不重抽、不带登录态。"""
        import app.services.page_object_runner as por

        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        # 故意留一份有效缓存:证明 skip_login 时连它都不读
        (tmp_path / "http_a.com.json").write_text(
            '{"cookies":[{"name":"sid","value":"old"}],"origins":[]}', encoding="utf-8"
        )

        calls = {"extract": 0, "storage": [], "relogin": 0}

        async def fake_extract(url, *, storage_state=None, timeout_ms=30000, **kwargs):
            calls["extract"] += 1
            calls["storage"].append(storage_state)
            return [{"tag": "input", "inputType": "password", "id": "pwd"}]  # 登录页元素

        async def fake_relogin(db, url, overrides, **kwargs):  # pragma: no cover - 不该被调用
            calls["relogin"] += 1
            return {"ok": True, "cache_path": str(tmp_path / "http_a.com.json")}

        monkeypatch.setattr(por, "extract_elements", fake_extract)
        monkeypatch.setattr(ba, "establish_login_session", fake_relogin)
        self._patch_browser_phase(monkeypatch)

        runner = PageObjectRunner(db_session, login_overrides=LOGIN_OVERRIDES, skip_login=True)
        run = self._make_extract_run(db_session)
        asyncio.run(runner.run(run.id))
        db_session.refresh(run)

        assert calls["extract"] == 1  # 不重抽
        assert calls["relogin"] == 0  # 不触发任何登录
        assert calls["storage"] == [None]  # 全程匿名
        assert run.status == "PASSED"

    def test_normal_page_does_not_relogin(self, db_session, tmp_path, monkeypatch):
        import app.services.page_object_runner as por

        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        (tmp_path / "http_a.com.json").write_text(
            '{"cookies":[{"name":"sid","value":"x"}],"origins":[]}', encoding="utf-8"
        )
        calls = {"extract": 0, "relogin": 0}

        async def fake_extract(url, *, storage_state=None, timeout_ms=30000, **kwargs):
            calls["extract"] += 1
            return [{"tag": "button", "text": "查询"}]

        async def fake_relogin(db, url, overrides, **kwargs):
            calls["relogin"] += 1
            return {"ok": True, "cache_path": ""}

        monkeypatch.setattr(por, "extract_elements", fake_extract)
        monkeypatch.setattr(ba, "establish_login_session", fake_relogin)
        self._patch_browser_phase(monkeypatch)

        runner = PageObjectRunner(db_session, login_overrides=LOGIN_OVERRIDES)
        run = self._make_extract_run(db_session)
        asyncio.run(runner.run(run.id))
        db_session.refresh(run)

        assert run.status == "PASSED"
        assert calls["extract"] == 1
        assert calls["relogin"] == 0

    def test_relogin_failure_keeps_login_page_elements(self, db_session, tmp_path, monkeypatch):
        import app.services.page_object_runner as por

        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        (tmp_path / "http_a.com.json").write_text(
            '{"cookies":[{"name":"sid","value":"old"}],"origins":[]}', encoding="utf-8"
        )

        async def fake_extract(url, *, storage_state=None, timeout_ms=30000, **kwargs):
            return [{"tag": "input", "inputType": "password", "id": "pwd"}]

        async def fake_relogin(db, url, overrides, **kwargs):
            return {"ok": False, "reason": "登录未成功"}

        monkeypatch.setattr(por, "extract_elements", fake_extract)
        monkeypatch.setattr(ba, "establish_login_session", fake_relogin)
        self._patch_browser_phase(monkeypatch)

        runner = PageObjectRunner(db_session, login_overrides=LOGIN_OVERRIDES)
        run = self._make_extract_run(db_session)
        asyncio.run(runner.run(run.id))
        db_session.refresh(run)

        # 重登录失败不崩任务,保留登录页元素(PASSED,raw_count=1)
        assert run.status == "PASSED"
        assert run.result["raw_count"] == 1

    def test_looks_like_login_page(self):
        fn = _looks_like_login_page()
        assert fn([{"tag": "input", "inputType": "password"}]) is True
        assert fn([{"tag": "input", "inputType": "PASSWORD "}]) is True
        assert fn([{"tag": "input", "inputType": "text"}, {"tag": "button"}]) is False
        assert fn([]) is False
        assert fn(None) is False


# ---------------------------------------------------------------------------
# 登录页对象自己的执行路径:匿名打开 + 失败诊断 + 落盘前等待
# ---------------------------------------------------------------------------

def _self_login_page():
    from app.services.page_object_runner import _is_self_login_page as fn
    return fn


def _diagnose():
    from app.services.page_object_runner import _diagnose_locator_failure as fn
    return fn


class TestIsSelfLoginPage:
    """判据:自己勾了「作为登录页对象」,或自己关联了自己。"""

    def test_marked_login_page(self):
        fn = _self_login_page()
        assert fn(PageObject(name="p", url="u", is_login_page=True)) is True

    def test_back_reference_to_itself_counts(self):
        fn = _self_login_page()
        row = PageObject(name="p", url="u", is_login_page=False)
        row.id = 12
        row.login_page_object_id = 12
        assert fn(row) is True

    def test_plain_page_object_and_none(self):
        fn = _self_login_page()
        row = PageObject(name="p", url="u", is_login_page=False)
        row.id = 12
        row.login_page_object_id = 99
        assert fn(row) is False
        assert fn(None) is False

    def test_unsaved_object_does_not_count_as_self_reference(self):
        """新建还没落库的对象 id 是 None,不能把 None == None 当成「关联自己」。"""
        fn = _self_login_page()
        row = PageObject(name="p", url="u", is_login_page=False)
        assert row.id is None and row.login_page_object_id is None
        assert fn(row) is False


class TestDiagnoseLocatorFailure:
    """定位器全失效时的文案要指向可操作的原因,而不是一句「都失效了」。"""

    def test_session_took_us_away_from_login_page(self):
        """带了登录态却没停在登录页 → 多半是把登录页对象当普通页面执行了。"""
        msg = _diagnose()([{"tag": "div"}], had_session=True)
        assert "作为登录页对象" in msg

    def test_bounced_back_to_login_page(self):
        """带了登录态却停在登录页 → 登录态已失效。"""
        msg = _diagnose()([{"tag": "input", "inputType": "password"}], had_session=True)
        assert "登录态可能已失效" in msg

    def test_anonymous_stuck_on_login_page(self):
        msg = _diagnose()([{"tag": "input", "inputType": "password"}], had_session=False)
        assert "关联一个登录页对象" in msg

    def test_plain_missing_element(self):
        msg = _diagnose()([{"tag": "div"}], had_session=False)
        assert "重新抽取" in msg


class TestSettleBeforeSnapshot:
    """落盘登录态前先等页面安静,否则抓到的是没有 token 的空壳。"""

    def test_waits_for_networkidle_then_extra_delay(self):
        from app.services.page_object_runner import SNAPSHOT_SETTLE_MS, _settle_before_snapshot

        calls = []

        class FakePage:
            async def wait_for_load_state(self, state, timeout=None):
                calls.append(("load_state", state))

            async def wait_for_timeout(self, ms):
                calls.append(("timeout", ms))

        asyncio.run(_settle_before_snapshot(FakePage()))
        assert calls == [("load_state", "networkidle"), ("timeout", SNAPSHOT_SETTLE_MS)]

    def test_never_raises_when_page_is_gone(self):
        from app.services.page_object_runner import _settle_before_snapshot

        class BrokenPage:
            async def wait_for_load_state(self, state, timeout=None):
                raise TimeoutError("never idle")

            async def wait_for_timeout(self, ms):
                raise RuntimeError("page closed")

        asyncio.run(_settle_before_snapshot(BrokenPage()))  # 不该抛
