# Browser-Use UI 自动化测试执行集成 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让功能测试用例可以通过 browser-use Agent 在无头浏览器中按自然语言步骤执行,自主判定 PASS/FAIL 并写回用例状态,形成与 API 执行对称的 UI 执行闭环。

**Architecture:** 进程内集成(方案 A)。browser-use API 全部收敛在 `app/services/browser_agent.py` 单文件;后台任务复用 pipeline_runner 的 `asyncio.create_task` + 内存字典进度模式;登录态按 origin 用 Playwright storage_state 文件缓存自愈;URL 是执行时上下文(origin + hash 路径两段式,变量替换复用 `substitute_variables`)。

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / browser-use(含 Playwright Chromium) / LangChain ChatOpenAI(复用 `build_chat_model`) / React 18 + Ant Design。

**Spec:** `docs/superpowers/specs/2026-08-28-browser-use-integration-design.md`

## Global Constraints

- 后端 Python 一律用 `backend/.venv`(运行 pytest 用 `cd backend && .venv\Scripts\python.exe -m pytest`,或 bash 下 `.venv/Scripts/python.exe -m pytest`)
- 测试使用内存 SQLite(conftest.py 已提供 `db_engine`/`db_session` fixtures),**绝不调用真 LLM 或真浏览器**;`agent_factory` stub 是隔离手段
- 所有 API 响应:`Response[T]`(`utils/base_response.py`),错误也可用 `HTTPException(status_code=400, detail=...)`(两种模式项目均在用;本功能路由统一用 `HTTPException` 400 + `Response` 成功)
- 时间戳用上海时区:`db/models.py` 的 `BaseModel` 已自动处理 `created_at/updated_at`,`finished_at` 手动 `datetime.now(tz=cn_tz)`
- 路由注册在 `app/main.py` 的 `api_router`,挂 `Permission.TESTCASE_EXECUTE` 级别权限(新权限常量,加入 `ADMIN_PERMISSIONS` 与 `user` 角色)
- 前端 TypeScript strict 模式(`noUnusedLocals`/`noUnusedParameters`),提交前 `npm run lint` 必须零警告
- `backend/data/browser_sessions/` 加入 `.gitignore`(敏感 storage_state)
- 模块串行:`asyncio.Semaphore(1)`,全局唯一
- browser-use 的导入隔离:`app/services/browser_agent.py` 内延迟导入(`from browser_use import Agent`),使测试文件 import 该模块时无需安装 browser-use

## File Structure

```
backend/
├── requirements.txt                          # +browser-use
├── .gitignore (根)                           # +browser_sessions/
├── db/models.py                              # +UIExecutionRecord 表
├── app/
│   ├── permissions.py                        # +Permission.UI_EXECUTE
│   ├── main.py                               # 注册 ui_execution_router
│   ├── routes/ui_execution.py                # 新增:3 个端点
│   └── services/
│       ├── browser_agent.py                  # 新增:runner 核心(状态机/prompt/登录缓存/闸门)
│       └── ui_steps.py                       # 新增:steps 归一化 + URL 拼接(纯函数)
├── tests/test_ui_steps.py                    # 新增
└── tests/test_ui_execution_runner.py         # 新增
frontend/src/
├── types/index.ts                            # +UIExecutionRecord/TaskStatus 类型
├── services/api.ts                           # +uiExecutionApi
├── components/test-case/TestCaseTable.tsx    # +「UI 执行」按钮
├── components/test-case/TestCaseManager.tsx  # 透传 onUiExecute
└── components/modals/UIExecutionModal.tsx    # 新增:执行弹窗(进度+历史 Tab)
App.tsx 由 TestCaseManager 透传链路接线(仅 2 处小改)
```

---

### Task 1: 依赖与数据模型

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `.gitignore`(项目根)
- Modify: `backend/db/models.py`(TestCase 之后、RequirementPoint 之前添加)

**Interfaces:**
- Produces: `UIExecutionRecord` SQLModel 表 — 字段 `id, testcase_id(int, FK testcase.id, index), user_id(str, index), target_url(str), status(str default "RUNNING"), steps_trace(List JSON), agent_summary(str default ""), error(Optional[str]), finished_at(Optional[datetime])`;`created_at/updated_at` 继承自 BaseModel
- Produces: `requirements.txt` 含 `browser-use>=0.9.0`

- [ ] **Step 1: requirements.txt 追加 browser-use**

在 `backend/requirements.txt` 末尾(`pdfplumber>=0.11.0` 之后)追加一行:

```
browser-use>=0.9.0
```

- [ ] **Step 2: 安装依赖并安装 Chromium**

Run(bash,在 backend/ 下):
```bash
.venv/Scripts/python.exe -m pip install browser-use
.venv/Scripts/python.exe -m playwright install chromium
```
Expected: 安装成功。注意:若 `browser-use` 与现有 `langchain`/`pydantic` 版本冲突,pip 会报 resolution error——此时停止并向用户报告冲突详情,不要擅自降级现有依赖。

- [ ] **Step 3: .gitignore 追加登录态缓存目录**

在根 `.gitignore` 的 `# Mock 代理自动生成的 CA/域名证书` 块之后追加:

```
# UI 执行登录态缓存(storage_state,含 token,禁止入库)
/backend/data/browser_sessions/
```

- [ ] **Step 4: 添加 UIExecutionRecord 模型**

在 `backend/db/models.py` 的 `TestCase` 类定义结束后(`session: Optional[Session] = Relationship(...)` 行与空行之后)、`class RequirementPoint` 之前插入:

```python
class UIExecutionRecord(BaseModel, table=True):
    """UI 自动化执行记录(browser-use Agent 驱动无头浏览器执行功能用例)"""
    testcase_id: int = Field(foreign_key="testcase.id", index=True, description="关联用例ID")
    user_id: Optional[str] = Field(default=None, index=True, description="发起人ID（Keycloak sub）")
    target_url: str = Field(default="", description="拼接后的目标页面完整地址")
    status: str = Field(default="RUNNING", description="执行状态: RUNNING/PASSED/FAILED/ERROR")
    steps_trace: List = Field(default_factory=list, sa_type=JSON, description="Agent每步动作轨迹[{step_no,action,url,result}]")
    agent_summary: str = Field(default="", description="Agent最终判定说明(逐条预期核对)")
    error: Optional[str] = Field(default=None, description="异常信息(仅ERROR时)")
    finished_at: Optional[datetime] = Field(default=None, description="执行完成时间(UTC+8)")
```

- [ ] **Step 5: 验证建表**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_smoke.py -v`
Expected: PASS(导入 db.models 触发全表注册,无语法/字段错误)

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt .gitignore backend/db/models.py
git commit -m "feat(ui-exec): browser-use 依赖与 UIExecutionRecord 模型"
```

---

### Task 2: steps 归一化与 URL 拼接(纯函数)

