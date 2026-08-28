"""browser-use UI 自动化执行 runner(spec 全部 browser-use API 收敛在此文件)。

- 状态机: queued → launching_browser → executing → judging → persisting → done
- agent_factory 注入实现测试隔离(默认真实现,延迟导入 browser-use)
- 登录态:按 origin 的 storage_state 文件缓存,每次执行结束覆盖(自愈)
- 判定映射(spec §8): success==True → 记录+用例 PASSED; success==False → FAILED;
  异常/超时 → 记录 ERROR、用例状态不变
- 并发闸: 模块级 asyncio.Semaphore(1),同一时间只跑一条 UI 执行
"""
import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
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


def _update_task(task_id: Optional[str], **fields) -> None:
    if task_id and task_id in _ui_tasks:
        _ui_tasks[task_id].update(fields)


def _find_task_id_by_record(record_id: int) -> Optional[str]:
    """按 record_id 反查 task_id(与 pipeline 的 _pipeline_tasks 查询方式同构)。"""
    for tid, state in _ui_tasks.items():
        if state.get("record_id") == record_id:
            return tid
    return None


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
    """复用全局 LLM 配置构建 chat model(browser-use 接受任意 langchain chat model)。"""
    cfg = config_manager
    if cfg.get_model_type() == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=cfg.config.get("ollama_model") or "llama3",
            base_url=cfg.get_ollama_url(),
            num_predict=16384,
        )
    from utils.llm_factory import build_chat_model
    return build_chat_model(
        api_key=cfg.get_api_key(),
        api_base_url=cfg.config.get("api_base_url"),
        api_model=cfg.config.get("api_model"),
        max_tokens=16384,
    )


def _default_agent_factory(llm, prompt: str):
    """真实现:延迟导入 browser-use,避免测试环境强依赖。

    browser-use 0.13.x: Agent(task=..., llm=...);run() 回调通过构造参数
    register_new_step_callback 挂载 —— 该参数是普通属性、run 时读取,可在构造后赋值。
    """
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
        # 构造期传入的 session:存活则复用(测试),否则 run() 内自建独立 session
        self._request_db = db
        self.agent_factory = agent_factory or _default_agent_factory
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    def _acquire_session(self) -> Session:
        """优先复用构造期传入的 session(测试用 in-memory session),否则自建独立 session。

        后台任务生命周期长于 HTTP 请求:路由层传入的 session 在请求结束后关闭,
        此时必须从 db.db.engine 自建会话。
        """
        req = self._request_db
        if req is not None:
            try:
                if req.is_active:
                    return req
            except Exception:
                pass
        from db.db import engine
        return Session(engine)

    def _release_session(self, db: Session) -> None:
        """仅关闭自建会话;复用的构造期会话由调用方/fixture 负责。"""
        if db is None or db is self._request_db:
            return
        try:
            db.close()
        except Exception:
            pass

    async def run(self, record_id: int) -> None:
        """完整状态机入口。"""
        db = None
        try:
            db = self._acquire_session()
            record = db.get(UIExecutionRecord, record_id)
            if record is None:
                logger.error(f"[UIExec] record {record_id} 不存在")
                return
            task_id = _find_task_id_by_record(record_id)
            await self._run(db, record, task_id)
        finally:
            self._release_session(db)

    async def _run(self, db: Session, record: UIExecutionRecord, task_id: Optional[str]) -> None:
        agent = None
        meta: Dict[str, Any] = {}
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

            # 每步回调:追加轨迹 + 更新进度
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
            status = "PASSED" if result.success else "FAILED"
            _persist(db, record, testcase, task_id, status=status, summary=summary)
        except Exception as e:
            logger.exception(f"[UIExec] record {record.id} 执行异常")
            _persist_error(db, record, task_id, str(e))
        finally:
            # 无条件导出登录态(真实现才有 browser context;stub 无则静默跳过)
            await self._export_storage_state(agent, meta.get("origin", ""))
            if task_id:
                _update_task(task_id, status="done", progress=100, message="执行结束")

    # ---- 真实现/Stub 差异收敛点 ----

    async def _export_storage_state(self, agent, origin: str) -> None:
        """执行结束导出并覆盖登录态缓存。stub agent 无 browser 上下文,静默跳过。"""
        try:
            if agent is None or not origin:
                return
            state = None
            # browser-use >= 0.13: BrowserSession.export_storage_state() → dict
            bs = getattr(agent, "browser_session", None)
            if bs is not None and hasattr(bs, "export_storage_state"):
                state = await bs.export_storage_state()
            else:
                # 旧版本: browser_context.storage_state()
                context = (
                    getattr(agent, "browser_context", None)
                    or getattr(getattr(agent, "browser", None), "context", None)
                )
                if context is not None:
                    state = await context.storage_state()
            if not state:
                return
            _sessions_dir.mkdir(parents=True, exist_ok=True)
            _cache_path(origin).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[UIExec] 导出登录态失败(不影响判定): {e}")


