"""browser-use UI 自动化执行 runner(spec 全部 browser-use API 收敛在此文件)。

模块分区(按依赖顺序,便于定位):
  §1  常量与预算        EXECUTION_TIMEOUT_SECONDS / MAX_STEPS / LOGIN_* / 执行状态 / 并发闸
  §2  内存任务状态      _ui_tasks / _ui_task_handles / get_ui_task / _update_task
  §3  登录上下文与 prompt  LoginContext / build_ui_prompt
  §4  登录态文件        _cache_path / read_state_file / state_file_stats / sync_state_file ...
  §5  仅登录会话        establish_login_session / mark_state_established / is_state_established
  §6  browser-use 适配  _build_llm / _JsonObjectClientProxy / 回调挂载 / 结果归一化
  §7  事件循环适配      _can_spawn_subprocess / _run_browser_phase
  §8  执行编排          UIExecutionRunner + 步骤轨迹/prompt 组装助手
  §9  状态落库          _persist / _persist_error / _mark_stopped
  §10 启动与取消        launch_ui_execution / cancel_ui_execution

设计要点:
- 状态机: queued → launching_browser → executing → judging → persisting → done
- agent_factory 注入实现测试隔离(默认真实现,延迟导入 browser-use)
- 登录态:按 origin 的 storage_state 文件缓存,每次执行结束覆盖(自愈)
- 判定映射(spec §8): success==True → 记录+用例 PASSED; success==False → FAILED;
  异常/超时 → 记录 ERROR、用例状态不变
- 并发闸: 模块级 asyncio.Semaphore(1),同一时间只跑一条 UI 执行

外部契约:app/services/page_login.py、app/services/page_object_runner.py、
app/routes/ui_automation.py 均以 `ba.<name>` 直接引用本模块的私有符号(_cache_path /
_sessions_dir / _established_sessions / _run_browser_phase /
_normalize_state_file_encoding / _EXECUTION_LOCK 等)。这些名字与签名属跨模块契约,
重构时不可重命名或移除。
"""
import asyncio
import json
import logging
import os
import shutil
import threading
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

# task_id → 后台 asyncio.Task 句柄,用于「停止执行」取消
_ui_task_handles: Dict[str, "asyncio.Task"] = {}

# storage_state 缓存目录(测试中 monkeypatch 为 tmp_path)
_sessions_dir = Path(PROJECT_ROOT) / "data" / "browser_sessions"

EXECUTION_TIMEOUT_SECONDS = 600
# 步数预算:登录(含企业选择)+ 导航到目标页 + 用例多步表单操作,25 步常不够用,
# 默认提高。MAX_STEPS_LIMIT 为请求可透传的上限,防止异常大值拖垮执行。
MAX_STEPS = 60
MAX_STEPS_LIMIT = 200

# 单步内最多批量动作数(browser-use 默认 5):提高以减少总步数消耗。
MAX_ACTIONS_PER_STEP = 10

# 仅登录任务的步数/超时预算(登录 + 企业选择;刻意收紧以抑制重复登录)
LOGIN_MAX_STEPS = 20
LOGIN_TIMEOUT_SECONDS = 300

# 执行记录/用例状态(spec §8 判定映射);值即落库与前端约定的字面量,不可改。
STATUS_RUNNING = "RUNNING"
STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
STATUS_ERROR = "ERROR"
STATUS_STOPPED = "STOPPED"

# 模块级串行闸(spec §8:Semaphore(1));page_object_runner 复用同一把闸
_EXECUTION_LOCK = asyncio.Semaphore(1)


def get_ui_task(task_id: str) -> Optional[Dict[str, Any]]:
    return _ui_tasks.get(task_id)


def _update_task(task_id: Optional[str], **fields: Any) -> None:
    """就地更新内存任务状态;task_id 为空或任务已回收时静默跳过(不抛错)。"""
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
    """登录提示上下文:缓存命中与否 + 是否显式配置登录入口,决定 prompt 措辞分支。

    凭证与入口均来自全局参数:`test_username` / `test_password` /
    `test_login_url`(SSO 或登录页直链) / `test_login_method`(登录方式名称)。
    """
    logged_in_cache: bool = False
    username: str = ""
    password: str = ""
    login_url: str = ""
    login_method: str = ""
    company: str = ""

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    def configured(self) -> bool:
        """是否配置了任一登录信息(入口或凭证)。"""
        return bool(self.login_url or self.has_credentials)

    def hint(self) -> str:
        if self.login_url:
            return self._explicit_hint()
        if not self.has_credentials:
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

    def _explicit_hint(self) -> str:
        """显式配置了登录入口:给出确切入口/方式/凭证,并禁止 Agent 猜测。"""
        if self.logged_in_cache:
            head = "当前会话应已持有登录态;若打开页面仍被重定向到登录页,请按以下配置重新登录:"
        else:
            head = "若用例步骤依赖已登录状态而当前未登录,请按以下配置登录:"
        lines = [head, f"- 登录入口:{self.login_url}"]
        if self.login_method:
            lines.append(f"- 登录方式:{self.login_method}")
        if self.has_credentials:
            lines.append(f"- 账号:{self.username}")
            lines.append(f"- 密码:{self.password}")
            lines.append("登录凭证以以上配置为准,不要猜测或尝试其他账号密码。登录成功后再开始用例步骤。")
        else:
            lines.append(
                "未配置登录凭证,不要猜测或尝试任何账号密码;若因此无法登录,"
                "直接在判定说明中写明「前置条件不满足(未登录)」,不要绕过登录继续执行。"
            )
        return "\n".join(lines)

    def post_login_hint(self, target_url: str) -> str:
        """登录成功后的收尾动作:企业/身份选择 + 回到目标页面。

        仅在有登录配置(入口或凭证)时返回;用于修复「登录后跳转工作台,回不到
        用例目标页」的问题。无登录场景不产生任何文本,避免污染 prompt。
        """
        if not self.configured:
            return ""
        lines = ["登录成功后的收尾(必须全部完成后再开始用例步骤):"]
        if self.company:
            lines.append(
                f"- 若弹出企业/身份选择窗口,选择企业「{self.company}」及对应身份,点击确定/进入。"
            )
        else:
            lines.append(
                "- 若弹出企业/身份选择窗口,选择与登录账号对应的企业及身份,点击确定/进入。"
            )
        lines.append(
            "- 登录后系统通常会跳转到工作台/首页,该页面不是用例目标页,不要在此开始执行用例步骤。"
            f"无论登录后停留在哪个页面,都必须立即重新访问目标页面地址 {target_url},"
            "等待页面加载完成并确认已进入用例所述页面后,再从第 1 步开始执行;"
            "若重新访问后仍被重定向到登录页,说明登录未成功,请重新执行登录。"
        )
        return "\n".join(lines)


