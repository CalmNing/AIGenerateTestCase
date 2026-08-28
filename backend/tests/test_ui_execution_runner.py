"""UIExecutionRunner 编排测试:stub agent_factory,不碰真浏览器/真 LLM(spec §11)。"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

import app.services.browser_agent as ba
from db.models import GlobalParameter, TestCase, UIExecutionRecord


class FakeAgent:
    """模拟 browser-use Agent:逐条上报历史,末步返回判定。"""

    def __init__(self, llm, prompt: str, *, success=True, fail_midway=False):
        self.prompt = prompt
        self._success = success
        self._fail_midway = fail_midway
        self.history_callback = None

    async def run(self, max_steps=25):
        if self.history_callback:
            self.history_callback("输入用户名")
            self.history_callback("点击登录")
        if self._fail_midway:
            raise RuntimeError("browser crashed")
        return SimpleNamespace(success=self._success, final_result=lambda: "逐条核对:全部预期满足" if self._success else "预期2未满足")


def _make_case(db_session, steps=None, expected=None):
    tc = TestCase(
        case_name="登录功能",
        steps=steps if steps is not None else ["打开登录页", "输入账号密码", "点击登录"],
        expected_results=expected if expected is not None else ["跳转到首页", "显示欢迎信息"],
        preset_conditions=["用户已注册"],
        status="NOT_RUN",
    )
    db_session.add(tc)
    db_session.commit()
    db_session.refresh(tc)
    return tc


def _make_record(db_session, tc, target_url="http://192.168.13.178:8182#/home/login"):
    rec = UIExecutionRecord(testcase_id=tc.id, user_id="u1", target_url=target_url)
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)
    return rec


class TestBuildUiPrompt:
    def test_prompt_contains_steps_expected_conditions(self):
        ctx = ba.LoginContext(logged_in_cache=True, username="admin", password="pw")
        prompt = ba.build_ui_prompt(
            "http://a.com#/login",
            ["打开登录页", "点击登录"],
            ["跳转首页"],
            ["用户已注册"],
            ctx,
        )
        assert "http://a.com#/login" in prompt
        assert "1. 打开登录页" in prompt
        assert "2. 点击登录" in prompt
        assert "1. 跳转首页" in prompt
        assert "用户已注册" in prompt
        # 有缓存分支措辞
        assert "已持有登录态" in prompt
        assert "admin" in prompt

    def test_prompt_no_cache_branch(self):
        ctx = ba.LoginContext(logged_in_cache=False, username="u", password="p")
        prompt = ba.build_ui_prompt("http://a.com", ["步骤1"], ["预期1"], [], ctx)
        assert "重新登录" not in prompt
        assert "先用账号 u 密码 p 登录" in prompt

    def test_prompt_no_credentials_no_login_section(self):
        ctx = ba.LoginContext(logged_in_cache=False, username="", password="")
        prompt = ba.build_ui_prompt("http://a.com", ["步骤1"], ["预期1"], [], ctx)
        assert "账号" not in prompt and "密码" not in prompt


class TestResolveLoginContext:
    def test_reads_credentials_and_cache_flag(self, db_session, tmp_path, monkeypatch):
        db_session.add(GlobalParameter(
            name="默认环境", is_default=True,
            parameters=[{"key": "test_username", "value": "tester"},
                        {"key": "test_password", "value": "secret"}],
        ))
        db_session.commit()
        cache_file = tmp_path / "http_192.168.13.178_8182.json"
        cache_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)

        param_map = {"test_username": "tester", "test_password": "secret"}
        ctx, _vars = ba.resolve_login_context(db_session, param_map, "http://192.168.13.178:8182")
        assert ctx.username == "tester"
        assert ctx.password == "secret"
        assert ctx.logged_in_cache is True

    def test_no_cache_no_creds(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        ctx, _vars = ba.resolve_login_context(db_session, {}, "http://a.com")
        assert ctx.username == ""
        assert ctx.logged_in_cache is False


class TestCachePath:
    """_cache_path 归一化:scheme/host 小写、默认端口剥离(Minor #10)。"""

    def test_default_ports_map_to_same_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        assert ba._cache_path("http://a.com:80") == tmp_path / "http_a.com.json"
        assert ba._cache_path("http://a.com") == tmp_path / "http_a.com.json"
        assert ba._cache_path("https://a.com:443") == tmp_path / "https_a.com.json"
        assert ba._cache_path("https://a.com") == tmp_path / "https_a.com.json"

    def test_normalizes_scheme_and_host_case(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        assert ba._cache_path("HTTPS://A.COM:443") == tmp_path / "https_a.com.json"
        assert ba._cache_path("HTTP://A.com") == tmp_path / "http_a.com.json"

    def test_keeps_non_default_port(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        assert ba._cache_path("http://a.com:8080") == tmp_path / "http_a.com_8080.json"
        assert ba._cache_path("http://192.168.13.178:8182") == tmp_path / "http_192.168.13.178_8182.json"


class TestRunnerRun:
    def test_success_passes_case(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)
        runner = ba.UIExecutionRunner(
            db_session,
            agent_factory=lambda llm, prompt, storage_state=None: FakeAgent(llm, prompt, success=True),
        )
        asyncio.run(runner.run(rec.id))
        db_session.refresh(rec)
        db_session.refresh(tc)
        assert rec.status == "PASSED"
        assert rec.finished_at is not None
        assert "全部预期满足" in rec.agent_summary
        assert len(rec.steps_trace) == 2
        assert tc.status == "PASSED"

    def test_agent_fail_fails_case(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)
        runner = ba.UIExecutionRunner(
            db_session,
            agent_factory=lambda llm, prompt, storage_state=None: FakeAgent(llm, prompt, success=False),
        )
        asyncio.run(runner.run(rec.id))
        db_session.refresh(rec)
        db_session.refresh(tc)
        assert rec.status == "FAILED"
        assert rec.error is None
        assert tc.status == "FAILED"

    def test_crash_marks_error_keeps_case_status(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        tc.status = "PASSED"  # 预置一个旧状态,验证 ERROR 不回写
        db_session.add(tc)
        db_session.commit()
        rec = _make_record(db_session, tc)
        runner = ba.UIExecutionRunner(
            db_session,
            agent_factory=lambda llm, prompt, storage_state=None: FakeAgent(llm, prompt, fail_midway=True),
        )
        asyncio.run(runner.run(rec.id))
        db_session.refresh(rec)
        db_session.refresh(tc)
        assert rec.status == "ERROR"
        assert "browser crashed" in (rec.error or "")
        assert tc.status == "PASSED"  # 未被污染

    def test_crash_sets_task_result_status_error(self, db_session, monkeypatch, tmp_path):
        """Finding 2 pin:崩溃路径 _ui_tasks[task_id]["result_status"] 必须是 ERROR。

        否则前端 `if (taskRes.data.result_status)` 永不视为终态,ERROR Alert 分支死代码。
        """
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)
        # 清理其它测试遗留的同 record_id 假任务(如 routes 测试的 _ui_tasks["t1"]),避免反查碰撞
        for _tid in [k for k, v in ba._ui_tasks.items() if v.get("record_id") == rec.id]:
            ba._ui_tasks.pop(_tid, None)
        ba._ui_tasks["crash-task"] = {
            "task_id": "crash-task", "status": "running", "phase": "queued",
            "progress": 0, "message": "", "record_id": rec.id,
            "result_status": None, "error": None, "created_at": "2026-01-01",
        }
        try:
            runner = ba.UIExecutionRunner(
                db_session,
                agent_factory=lambda llm, prompt, storage_state=None: FakeAgent(llm, prompt, fail_midway=True),
            )
            asyncio.run(runner.run(rec.id))
            tid = ba._find_task_id_by_record(rec.id)
            assert tid == "crash-task"
            assert ba._ui_tasks[tid]["status"] == "done"
            assert ba._ui_tasks[tid]["result_status"] == "ERROR"
            assert "browser crashed" in (ba._ui_tasks[tid]["error"] or "")
        finally:
            ba._ui_tasks.pop("crash-task", None)

    def test_timeout_marks_error(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)

        class SlowAgent(FakeAgent):
            async def run(self, max_steps=25):
                await asyncio.sleep(5)
                return SimpleNamespace(success=True, final_result=lambda: "")

        runner = ba.UIExecutionRunner(
            db_session,
            agent_factory=lambda llm, prompt, storage_state=None: SlowAgent(llm, prompt),
            timeout_seconds=0.1,
        )
        asyncio.run(runner.run(rec.id))
        db_session.refresh(rec)
        assert rec.status == "ERROR"
        assert "超时" in (rec.error or "")

    def test_prompt_passed_to_agent(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)
        captured = {}

        def factory(llm, prompt, storage_state=None):
            captured["prompt"] = prompt
            captured["storage_state"] = storage_state
            return FakeAgent(llm, prompt)

        runner = ba.UIExecutionRunner(db_session, agent_factory=factory)
        asyncio.run(runner.run(rec.id))
        assert captured["prompt"].startswith("打开 http://192.168.13.178:8182#/home/login")
        # 无缓存时 storage_state 必须为 None(不注入假登录态)
        assert captured["storage_state"] is None

    def test_cached_storage_state_injected_into_factory(self, db_session, monkeypatch, tmp_path):
        """Finding 3 pin:存在登录态缓存时,读取 JSON dict 并注入 agent_factory(而非仅改措辞)。"""
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)  # origin http://192.168.13.178:8182
        state = {"cookies": [{"name": "sid", "value": "abc"}], "origins": []}
        cache_file = tmp_path / "http_192.168.13.178_8182.json"
        cache_file.write_text(json.dumps(state), encoding="utf-8")

        captured = {}

        def factory(llm, prompt, storage_state=None):
            captured["storage_state"] = storage_state
            return FakeAgent(llm, prompt)

        runner = ba.UIExecutionRunner(db_session, agent_factory=factory)
        asyncio.run(runner.run(rec.id))
        assert captured.get("storage_state") == state


class TestNormalizeAgentResult:
    """browser-use 真实 API 适配层测试(0.13.x AgentHistoryList 形状 vs legacy,纯 double)。"""

    def test_0138_shape_is_successful_and_final_result(self):
        """browser-use 0.13.x:AgentHistoryList 无 .success,只有 is_successful()/final_result()。"""

        class AgentHistoryListLike:
            def __init__(self, ok, text):
                self._ok = ok
                self._text = text

            def is_successful(self):
                return self._ok

            def final_result(self):
                return self._text

        ok = ba._normalize_agent_result(AgentHistoryListLike(True, "全部预期满足"))
        assert ok.success is True
        assert ok.final_result() == "全部预期满足"

        fail = ba._normalize_agent_result(AgentHistoryListLike(False, "预期2未满足"))
        assert fail.success is False
        assert fail.final_result() == "预期2未满足"

    def test_legacy_shape_uses_success_attr(self):
        """旧形状/FakeAgent:存在 .success 属性时直接取用,不调用 is_successful()。"""

        class LegacyResult:
            def __init__(self, ok, text):
                self.success = ok
                self._text = text

            def final_result(self):
                return self._text

        res = ba._normalize_agent_result(LegacyResult(True, "legacy text"))
        assert res.success is True
        assert res.final_result() == "legacy text"

    def test_final_result_none_maps_to_empty_summary(self):
        """真实 browser-use 末步无提取内容时 final_result() 返回 None → 摘要必须为空串而非 "None"。"""

        class NoneFinalResult:
            def is_successful(self):
                return True

            def final_result(self):
                return None

        res = ba._normalize_agent_result(NoneFinalResult())
        assert res.success is True
        assert res.final_result() == ""
        assert res.final_result() != "None"

    def test_unknown_shape_safe_default(self):
        """未知形状:当前实现不抛错,success=False + 空摘要。固化该行为。"""

        class Unknown:
            pass

        res = ba._normalize_agent_result(Unknown())
        assert res.success is False
        assert res.final_result() == ""


class TestAttachHistoryCallback:
    """回调挂载分派:register_new_step_callback 属性存在 → 走 0.13.x 包装;否则落到 history_callback。"""

    def test_new_style_register_new_step_callback_dispatch(self):
        class NewStyleAgent:
            register_new_step_callback = None

        agent = NewStyleAgent()
        seen = []
        ba._attach_history_callback(agent, lambda text: seen.append(text))
        assert callable(agent.register_new_step_callback)
        # 0.13.x 回调签名 (BrowserStateSummary, AgentOutput, int),包装后转发渲染出的动作文本
        output = SimpleNamespace(action=[], next_goal="检查页面标题")
        agent.register_new_step_callback(None, output, 1)
        assert seen == ["检查页面标题"]

    def test_fallback_assigns_history_callback(self):
        class PlainAgent:
            pass

        agent = PlainAgent()
        cb = lambda text: None
        ba._attach_history_callback(agent, cb)
        assert agent.history_callback is cb