**Files:**
- Create: `backend/app/services/ui_steps.py`
- Test: `backend/tests/test_ui_steps.py`

**Interfaces:**
- Produces:
  - `normalize_ui_steps(steps: list) -> tuple[list[str], bool]` — 返回 `(归一化步骤文本列表, 是否含api_call步骤)`。字符串取原值;dict 且含 `action` 键取 `str(step["action"])`;dict 且判定为 api_call(type=="api_call" 或含 endpoint_id/endpoint_ref)计入 api_call 标志;其余跳过;空字符串跳过
  - `has_api_call_step(steps: list) -> bool` — 任一步为 api_call dict 则 True
  - `build_target_url(site_origin: str, page_path: str) -> str` — 拼接规则:origin 去尾斜杠;path 空 → 仅 origin;path 以 `#`/`/` 开头 → `origin + path`;其余 → `origin + "/" + path`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_ui_steps.py`:

```python
"""UI steps 归一化与 URL 拼接的纯函数测试(spec §4/§5)。"""
from app.services.ui_steps import normalize_ui_steps, has_api_call_step, build_target_url


class TestNormalizeUiSteps:
    def test_plain_strings_pass_through(self):
        steps, _ = normalize_ui_steps(["打开登录页", "点击登录"])
        assert steps == ["打开登录页", "点击登录"]

    def test_pipeline_dict_takes_action_field(self):
        steps, _ = normalize_ui_steps([
            {"step": 1, "action": "输入有效用户名", "expected": "输入成功"},
            {"step": 2, "action": "点击登录按钮", "expected": "跳转首页"},
        ])
        assert steps == ["输入有效用户名", "点击登录按钮"]

    def test_mixed_formats(self):
        steps, _ = normalize_ui_steps([
            "打开登录页",
            {"step": 1, "action": "输入密码", "expected": "成功"},
        ])
        assert steps == ["打开登录页", "输入密码"]

    def test_api_call_steps_flagged_and_skipped(self):
        steps, has_api = normalize_ui_steps([
            {"type": "api_call", "endpoint_id": 1, "method": "POST"},
            "真实操作",
        ])
        assert has_api is True
        assert steps == ["真实操作"]

    def test_all_api_call_returns_empty(self):
        steps, has_api = normalize_ui_steps([
            {"endpoint_ref": 1, "body": "{}"},
        ])
        assert has_api is True
        assert steps == []

    def test_empty_and_garbage_skipped(self):
        steps, has_api = normalize_ui_steps(["", None, 123, "  "])
        assert steps == []
        assert has_api is False


class TestHasApiCallStep:
    def test_true_for_type_marker(self):
        assert has_api_call_step([{"type": "api_call"}]) is True

    def test_true_for_endpoint_ref(self):
        assert has_api_call_step([{"endpoint_ref": 1}]) is True

    def test_false_for_text(self):
        assert has_api_call_step(["打开页面"]) is False


class TestBuildTargetUrl:
    def test_origin_only(self):
        assert build_target_url("http://192.168.13.178:8182", "") == "http://192.168.13.178:8182"

    def test_hash_path(self):
        assert build_target_url(
            "http://192.168.13.178:8182/",
            "#/home/cmp/contract-web/main/ent/contractRegistration",
        ) == "http://192.168.13.178:8182#/home/cmp/contract-web/main/ent/contractRegistration"

    def test_slash_path(self):
        assert build_target_url("http://a.com", "/login") == "http://a.com/login"

    def test_bare_path_gets_slash(self):
        assert build_target_url("http://a.com", "login") == "http://a.com/login"
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_steps.py -v`
Expected: FAIL(`ModuleNotFoundError: app.services.ui_steps`)

- [ ] **Step 3: 实现**

创建 `backend/app/services/ui_steps.py`:

```python
"""UI 执行的 steps 归一化与目标 URL 拼接(纯函数,spec §4/§5)。

数据库中 steps 有三种形态:
  - 纯字符串(单次生成路径功能用例)          → 原样可用
  - {step, action, expected} dict(4阶段流水线) → 取 action 字段
  - {type:"api_call", ...} dict(接口用例)     → UI 执行不支持,跳过并打标志
"""
from typing import Any


def _is_api_call_step(step: Any) -> bool:
    """判定一个 step 是否为接口调用步骤(与 pipeline_runner._is_pipeline_api_call_step 同口径)。"""
    if not isinstance(step, dict):
        return False
    return (
        step.get("type") == "api_call"
        or step.get("endpoint_id") is not None
        or step.get("endpoint_ref") is not None
    )


def has_api_call_step(steps: list) -> bool:
    """任一步骤为接口调用则 True。"""
    return any(_is_api_call_step(s) for s in (steps or []))


def normalize_ui_steps(steps: list) -> tuple[list[str], bool]:
    """归一化 steps 为 Agent 可执行的文本动作列表。

    Returns:
        (动作文本列表, 是否含接口调用步骤)。
    """
    actions: list[str] = []
    has_api = False
    for step in (steps or []):
        if _is_api_call_step(step):
            has_api = True
            continue
        if isinstance(step, str):
            text = step.strip()
            if text:
                actions.append(text)
        elif isinstance(step, dict) and step.get("action"):
            text = str(step["action"]).strip()
            if text:
                actions.append(text)
    return actions, has_api


def build_target_url(site_origin: str, page_path: str) -> str:
    """拼接目标页面地址:origin(去尾斜杠) + hash/普通路径。"""
    origin = (site_origin or "").strip().rstrip("/")
    path = (page_path or "").strip()
    if not path:
        return origin
    if path.startswith("#") or path.startswith("/"):
        return origin + path
    return origin + "/" + path
```

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_steps.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ui_steps.py backend/tests/test_ui_steps.py
git commit -m "feat(ui-exec): steps 归一化与目标 URL 拼接纯函数"
```

---

### Task 3: 权限常量

**Files:**
- Modify: `backend/app/permissions.py`

**Interfaces:**
- Produces: `Permission.UI_EXECUTE = "ui_execute:manage"`;已加入 `ADMIN_PERMISSIONS` 与 `user` 角色集合

- [ ] **Step 1: 添加常量并加入角色集合**

在 `backend/app/permissions.py` 的 `Permission` 类中(`MOCK_LOG_DELETE = "mock_log:delete"` 之后)追加:

```python
    UI_EXECUTE = "ui_execute:manage"
```

在 `ADMIN_PERMISSIONS` 集合中(`Permission.MOCK_LOG_DELETE,` 之后)追加:

```python
    Permission.UI_EXECUTE,
```

打开 `ROLE_PERMISSIONS` 中 `"user"` 角色的集合(约 74-90 行),在其中追加同样的 `Permission.UI_EXECUTE,`(与 `Permission.TESTCASE_GENERATE,` 同组位置)。注意:`ALL_PERMISSIONS` 用反射自动收集,无需修改。