def _cache_path(origin: str) -> Path:
    """origin → 缓存文件名,归一化 scheme/host 大小写与默认端口。

    http://a.com:80 与 http://a.com 映射同一文件(http_a.com.json);非默认端口保留。
    """
    parsed = urlparse(origin)
    scheme = (parsed.scheme or "http").lower()
    host = (parsed.hostname or "unknown").lower()
    port = parsed.port
    default_port = {"http": 80, "https": 443}.get(scheme)
    if port is not None and port == default_port:
        port = None
    safe = f"{scheme}_{host}"
    if port is not None:
        safe += f"_{port}"
    return _sessions_dir / f"{safe}.json"


# ---------------------------------------------------------------------------
# 登录配置(LoginProfile)的登录态文件:按配置 id 隔离,可与 origin 缓存互相同步
# ---------------------------------------------------------------------------

def read_state_file(path: Optional[Path]) -> Optional[dict]:
    """读取 storage_state 文件;不存在/损坏返回 None。"""
    if not path:
        return None
    try:
        file_path = Path(path)
        if not file_path.exists():
            return None
        return json.loads(file_path.read_text(encoding="utf-8") or "{}")
    except Exception as e:  # noqa: BLE001 - 状态文件损坏按「无登录态」处理
        logger.warning(f"[UIAuto] 读取登录态文件失败({path}): {e}")
        return None


def state_meaningful(state: dict) -> bool:
    """登录态是否含真实凭据:有 cookie,或任一 origin 的 localStorage 里有足够长的条目。

    鲁班等 localStorage 型站点 cookies 恒为 0,登录态(LBuilderToken 等 JWT)全在
    localStorage;两类「空壳」都不算已登录 —— 实测会误判成已登录后直接复用,
    页面被重定向回登录页:
      1. 只有 origin 没有任何 localStorage 条目({"cookies":[],"origins":[{...}]}),
      2. 只剩 LBuilderTitle 这类短展示键(值 < 20 字符),没有 token。
    """
    if not isinstance(state, dict):
        return False
    if state.get("cookies"):
        return True
    for origin in state.get("origins") or []:
        for item in origin.get("localStorage") or []:
            if len(str(item.get("value") or "")) >= 20:
                return True
    return False


def state_file_stats(path: Optional[Path]) -> Dict[str, Any]:
    """登录态文件统计(给前端展示用,不含 cookie/token 明文)。"""
    file_path = Path(path) if path else None
    stats = {
        "path": str(file_path) if file_path else "",
        "exists": bool(file_path and file_path.exists()),
        "cookies": 0,
        "origins": 0,
        "meaningful": False,
        "updated_at": None,
    }
    if not stats["exists"]:
        return stats
    try:
        stats["updated_at"] = datetime.fromtimestamp(file_path.stat().st_mtime, tz=cn_tz).isoformat()
    except Exception:  # noqa: BLE001
        pass
    state = read_state_file(file_path) or {}
    stats["cookies"] = len(state.get("cookies") or [])
    stats["origins"] = len(state.get("origins") or [])
    stats["meaningful"] = state_meaningful(state)
    return stats


def has_valid_state_file(path: Optional[Path], ttl_hours: int = 0) -> bool:
    """登录态文件是否存在且含真实凭据(空壳不算);ttl_hours>0 时额外要求未过期。"""
    stats = state_file_stats(path)
    if not stats["exists"] or not stats["meaningful"]:
        return False
    if ttl_hours and ttl_hours > 0:
        updated = stats.get("updated_at")
        if updated:
            try:
                age_hours = (datetime.now(tz=cn_tz) - datetime.fromisoformat(updated)).total_seconds() / 3600
                if age_hours > ttl_hours:
                    return False
            except Exception:  # noqa: BLE001 - 时间解析异常按有效处理,避免误触发重登录
                return True
    return True


def sync_state_file(src: Optional[Path], dst: Optional[Path]) -> bool:
    """把登录态文件复制一份到目标路径(归一化编码后写入)。"""
    if not src or not dst:
        return False
    try:
        state = read_state_file(src)
        if not state or not (state.get("cookies") or state.get("origins")):
            return False
        dst_path = Path(dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_text(json.dumps(state, ensure_ascii=True), encoding="ascii")
        return True
    except Exception as e:  # noqa: BLE001 - 同步失败不影响主流程
        logger.warning(f"[UIAuto] 登录态同步失败({src} → {dst}): {e}")
        return False


def copy_state_file(src: Optional[Path], dst: Optional[Path]) -> bool:
    """原样复制登录态文件(含备份语义,不做结构校验)。"""
    if not src or not dst:
        return False
    try:
        src_path, dst_path = Path(src), Path(dst)
        if not src_path.exists():
            return False
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_path, dst_path)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[UIAuto] 登录态文件复制失败({src} → {dst}): {e}")
        return False


