"""UIExecutionRunner 编排测试:stub agent_factory,不碰真浏览器/真 LLM(spec §11)。"""
import asyncio
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
        assert "登录" not in prompt.replace("登录页", "").replace("登录", "登录", 0) or "密码" not in prompt


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


class TestRunnerRun:
    def test_success_passes_case(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)
        runner = ba.UIExecutionRunner(
            db_session,
            agent_factory=lambda llm, prompt: FakeAgent(llm, prompt, success=True),
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
            agent_factory=lambda llm, prompt: FakeAgent(llm, prompt, success=False),
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
            agent_factory=lambda llm, prompt: FakeAgent(llm, prompt, fail_midway=True),
        )
        asyncio.run(runner.run(rec.id))
        db_session.refresh(rec)
        db_session.refresh(tc)
        assert rec.status == "ERROR"
        assert "browser crashed" in (rec.error or "")
        assert tc.status == "PASSED"  # 未被污染

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
            agent_factory=lambda llm, prompt: SlowAgent(llm, prompt),
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

        def factory(llm, prompt):
            captured["prompt"] = prompt
            return FakeAgent(llm, prompt)

        runner = ba.UIExecutionRunner(db_session, agent_factory=factory)
        asyncio.run(runner.run(rec.id))
        assert captured["prompt"].startswith("打开 http://192.168.13.178:8182#/home/login")
