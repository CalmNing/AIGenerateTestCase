"""UIExecutionRunner 编排测试:stub agent_factory,不碰真浏览器/真 LLM(spec §11)。"""
import asyncio
import json
import os
from pathlib import Path
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

    def test_explicit_login_entry_with_credentials(self):
        """显式配置登录入口/方式/凭证:prompt 必须给出确切入口与方式,并禁止猜测。"""
        ctx = ba.LoginContext(
            logged_in_cache=False,
            username="tester",
            password="secret",
            login_url="http://192.168.13.178:8182/sso/",
            login_method="鲁班通行证登录",
        )
        prompt = ba.build_ui_prompt("http://a.com", ["步骤1"], ["预期1"], [], ctx)
        assert "http://192.168.13.178:8182/sso/" in prompt
        assert "鲁班通行证登录" in prompt
        assert "tester" in prompt and "secret" in prompt
        assert "不要猜测" in prompt

    def test_explicit_login_entry_cached_branch(self):
        ctx = ba.LoginContext(
            logged_in_cache=True,
            username="tester",
            password="secret",
            login_url="http://sso.example.com/login",
            login_method="统一身份认证",
        )
        prompt = ba.build_ui_prompt("http://a.com", ["步骤1"], ["预期1"], [], ctx)
        assert "已持有登录态" in prompt
        assert "重新登录" in prompt
        assert "http://sso.example.com/login" in prompt

    def test_explicit_login_entry_without_credentials_refuses_guessing(self):
        """只有登录入口、无凭证时:必须明确禁止猜测,并要求把前置条件不满足写入判定。"""
        ctx = ba.LoginContext(
            logged_in_cache=False,
            login_url="http://sso.example.com/login",
            login_method="统一身份认证",
        )
        prompt = ba.build_ui_prompt("http://a.com", ["步骤1"], ["预期1"], [], ctx)
        assert "http://sso.example.com/login" in prompt
        assert "统一身份认证" in prompt
        assert "不要猜测" in prompt
        assert "前置条件不满足" in prompt

    def test_url_is_isolated_on_own_line(self):
        """目标 URL 独立成行:避免 Agent 把 URL 与后续说明连读导致路由被截断。"""
        ctx = ba.LoginContext()
        prompt = ba.build_ui_prompt("http://a.com#/x/y", ["步骤1"], ["预期1"], [], ctx)
        assert "目标页面地址(完整 URL" in prompt
        assert "http://a.com#/x/y" in prompt
        # URL 后必须换行,不能紧跟后续文字
        assert "http://a.com#/x/y\n" in prompt

    def test_post_login_navigation_and_company_selection(self):
        """登录成功后要选择企业,并重新导航回目标页面(修复登录后跳工作台)。"""
        ctx = ba.LoginContext(
            logged_in_cache=False,
            username="anning",
            password="pw",
            login_url="http://a.com/sso/",
            company="建设集团公司",
        )
        prompt = ba.build_ui_prompt(
            "http://a.com#/home/cmp/contractRegistration", ["步骤1"], ["预期1"], [], ctx
        )
        assert "登录成功后的收尾" in prompt
        assert "建设集团公司" in prompt
        assert "重新访问目标页面地址 http://a.com#/home/cmp/contractRegistration" in prompt
        assert "第 1 步开始执行" in prompt
        # 工作台不是目标页;必须强制重新导航,避免登录后停在工作台
        assert "不是用例目标页" in prompt
        assert "必须立即重新访问" in prompt

    def test_no_login_config_no_post_login_hint(self):
        ctx = ba.LoginContext()
        prompt = ba.build_ui_prompt("http://a.com", ["步骤1"], ["预期1"], [], ctx)
        assert "登录成功后的收尾" not in prompt
        assert "重新访问目标页面地址" not in prompt

    def test_prompt_instructs_new_tab_and_wait(self):
        """点击后可能开新标签页/弹窗:prompt 必须给出切换与等待指引,避免停在原列表页。"""
        ctx = ba.LoginContext()
        prompt = ba.build_ui_prompt("http://a.com", ["点击新增", "上传文件"], ["表单打开"], [], ctx)
        assert "操作约定" in prompt
        assert "新标签页" in prompt
        assert "切换到新标签页" in prompt
        assert "不要在同一步骤上反复重试" in prompt


