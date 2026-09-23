"""页面对象登录态管理。

模型:登录态挂在**页面对象**上,不引入独立实体。
- 一个页面对象把 `is_login_page` 打开(通常是登录页),它「执行(自愈)」里配置的
  步骤就承担登录动作;执行产生的登录态保存到该对象的专属文件。
- 其他页面对象通过 `login_page_object_id` 关联它,即可「判断有没有登录态 → 有就用,
  没有就先执行它的步骤完成登录并保持」,无需重复配置账号密码。
- 登录态文件按页面对象 id 隔离(`<sessions>/pages/po{id}.json`),同一站点多个账号
  各建各的登录页对象,互不覆盖;同时会同步一份到该 origin 的通用缓存。

与 browser_agent 的分工:这里只做「文件/状态」层面的判断与记录,真正驱动浏览器
执行的逻辑在 page_object_runner。
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sqlmodel import Session

from app.services import browser_agent as ba
from db.models import PageObject, cn_tz

logger = logging.getLogger(__name__)

STATUS_NONE = "NONE"      # 从未建立
STATUS_READY = "READY"    # 文件有效,可直接复用
STATUS_FAILED = "FAILED"  # 最近一次登录失败


def page_session_path(page_object_id: int) -> Path:
    """页面对象的登录态文件路径(`<sessions>/pages/po{id}.json`)。

    `_sessions_dir` 运行时读取,便于测试 monkeypatch。
    """
    return _sessions_dir() / "pages" / f"po{int(page_object_id)}.json"


def _sessions_dir() -> Path:
    return ba._sessions_dir


def ensure_session_path(page_object: PageObject) -> Path:
    """返回(并在需要时补写)该页面对象的登录态文件路径。"""
    path = (
        Path(page_object.session_path)
        if page_object.session_path
        else page_session_path(page_object.id or 0)
    )
    if not page_object.session_path:
        page_object.session_path = str(path)
    return path


def session_view(page_object: PageObject) -> Dict[str, Any]:
    """登录态视图(前端展示用;不含任何 cookie/token 明文)。"""
    path = Path(page_object.session_path) if page_object.session_path else None
    stats = ba.state_file_stats(path)
    ready = bool(stats["cookies"] or stats["origins"])
    return {
        "page_object_id": page_object.id,
        "is_login_page": bool(page_object.is_login_page),
        "login_page_object_id": page_object.login_page_object_id,
        "session_path": page_object.session_path or "",
        "session_status": page_object.session_status or STATUS_NONE,
        "session_ready": ready,
        "session_cookie_count": stats["cookies"],
        "session_origin_count": stats["origins"],
        "session_updated_at": stats.get("updated_at")
        or (page_object.session_updated_at.isoformat() if page_object.session_updated_at else None),
        "last_login_at": page_object.last_login_at.isoformat() if page_object.last_login_at else None,
        "last_login_error": page_object.last_login_error,
    }


def session_usable(page_object: PageObject) -> bool:
    """该页面对象当前是否存在可直接复用的登录态。

    本进程内刚建立过的登录态即便文件暂时被写空也视为可用(避免重复登录)。
    """
    path = ensure_session_path(page_object)
    if ba.is_state_established(path):
        return True
    return ba.has_valid_state_file(path)


def refresh_status(
    db: Session,
    page_object: PageObject,
    *,
    logged_in: bool = False,
    error: str = "",
    capture_from_origin: bool = False,
    origin: str = "",
) -> Dict[str, Any]:
    """把登录态文件实况写回页面对象,返回最新视图。

    - logged_in:明确知道本次产生了有效登录态(执行登录步骤成功)。
    - capture_from_origin:从该站点通用缓存反向同步进本对象(执行链路只写了通用缓存时用)。
    """
    path = ensure_session_path(page_object)
    if capture_from_origin and origin:
        origin_file = ba._cache_path(origin)
        if str(origin_file) != str(path):
            ba.sync_state_file(origin_file, path)

    stats = ba.state_file_stats(path)
    has_state = bool(stats["cookies"] or stats["origins"])
    now = datetime.now(tz=cn_tz)

    if has_state:
        page_object.session_cookie_count = stats["cookies"]
        page_object.session_origin_count = stats["origins"]
        page_object.session_status = STATUS_READY
        if stats.get("updated_at"):
            try:
                page_object.session_updated_at = datetime.fromisoformat(stats["updated_at"])
            except Exception:  # noqa: BLE001
                page_object.session_updated_at = now
        else:
            page_object.session_updated_at = now
        if logged_in:
            page_object.last_login_at = now
            page_object.last_login_error = None
    elif logged_in:
        # 执行报成功但登录态没落盘:如实标记失败,不让界面显示「已登录」
        page_object.session_status = STATUS_FAILED
        page_object.last_login_error = error or "执行后未产生有效登录态"
    elif error:
        page_object.session_status = STATUS_FAILED
        page_object.last_login_error = error
    else:
        # 只是收尾同步、没有新的判定信息:不把已记录的失败抹成「未建立」,
        # 否则任务结束时的无参刷新会覆盖掉真正的原因。
        page_object.session_status = (
            STATUS_FAILED if page_object.last_login_error else STATUS_NONE
        )
        page_object.session_cookie_count = 0
        page_object.session_origin_count = 0

    db.add(page_object)
    db.commit()
    db.refresh(page_object)
    return session_view(page_object)


def clear_session(db: Session, page_object: PageObject) -> Dict[str, Any]:
    """清除该页面对象的登录态(文件 + 备份 + 内存标记),下次使用会重新登录。"""
    path = ensure_session_path(page_object)
    for target in (path, path.with_suffix(".json.bak")):
        try:
            if target.exists():
                target.unlink()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[PageLogin] 删除登录态文件失败({target}): {e}")
    ba._established_sessions.discard(str(path))
    page_object.session_status = STATUS_NONE
    page_object.session_updated_at = None
    page_object.session_cookie_count = 0
    page_object.session_origin_count = 0
    db.add(page_object)
    db.commit()
    db.refresh(page_object)
    return session_view(page_object)