- [ ] **Step 2: 验证导入无误**

Run: `cd backend && .venv/Scripts/python.exe -c "from app.permissions import Permission; print(Permission.UI_EXECUTE)"`
Expected: 打印 `ui_execute:manage`

- [ ] **Step 3: Commit**

```bash
git add backend/app/permissions.py
git commit -m "feat(ui-exec): 新增 UI_EXECUTE 权限常量"
```

---

### Task 4: Runner 核心(状态机、prompt、登录缓存、闸门)

**Files:**
- Create: `backend/app/services/browser_agent.py`
- Test: `backend/tests/test_ui_execution_runner.py`

**Interfaces:**
- Consumes: Task 1 `UIExecutionRecord`;Task 2 `normalize_ui_steps`;`app.routes.proxy.build_param_map(db, environment_id=None, local_parameters=[])` 与 `app.routes.proxy.substitute_variables(text, param_map, unresolved)`;`utils.llm_factory.build_chat_model`;`config.config_manager`(读 `api_key/api_base_url/api_model/ollama_url/ollama_model/model_type`)
- Produces:
  - `_ui_tasks: dict[str, dict]` 模块级任务状态;`get_ui_task(task_id: str) -> Optional[dict]`
  - `build_ui_prompt(target_url: str, actions: list[str], expected: list[str], preset_conditions: list[str], login_ctx: "LoginContext") -> str`
  - `LoginContext` dataclass:`logged_in_cache: bool, username: str, password: str`、方法 `hint() -> str`(按 spec §7.2 双分支措辞;无凭证无缓存返回 `""`)
  - `resolve_login_context(db, param_map: dict) -> tuple[LoginContext, dict]` — 从 param_map 取 `test_username/test_password`;检查 `browser_sessions/{origin}.json` 是否存在
  - `class UIExecutionRunner` — `__init__(self, db, agent_factory=None, max_steps=25, timeout_seconds=600)`;`async run(self, record_id: int) -> None`(完整状态机,详见 Step 3);`agent_factory` 签名:`(llm, prompt: str) -> object`,返回对象需有 `async run(max_steps=...) -> AgentResult`,AgentResult 需有 `.success: bool` 与 `.final_result(): str`(browser-use Agent 原生接口即满足);`agent_factory=None` 时使用真实现(browser-use)
  - `launch_ui_execution(db, record_id: int, agent_factory=None) -> str` — 创建内存任务、`asyncio.create_task(runner.run(record_id))`、返回 task_id

**关键设计(实现时遵循):**
- runner.run 内**自建独立 DB Session**(`from db.db import engine; with Session(engine) as session`),因为路由层传入的 session 在请求结束后关闭
- 每步进度通过 browser-use 的 `register_history_step_callback` (或等价 API;stub 模式下由 fake agent 触发) 更新 `_ui_tasks[task_id]["message"] = f"执行步骤 {n}: {action}"`
- 判定映射(spec §8):`agent_result.success == True` → record PASSED + 用例 PASSED;`success == False` → record FAILED + 用例 FAILED;抛异常/超时 → record ERROR + 用例**不变**
- 登录缓存:`_cache_path(origin) -> Path`(= `backend/data/browser_sessions/{origin转义}.json`,origin 中 `/`、`:` 替换为 `_`);执行前 `exists()` 判断;真实现执行完在 finally 中无条件导出 `context.storage_state()` 覆盖(stub 模式跳过——由 `agent_factory` 是否返回真 browser-use Agent 决定,runner 不感知)
- 超时:`await asyncio.wait_for(agent_run_coro, timeout=self.timeout_seconds)`,`TimeoutError` → ERROR
- `_ui_tasks` 条目结构与 pipeline 同构:`{task_id, status: "running"|"done", phase, progress, message, record_id, error, created_at}`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_ui_execution_runner.py`:

```python
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
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_execution_runner.py -v`
Expected: FAIL(`ModuleNotFoundError: app.services.browser_agent`)

- [ ] **Step 3: 实现 browser_agent.py**

创建 `backend/app/services/browser_agent.py`:

```python
"""browser-use UI 自动化执行 runner(spec 全部 browser-use API 收敛在此文件)。

- 状态机: queued → launching_browser → executing → judging → persisting → done
- agent_factory 注入实现测试隔离(默认真实现,延迟导入 browser-use)
- 登录态:按 origin 的 storage_state 文件缓存,每次执行结束覆盖(自愈)
"""
import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from sqlmodel import Session

from config import config_manager, PROJECT_ROOT
from db.models import TestCase, UIExecutionRecord, cn_tz

logger = logging.getLogger(__name__)

# 内存任务状态(与 pipeline._pipeline_tasks 同构)
_ui_tasks: Dict[str, Dict[str, Any]] = {}

# storage_state 缓存目录(测试中 monkeypatch 为 tmp_path)
_sessions_dir = Path(PROJECT_ROOT) / "data" / "browser_sessions"

EXECUTION_TIMEOUT_SECONDS = 600
MAX_STEPS = 25


def get_ui_task(task_id: str) -> Optional[Dict[str, Any]]:
    return _ui_tasks.get(task_id)


def _update_task(task_id: str, **fields) -> None:
    if task_id in _ui_tasks:
        _ui_tasks[task_id].update(fields)


@dataclass
class LoginContext:
    """登录提示上下文:缓存命中与否决定 prompt 措辞分支。"""
    logged_in_cache: bool = False
    username: str = ""
    password: str = ""

    def hint(self) -> str:
        if not self.username or not self.password:
            return ""
        if self.logged_in_cache:
            return (
                "当前会话应已持有登录态,若打开页面仍被重定向到登录页,"
                f"使用账号 {self.username} 密码 {self.password} 重新登录后继续。"
            )
        return (
            "若用例步骤依赖已登录状态而当前未登录,"
            f"先用账号 {self.username} 密码 {self.password} 登录,再开始用例步骤。"
        )


def _cache_path(origin: str) -> Path:
    """origin → 缓存文件名(http://a.com:80 → http_a.com_80.json)。"""
    safe = origin.replace("://", "_").replace("/", "_").replace(":", "_")
    return _sessions_dir / f"{safe}.json"