class TestResolveLoginContext:
    def test_reads_credentials_and_cache_flag(self, db_session, tmp_path, monkeypatch):
        db_session.add(GlobalParameter(
            name="默认环境", is_default=True,
            parameters=[{"key": "test_username", "value": "tester"},
                        {"key": "test_password", "value": "secret"}],
        ))
        db_session.commit()
        cache_file = tmp_path / "http_192.168.13.178_8182.json"
        cache_file.write_text(
            json.dumps({"cookies": [{"name": "sid", "value": "x"}], "origins": []}),
            encoding="utf-8",
        )
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)

        param_map = {"test_username": "tester", "test_password": "secret"}
        ctx, _vars = ba.resolve_login_context(db_session, param_map, "http://192.168.13.178:8182")
        assert ctx.username == "tester"
        assert ctx.password == "secret"
        assert ctx.logged_in_cache is True

    def test_empty_cache_file_is_not_logged_in(self, db_session, tmp_path, monkeypatch):
        """空缓存文件(0 cookies/0 origins)不代表持有登录态,不能据此告诉 Agent「已登录」。"""
        (tmp_path / "http_a.com.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        ctx, _vars = ba.resolve_login_context(db_session, {}, "http://a.com")
        assert ctx.logged_in_cache is False

    def test_no_cache_no_creds(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        ctx, _vars = ba.resolve_login_context(db_session, {}, "http://a.com")
        assert ctx.username == ""
        assert ctx.logged_in_cache is False
        assert ctx.login_url == "" and ctx.login_method == ""

    def test_reads_explicit_login_entry_and_method(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        param_map = {
            "test_username": "tester",
            "test_password": "secret",
            "test_login_url": "http://192.168.13.178:8182/sso/",
            "test_login_method": "鲁班通行证登录",
            "test_company": "建设集团公司",
        }
        ctx, _vars = ba.resolve_login_context(
            db_session, param_map, "http://192.168.13.178:8182"
        )
        assert ctx.login_url == "http://192.168.13.178:8182/sso/"
        assert ctx.login_method == "鲁班通行证登录"
        assert ctx.company == "建设集团公司"
        assert ctx.has_credentials is True

    def test_login_keys_tolerate_whitespace_and_case(self, db_session, tmp_path, monkeypatch):
        """实测参数表存在 "test_password " 这类手误,登录键需容忍首尾空白与大小写。"""
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        param_map = {
            "test_password ": "secret",
            " test_username": "tester",
            "Test_Company": "建设集团公司",
        }
        ctx, _vars = ba.resolve_login_context(db_session, param_map, "http://a.com")
        assert ctx.username == "tester"
        assert ctx.password == "secret"
        assert ctx.company == "建设集团公司"
        assert ctx.has_credentials is True

    def test_overrides_take_precedence_over_global_params(self, db_session, tmp_path, monkeypatch):
        """执行弹窗填写的登录配置优先于全局参数。"""
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        param_map = {
            "test_username": "global_u",
            "test_password": "global_p",
            "test_login_url": "http://global/login",
            "test_login_method": "全局方式",
        }
        overrides = {
            "login_url": "http://run/login",
            "login_method": "本次方式",
            "username": "run_u",
            "password": "run_p",
        }
        ctx, _vars = ba.resolve_login_context(db_session, param_map, "http://a.com", overrides)
        assert ctx.login_url == "http://run/login"
        assert ctx.login_method == "本次方式"
        assert ctx.username == "run_u"
        assert ctx.password == "run_p"

    def test_partial_overrides_fall_back_to_global_params(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        param_map = {"test_username": "global_u", "test_password": "global_p"}
        ctx, _vars = ba.resolve_login_context(
            db_session, param_map, "http://a.com", {"login_url": "http://run/login"}
        )
        assert ctx.login_url == "http://run/login"
        assert ctx.username == "global_u"
        assert ctx.password == "global_p"
        assert ctx.login_method == ""


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
        assert (
            "目标页面地址(完整 URL,仅用于导航,不要与后续文字连读):"
            "http://192.168.13.178:8182#/home/login"
        ) in captured["prompt"]
        # 无缓存时也传路径:browser-use 侧加载为空操作,执行结束写回即建立缓存
        assert captured["storage_state"] == str(tmp_path / "http_192.168.13.178_8182.json")

    def test_cancel_marks_record_stopped_and_keeps_case_status(self, db_session, monkeypatch, tmp_path):
        """用户取消(停止执行):record STOPPED、用例状态不变、任务终态同步。"""
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        tc.status = "NOT_RUN"
        db_session.add(tc)
        db_session.commit()
        rec = _make_record(db_session, tc)

        class SlowAgent(FakeAgent):
            async def run(self, max_steps=25):
                await asyncio.sleep(30)
                return SimpleNamespace(success=True, final_result=lambda: "")

        async def _scenario():
            runner = ba.UIExecutionRunner(
                db_session,
                agent_factory=lambda llm, prompt, storage_state=None: SlowAgent(llm, prompt),
            )
            task = asyncio.create_task(runner.run(rec.id))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_scenario())
        db_session.refresh(rec)
        db_session.refresh(tc)
        assert rec.status == "STOPPED"
        assert rec.finished_at is not None
        assert tc.status == "NOT_RUN"  # 停止不回写用例状态

    def test_login_overrides_injected_into_prompt(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)
        captured = {}

        def factory(llm, prompt, storage_state=None):
            captured["prompt"] = prompt
            return FakeAgent(llm, prompt)

        runner = ba.UIExecutionRunner(
            db_session,
            agent_factory=factory,
            login_overrides={
                "login_url": "http://sso.example.com/login",
                "login_method": "统一身份认证",
                "username": "tester",
                "password": "secret",
                "company": "建设集团公司",
            },
        )
        asyncio.run(runner.run(rec.id))
        prompt = captured["prompt"]
        assert "http://sso.example.com/login" in prompt
        assert "统一身份认证" in prompt
        assert "tester" in prompt and "secret" in prompt
        assert "不要猜测" in prompt
        # 登录后企业选择 + 回到目标页面
        assert "建设集团公司" in prompt
        assert f"重新访问目标页面地址 {rec.target_url}" in prompt

    def test_cached_storage_state_path_injected_into_factory(self, db_session, monkeypatch, tmp_path):
        """Finding 3 pin:登录态以**文件路径**注入 agent_factory,由 browser-use 自行加载/写回。"""
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
        assert captured.get("storage_state") == str(cache_file)
        # runner 不直接改写缓存文件(browser-use 负责写回)
        assert json.loads(cache_file.read_text(encoding="utf-8")) == state


class TestJsonObjectClientProxy:
    """DeepSeek 不支持 json_schema:结构化调用必须补 json_object,普通调用不补。"""

    def test_injects_json_object_only_for_structured_calls(self):
        calls = []

        class FakeCompletions:
            async def create(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")]
                )

        class FakeClient:
            def __init__(self):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        proxy = ba._JsonObjectClientProxy(FakeClient())

        async def _run():
            await proxy.create(messages=[{"role": "system", "content": "<json_schema>{}</json_schema>"}])
            await proxy.create(messages=[{"role": "user", "content": "hello"}])

        asyncio.run(_run())
        assert calls[0]["response_format"] == {"type": "json_object"}
        assert "response_format" not in calls[1]


class TestNormalizeStateFileEncoding:
    """browser-use 用 gbk 读 storage_state 文件;含中文的登录态必须先转义为 ASCII。"""

    def test_rewrites_non_ascii_to_escaped_ascii(self, tmp_path):
        p = tmp_path / "state.json"
        state = {
            "cookies": [],
            "origins": [{
                "origin": "http://192.168.13.178:8182",
                "localStorage": [{"name": "LBuilderTitle", "value": "鲁班数字工程管理平台"}],
            }],
        }
        p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        ba._normalize_state_file_encoding(p)
        raw = p.read_bytes()
        assert all(b < 128 for b in raw)  # 纯 ASCII
        raw.decode("gbk")  # 不再 UnicodeDecodeError
        assert json.loads(p.read_text(encoding="utf-8")) == state  # 值可还原

    def test_missing_file_is_noop(self, tmp_path):
        ba._normalize_state_file_encoding(tmp_path / "nope.json")

    def test_runner_normalizes_cache_with_chinese(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)
        cache = tmp_path / "http_192.168.13.178_8182.json"
        cache.write_text(
            json.dumps(
                {"cookies": [], "origins": [{
                    "origin": "http://192.168.13.178:8182",
                    "localStorage": [{"name": "LBuilderTitle", "value": "平台"}],
                }]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        runner = ba.UIExecutionRunner(
            db_session,
            agent_factory=lambda llm, prompt, storage_state=None: FakeAgent(llm, prompt),
        )
        asyncio.run(runner.run(rec.id))
        cache.read_text(encoding="ascii")  # 纯 ASCII 才能被 gbk 正常读取


class TestStorageStateRestore:
    def test_restores_from_backup_when_cache_empty(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        runner = ba.UIExecutionRunner(db_session)
        cache = tmp_path / "http_192.168.13.178_8182.json"
        backup = tmp_path / "http_192.168.13.178_8182.json.bak"
        cache.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
        backup.write_text(
            json.dumps({"cookies": [], "origins": [{"origin": "http://x", "localStorage": []}]}),
            encoding="utf-8",
        )
        runner._restore_storage_state_if_empty("http://192.168.13.178:8182")
        assert json.loads(cache.read_text(encoding="utf-8"))["origins"]

    def test_keeps_valid_cache_when_nonempty(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        runner = ba.UIExecutionRunner(db_session)
        cache = tmp_path / "http_192.168.13.178_8182.json"
        backup = tmp_path / "http_192.168.13.178_8182.json.bak"
        state = {"cookies": [{"name": "sid", "value": "1"}], "origins": []}
        cache.write_text(json.dumps(state), encoding="utf-8")
        backup.write_text(json.dumps({"cookies": [], "origins": [{"origin": "http://old"}]}), encoding="utf-8")
        runner._restore_storage_state_if_empty("http://192.168.13.178:8182")
        assert json.loads(cache.read_text(encoding="utf-8")) == state


class TestCancelUIExecution:
    def test_unknown_and_terminal_return_false(self):
        assert ba.cancel_ui_execution("nope") is False
        ba._ui_tasks["done-t1"] = {"task_id": "done-t1", "status": "done", "record_id": 1}
        try:
            assert ba.cancel_ui_execution("done-t1") is False
        finally:
            ba._ui_tasks.pop("done-t1", None)

    def test_running_sets_stop_requested(self):
        ba._ui_tasks["run-t1"] = {
            "task_id": "run-t1", "status": "running", "record_id": 1, "message": "执行中",
        }
        try:
            assert ba.cancel_ui_execution("run-t1") is True
            assert ba._ui_tasks["run-t1"]["stop_requested"] is True
            assert ba._ui_tasks["run-t1"]["message"] == "正在停止执行..."
        finally:
            ba._ui_tasks.pop("run-t1", None)

    def test_mark_stopped_does_not_overwrite_terminal_record(self, db_session):
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)
        rec.status = "PASSED"
        db_session.add(rec)
        db_session.commit()
        ba._mark_stopped(db_session, rec, None)
        db_session.refresh(rec)
        assert rec.status == "PASSED"


class TestBuildLlm:
    """回归:browser-use 0.13.x Agent 只接受带 `provider` 的原生 LLM。

    langchain 的 ChatOpenAI 无 `provider` 属性,会在 Agent.__init__ 抛 AttributeError。
    """

    def test_build_llm_returns_browser_use_compatible_model(self):
        pytest.importorskip("browser_use")
        llm = ba._build_llm()
        assert getattr(llm, "provider", None)
        assert hasattr(llm, "ainvoke")
        # DeepSeek 等网关不支持 response_format=json_schema,必须走 schema 入 prompt 的兼容路径
        assert llm.add_schema_to_system_prompt is True
        assert llm.dont_force_structured_output is True

    def test_build_llm_client_is_json_object_proxy(self):
        pytest.importorskip("browser_use")
        llm = ba._build_llm()
        client = llm.get_client()
        assert isinstance(client, ba._JsonObjectClientProxy)

    def test_default_factory_wires_storage_state_path(self, tmp_path):
        """登录态路径必须落到 BrowserProfile.storage_state,交由 browser-use 加载/写回。"""
        pytest.importorskip("browser_use")
        cache_file = tmp_path / "http_a.com.json"
        agent = ba._default_agent_factory(ba._build_llm(), "task", str(cache_file))
        profile = agent.browser_session.browser_profile
        assert Path(str(profile.storage_state)) == cache_file
        assert agent.settings.max_actions_per_step == ba.MAX_ACTIONS_PER_STEP


class TestLaunchMaxSteps:
    def test_launch_passes_max_steps_to_runner(self, db_session, monkeypatch):
        captured = {}

        class FakeRunner:
            def __init__(self, db, agent_factory=None, max_steps=25, timeout_seconds=600,
                         login_overrides=None, environment_id=None, final_judgement=False):
                captured["max_steps"] = max_steps
                captured["environment_id"] = environment_id
                captured["final_judgement"] = final_judgement

            async def run(self, record_id):
                return None

            def mark_stopped(self, record_id, task_id):
                return None

        monkeypatch.setattr(ba, "UIExecutionRunner", FakeRunner)

        async def _call():
            return ba.launch_ui_execution(db_session, 123, max_steps=77)

        asyncio.run(_call())
        assert captured["max_steps"] == 77


class TestSubprocessCapableLoop:
    """回归:Windows 下 uvicorn --reload 用 SelectorEventLoop,无法 create_subprocess_exec,
    browser-use 启动 Chromium 会 NotImplementedError;必须在 Proactor loop 上执行浏览器阶段。
    """

    def test_selector_loop_cannot_spawn_subprocess(self):
        loop = asyncio.SelectorEventLoop()
        try:
            loop.run_until_complete(self._assert_no_subprocess_support())
        finally:
            loop.close()

    @staticmethod
    async def _assert_no_subprocess_support():
        if os.name != "nt":
            pytest.skip("仅 Windows 的 SelectorEventLoop 不支持子进程")
        assert ba._can_spawn_subprocess() is False

    def test_runner_succeeds_under_selector_loop(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        tc = _make_case(db_session)
        rec = _make_record(db_session, tc)
        runner = ba.UIExecutionRunner(
            db_session,
            agent_factory=lambda llm, prompt, storage_state=None: FakeAgent(llm, prompt),
        )
        if os.name == "nt":
            loop = asyncio.SelectorEventLoop()
            try:
                loop.run_until_complete(runner.run(rec.id))
            finally:
                loop.close()
        else:
            asyncio.run(runner.run(rec.id))
        db_session.refresh(rec)
        db_session.refresh(tc)
        assert rec.status == "PASSED"
        assert len(rec.steps_trace) == 2
        assert tc.status == "PASSED"


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