def _normalize_state_file_encoding(path: Path) -> None:
    """把 storage_state 文件重写为纯 ASCII(`\\uXXXX` 转义)。

    browser-use 0.13 的 StorageStateWatchdog 读写文件时**未指定 encoding**
    (load: `anyio.Path.read_text()`;save merge: `json_path.read_text()`),在中文
    Windows 上默认为 gbk。而登录态里的中文值(如 `LBuilderTitle`、`tenantName`)
    以 UTF-8 写入,gbk 解码直接 UnicodeDecodeError → 登录态加载失败、每次执行都
    重新登录。转义成 ASCII 后 gbk/utf-8 均可读,`json.loads` 会还原为原文。
    """
    try:
        if not path.exists():
            return
        raw = path.read_text(encoding="utf-8")
        state = json.loads(raw or "{}")
        normalized = json.dumps(state, ensure_ascii=True)
        # 内容已一致就不再写回:mtime 是登录态新鲜度(TTL)的判据,
        # 每次访问都重写会把它抹成「刚刚」,导致过期登录态永远被判为可用。
        if normalized == (raw or "").strip():
            return
        path.write_text(normalized, encoding="ascii")
    except Exception as e:  # noqa: BLE001 - 归一化失败不应影响执行
        logger.warning(f"[UIExec] 登录态缓存编码归一化失败(忽略): {e}")


def restore_state_file_backup(state_path: Optional[Path]) -> None:
    """主文件为空(0 cookies 且 0 origins)但 `.json.bak` 备份非空时,恢复登录态。

    与 origin 无关的通用版本:登录配置的登录态文件同样需要这层自愈。
    """
    if not state_path:
        return
    cache = Path(state_path)
    backup = cache.with_suffix(".json.bak")
    try:
        if not backup.exists():
            return
        if cache.exists():
            current = json.loads(cache.read_text(encoding="utf-8") or "{}")
            if current.get("cookies") or current.get("origins"):
                return
        backup_state = json.loads(backup.read_text(encoding="utf-8") or "{}")
        if not (backup_state.get("cookies") or backup_state.get("origins")):
            return
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backup, cache)
        logger.info(f"[UIExec] 登录态缓存为空,已从备份恢复:{cache.name}")
    except Exception as e:
        logger.warning(f"[UIExec] 恢复登录态缓存失败(忽略): {e}")


def _restore_storage_state_if_empty(origin: str) -> None:
    """按 origin 恢复登录态备份(抽取/执行前的自愈入口)。"""
    if not origin:
        return
    restore_state_file_backup(_cache_path(origin))


def _normalize_origin_state(origin: str) -> None:
    """把某 origin 的登录态缓存归一化为 ASCII(browser-use 用 gbk 读文件)。

    执行前后各调一次:执行前保证既有登录态能被加载;执行后抹平 browser-use 以
    UTF-8 写回的中文值,否则下次执行 gbk 解码失败 → 登录态丢失 → 重复登录。
    """
    if origin:
        _normalize_state_file_encoding(_cache_path(origin))


def _prepare_state_file(state_file: Path) -> None:
    """登录态文件落盘前的准备:建父目录 → 备份自愈 → 编码归一化。

    browser-use 写回 storage_state 时不建父目录(目录缺失会静默写盘失败);
    备份自愈避免一次失败执行把已有登录态清空。
    """
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001 - 建目录失败不阻断,写盘时会再报
        logger.warning(f"[UIAuto] 创建登录态目录失败(忽略): {e}")
    restore_state_file_backup(state_file)
    _normalize_state_file_encoding(state_file)