def resolve_login_context(db: Session, param_map: dict, target_url: str) -> tuple[LoginContext, dict]:
    """从参数表取登录凭证 + 检查该 origin 是否已有登录态缓存。"""
    ctx = LoginContext(
        username=str(param_map.get("test_username", "") or ""),
        password=str(param_map.get("test_password", "") or ""),
    )
    parsed = urlparse(target_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else target_url
    ctx.logged_in_cache = _cache_path(origin).exists()
    return ctx, {"origin": origin}


def build_ui_prompt(
    target_url: str,
    actions: List[str],
    expected: List[str],
    preset_conditions: List[str],
    login_ctx: LoginContext,
) -> str:
    """组装 Agent 任务 prompt(spec §7.2 模板)。"""
    lines = [f"打开 {target_url},依次完成以下测试步骤。这是软件测试执行,严格按步骤顺序操作:"]
    for i, action in enumerate(actions, 1):
        lines.append(f"{i}. {action}")
    if preset_conditions:
        lines.append("\n前置条件提示(请据此判断当前环境是否满足,不满足时在判定说明中指出):")
        for cond in preset_conditions:
            lines.append(f"- {cond}")
    login_hint = login_ctx.hint()
    if login_hint:
        lines.append(f"\n{login_hint}")
    lines.append("\n全部步骤完成后,对照以下预期结果逐条核对:")
    for i, exp in enumerate(expected, 1):
        lines.append(f"{i}. {exp}")
    lines.append(
        "\n最后调用 done:若所有步骤成功且预期全部满足,success=true,否则 false,"
        "text 中逐条说明每条预期的实际表现。"
    )
    return "\n".join(lines)


def _build_llm():
    """复用全局 LLM 配置构建 ChatOpenAI(browser-use 接受任意 langchain chat model)。"""
    from utils.llm_factory import build_chat_model
    cfg = config_manager
    return build_chat_model(
        api_key=cfg.get_api_key(),
        api_base_url=cfg.config.get("api_base_url"),
        api_model=cfg.config.get("api_model"),
        max_tokens=16384,
    )


def _default_agent_factory(llm, prompt: str):
    """真实现:延迟导入 browser-use,避免测试环境强依赖。"""
    from browser_use import Agent
    return Agent(task=prompt, llm=llm)


class UIExecutionRunner:
    """单条 UI 执行记录的完整生命周期编排。"""

    def __init__(
        self,
        db: Session,
        agent_factory: Optional[Callable] = None,
        max_steps: int = MAX_STEPS,
        timeout_seconds: int = EXECUTION_TIMEOUT_SECONDS,
    ):
        self._request_db = db  # 仅用于构造期读取;run() 内自建 session
        self.agent_factory = agent_factory or _default_agent_factory
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    async def run(self, record_id: int) -> None:
        # 独立 session:后台任务生命周期长于 HTTP 请求
        from db.db import engine
        with Session(engine) as db:
            record = db.get(UIExecutionRecord, record_id)
            if record is None:
                logger.error(f"[UIExec] record {record_id} 不存在")
                return
            task_id = record.task_id if hasattr(record, "task_id") else None
            await self._run(db, record)

    async def _run(self, db: Session, record: UIExecutionRecord) -> None:
        task_id = None
        for tid, state in _ui_tasks.items():
            if state.get("record_id") == record.id:
                task_id = tid
                break
        try:
            testcase = db.get(TestCase, record.testcase_id)
            if testcase is None:
                raise RuntimeError(f"用例 {record.testcase_id} 不存在")

            _update_task(task_id, phase="launching_browser", progress=10, message="正在启动浏览器...")
            from app.routes.proxy import build_param_map
            param_map = build_param_map(db, None, [])
            login_ctx, meta = resolve_login_context(db, param_map, record.target_url)

            llm = _build_llm()
            agent = self.agent_factory(llm, _agent_prompt_for(db, record, testcase, login_ctx))
            _update_task(task_id, phase="executing", progress=30, message="Agent 正在执行步骤...")

            # 每步回调:追加轨迹 + 更新进度(browser-use Agent 的 register_history_step_callback)
            def on_history_step(history_item, _agent=agent):
                text = getattr(history_item, "action", "") or str(history_item)
                record.steps_trace = list(record.steps_trace or []) + [{
                    "step_no": len(record.steps_trace or []) + 1,
                    "action": str(text)[:500],
                    "result": str(getattr(history_item, "result", "") or "")[:500],
                }]
                _update_task(task_id, message=f"执行步骤 {len(record.steps_trace)}: {str(text)[:80]}")

            _attach_history_callback(agent, on_history_step)

            _update_task(task_id, phase="judging", progress=80, message="Agent 正在核对预期结果...")
            try:
                result = await asyncio.wait_for(
                    _invoke_agent(agent, self.max_steps), timeout=self.timeout_seconds
                )
            except asyncio.TimeoutError:
                raise TimeoutError(f"执行超时(>{self.timeout_seconds}s)")

            # 判定映射(spec §8)
            summary = str(result.final_result() or "")
            if result.success:
                _persist(db, record, testcase, task_id, status="PASSED", summary=summary)
            else:
                _persist(db, record, testcase, task_id, status="FAILED", summary=summary)
        except Exception as e:
            logger.exception(f"[UIExec] record {record.id} 执行异常")
            _persist_error(db, record, task_id, str(e))
        finally:
            # 无条件导出登录态(真实现才有 storage_state;stub 无此方法则跳过)
            self._export_storage_state(agent if "agent" in dir() else None, meta.get("origin", "") if "meta" in dir() else "")
            if task_id:
                _update_task(task_id, status="done", progress=100, message="执行结束")

    # ---- 真实现/Stub 差异收敛点 ----

    def _export_storage_state(self, agent, origin: str) -> None:
        """执行结束导出并覆盖登录态缓存。stub agent 无 browser context,静默跳过。"""
        try:
            context = getattr(agent, "browser_context", None) or getattr(
                getattr(agent, "browser", None), "context", None
            )
            if context is None or not origin:
                return
            state = asyncio.get_event_loop().run_until_complete(context.storage_state())
            _sessions_dir.mkdir(parents=True, exist_ok=True)
            _cache_path(origin).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[UIExec] 导出登录态失败(不影响判定): {e}")

    # ---- 持久化 ----


def _persist(db, record, testcase, task_id, status, summary):
    record.status = status
    record.agent_summary = summary
    record.finished_at = datetime.now(tz=cn_tz)
    testcase.status = status  # PASSED/FAILED 均回写;ERROR 不走此处
    db.add(record)
    db.add(testcase)
    db.commit()
    _update_task(task_id, phase="persisting", progress=95, status="done" if False else _ui_tasks.get(task_id, {}).get("status", "running"))
    _update_task(task_id, phase="done", result_status=status)


def _persist_error(db, record, task_id, error_msg):
    record.status = "ERROR"
    record.error = error_msg[:2000]
    record.finished_at = datetime.now(tz=cn_tz)
    db.add(record)
    db.commit()  # 用例状态不动
    _update_task(task_id, status="done", error=error_msg[:200])


def _agent_prompt_for(db, record, testcase, login_ctx) -> str:
    """从用例构建 prompt;steps 未归一化时现场归一化。"""
    from app.services.ui_steps import normalize_ui_steps
    actions, _has_api = normalize_ui_steps(testcase.steps or [])
    return build_ui_prompt(
        record.target_url,
        actions,
        [str(e) for e in (testcase.expected_results or [])],
        [str(c) for c in (testcase.preset_conditions or [])],
        login_ctx,
    )


def _attach_history_callback(agent, callback) -> None:
    """兼容 browser-use Agent 与 FakeAgent 两种回调挂载方式。"""
    register = getattr(agent, "register_history_step_callback", None)
    if register is not None:
        register(callback)
    else:
        agent.history_callback = callback  # FakeAgent 约定


async def _invoke_agent(agent, max_steps: int):
    # browser-use: Agent.run(max_steps=...) → AgentHistoryList(.success/.final_result())
    result = await agent.run(max_steps=max_steps)
    return result


def launch_ui_execution(db: Session, record_id: int, agent_factory: Optional[Callable] = None) -> str:
    """创建内存任务并后台启动 runner,返回 task_id。"""
    import asyncio as _asyncio
    task_id = str(uuid.uuid4())
    _ui_tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "phase": "queued",
        "progress": 0,
        "message": "排队等待中(串行执行)...",
        "record_id": record_id,
        "result_status": None,
        "error": None,
        "created_at": datetime.now(tz=cn_tz).isoformat(),
    }
    runner = UIExecutionRunner(db, agent_factory=agent_factory)

    async def _guarded():
        async with _EXECUTION_LOCK:
            await runner.run(record_id)

    _asyncio.create_task(_guarded())
    return task_id