def _persist(db, record, testcase, task_id, status, summary):
    record.status = status
    record.agent_summary = summary
    record.finished_at = datetime.now(tz=cn_tz)
    testcase.status = status  # PASSED/FAILED 均回写;ERROR 不走此处
    db.add(record)
    db.add(testcase)
    db.commit()
    _update_task(task_id, phase="done", progress=100, result_status=status)


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


def _render_agent_output_action(output) -> str:
    """从 browser-use 0.13 的 AgentOutput 提取可读动作名,用于步骤轨迹。"""
    if output is None:
        return ""
    actions = getattr(output, "action", None) or []
    names = []
    for a in actions:
        try:
            data = a.model_dump(exclude_unset=True)
        except Exception:
            data = {}
        if data:
            names.append(str(next(iter(data))))
    return ", ".join(names) or getattr(output, "next_goal", "") or ""


def _wrap_new_step_callback(callback: Callable[[str], None]):
    """把 browser-use 0.13 的 (BrowserStateSummary, AgentOutput, int) 回调转成字符串回调。"""
    def wrapper(summary, output, step_num):
        callback(_render_agent_output_action(output))
    return wrapper


def _attach_history_callback(agent, callback) -> None:
    """兼容不同版本 browser-use Agent 与 FakeAgent 的回调挂载。"""
    register = getattr(agent, "register_history_step_callback", None)
    if register is not None:
        # 旧版本 browser-use:存在方法级注册
        register(callback)
        return
    if hasattr(agent, "register_new_step_callback"):
        # browser-use >= 0.13:register_new_step_callback 是构造参数,但为普通属性、
        # run 时读取,故构造后可赋值;签名 (BrowserStateSummary, AgentOutput, int)
        agent.register_new_step_callback = _wrap_new_step_callback(callback)
        return
    # FakeAgent 约定
    agent.history_callback = callback


async def _invoke_agent(agent, max_steps: int):
    """统一调用 agent 并归一化结果,使判定逻辑只依赖 success/final_result。

    - FakeAgent: SimpleNamespace(success=..., final_result=...) 直接透传
    - browser-use 0.13.x: Agent.run() → AgentHistoryList,只有 is_successful()/final_result(),
      无 .success 属性 → 在此适配
    """
    result = await agent.run(max_steps=max_steps)
    return _normalize_agent_result(result)


def _normalize_agent_result(result):
    success = getattr(result, "success", None)
    if success is None:
        is_ok = getattr(result, "is_successful", None)
        success = is_ok() if callable(is_ok) else False
    fr = getattr(result, "final_result", None)
    final_text = fr() if callable(fr) else (fr or "")
    return SimpleNamespace(success=bool(success), final_result=lambda: str(final_text or ""))


def launch_ui_execution(db: Session, record_id: int, agent_factory: Optional[Callable] = None) -> str:
    """创建内存任务并后台启动 runner,返回 task_id。"""
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
    # 后台任务生命周期长于 HTTP 请求,路由层 session 在请求结束后关闭,
    # 故生产路径始终自建独立 session(仅测试路径复用构造期传入的 in-memory session)。
    runner = UIExecutionRunner(None, agent_factory=agent_factory)

    async def _guarded():
        async with _EXECUTION_LOCK:
            await runner.run(record_id)

    asyncio.create_task(_guarded())
    return task_id


# 模块级串行闸(spec §8:Semaphore(1))
_EXECUTION_LOCK = asyncio.Semaphore(1)