def resolve_login_context(
    db: Session,
    param_map: dict,
    target_url: str,
    overrides: Optional[dict] = None,
    session_path: Optional[str] = None,
) -> tuple[LoginContext, dict]:
    """从参数表取登录入口/方式/凭证 + 检查该 origin 是否已有登录态缓存。

    支持的全局参数键:`test_login_url`(登录入口,SSO 或登录页直链)、
    `test_login_method`(登录方式名称)、`test_username` / `test_password`(凭证)、
    `test_company`(登录后企业/身份选择弹窗中要选择的企业)。

    overrides 为本次执行的显式登录配置(执行弹窗直接填写),非空字段优先于
    全局参数;字段名与 LoginContext 对齐:
    login_url / login_method / username / password / company。
    """
    ov = overrides or {}
    # 容忍参数表 key 的大小写/首尾空白(实测存在 "test_password " 这类手误导致凭证读不到)
    normalized = {
        str(k).strip().lower(): v for k, v in (param_map or {}).items()
    }

    def pick(field: str, key: str) -> str:
        value = ov.get(field)
        if value:
            return str(value)
        return str(normalized.get(key.strip().lower(), "") or "")

    ctx = LoginContext(
        username=pick("username", "test_username"),
        password=pick("password", "test_password"),
        login_url=pick("login_url", "test_login_url"),
        login_method=pick("login_method", "test_login_method"),
        company=pick("company", "test_company"),
    )
    parsed = urlparse(target_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else target_url
    # 登录态来源:显式 path(登录配置)优先,否则该 origin 的通用缓存
    ctx.logged_in_cache = has_valid_state_file(
        Path(session_path) if session_path else _cache_path(origin)
    )
    return ctx, {"origin": origin}


# ---- prompt 文案片段(逐字与 spec §7.2 模板对齐,改动需同步测试断言) ----
_STEP_HEADER = "依次完成以下测试步骤。这是软件测试执行,严格按步骤顺序操作:"

_OPERATION_CONVENTIONS = (
    "\n操作约定(遇到以下情况按此处理,勿跳过核心步骤):"
    "\n- 点击按钮/链接后可能跳转到新页面、打开新标签页或弹出弹窗/抽屉;"
    "若出现新标签页/新窗口,先切换到新标签页,等待目标区域加载完成后再继续。"
    "\n- 若点击后页面看似无变化,先等待页面加载,并确认是否打开了新标签页/弹窗,"
    "再决定下一步;不要在同一步骤上反复重试。"
    "\n- 优先完成上述测试步骤,减少与用例无关的探索性操作,把步骤预算用在核心操作上。"
    "\n- 开始任何步骤前,先确认浏览器当前位于上面的目标页面地址;若中途被重定向到"
    "登录页、工作台或首页,处理完登录后必须重新打开目标页面地址,再继续执行步骤。"
)

_CONDITION_HEADER = "\n前置条件提示(请据此判断当前环境是否满足,不满足时在判定说明中指出):"
_EXPECTED_HEADER = "\n全部步骤完成后,对照以下预期结果逐条核对:"
_DONE_INSTRUCTION = (
    "\n最后调用 done:若所有步骤成功且预期全部满足,success=true,否则 false,"
    "text 中逐条说明每条预期的实际表现。"
)


def _numbered(items: List[str]) -> List[str]:
    """条目渲染为 `1. xxx`。"""
    return [f"{i}. {item}" for i, item in enumerate(items, 1)]


def _bulleted(items: List[str]) -> List[str]:
    """条目渲染为 `- xxx`。"""
    return [f"- {item}" for item in items]


def build_ui_prompt(
    target_url: str,
    actions: List[str],
    expected: List[str],
    preset_conditions: List[str],
    login_ctx: LoginContext,
) -> str:
    """组装 Agent 任务 prompt(spec §7.2 模板)。

    段落顺序即语义顺序:目标地址 → 步骤 → 操作约定 → 前置条件 → 登录提示 →
    预期结果 → 收尾判定。目标地址必须独立成行:与后续文字连读会导致路由被截断。
    """
    lines = [
        f"目标页面地址(完整 URL,仅用于导航,不要与后续文字连读):{target_url}",
        "",
        _STEP_HEADER,
        *_numbered(actions),
        _OPERATION_CONVENTIONS,
    ]
    if preset_conditions:
        lines.append(_CONDITION_HEADER)
        lines.extend(_bulleted(preset_conditions))

    login_hint = login_ctx.hint()
    if login_hint:
        lines.append(f"\n{login_hint}")
        post_hint = login_ctx.post_login_hint(target_url)
        if post_hint:
            lines.append(f"\n{post_hint}")

    lines.append(_EXPECTED_HEADER)
    lines.extend(_numbered(expected))
    lines.append(_DONE_INSTRUCTION)
    return "\n".join(lines)


class _JsonObjectClientProxy:
    """包装 AsyncOpenAI 客户端,在结构化调用上补 `response_format={"type":"json_object"}`。

    DeepSeek 网关不支持 `json_schema`(实测 400 "This response_format type is
    unavailable now"),但支持 `json_object`。browser-use 在
    `dont_force_structured_output=True` 时不设置任何 response_format,模型可能输出
    非 JSON 文本,导致本地 `model_validate_json` 反复失败(表现为「无法以正确格式
    输出操作指令」)。此处仅在 system prompt 携带 `<json_schema>` 的结构化调用上
    注入 json_object,强制返回合法 JSON;普通调用不受影响。
    """

    def __init__(self, client):
        self._client = client

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        if "response_format" not in kwargs:
            messages_blob = str(kwargs.get("messages", ""))
            if "<json_schema>" in messages_blob:
                kwargs["response_format"] = {"type": "json_object"}
        return await self._client.chat.completions.create(**kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


def _build_llm():
    """复用全局 LLM 配置构建 browser-use 原生 LLM(延迟导入,避免测试环境强依赖)。

    browser-use 0.13.x 起 Agent 只接受 browser_use.llm.BaseChatModel(带 `provider`
    属性的自研封装),langchain 的 ChatOpenAI/ChatOllama 已不再兼容,否则在
    `Agent.__init__` 里访问 `llm.provider` 直接 AttributeError。
    Ollama 走其 OpenAI 兼容端点(/v1),同样用 browser-use 的 ChatOpenAI。

    DeepSeek 等网关不支持 `response_format=json_schema`,故关掉强制结构化输出、改为
    把 schema 注入 system prompt,并由 `_JsonObjectClientProxy` 补 `json_object`
    强制模型返回合法 JSON,再由 browser-use 本地解析。
    """
    cfg = config_manager
    from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI

    class _JsonObjectChatOpenAI(BrowserUseChatOpenAI):
        def get_client(self):
            return _JsonObjectClientProxy(super().get_client())

    structured_output_compat = dict(
        max_completion_tokens=16384,
        add_schema_to_system_prompt=True,
        dont_force_structured_output=True,
    )
    if cfg.get_model_type() == "ollama":
        base_url = (cfg.get_ollama_url() or "http://localhost:11434").rstrip("/")
        return _JsonObjectChatOpenAI(
            model=cfg.get_ollama_model(),
            base_url=f"{base_url}/v1",
            api_key="ollama",
            **structured_output_compat,
        )
    return _JsonObjectChatOpenAI(
        model=cfg.config.get("api_model") or "deepseek-v4-flash",
        api_key=cfg.get_api_key(),
        base_url=cfg.config.get("api_base_url") or "https://api.deepseek.com",
        **structured_output_compat,
    )


def _default_agent_factory(llm, prompt: str, storage_state: Optional[Any] = None):
    """真实现:延迟导入 browser-use,避免测试环境强依赖。

    browser-use 0.13.x: Agent(task=..., llm=..., browser_session=...)。登录态传缓存
    文件**路径**(而非 dict),交给 browser-use 的 StorageStateWatchdog 托管:
    浏览器连接时自动加载,关闭(kill)时在 CDP 仍连接的情况下自动写回文件。
    """
    from browser_use import Agent, BrowserSession
    if storage_state is not None:
        browser_session = BrowserSession(storage_state=storage_state)
        return Agent(
            task=prompt,
            llm=llm,
            browser_session=browser_session,
            max_actions_per_step=MAX_ACTIONS_PER_STEP,
            include_tool_call_examples=True,
        )
    return Agent(
        task=prompt,
        llm=llm,
        max_actions_per_step=MAX_ACTIONS_PER_STEP,
        include_tool_call_examples=True,
    )


def _login_only_prompt(login_ctx: LoginContext, target_url: str) -> str:
    """仅登录任务的 prompt:完成一次登录即可,不校验目标页、不重复登录。

    实战教训:该站点裸根地址恒重定向到 /sso/;若要求登录后「重新访问目标地址并确认
    未被重定向」,Agent 会反复登录(4 次)并被判失败。登录任务只负责建立登录态,
    目标页是否可达由后续抽取/验证/执行用同一登录态访问时判断。
    """
    entry = login_ctx.login_url or target_url
    lines = [
        "这是一次「仅登录」任务:只完成一次登录即可,不执行任何业务操作或用例步骤。",
        f"1. 打开登录入口:{entry}",
    ]
    if login_ctx.login_method:
        lines.append(f"2. 若页面默认不是「{login_ctx.login_method}」,先切换到该登录方式。")
    if login_ctx.has_credentials:
        lines.append(f"3. 使用账号 {login_ctx.username}、密码 {login_ctx.password} 提交登录。")
    else:
        lines.append("3. 未配置登录凭证,不要猜测或尝试任何账号密码。")
    lines.append("- 登录凭证以以上配置为准,不要猜测或尝试其他账号密码。")
    if login_ctx.company:
        lines.append(
            f"- 若弹出企业/身份选择窗口,选择企业「{login_ctx.company}」及对应身份,点击确定/进入。"
        )
    lines.append(
        "\n严格约束(必须遵守,否则视为任务失败):"
        "\n- 只提交一次登录表单:提交后等待页面跳转(可等待数秒),不要重复点击登录或重复提交。"
        "\n- 不要访问目标页面地址做二次校验,也不要因为目标地址重定向回登录页而重新登录。"
        "\n- 登录成功后即结束,不要再打开登录页、不要重复登录。"
    )
    lines.append(
        "\n判定:出现工作台/首页等登录后页面(或登录页已跳转离开)即视为登录成功,"
        "调用 done 并设 success=true;始终无法登录时才设 success=false 并说明原因。"
    )
    return "\n".join(lines)


def has_valid_session(origin: str) -> bool:
    """该 origin 的登录态缓存是否含真实凭据(空壳不算,见 state_meaningful)。"""
    if not origin:
        return False
    cache = _cache_path(origin)
    if not cache.exists():
        return False
    try:
        state = json.loads(cache.read_text(encoding="utf-8") or "{}")
    except Exception:
        return False
    return state_meaningful(state)


# 本进程内已建立过登录态的登录态文件(按文件路径),避免缓存文件被并发写空时重复登录
_established_sessions: set = set()


def mark_state_established(state_path: Optional[Any]) -> None:
    """标记某登录态文件已在本进程内建立(登录配置专属文件亦适用)。"""
    if state_path:
        _established_sessions.add(str(Path(state_path)))


def is_state_established(state_path: Optional[Any]) -> bool:
    return bool(state_path) and str(Path(state_path)) in _established_sessions


def mark_session_established(origin: str) -> None:
    if origin:
        mark_state_established(_cache_path(origin))


def is_session_established(origin: str) -> bool:
    return bool(origin) and is_state_established(_cache_path(origin))


def _login_result(
    ok: bool,
    origin: str,
    state_file: Optional[Path],
    session_path: Optional[str],
    reason: str = "",
) -> Dict[str, Any]:
    """统一返回结构;cache_path 为实际落点文件(字段名沿用旧链路契约)。"""
    return {
        "ok": ok,
        "origin": origin,
        "cache_path": str(state_file) if state_file is not None else "",
        "session_path": session_path or "",
        "reason": reason,
    }


def _resolve_state_file(session_path: Optional[str], origin: str) -> Optional[Path]:
    """登录态落点:登录配置专属文件优先,否则该 origin 的通用缓存。"""
    if session_path:
        return Path(session_path)
    if origin:
        return _cache_path(origin)
    return None


async def _run_login_task(
    prompt: str,
    storage_state: Optional[str],
    agent_factory: Optional[Callable],
    llm: Optional[Any],
) -> Any:
    """跑一次「仅登录」任务:构造 Agent → 执行 → 关闭浏览器。

    浏览器生命周期必须与调用方处于同一事件循环(Playwright 会话不可跨 loop)。
    """
    async def _browser_phase():
        # 测试注入 agent_factory 时不构建真实 LLM,避免拉起模型依赖
        active_llm = llm if llm is not None else (None if agent_factory is not None else _build_llm())
        agent = (agent_factory or _default_agent_factory)(active_llm, prompt, storage_state)
        try:
            return await _invoke_agent(agent, LOGIN_MAX_STEPS)
        finally:
            await close_agent_safe(agent)

    return await _run_browser_phase(_browser_phase())


async def _wait_for_state_persist(state_file: Path) -> None:
    """轮询等待 browser-use 的 StorageStateWatchdog 写回登录态。

    关闭浏览器时的写回可能略晚于 run() 返回;不等待会把「刚登录成功但文件还没写完」
    误判为失败,进而触发重复登录。
    """
    if has_valid_state_file(state_file):
        return
    for _ in range(10):
        await asyncio.sleep(0.3)
        _normalize_state_file_encoding(state_file)
        if has_valid_state_file(state_file):
            return


def _mark_and_propagate_state(state_file: Path, origin: str) -> None:
    """标记登录态已在本进程建立,并同步一份回 origin 通用缓存。"""
    mark_state_established(state_file)
    if not origin:
        return
    mark_session_established(origin)
    origin_file = _cache_path(origin)
    if str(origin_file) != str(state_file):
        # 未关联登录配置的抽取/执行也能复用同一登录态
        sync_state_file(state_file, origin_file)


async def establish_login_session(
    db: Session,
    target_url: str,
    login_overrides: Optional[dict] = None,
    *,
    agent_factory: Optional[Callable] = None,
    task_id: Optional[str] = None,
    llm: Optional[Any] = None,
    session_path: Optional[str] = None,
) -> Dict[str, Any]:
    """执行一次「仅登录」browser-use 任务,把登录态写入登录态文件。

    - session_path 为空:写该 origin 的通用缓存(原行为,兼容旧链路)。
    - session_path 非空:写登录配置专属文件(按配置隔离多账号),成功后顺带同步
      一份到 origin 通用缓存,让未关联登录配置的旧链路也能直接复用。

    返回 {"ok": bool, "origin": str, "cache_path": str, "session_path": str, "reason": str}。
    未配置登录信息时直接返回 ok=False(调用方决定是否放行匿名访问)。
    """
    from app.routes.proxy import build_param_map

    param_map = build_param_map(db, None, [])
    login_ctx, meta = resolve_login_context(
        db, param_map, target_url, login_overrides, session_path=session_path
    )
    origin = meta.get("origin", "")
    if not login_ctx.configured:
        return _login_result(False, origin, None, session_path, "未配置登录信息")

    state_file = _resolve_state_file(session_path, origin)
    if state_file is not None:
        _prepare_state_file(state_file)
    storage_state = str(state_file) if state_file is not None else None

    try:
        result = await asyncio.wait_for(
            _run_login_task(
                _login_only_prompt(login_ctx, target_url), storage_state, agent_factory, llm
            ),
            timeout=LOGIN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _login_result(False, origin, state_file, session_path, "登录超时")
    finally:
        if task_id:
            _update_task(task_id, message="登录完成,正在确认登录态...")

    if state_file is not None:
        _normalize_state_file_encoding(state_file)
        await _wait_for_state_persist(state_file)
    # 以「是否真的产生了登录态」为准,而不是 Agent 的 success 判定:
    # Agent 常因页面加载慢误判失败,据此重试会导致反复登录。
    valid = (
        has_valid_state_file(state_file)
        if state_file is not None
        else bool(getattr(result, "success", False))
    )
    if valid and state_file is not None:
        _mark_and_propagate_state(state_file, origin)
    return _login_result(
        valid, origin, state_file, session_path, "" if valid else "登录未成功或未产生有效登录态"
    )


async def close_agent_safe(agent: Any) -> None:
    """兜底关闭 browser-use 浏览器资源;stub agent 无 close() 时静默跳过。

    Agent.run() 正常/异常收尾时已 close 并断开 CDP,此时跳过二次 kill。
    """
    if agent is None:
        return
    browser_session = getattr(agent, "browser_session", None)
    if browser_session is not None and getattr(browser_session, "is_cdp_connected", True) is False:
        return
    close = getattr(agent, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await asyncio.wait_for(result, timeout=10)
    except Exception as e:  # noqa: BLE001 - 关闭失败非致命
        logger.warning(f"[UIExec] 关闭浏览器失败(非致命): {e}")


@dataclass
class _ExecutionInputs:
    """单次执行的 Agent 输入;由浏览器阶段一次性消费。"""

    llm: Any
    prompt: str
    storage_state: Optional[str]


def _step_recorder(record: UIExecutionRecord, task_id: Optional[str]) -> Callable[[Any], None]:
    """生成 browser-use 每步回调:追加步骤轨迹 + 刷新进度文案。

    step_no 与 message 里的步号都取追加后的长度,保证 1-based 且与前端展示一致。
    """
    def on_history_step(history_item: Any) -> None:
        text = getattr(history_item, "action", "") or str(history_item)
        trace = list(record.steps_trace or [])
        trace.append({
            "step_no": len(trace) + 1,
            "action": str(text)[:500],
            "result": str(getattr(history_item, "result", "") or "")[:500],
        })
        record.steps_trace = trace
        _update_task(task_id, message=f"执行步骤 {len(trace)}: {str(text)[:80]}")

    return on_history_step


class UIExecutionRunner:
    """单条 UI 执行记录的完整生命周期编排。"""

    def __init__(
        self,
        db: Session,
        agent_factory: Optional[Callable] = None,
        max_steps: int = MAX_STEPS,
        timeout_seconds: int = EXECUTION_TIMEOUT_SECONDS,
        login_overrides: Optional[dict] = None,
    ):
        # 构造期传入的 session:存活则复用(测试),否则 run() 内自建独立 session
        self._request_db = db
        self.agent_factory = agent_factory or _default_agent_factory
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds
        # 本次执行的显式登录配置,不落库、不进任务状态(避免凭据经 /tasks 泄露)
        self.login_overrides = login_overrides or {}

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

    def mark_stopped(self, record_id: int, task_id: Optional[str]) -> None:
        """把仍在 RUNNING 的记录落为 STOPPED(供取消兜底;幂等)。"""
        db = None
        try:
            db = self._acquire_session()
            record = db.get(UIExecutionRecord, record_id)
            if record is None:
                return
            _mark_stopped(db, record, task_id)
        except Exception as e:  # noqa: BLE001 - 兜底路径不应再抛错
            logger.warning(f"[UIExec] 落库停止状态失败(非致命): {e}")
        finally:
            self._release_session(db)

    async def _run(self, db: Session, record: UIExecutionRecord, task_id: Optional[str]) -> None:
        """状态机主体:准备 → 浏览器阶段(执行+判定) → 落库。

        origin 提升到 try 之外,供 finally 统一做登录态编码归一化(即使中途异常)。
        """
        origin = ""
        try:
            testcase = self._load_testcase(db, record)

            _update_task(task_id, phase="launching_browser", progress=10, message="正在启动浏览器...")
            login_ctx, origin = self._resolve_login(db, record)
            inputs = _ExecutionInputs(
                llm=_build_llm(),
                storage_state=self._prepare_origin_state(origin),
                prompt=_agent_prompt_for(db, record, testcase, login_ctx),
            )

            try:
                result = await asyncio.wait_for(
                    _run_browser_phase(
                        self._browser_phase(inputs, task_id, _step_recorder(record, task_id))
                    ),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError:
                raise TimeoutError(f"执行超时(>{self.timeout_seconds}s)")

            # 判定映射(spec §8): success → 记录+用例 PASSED;失败 → FAILED
            _persist(
                db,
                record,
                testcase,
                task_id,
                status=STATUS_PASSED if result.success else STATUS_FAILED,
                summary=str(result.final_result() or ""),
            )
        except asyncio.CancelledError:
            # 用户「停止执行」:落 STOPPED,不回写用例状态,再向上传播让 asyncio 收尾
            logger.info(f"[UIExec] record {record.id} 被用户停止")
            _mark_stopped(db, record, task_id)
            raise
        except Exception as e:
            logger.exception(f"[UIExec] record {record.id} 执行异常")
            _persist_error(db, record, task_id, str(e))
        finally:
            # browser-use 执行结束时会以 UTF-8 写回含中文的登录态;重新归一化为
            # ASCII,否则下次执行会因 gbk 解码失败而丢失登录态、重复登录。
            _normalize_origin_state(origin)
            if task_id:
                _update_task(task_id, status="done", progress=100, message="执行结束")

    # ---- 真实现/Stub 差异收敛点 ----

    def _restore_storage_state_if_empty(self, origin: str) -> None:
        """实例方法委托到模块级实现(供 UI 执行与 UI 自动化共用)。"""
        _restore_storage_state_if_empty(origin)

    def _storage_state_path(self, origin: str) -> Optional[str]:
        """该 origin 的登录态缓存文件路径(browser-use 负责加载与写回)。

        spec §6:同一 origin 的后续执行复用缓存登录态。传**路径**而非 dict,由
        browser-use 的 StorageStateWatchdog 在浏览器连接时加载、在 kill 时(CDP 仍
        连接)写回文件。文件不存在时加载为空操作,首次执行结束即自动创建缓存。
        """
        if not origin:
            return None
        return str(_cache_path(origin))

    async def _close_agent(self, agent) -> None:
        """兜底关闭浏览器资源(委托模块级实现,供仅登录流程共用)。"""
        await close_agent_safe(agent)

    # ---- 执行阶段拆分(供 _run 组合) ----

    def _load_testcase(self, db: Session, record: UIExecutionRecord) -> TestCase:
        """取执行记录关联的用例;缺失视为不可恢复错误(落 ERROR)。"""
        testcase = db.get(TestCase, record.testcase_id)
        if testcase is None:
            raise RuntimeError(f"用例 {record.testcase_id} 不存在")
        return testcase

    def _resolve_login(self, db: Session, record: UIExecutionRecord) -> tuple:
        """解析登录上下文 + 目标地址 origin(供登录态缓存定位)。"""
        from app.routes.proxy import build_param_map

        param_map = build_param_map(db, None, [])
        login_ctx, meta = resolve_login_context(
            db, param_map, record.target_url, self.login_overrides
        )
        return login_ctx, meta.get("origin", "")

    def _prepare_origin_state(self, origin: str) -> Optional[str]:
        """执行前准备登录态:备份自愈 + 编码归一化,返回给 browser-use 的路径。

        传**路径**而非 dict,由 browser-use 的 StorageStateWatchdog 在浏览器连接时
        加载、在 kill 时(CDP 仍连接)写回;文件不存在时加载为空操作,首次执行结束
        即自动创建缓存(spec §6)。
        """
        self._restore_storage_state_if_empty(origin)
        # browser-use 用 gbk 读 storage_state,先归一化为 ASCII 保证登录态可加载
        _normalize_origin_state(origin)
        return self._storage_state_path(origin)

    async def _browser_phase(
        self,
        inputs: _ExecutionInputs,
        task_id: Optional[str],
        on_step: Callable[[Any], None],
    ) -> Any:
        """构造 Agent → 执行 → 关闭浏览器。

        必须与 browser-use 的浏览器生命周期在同一事件循环内完成(Playwright 会话与
        CDP 连接不可跨 loop)。登录态的加载与写回由 browser-use 的
        StorageStateWatchdog 依 storage_state 路径自动完成,runner 不再插手。
        """
        agent = self.agent_factory(inputs.llm, inputs.prompt, inputs.storage_state)
        _attach_history_callback(agent, on_step)
        _update_task(task_id, phase="executing", progress=30, message="Agent 正在执行步骤...")
        try:
            _update_task(task_id, phase="judging", progress=80, message="Agent 正在核对预期结果...")
            return await _invoke_agent(agent, self.max_steps)
        finally:
            # 超时/异常时浏览器可能未关闭,兜底关闭避免残留 Chromium 进程
            await self._close_agent(agent)


def _persist(
    db: Session,
    record: UIExecutionRecord,
    testcase: TestCase,
    task_id: Optional[str],
    status: str,
    summary: str,
) -> None:
    """判定成功/失败:记录置终态 + 回写用例状态(PASSED/FAILED 均回写;ERROR 不走此处)。"""
    record.status = status
    record.agent_summary = summary
    record.finished_at = datetime.now(tz=cn_tz)
    testcase.status = status
    db.add(record)
    db.add(testcase)
    db.commit()
    _update_task(task_id, phase="done", progress=100, result_status=status)


def _persist_error(
    db: Session, record: UIExecutionRecord, task_id: Optional[str], error_msg: str
) -> None:
    """异常落库:记录置 ERROR,**不回写用例状态**(执行未得出结论,不得污染用例)。"""
    record.status = STATUS_ERROR
    record.error = error_msg[:2000]
    record.finished_at = datetime.now(tz=cn_tz)
    db.add(record)
    db.commit()
    _update_task(task_id, status="done", error=error_msg[:200], result_status=STATUS_ERROR)


def _mark_stopped(
    db: Session,
    record: UIExecutionRecord,
    task_id: Optional[str],
    reason: str = "用户已停止执行",
) -> None:
    """把执行记录落为 STOPPED(仅当仍 RUNNING),不回写用例状态。

    幂等:若记录已是终态(PASSED/FAILED/ERROR/STOPPED)则只同步任务终态,不覆盖记录。
    """
    if record.status != STATUS_RUNNING:
        _update_task(task_id, status="done", progress=100, result_status=record.status)
        return
    record.status = STATUS_STOPPED
    record.agent_summary = record.agent_summary or reason
    record.finished_at = datetime.now(tz=cn_tz)
    db.add(record)
    db.commit()
    _update_task(task_id, status="done", progress=100, result_status=STATUS_STOPPED, message=reason)


def cancel_ui_execution(task_id: str) -> bool:
    """请求停止任务:标记 stop_requested 并取消后台 asyncio.Task。

    返回 False 表示任务不存在或已处于终态(无可停止)。
    """
    state = _ui_tasks.get(task_id)
    if state is None:
        return False
    if state.get("status") == "done":
        return False
    state["stop_requested"] = True
    state["message"] = "正在停止执行..."
    handle = _ui_task_handles.get(task_id)
    if handle is not None and not handle.done():
        handle.cancel()
    return True


def _agent_prompt_for(
    db: Session, record: UIExecutionRecord, testcase: TestCase, login_ctx: LoginContext
) -> str:
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


def _render_agent_output_action(output: Any) -> str:
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


def _wrap_new_step_callback(
    callback: Callable[[str], None]
) -> Callable[[Any, Any, int], None]:
    """把 browser-use 0.13 的 (BrowserStateSummary, AgentOutput, int) 回调转成字符串回调。"""
    def wrapper(summary, output, step_num):
        callback(_render_agent_output_action(output))
    return wrapper


def _attach_history_callback(agent: Any, callback: Callable[[str], None]) -> None:
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


async def _invoke_agent(agent: Any, max_steps: int) -> Any:
    """统一调用 agent 并归一化结果,使判定逻辑只依赖 success/final_result。

    - FakeAgent: SimpleNamespace(success=..., final_result=...) 直接透传
    - browser-use 0.13.x: Agent.run() → AgentHistoryList,只有 is_successful()/final_result(),
      无 .success 属性 → 在此适配
    """
    result = await agent.run(max_steps=max_steps)
    return _normalize_agent_result(result)


def _can_spawn_subprocess() -> bool:
    """当前事件循环是否支持 asyncio.create_subprocess_exec。

    Windows 上 uvicorn 以 `--reload`/多 worker 启动时会选用 SelectorEventLoop,
    该 loop 不支持子进程;browser-use 启动 Chromium 会抛 NotImplementedError。
    """
    if os.name != "nt":
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return True
    return isinstance(loop, asyncio.ProactorEventLoop)


async def _run_browser_phase(coro) -> Any:
    """在能创建子进程的事件循环中执行浏览器阶段。

    当前 loop 不支持子进程时,把整个浏览器阶段(建 Agent/run/导出/关闭)搬到独立线程
    的 ProactorEventLoop 执行,使 UI 执行不受 uvicorn 启动参数影响。
    """
    if _can_spawn_subprocess():
        return await coro
    return await _run_in_proactor_thread(coro)


async def _run_in_proactor_thread(coro) -> Any:
    """在独立线程的 ProactorEventLoop 中运行协程,并把结果/异常回传主线程。"""
    main_loop = asyncio.get_running_loop()
    done = main_loop.create_future()
    box: Dict[str, Any] = {}

    def _settle(_box=box):
        if done.cancelled():
            return
        if isinstance(_box.get("error"), BaseException):
            done.set_exception(_box["error"])
        else:
            done.set_result(_box.get("value"))

    def _worker():
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            box["value"] = loop.run_until_complete(coro)
        except BaseException as e:  # noqa: BLE001 - 原样回传主线程处理
            box["error"] = e
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
                asyncio.set_event_loop(None)
        main_loop.call_soon_threadsafe(_settle)

    threading.Thread(target=_worker, name="ui-exec-proactor", daemon=True).start()
    return await done


def _normalize_agent_result(result: Any) -> Any:
    """把各版本 Agent 结果归一化为最小契约:`success` + 无参 `final_result()`。"""
    success = getattr(result, "success", None)
    if success is None:
        is_ok = getattr(result, "is_successful", None)
        success = is_ok() if callable(is_ok) else False
    fr = getattr(result, "final_result", None)
    final_text = fr() if callable(fr) else (fr or "")
    return SimpleNamespace(success=bool(success), final_result=lambda: str(final_text or ""))


def launch_ui_execution(
    db: Session,
    record_id: int,
    agent_factory: Optional[Callable] = None,
    login_overrides: Optional[dict] = None,
    max_steps: int = MAX_STEPS,
) -> str:
    """创建内存任务并后台启动 runner,返回 task_id。

    login_overrides 仅传给 runner 实例(不写入 _ui_tasks,避免 GET /tasks 泄露凭据)。
    max_steps 透传步数预算;<=0 时回退默认值。
    """
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
    runner = UIExecutionRunner(
        None,
        agent_factory=agent_factory,
        login_overrides=login_overrides,
        max_steps=max_steps if max_steps and max_steps > 0 else MAX_STEPS,
    )

    async def _guarded():
        try:
            async with _EXECUTION_LOCK:
                await runner.run(record_id)
        except asyncio.CancelledError:
            # 取消可能发生在排队等锁阶段(runner.run 尚未执行),此处兜底落 STOPPED
            runner.mark_stopped(record_id, task_id)
            raise

    handle = asyncio.create_task(_guarded())
    _ui_task_handles[task_id] = handle
    handle.add_done_callback(lambda _t: _ui_task_handles.pop(task_id, None))
    return task_id