# 模块级串行闸(spec §8:Semaphore(1))
_EXECUTION_LOCK = asyncio.Semaphore(1)
```

> 注:上面 `_run` 的 finally 中 `agent`/`meta` 变量在异常早退时可能未定义——实现时把 `agent = None`、`meta = {}` 提到 try 块开头初始化,`finally` 里直接用;删除 `"agent" in dir()` 这类防御写法。`_persist` 中的双重 `_update_task` 合并为一行 `_update_task(task_id, phase="done", progress=100, result_status=status)`。

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_execution_runner.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/browser_agent.py backend/tests/test_ui_execution_runner.py
git commit -m "feat(ui-exec): UIExecutionRunner 状态机/prompt/登录缓存/闸门"
```

---

### Task 5: 路由层

**Files:**
- Create: `backend/app/routes/ui_execution.py`
- Modify: `backend/app/main.py`(import + 注册)
- Test: `backend/tests/test_ui_execution_routes.py`

**Interfaces:**
- Consumes: Task 2 `normalize_ui_steps/build_target_url`;Task 4 `launch_ui_execution/get_ui_task/UIExecutionRunner`;Task 3 `Permission.UI_EXECUTE`;`UIExecutionRecord`
- Produces:
  - `POST /api/ui-execution/run` — body `{testcase_id: int, site_origin: str, page_path: str}`;校验(全 400):用例不存在或不属于当前用户、steps 含 api_call、归一化后 steps 为空、origin 为空;`site_origin/page_path` 先过 `substitute_variables` 再拼接;创建 record(RUNNING)→ `launch_ui_execution` → `Response[{task_id, record_id}]`
  - `GET /api/ui-execution/tasks/{task_id}` — `Response[task_dict]`;含 `result_status`(终态时前端据此刷新用例列表)
  - `GET /api/ui-execution/records?testcase_id=N` — `Response[List[record]]` 倒序
- 路由前缀 `prefix="/ui-execution", tags=["ui-execution"]`;main.py 注册挂 `require_permissions(Permission.UI_EXECUTE)`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_ui_execution_routes.py`:

```python
"""ui_execution 路由校验逻辑测试(HTTPException 400 分支 + record 创建)。"""
import pytest
from fastapi import HTTPException
from sqlmodel import select

from db.models import TestCase, UIExecutionRecord


@pytest.fixture()
def fake_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester")


@pytest.fixture()
def fake_tc(db_session):
    tc = TestCase(case_name="功能用例", steps=["打开页面", "点击按钮"], expected_results=["页面变化"])
    db_session.add(tc)
    db_session.commit()
    db_session.refresh(tc)
    return tc


def _run(db_session, fake_user, fake_tc, monkeypatch, site_origin="http://a.com", page_path="#/login"):
    import app.routes.ui_execution as route
    captured = {}

    def fake_launch(db, record_id, agent_factory=None):
        captured["record_id"] = record_id
        return "task-123"

    monkeypatch.setattr(route, "launch_ui_execution", fake_launch)
    return route.run_ui_execution(
        __import__("app.routes.ui_execution", fromlist=["RunRequest"]).RunRequest(
            testcase_id=fake_tc.id, site_origin=site_origin, page_path=page_path
        ),
        db_session,
        fake_user,
    )


def test_run_creates_record_and_launches(db_session, fake_user, fake_tc, monkeypatch):
    result = _run(db_session, fake_user, fake_tc, monkeypatch)
    assert result.data["task_id"] == "task-123"
    rec = db_session.exec(select(UIExecutionRecord)).first()
    assert rec is not None
    assert rec.status == "RUNNING"
    assert rec.target_url == "http://a.com#/login"


def test_run_rejects_api_call_case(db_session, fake_user, fake_tc, monkeypatch):
    fake_tc.steps = [{"type": "api_call", "endpoint_id": 1}]
    db_session.add(fake_tc)
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        _run(db_session, fake_user, fake_tc, monkeypatch)
    assert exc.value.status_code == 400
    assert "接口测试用例" in exc.value.detail


