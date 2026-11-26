"""AI 用例对话页面洞察工具

让 AI 在对话中用真实浏览器访问目标页面、提取实际页面元素，按页面实况优化用例：
- browse_page              访问页面并返回结构化元素摘要（登录态缓存复用）
- submit_login_credentials 用用户在对话中提供的凭证建立登录态（browser-use AI 登录）

登录闭环：凭证优先读全局参数（test_username / test_password / test_login_url），
缺失时 browse_page 返回 LOGIN_REQUIRED 标记，模型向用户询问，用户下一条消息给出
凭证后由 submit_login_credentials 落库登录态（per-origin 缓存，之后免重复登录）。
凭证只在本次工具调用的内存里短暂存在，不写入任何持久化存储，日志不打印密码。

权限：两个工具均要求 ui_automation:manage（与 UI 自动化平台一致）。
"""
import logging
from typing import Optional

from pydantic import BaseModel as PydanticBaseModel, Field

logger = logging.getLogger(__name__)

# browse_page 返回的特殊状态前缀：模型据此向用户询问凭证
LOGIN_REQUIRED_MARKER = "LOGIN_REQUIRED"

# 元素摘要的字符上限（防止超长页面撑爆上下文；超限按区域截断）
_SUMMARY_CHAR_LIMIT = 12000

_TYPE_LABELS = {
    "button": "按钮",
    "input": "输入框",
    "link": "链接",
    "select": "下拉框",
    "checkbox": "复选框",
    "radio": "单选框",
    "textarea": "文本域",
    "tab": "选项卡",
    "menuitem": "菜单项",
}


def _has_permission(current_user, permission: str) -> bool:
    if current_user is None:
        return False
    from app.permissions import get_user_permissions

    return permission in get_user_permissions(current_user)


def _deny(permission: str) -> str:
    return f"权限不足：执行该操作需要「{permission}」权限，请联系管理员开通。"


def _origin_of(url: str) -> str:
    """取 URL 的 origin（scheme://host）；解析失败返回空串。"""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        if parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:  # noqa: BLE001
        pass
    return ""



def _looks_like_login_page(elements: list[dict]) -> bool:
    """判断采集到的元素是否是登录页特征（密码输入框存在 + 元素极少）。"""
    if not elements:
        return True
    has_password = any(
        (el.get("inputType") or "") == "password" or "密码" in str(el.get("placeholder") or "")
        for el in elements
    )
    texted = [el for el in elements if any(str(el.get(k) or "").strip() for k in ("text", "label", "placeholder"))]
    # 密码框 + 少量文本元素（登录页通常 <15 个），或只有密码框
    return has_password and len(texted) <= 15
def _format_elements(raw_elements: list[dict], limit: int = _SUMMARY_CHAR_LIMIT) -> str:
    """把 extract_elements 的原始元素列表渲染成模型可读的分组摘要。

    extract_elements 的真实字段：tag/text/label/aria_label/placeholder/name/id/role/
    cssPath/frame（见 build_fingerprint）。这里以「可见文本」为主展示键，
    超出字符上限时按组截断并标注省略。
    """
    groups: dict[str, list[str]] = {}
    seen = set()
    for el in raw_elements:
        tag = str(el.get("tag") or "other").lower()
        # 可见文本优先级：text > label > aria_label > placeholder > name
        text = ""
        for key in ("text", "label", "aria_label", "placeholder", "name"):
            value = str(el.get(key) or "").strip()
            if value:
                text = value
                break
        if not text:
            continue  # 无可见文本的容器节点不进摘要（148 个元素里大量 div 壳）
        role = str(el.get("role") or "").strip()
        # 展示类型：优先语义 role，其次 tag
        display_type = role or tag
        # 判断交互类型分组（按钮/输入/链接/页签）
        if tag == "button" or role == "button":
            label = "按钮"
        elif tag in ("input", "textarea") or role in ("textbox", "searchbox"):
            label = "输入框"
        elif tag == "a" or role == "link":
            label = "链接"
        elif role in ("tab", "tablist") or "tab" in str(el.get("class") or "").lower():
            label = "页签"
        elif tag == "select" or role == "combobox":
            label = "下拉框"
        else:
            label = "其他"
        line = f"- {text[:60]}"
        if el.get("placeholder") and el.get("placeholder") != text:
            line += f"（placeholder: {str(el['placeholder'])[:30]}）"
        key = line
        if key in seen:
            continue  # 表格行内重复按钮去重
        seen.add(key)
        groups.setdefault(label, []).append(line)

    lines = []
    total = 0
    truncated = False
    for label, items in groups.items():
        if total >= limit:
            truncated = True
            lines.append(f"### {label}（{len(items)} 个，因长度限制省略）")
            continue
        lines.append(f"### {label}（{len(items)} 个）")
        for item in items:
            if total + len(item) >= limit:
                truncated = True
                lines.append(f"  ……（{label} 其余项因长度限制省略）")
                break
            lines.append(item)
            total += len(item)
    if truncated:
        lines.append("……（页面元素过多，摘要已截断；可让 AI 按页签/区域进一步聚焦）")
    return "\n".join(lines)

def build_page_insight_tools(current_user, environment_id: Optional[int] = None) -> list:
    """构建页面洞察工具；闭包捕获权限主体与目标 URL 状态。

    _last_target_url 在 browse_page 与 submit_login_credentials 之间传递
    目标页面（同一次对话轮次内），登录成功后模型重新调用 browse_page。
    """
    from langchain_core.tools import StructuredTool

    state = {"last_target_url": None}

    class BrowsePageArgs(PydanticBaseModel):
        url: str = Field(description="要访问的完整页面 URL（含 https://）")
        clicks: Optional[list[str]] = Field(
            default=None,
            description="打开页面后依次点击的元素文案列表（如切换页签 [\"物资部位管理\"]）；每次点击后重新采集。切页签/展开菜单等无路由交互必传。"
        )

    class SubmitLoginArgs(PydanticBaseModel):
        username: str = Field(description="登录账号（用户在对话中提供）")
        password: str = Field(description="登录密码（用户在对话中提供）")

    async def _browse_page(url: str, clicks: Optional[list[str]] = None) -> str:
        if not _has_permission(current_user, "ui_automation:manage"):
            return _deny("ui_automation:manage")
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            return "URL 非法：必须以 http:// 或 https:// 开头。"
        origin = _origin_of(url)
        state["last_target_url"] = url
        try:
            from sqlmodel import Session

            from app.routes.proxy import build_param_map
            from db.db import engine
            from app.services import browser_agent as ba

            with Session(engine) as db:
                param_map = build_param_map(db, environment_id, [])
                login_ctx, meta = ba.resolve_login_context(db, param_map, url)
            origin = meta.get("origin") or origin

            cached = ba.has_valid_state_file(ba._cache_path(origin)) if origin else False
            storage_state = str(ba._cache_path(origin)) if cached and origin else None

            from app.services import browser_agent as ba
            from app.services.page_extractor import extract_elements

            # Windows + uvicorn --reload 下主 loop 是 SelectorEventLoop，无法启动
            # Chromium 子进程；沿用 browser_agent 的 Proactor 线程兜底模式。
            raw = await ba._run_browser_phase(
                extract_elements(
                    url,
                    storage_state=storage_state,
                    timeout_ms=45000,
                    post_open_clicks=[c for c in (clicks or []) if str(c).strip()],
                )
            )
            if not raw:
                return (
                    f"页面可访问但未采集到交互元素：{url}。"
                    "可能是纯展示页或渲染极慢，可让用户确认页面已加载完成。"
                )
            # 登录页检测：没带登录态（或登录态失效）时被重定向到登录页，
            # 采集到的是登录表单——明确告诉模型「被跳转到登录页」，而不是当成页面实况。
            if not cached and _looks_like_login_page(raw):
                return (
                    f"{LOGIN_REQUIRED_MARKER}|{url}\n"
                    f"访问 {url} 被重定向到了登录页（未携带有效登录态）。"
                    "请向用户询问该站点的账号和密码，用户回复后调用 submit_login_credentials 提交，"
                    "登录成功后重新调用 browse_page 访问本页面。"
                    "同时提醒用户：也可以把账号密码配置到「全局参数」（test_username / test_password），避免每次输入。"
                )
            summary = _format_elements(raw)
            head = f"页面访问成功：{url}"
            if cached:
                head += "（使用已有登录态）"
            return head + "\n\n共采集到 " + str(len(raw)) + " 个交互元素（已按可见文本去重）：\n\n" + summary
        except Exception as exc:  # noqa: BLE001
            logger.warning("browse_page 访问失败 url=%s: %s", url, exc, exc_info=True)
            # str(exc) 可能为空（如 NotImplementedError()），补类型名保证诊断信息不为空
            detail = (str(exc) or type(exc).__name__)[:200]
            return (
                f"页面访问失败：{detail}。"
                "可能是网络不可达或页面加载超时；若确认站点需要登录，可先调用 submit_login_credentials 建立登录态后重试。"
            )

    async def _submit_login_credentials(username: str, password: str) -> str:
        if not _has_permission(current_user, "ui_automation:manage"):
            return _deny("ui_automation:manage")
        target = state.get("last_target_url")
        if not target:
            return "请先用 browse_page 访问目标页面，再提交登录凭证。"
        username = str(username or "").strip()
        password = str(password or "")
        if not username or not password:
            return "账号和密码都不能为空。"
        try:
            from sqlmodel import Session

            from db.db import engine
            from app.services import browser_agent as ba

            with Session(engine) as db:
                result = await ba.establish_login_session(
                    db,
                    target,
                    {"username": username, "password": password},
                    environment_id=environment_id,
                )
            ok = bool(result.get("ok"))
            reason = str(result.get("reason") or "")
            if ok:
                # 登录成功后立刻用新登录态重访目标页：既验证登录态真实可复用，
                # 又直接把页面元素交给模型（省一轮模型往返）。失败则如实报告。
                re_browse = await _browse_page(target)
                if re_browse.startswith(LOGIN_REQUIRED_MARKER):
                    return (
                        "登录流程完成，但重新访问目标页仍被重定向到登录页——"
                        "登录态可能存放在本工具无法跨实例复用的位置（如部分 SSO 会话）。"
                        f"重新访问结果：{re_browse[:200]}"
                    )
                return (
                    "登录成功，登录态已保存到本地缓存（同站点后续访问免重复登录）。"
                    "以下是用新登录态重新访问目标页面的实况：\n\n" + re_browse
                )
            return (
                f"登录失败：{reason or '未知原因'}。"
                "请检查账号密码是否正确（或该站点是否有验证码/SSO 等额外步骤），可让用户重新提供凭证后再试。"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("submit_login_credentials 失败: %s", exc)
            return f"登录执行失败：{str(exc)[:200]}。请稍后重试。"

    return [
        StructuredTool(
            name="browse_page",
            description="用真实浏览器访问指定 URL 并返回页面实际交互元素摘要（按钮/输入框/链接等），支持 clicks 参数依次点击页签/按钮后再采集（纯前端页签切换必须用）。用于按页面实际实现核对和优化用例。若页面需要登录且无可用登录态，返回 LOGIN_REQUIRED 标记，此时向用户询问账号密码。",
            coroutine=_browse_page, args_schema=BrowsePageArgs,
        ),
        StructuredTool(
            name="submit_login_credentials",
            description="用用户在对话中提供的账号密码执行浏览器登录，建立并缓存登录态。仅在 browse_page 返回 LOGIN_REQUIRED 且用户已提供凭证后调用。登录成功后需重新 browse_page。",
            coroutine=_submit_login_credentials, args_schema=SubmitLoginArgs,
        ),
    ]


def load_page_insight_tools(mode: str, testcase_session_id: Optional[int], current_user, environment_id: Optional[int] = None) -> list:
    """门控：仅 testcase_optimize 模式且携带会话 ID 时挂载（与用例写工具同门控）。"""
    if mode != "testcase_optimize" or not testcase_session_id:
        return []
    try:
        tools = build_page_insight_tools(current_user, environment_id)
        logger.info("已挂载 %d 个页面洞察工具", len(tools))
        return tools
    except Exception as e:  # noqa: BLE001
        logger.warning("加载页面洞察工具失败: %s", e)
        return []