def test_run_rejects_empty_origin(db_session, fake_user, fake_tc, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _run(db_session, fake_user, fake_tc, monkeypatch, site_origin="  ")
    assert exc.value.status_code == 400


def test_run_rejects_missing_case(db_session, fake_user, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _run(
            db_session, fake_user,
            __import__("app.routes.ui_execution", fromlist=["RunRequest"]).RunRequest(
                testcase_id=99999, site_origin="http://a.com", page_path=""
            ),
            monkeypatch,
        )
    assert exc.value.status_code == 404


def test_get_task_returns_state(db_session, fake_user, fake_tc, monkeypatch):
    import app.routes.ui_execution as route
    route._ui_tasks["t1"] = {"task_id": "t1", "status": "running", "phase": "executing",
                             "progress": 40, "message": "执行步骤 2", "record_id": 1,
                             "result_status": None, "error": None, "created_at": "2026-01-01"}
    result = route.get_ui_execution_task("t1", db_session, fake_user)
    assert result.data["phase"] == "executing"


def test_get_task_404(db_session, fake_user):
    import app.routes.ui_execution as route
    with pytest.raises(HTTPException) as exc:
        route.get_ui_execution_task("nope", db_session, fake_user)
    assert exc.value.status_code == 404


def test_records_list_desc(db_session, fake_user, fake_tc):
    import app.routes.ui_execution as route
    for s in ("PASSED", "FAILED"):
        db_session.add(UIExecutionRecord(testcase_id=fake_tc.id, user_id="u1",
                                         target_url="http://a.com", status=s))
    db_session.commit()
    result = route.list_ui_execution_records(fake_tc.id, db_session, fake_user)
    assert [r.status for r in result.data] == ["FAILED", "PASSED"]  # id 倒序
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_execution_routes.py -v`
Expected: FAIL(`ModuleNotFoundError: app.routes.ui_execution`)

- [ ] **Step 3: 实现路由**

创建 `backend/app/routes/ui_execution.py`:

```python
"""UI 自动化执行路由(spec §9 API 契约)。"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.auth import UserInfo
from app.deps import CurrentUser, SessionDep
from app.services.browser_agent import get_ui_task, launch_ui_execution
from app.services.ui_steps import build_target_url, normalize_ui_steps
from db.models import TestCase, UIExecutionRecord, cn_tz
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ui-execution", tags=["ui-execution"])


class RunRequest(BaseModel):
    testcase_id: int
    site_origin: str = ""
    page_path: str = ""


def _substitute(text: str, db, user: UserInfo) -> str:
    """对输入做 {{var}} 变量替换(默认环境参数)。"""
    from app.routes.proxy import build_param_map, substitute_variables
    param_map = build_param_map(db, None, [])
    return substitute_variables(text or "", param_map, set())


@router.post("/run", response_model=Response)
def run_ui_execution(body: RunRequest, db: SessionDep, user: CurrentUser):
    testcase = db.get(TestCase, body.testcase_id)
    if testcase is None or (testcase.user_id and testcase.user_id != user.user_id):
        raise HTTPException(status_code=404, detail="测试用例不存在")

    actions, has_api = normalize_ui_steps(testcase.steps or [])
    if has_api:
        raise HTTPException(status_code=400, detail="接口测试用例不支持 UI 执行")
    if not actions:
        raise HTTPException(status_code=400, detail="用例无可执行的 UI 步骤")

    site_origin = _substitute(body.site_origin, db, user).strip()
    page_path = _substitute(body.page_path, db, user).strip()
    if not site_origin:
        raise HTTPException(status_code=400, detail="站点地址不能为空")
    target_url = build_target_url(site_origin, page_path)

    record = UIExecutionRecord(
        testcase_id=testcase.id,
        user_id=user.user_id,
        target_url=target_url,
        status="RUNNING",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    task_id = launch_ui_execution(db, record.id)
    logger.info(f"[UIExec] 用户 {user.username} 发起执行 record={record.id} url={target_url}")
    return Response(data={"task_id": task_id, "record_id": record.id})


@router.get("/tasks/{task_id}", response_model=Response)
def get_ui_execution_task(task_id: str, db: SessionDep, user: CurrentUser):
    task = get_ui_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return Response(data=task)


@router.get("/records", response_model=Response)
def list_ui_execution_records(
    db: SessionDep,
    user: CurrentUser,
    testcase_id: int = None,
):
    if testcase_id is None:
        raise HTTPException(status_code=400, detail="缺少 testcase_id 参数")
    records = db.exec(
        select(UIExecutionRecord)
        .where(UIExecutionRecord.testcase_id == testcase_id)
        .order_by(UIExecutionRecord.id.desc())
    ).all()
    return Response(data=records)
```

> 注:`testcase_id: int = None` 用 FastAPI query 参数写法;若 strict lint 报 Optional 类型警告,改为 `testcase_id: Optional[int] = None`(文件顶部已 import Optional)。

- [ ] **Step 4: 注册路由到 main.py**

在 `backend/app/main.py`:

import 区(`from app.routes.testcase import ...` 行后)追加:
```python
from app.routes.ui_execution import router as ui_execution_router
```

注册区(在 `api_router.include_router(chat_router)` 之前)追加:
```python
api_router.include_router(
    ui_execution_router,
    dependencies=[Depends(require_permissions(Permission.UI_EXECUTE))],
)
```

- [ ] **Step 5: 运行验证通过 + 全量回归**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_execution_routes.py tests/test_ui_execution_runner.py tests/test_ui_steps.py -v`
Expected: 全部 passed

Run: `cd backend && .venv/Scripts/python.exe -m pytest`
Expected: 全量通过,无回归

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/ui_execution.py backend/app/main.py backend/tests/test_ui_execution_routes.py
git commit -m "feat(ui-exec): UI 执行路由(run/tasks/records)与权限注册"
```

---

### Task 6: 前端类型与 API 客户端

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/services/api.ts`

**Interfaces:**
- Produces(types/index.ts,追加在 `GlobalParameter` 接口之后):
```typescript
// UI 自动化执行记录(browser-use Agent)
export interface UIExecutionRecord {
  id: number;
  testcase_id: number;
  user_id?: string;
  target_url: string;
  status: 'RUNNING' | 'PASSED' | 'FAILED' | 'ERROR';
  steps_trace: Array<{ step_no: number; action: string; result?: string; url?: string }>;
  agent_summary: string;
  error?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface UIExecutionTaskStatus {
  task_id: string;
  status: 'running' | 'done';
  phase: 'queued' | 'launching_browser' | 'executing' | 'judging' | 'persisting' | 'done';
  progress: number;
  message: string;
  record_id: number;
  result_status: 'PASSED' | 'FAILED' | 'ERROR' | null;
  error: string | null;
  created_at: string;
}
```
- Produces(api.ts,追加在 `globalParameterApi` 之后):
```typescript
// UI 自动化执行 API
export const uiExecutionApi = {
  run: (testcaseId: number, siteOrigin: string, pagePath: string): Promise<ApiResponse<{ task_id: string; record_id: number }>> =>
    api.post('/ui-execution/run', { testcase_id: testcaseId, site_origin: siteOrigin, page_path: pagePath }),

  getTask: (taskId: string): Promise<ApiResponse<UIExecutionTaskStatus>> =>
    api.get(`/ui-execution/tasks/${taskId}`),

  getRecords: (testcaseId: number): Promise<ApiResponse<UIExecutionRecord[]>> =>
    api.get('/ui-execution/records', { params: { testcase_id: testcaseId } })
};
```
并在 `api.ts` 顶部 types import 中补 `UIExecutionRecord, UIExecutionTaskStatus`(与现有 type import 合并;import 形如 `import type { ..., UIExecutionRecord, UIExecutionTaskStatus } from '../types';`——以文件中现有 import 语句实际写法为准,追加两个名字)。

- [ ] **Step 1: 追加类型与 API**

按上述 Interfaces 修改两个文件。`ApiResponse` 类型在 api.ts 已存在,直接使用。

- [ ] **Step 2: Lint 验证**

Run: `cd frontend && npm run lint`
Expected: 零 error 零 warning

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/services/api.ts
git commit -m "feat(ui-exec): 前端 UI 执行类型与 API 客户端"
```

---

### Task 7: UIExecutionModal 组件

**Files:**
- Create: `frontend/src/components/modals/UIExecutionModal.tsx`

**Interfaces:**
- Consumes: Task 6 `uiExecutionApi`/类型;`globalParameterApi.getEnvironments()`(取默认环境 `web_url`/`test_username` 预填与提示)
- Produces: 组件 props 契约:
```typescript
interface UIExecutionModalProps {
  open: boolean;
  testcase: TestCase | null;
  onClose: () => void;
  onFinished?: () => void;  // 终态后通知父级刷新用例列表
}
```
组件导出 `isUiExecutable(testcase: TestCase): boolean`(steps 存在且无 api_call 步骤——复用前端判定:`steps.every(s => typeof s === 'string' || !('type' in s && (s as any).type === 'api_call' || s.endpoint_id != null || s.endpoint_ref != null))`; TestCase 类型已有 `steps: (string | Record<string, any>)[]`)

**组件结构(spec §10):**
- Tabs:「执行」/「执行历史」
- 执行 Tab:
  - 站点地址 Input(预填链:localStorage `ui_exec_last_{testcaseId}` 的 origin → 默认环境 `web_url` 参数 → 空)
  - 页面路径 Input(预填链:localStorage path → 空)
  - 前置条件只读 Alert(`testcase.preset_conditions` 逐条;空则不渲染)
  - 登录提示:默认环境有 `test_username` → 绿色 Alert「已配置登录凭证,Agent 将按需自动登录」;无 → 灰色 Alert「未配置 test_username/test_password 环境参数,目标系统需登录时请先在全局参数中配置」
  - 「开始执行」按钮 → `uiExecutionApi.run` → 进入进度模式:antd `Progress`(percent=task.progress)+ `message` 文本,`setInterval` 2s 轮询 `getTask`;`result_status` 非空即终态:`PASSED`→`Modal.success` 风格 Alert + `onFinished?.()`;`FAILED`→红色 Alert 显示 `agent_summary`;`ERROR`→橙色 Alert 显示 `error`;轮询停止
- 执行历史 Tab:打开时调 `getRecords(testcase.id)`,antd Table(status Tag 颜色 PASSED=green/FAILED=red/ERROR=orange/RUNNING=blue、target_url(ellipsis)、finished_at、agent_summary Tooltip;行展开展示 steps_trace 逐条 + agent_summary 全文)

- [ ] **Step 1: 实现组件**

按上述结构与 props 契约实现完整组件。样式参照 `components/modals/` 下现有 modal(antd `Modal` + `Tabs`),表单用受控 `useState`,轮询清理放 `useEffect` return。关键代码骨架(完整实现由执行者按骨架补齐 JSX 细节,所有行为必须落地,不得省略):

```typescript
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Progress, Table, Tabs, Tag, Tooltip, message } from 'antd';
import { PlayCircleOutlined } from '@ant-design/icons';
import { TestCase, UIExecutionRecord, UIExecutionTaskStatus } from '../../types';
import { globalParameterApi, uiExecutionApi } from '../../services/api';

export const isUiExecutable = (testcase: TestCase): boolean => {
  if (!testcase.steps || testcase.steps.length === 0) return false;
  return !testcase.steps.some((s) => {
    if (typeof s !== 'object' || s === null) return false;
    const dict = s as Record<string, any>;
    return dict.type === 'api_call' || dict.endpoint_id != null || dict.endpoint_ref != null;
  });
};

const LAST_URL_KEY = (id: number) => `ui_exec_last_${id}`;

interface UIExecutionModalProps {
  open: boolean;
  testcase: TestCase | null;
  onClose: () => void;
  onFinished?: () => void;
}

const UIExecutionModal: React.FC<UIExecutionModalProps> = ({ open, testcase, onClose, onFinished }) => {
  const [siteOrigin, setSiteOrigin] = useState('');
  const [pagePath, setPagePath] = useState('');
  const [hasCredentials, setHasCredentials] = useState<boolean | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<UIExecutionTaskStatus | null>(null);
  const [records, setRecords] = useState<UIExecutionRecord[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 打开时:预填 + 拉凭证状态 + 拉历史
  useEffect(() => {
    if (!open || !testcase) return;
    const last = localStorage.getItem(LAST_URL_KEY(testcase.id));
    if (last) {
      try {
        const parsed = JSON.parse(last) as { origin: string; path: string };
        setSiteOrigin(parsed.origin ?? '');
        setPagePath(parsed.path ?? '');
      } catch { /* 忽略损坏缓存 */ }
    } else {
      setSiteOrigin(''); setPagePath('');
    }
    setProgress(null); setRunning(false);
    globalParameterApi.getEnvironments().then((res) => {
      const env = (res.data ?? []).find((e) => e.is_default) ?? (res.data ?? [])[0];
      if (!env) return;
      if (!siteOriginPrefilled.current) {
        const webUrl = env.parameters.find((p) => p.key === 'web_url')?.value ?? '';
        if (webUrl && !last) setSiteOrigin(webUrl);
      }
      setHasCredentials(!!env.parameters.find((p) => p.key === 'test_username')?.value);
    }).catch(() => setHasCredentials(null));
    uiExecutionApi.getRecords(testcase.id).then((res) => setRecords(res.data ?? [])).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, testcase?.id]);

  const siteOriginPrefilled = useRef(false);
  useEffect(() => { if (siteOrigin) siteOriginPrefilled.current = true; }, [siteOrigin]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const startExecution = async () => {
    if (!testcase) return;
    if (!siteOrigin.trim()) { message.warning('请填写站点地址'); return; }
    try {
      const res = await uiExecutionApi.run(testcase.id, siteOrigin.trim(), pagePath.trim());
      if (res.code !== 200 || !res.data) { message.error(res.message || '发起执行失败'); return; }
      localStorage.setItem(LAST_URL_KEY(testcase.id), JSON.stringify({ origin: siteOrigin.trim(), path: pagePath.trim() }));
      setRunning(true);
      pollRef.current = setInterval(async () => {
        const taskRes = await uiExecutionApi.getTask(res.data!.task_id);
        if (taskRes.code === 200 && taskRes.data) {
          setProgress(taskRes.data);
          if (taskRes.data.status === 'done') {
            stopPolling();
            setRunning(false);
            onFinished?.();
            uiExecutionApi.getRecords(testcase.id).then((r) => setRecords(r.data ?? [])).catch(() => {});
          }
        }
      }, 2000);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '发起执行失败');
    }
  };

  // ... JSX:Modal(title=`UI 执行 - ${testcase?.case_name ?? ''}`) > Tabs(执行/执行历史)
  // 执行 Tab 按上面结构渲染 Input×2、Alert×2、Progress(running 时)、终态 Alert、开始执行按钮(running 时 loading 禁用)
  // 历史 Tab 渲染 Table,行展开渲染 steps_trace 列表与 agent_summary
  return null; // 执行者替换为完整 JSX
};

export default UIExecutionModal;
```

> **执行者注意**:`return null` 必须替换为完整 JSX(两 Tab 全部内容);`siteOriginPrefilled` 的预填去重逻辑可简化——若实现中发现更清晰的写法(如把 web_url 预填合并进首次 useEffect 的一次性赋值),采用更清晰写法,行为要求不变:用户未手动输入时才用 web_url 预填,localStorage 优先于 web_url。

- [ ] **Step 2: Lint 验证**

Run: `cd frontend && npm run lint`
Expected: 零 error 零 warning(严格模式:noUnusedLocals/noUnusedParameters——确认无未使用 import)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/modals/UIExecutionModal.tsx
git commit -m "feat(ui-exec): UIExecutionModal 执行弹窗(进度轮询+历史 Tab)"
```

---

### Task 8: 用例表格接线

**Files:**
- Modify: `frontend/src/components/test-case/TestCaseTable.tsx`
- Modify: `frontend/src/components/test-case/TestCaseManager.tsx`
- Modify: `frontend/src/App.tsx`(两处 TestCaseManager 调用点,约 1397 与 1487 行)

**Interfaces:**
- Consumes: Task 7 `UIExecutionModal`/`isUiExecutable`
- Produces: `TestCaseTableProps` 新增 `onUiExecute?: (testcase: TestCase) => void`;App.tsx 新增 state `uiExecTarget: TestCase | null` + handler `handleUiExecuteTestcase`

- [ ] **Step 1: TestCaseTable 加按钮**

props 接口(`TestCaseTableProps`,约 16-31 行)追加:
```typescript
  onUiExecute?: (testcase: TestCase) => void;
```

解构参数(约 55-70 行)追加 `onUiExecute,`。

import 区(`@ant-design/icons` 导入)追加 `ChromeOutlined`。

操作列(`{onValidate && ...}` 块之后、`移动` Tooltip 之前)插入:
```tsx
          {onUiExecute && isUiExecutable(record) && (
            <Tooltip title="UI 执行" placement="top">
              <button className="tcm-action-btn tcm-action-btn--ui" onClick={(event) => { stopRowClick(event); onUiExecute(record); }}>
                <ChromeOutlined />
              </button>
            </Tooltip>
          )}
```

文件顶部 import 追加:
```typescript
import { isUiExecutable } from '../modals/UIExecutionModal';
```

操作列 `width: 224` 改为 `width: 264`(容纳新按钮)。

- [ ] **Step 2: TestCaseManager 透传**

props 接口追加 `onUiExecute?: (testcase: TestCase) => void;`,解构追加 `onUiExecute,`,`<TestCaseTable ...>` 处追加 `onUiExecute={onUiExecute}`。

- [ ] **Step 3: App.tsx 接线**

在 `handleApiExecuteTestcase` 定义附近新增 state 与 handler:
```typescript
  const [uiExecTarget, setUiExecTarget] = useState<TestCase | null>(null);
  const handleUiExecuteTestcase = (testcase: TestCase) => setUiExecTarget(testcase);
```

两处 `<TestCaseManager ...>`(1397、1487 行附近)追加 prop:
```typescript
                                onUiExecute={handleUiExecuteTestcase}
```

在 App.tsx 现有 modal 渲染区(与其它模态框并列处)追加:
```tsx
            <UIExecutionModal
              open={uiExecTarget !== null}
              testcase={uiExecTarget}
              onClose={() => setUiExecTarget(null)}
              onFinished={() => {
                if (selectedSession) loadTestcases(selectedSession.id, filters ?? undefined);
              }}
            />
```
import 追加 `import UIExecutionModal from './components/modals/UIExecutionModal';` 与(若无)`import { TestCase } from './types';`(App.tsx 已有 TestCase 类型,确认即可)。

- [ ] **Step 4: Lint 验证**

Run: `cd frontend && npm run lint`
Expected: 零 error 零 warning

- [ ] **Step 5: 构建验证**

Run: `cd frontend && npm run build`
Expected: tsc + vite 构建成功

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/test-case/TestCaseTable.tsx frontend/src/components/test-case/TestCaseManager.tsx frontend/src/App.tsx
git commit -m "feat(ui-exec): 用例表格接入 UI 执行按钮与弹窗"
```

---

### Task 9: 全量验证与真实冒烟

**Files:**
- 无新文件;验证性任务

**Interfaces:**
- Consumes: 全部前序任务

- [ ] **Step 1: 后端全量测试**

Run: `cd backend && .venv/Scripts/python.exe -m pytest`
Expected: 全部通过

- [ ] **Step 2: 前端 lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: 双绿

- [ ] **Step 3: 真实冒烟(需用户参与)**

启动后端(`cd backend && .venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000`)与前端(`cd frontend && npm run dev`),用户登录后在任一功能用例行点「UI 执行」:
1. 输入 `http://192.168.13.178:8182` + `#/home/cmp/contract-web/main/ent/contractRegistration`,配置过 `test_username/test_password` 的话验证登录分支
2. 观察进度轮询与终态展示
3. 确认 `TestCase.status` 已更新、执行历史 Tab 有记录
4. **不做代码改动**;发现问题记录并报告,由用户决定是否修复

- [ ] **Step 4: Commit(如有冒烟期微调)**

```bash
git add -A
git commit -m "fix(ui-exec): 冒烟反馈修正"
```
(无改动则跳过)

---

## Self-Review 结论

1. **Spec 覆盖**:§2 架构→Task 1/4/5;§3 数据模型→Task 1;§4 兼容性→Task 2(归一化+拒绝);§5 URL→Task 2(拼接)+Task 5(变量替换/校验)+Task 7(两段输入/预填链);§6 登录缓存→Task 4;§7 流程/prompt/闸门→Task 4;§8 错误处理/串行→Task 4(Semaphore/ERROR 不回写)+Task 5(400 校验);§9 API→Task 5;§10 前端→Task 6/7/8;§11 测试→各任务 TDD + Task 9;§13 YAGNI 未引入批量/定时/断言。无缺口。
2. **占位符扫描**:Task 7 Step 1 的 `return null` 是**明示的执行者替换指令**并附完整行为规格与允许的简化说明——已标记为必须替换项,非隐性 TBD。其余步骤均含完整代码。
3. **类型一致性**:`normalize_ui_steps` 返回 `tuple[list[str], bool]` 在 Task 2 定义、Task 4/5 使用一致;`launch_ui_execution(db, record_id, agent_factory=None) -> str` Task 4 定义、Task 5 使用一致(路由测试以 `fake_launch(db, record_id, agent_factory=None)` 捕获,签名匹配);`UIExecutionRecord` 字段在 Task 1/4/5/6 一致;`Permission.UI_EXECUTE` Task 3 定义、Task 5 使用一致;前端 `UIExecutionTaskStatus` 字段与 Task 4 `_ui_tasks` 条目键一致(`status/phase/progress/message/record_id/result_status/error`)。
