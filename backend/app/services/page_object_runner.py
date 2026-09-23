"""UI 自动化 PageObject 后台任务编排(元素抽取 / 定位器验证 / 自愈执行)。

- 任务状态:内存字典(与 pipeline/_ui_tasks 同构),供前端轮询。
- 浏览器阶段复用 browser_agent 的登录态缓存与 Proactor 线程兜底。
- 抽取:Playwright 采集 DOM → (可选)LLM 语义命名 → 归一化 → 落 PageObject。
- 验证:打开页面逐个定位器统计命中数。
- 执行:按步骤用 PageObject 定位器操作,失败时多候选降级 / DOM 重扫自愈并回写。
"""
import asyncio
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sqlmodel import Session, select

from app.services import browser_agent as ba
from app.services import page_login as pl
from app.services.locator_healer import (
    heal_from_candidates,
    merge_elements,
    promote_locator,
    resolve_target,
    validate_element,
)
from app.services.locator_strategies import playwright_locator
from app.services.page_extractor import (
    apply_llm_names,
    build_llm_naming_prompt,
    extract_elements,
    normalize_elements,
    parse_llm_names,
)
from db.models import PageObject, UIAutomationRun, cn_tz

logger = logging.getLogger(__name__)

# 内存任务状态
_po_tasks: Dict[str, Dict[str, Any]] = {}
_po_task_handles: Dict[str, "asyncio.Task"] = {}

EXTRACT_TIMEOUT_SECONDS = 180
VALIDATE_TIMEOUT_SECONDS = 240
EXECUTE_TIMEOUT_SECONDS = 600


def get_po_task(task_id: str) -> Optional[Dict[str, Any]]:
    return _po_tasks.get(task_id)


def _update_task(task_id: Optional[str], **fields) -> None:
    if task_id and task_id in _po_tasks:
        _po_tasks[task_id].update(fields)


def _find_task_id_by_record(record_id: int) -> Optional[str]:
    for tid, state in _po_tasks.items():
        if state.get("record_id") == record_id:
            return tid
    return None


# ---------------------------------------------------------------------------
# LLM 语义命名
# ---------------------------------------------------------------------------

async def name_elements_with_llm(raw_elements: list[dict]) -> list[dict]:
    """调用全局 LLM 为元素做语义命名;失败时原样返回(降级为启发式)。"""
    if not raw_elements:
        return raw_elements
    try:
        from config import config_manager
        from utils.llm_factory import build_chat_model

        model = build_chat_model(
            api_key=config_manager.get_api_key(),
            api_base_url=config_manager.config.get("api_base_url"),
            api_model=config_manager.config.get("api_model"),
            max_tokens=4096,
            temperature=0,
        )
        prompt = build_llm_naming_prompt(raw_elements)
        response = await model.ainvoke(prompt)
        text = getattr(response, "content", response)
        names = parse_llm_names(text if isinstance(text, str) else str(text))
        return apply_llm_names(raw_elements, names)
    except Exception as exc:  # noqa: BLE001 - 命名失败不阻塞抽取
        logger.warning(f"[UIAuto] LLM 语义命名失败,回退启发式: {exc}")
        return raw_elements


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class PageObjectRunner:
    """单条 UIAutomationRun 的生命周期编排。"""

    def __init__(
        self,
        request_db: Optional[Session] = None,
        login_overrides: Optional[dict] = None,
        agent_factory: Optional[Any] = None,
        login_page_object_id: Optional[int] = None,
        mark_login_page: bool = False,
        skip_login: bool = False,
    ):
        self._request_db = request_db
        # 本次请求显式关联的登录页对象(优先级高于页面对象自身已关联的那个)
        self.login_page_object_id = login_page_object_id or None
        # 本次抽取的页面是否直接标记为「登录页对象」(登录 = 该对象 + 它的执行步骤)
        self.mark_login_page = bool(mark_login_page)
        # 本次显式要求「不使用登录态」:既不关联登录页对象,也不读该站点的通用缓存,
        # 直接匿名访问。用户没选登录页对象时就是这个语义。
        self.skip_login = bool(skip_login)
        # 本次显式填写的登录信息(留空则回退全局参数),只经内存传递,不落库
        self.login_overrides = {k: v for k, v in (login_overrides or {}).items() if v}
        # 仅登录阶段的 browser-use Agent 工厂(测试注入 fake;None 时用真实现)
        self.agent_factory = agent_factory

    def _acquire_session(self) -> Session:
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
        if db is None or db is self._request_db:
            return
        try:
            db.close()
        except Exception:
            pass

    async def run(self, run_id: int) -> None:
        db = None
        try:
            db = self._acquire_session()
            run = db.get(UIAutomationRun, run_id)
            if run is None:
                logger.error(f"[UIAuto] run {run_id} 不存在")
                return
            task_id = _find_task_id_by_record(run_id)
            if run.run_type == "extract":
                await self._run_extract(db, run, task_id)
            elif run.run_type == "validate":
                await self._run_validate(db, run, task_id)
            elif run.run_type == "execute":
                await self._run_execute(db, run, task_id)
            else:
                raise RuntimeError(f"未知运行类型: {run.run_type}")
        finally:
            self._release_session(db)

    # ---- 抽取 ----

    async def _run_extract(self, db: Session, run: UIAutomationRun, task_id: Optional[str]) -> None:
        try:
            spec = run.result.get("request") or {}
            page_object = db.get(PageObject, run.page_object_id) if run.page_object_id else None
            _update_task(task_id, phase="launching_browser", progress=15, message="正在启动浏览器...")
            storage_state = await self._ensure_session(
                db, run.target_url, task_id, page_object, run.user_id
            )
            # 抽取器「疑似可交互但未采集」的诊断清单(闭包外持有,供 _phase 填充)
            skipped: list = []

            async def _phase():
                _update_task(task_id, phase="extracting", progress=40, message="正在抽取页面元素...")
                raw = await extract_elements(
                    run.target_url,
                    storage_state=storage_state,
                    trigger_texts=spec.get("trigger_texts"),
                    skipped_out=skipped,
                )
                # 登录态过期探测:页面采到密码输入框 ≈ 被重定向到登录页
                # (缓存文件非空但服务端会话已失效,_ensure_session 无法识别)。
                # 强制重登录一次并重抽;未配置登录信息时保留登录页元素。
                #
                # 例外一:本次显式不使用登录态(skip_login)时整段跳过 —— 匿名访问落到登录页
                # 是预期结果,不存在「登录态过期」,更不该自作主张去登录。
                # 例外二:本次抽取的主体就是登录页对象自己 —— 它本来就是登录页,采到密码框
                # 是预期结果;若在这里重登录,重抽时会带上新登录态,登录页直接被站点跳走,
                # 反而抽到登录后的业务页(2026-09-21 实测)。
                if (
                    not self.skip_login
                    and not _is_self_login_page(page_object)
                    and _looks_like_login_page(raw)
                ):
                    _update_task(
                        task_id, phase="logging_in", progress=50,
                        message="检测到登录页(登录态可能已过期),正在重新登录...",
                    )
                    try:
                        fresh = await self._relogin(
                            db, run.target_url, task_id, page_object, run.user_id
                        )
                    except Exception as exc:  # noqa: BLE001 - 重登录失败不崩抽取,保留登录页元素
                        logger.warning(f"[UIAuto] 抽取中重登录异常: {exc}")
                        fresh = None
                    if fresh:
                        _update_task(
                            task_id, phase="extracting", progress=60,
                            message="重新登录成功,再次抽取页面元素...",
                        )
                        skipped.clear()
                        raw = await extract_elements(
                            run.target_url,
                            storage_state=fresh,
                            trigger_texts=spec.get("trigger_texts"),
                            skipped_out=skipped,
                        )
                if spec.get("use_llm_naming", True):
                    _update_task(task_id, phase="naming", progress=65, message="AI 正在语义命名元素...")
                    raw = await name_elements_with_llm(raw)
                return raw, skipped

            raw, skipped = await asyncio.wait_for(
                ba._run_browser_phase(_phase()), timeout=EXTRACT_TIMEOUT_SECONDS
            )
            _update_task(task_id, phase="persisting", progress=90, message="正在生成 PageObject...")
            new_elements = normalize_elements(raw)
            existing = page_object
            if existing is not None:
                # 重新生成:合并刷新现有 PageObject,保留用户编辑与自愈历史
                merged = (
                    merge_elements(existing.elements or [], new_elements)
                    if spec.get("merge", True)
                    else new_elements
                )
                existing.elements = merged
                # 页面对象地址保持原文(可含 {{var}} 变量),不用已解析的 run.target_url 回写覆盖
                if not existing.url and run.target_url:
                    existing.url = run.target_url
                # 本次抽取显式指定了登录页对象:同步绑定,后续无需重复选择
                if self.login_page_object_id:
                    existing.login_page_object_id = self.login_page_object_id
                if self.mark_login_page:
                    existing.is_login_page = True
                db.add(existing)
                db.commit()
                db.refresh(existing)
                page_object = existing
                elements = merged
            else:
                elements = new_elements
                page_object = PageObject(
                    name=spec.get("name") or _default_page_object_name(run.target_url),
                    # 原文落库(可含 {{var}}):换环境只需改全局参数,执行时才解析
                    site_origin=spec.get("site_origin", ""),
                    page_path=spec.get("page_path", ""),
                    url=spec.get("url") or run.target_url,
                    description=spec.get("description", ""),
                    elements=elements,
                    is_login_page=self.mark_login_page,
                    login_page_object_id=self.login_page_object_id,
                    user_id=run.user_id,
                )
                db.add(page_object)
                db.commit()
                db.refresh(page_object)

            run.page_object_id = page_object.id
            # uncollected:界面上看得见、像控件,却没被采到的元素。非空说明抽取器有覆盖盲区,
            # 前端据此提示用户 —— 让「漏采」当场可见,而不是写用例时才发现少了个字段。
            uncollected = [item for item in skipped if item.get("reason") == "visible-not-collected"]
            run.result = {
                "elements": elements,
                "raw_count": len(raw),
                "element_count": len(elements),
                "uncollected": uncollected,
                "uncollected_count": len(uncollected),
            }
            run.status = "PASSED"
            run.summary = f"共采集 {len(raw)} 个交互元素,生成 {len(elements)} 个带定位器的元素"
            if uncollected:
                run.summary += f";另有 {len(uncollected)} 个疑似控件未采集,请核对"
            run.finished_at = datetime.now(tz=cn_tz)
            db.add(run)
            db.commit()
            _update_task(
                task_id, status="done", phase="done", progress=100, result_status="PASSED",
                page_object_id=page_object.id,
                # 截断到 20 条:task 是内存态、前端只做提示,不需要全量
                uncollected=uncollected[:20], uncollected_count=len(uncollected),
            )
        except asyncio.CancelledError:
            _mark_stopped(db, run, task_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[UIAuto] run {run.id} 抽取异常")
            _persist_error(db, run, task_id, str(exc))
        finally:
            origin = _origin_of(run.target_url)
            if origin:
                ba._normalize_state_file_encoding(ba._cache_path(origin))
            # 浏览器侧会把刷新后的会话写回本次使用的文件:把实况(条目数/时间/状态)
            # 回写登录页对象,下次抽取/执行据此直接复用,不再重复登录。
            self._refresh_login_page_status(db, run, origin=origin)

    # ---- 验证 ----

    async def _run_validate(self, db: Session, run: UIAutomationRun, task_id: Optional[str]) -> None:
        try:
            page_object = db.get(PageObject, run.page_object_id) if run.page_object_id else None
            if page_object is None:
                raise RuntimeError("页面对象不存在")
            _update_task(task_id, phase="launching_browser", progress=15, message="正在启动浏览器...")
            storage_state = await self._ensure_session(
                db, run.target_url, task_id, page_object, run.user_id
            )

            async def _phase():
                _update_task(task_id, phase="validating", progress=45, message="正在验证定位器...")
                return await _validate_in_browser(run.target_url, page_object.elements or [], storage_state)

            results = await asyncio.wait_for(
                ba._run_browser_phase(_phase()), timeout=VALIDATE_TIMEOUT_SECONDS
            )
            matched = sum(1 for item in results if item.get("matched"))
            run.result = {"elements": results, "matched": matched, "total": len(results)}
            run.status = "PASSED"
            run.summary = f"{matched}/{len(results)} 个元素存在可用定位器"
            run.finished_at = datetime.now(tz=cn_tz)
            db.add(run)
            db.commit()
            _update_task(task_id, status="done", phase="done", progress=100, result_status="PASSED")
        except asyncio.CancelledError:
            _mark_stopped(db, run, task_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[UIAuto] run {run.id} 验证异常")
            _persist_error(db, run, task_id, str(exc))
        finally:
            origin = _origin_of(run.target_url)
            if origin:
                ba._normalize_state_file_encoding(ba._cache_path(origin))
            # 浏览器侧会把刷新后的会话写回本次使用的文件:把实况(条目数/时间/状态)
            # 回写登录页对象,下次抽取/执行据此直接复用,不再重复登录。
            self._refresh_login_page_status(db, run, origin=origin)

    # ---- 执行(自愈) ----

    async def _run_execute(self, db: Session, run: UIAutomationRun, task_id: Optional[str]) -> None:
        try:
            page_object = db.get(PageObject, run.page_object_id) if run.page_object_id else None
            if page_object is None:
                raise RuntimeError("页面对象不存在")
            # 步骤来源:本次提交的步骤 > 页面对象上保存的步骤(「保存步骤」后无需每次重填)
            steps = run.steps or list(page_object.steps or [])
            if not steps:
                raise RuntimeError("执行步骤为空")
            _update_task(task_id, phase="launching_browser", progress=15, message="正在启动浏览器...")
            storage_state = await self._ensure_session(
                db, run.target_url, task_id, page_object, run.user_id
            )

            elements = list(page_object.elements or [])
            trace: list[dict] = []
            heal_events: list[dict] = []
            # 执行过程中的登录/会话变化保存回登录页对象 → 登录状态得以保持并被其他页面对象复用
            save_state_path = self._state_save_path(db, page_object, run.user_id)

            async def _phase():
                _update_task(task_id, phase="executing", progress=30, message="正在执行步骤...")
                return await _execute_in_browser(
                    run.target_url, elements, steps, storage_state, trace, heal_events, task_id,
                    save_state_path=save_state_path,
                )

            passed = await asyncio.wait_for(
                ba._run_browser_phase(_phase()), timeout=EXECUTE_TIMEOUT_SECONDS
            )

            # 回写自愈后的定位器(仅元素定位器变化时)
            page_object.elements = elements
            db.add(page_object)
            run.steps_trace = trace
            run.heal_events = heal_events
            run.result = {**(run.result or {}), "heal_count": len(heal_events)}
            run.status = "PASSED" if passed else "FAILED"
            run.summary = (
                f"步骤 {sum(1 for t in trace if t.get('status') == 'passed')}/{len(trace)} 通过,"
                f"自愈 {len(heal_events)} 次"
            )
            run.finished_at = datetime.now(tz=cn_tz)
            db.add(run)
            db.commit()
            _update_task(task_id, status="done", phase="done", progress=100, result_status=run.status)
        except asyncio.CancelledError:
            _mark_stopped(db, run, task_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[UIAuto] run {run.id} 执行异常")
            _persist_error(db, run, task_id, str(exc))
        finally:
            origin = _origin_of(run.target_url)
            if origin:
                ba._normalize_state_file_encoding(ba._cache_path(origin))
            # 浏览器侧会把刷新后的会话写回本次使用的文件:把实况(条目数/时间/状态)
            # 回写登录页对象,下次抽取/执行据此直接复用,不再重复登录。
            self._refresh_login_page_status(db, run, origin=origin)

    # ---- 登录态(载体是页面对象) ----

    def _resolve_login_page(
        self, db: Session, page_object: Optional[PageObject], user_id: Optional[str]
    ) -> Optional[PageObject]:
        """解析本次要复用的「登录页对象」。

        优先级:请求显式指定 > 页面对象自身已关联 > 页面对象自己就是登录页对象。
        对方已删除时视为未关联(关联失效不该阻断抽取/执行本身)。有权限即可跨用户复用。
        """
        login_page_id = self.login_page_object_id or (
            page_object.login_page_object_id if page_object else None
        )
        if not login_page_id:
            if page_object is not None and page_object.is_login_page:
                return page_object
            return None
        if page_object is not None and login_page_id == page_object.id:
            return page_object
        row = db.get(PageObject, login_page_id)
        if row is None:
            return None
        return row

    async def _bootstrap_login(
        self, db: Session, login_page: PageObject, task_id: Optional[str]
    ) -> bool:
        """执行登录页对象的步骤完成登录,并把登录态保存到它自己的文件。

        登录态的唯一来源就是这里 —— 在「执行(自愈)」里配置好的步骤;执行收尾时
        `_execute_in_browser` 会把会话落盘。成功后其他页面对象关联它即可直接复用。
        没配步骤属于配置缺失,直接失败并给出可操作提示,不做任何猜测式登录。
        """
        state_file = pl.ensure_session_path(login_page)
        db.add(login_page)
        db.commit()
        db.refresh(login_page)

        if not login_page.url:
            pl.refresh_status(db, login_page, error="登录页对象缺少目标地址")
            return False

        steps = [step for step in (login_page.steps or []) if isinstance(step, dict)]
        if not steps:
            reason = f"「{login_page.name}」未配置登录步骤:请到「执行(自愈)」里配置登录步骤并保存"
            _update_task(task_id, message=reason)
            pl.refresh_status(db, login_page, error=reason)
            return False

        # 登录页对象存的可能是 {{web_url}} 等占位符原文;goto 前必须按默认环境参数解析,
        # 否则 page.goto("{{web_url}}/") 直接失败,重登录永远建不出登录态(实测 PO514)。
        goto_url = _resolve_page_url(db, login_page.url)
        if not goto_url:
            pl.refresh_status(db, login_page, error="登录页对象目标地址解析后为空")
            return False

        _update_task(
            task_id, phase="logging_in", progress=25,
            message=f"未检测到登录态,正在执行「{login_page.name}」的登录步骤...",
        )
        trace: list[dict] = []
        heal_events: list[dict] = []
        passed = await _execute_in_browser(
            goto_url,
            list(login_page.elements or []),
            steps,
            # 登录步骤必须在登录页上跑:带上历史登录态会被站点直接带进业务页,
            # 登录页元素全部定位不到(与 _ensure_session 的 is_self 分支同一原理)。
            None,
            trace,
            heal_events,
            task_id,
            save_state_path=str(state_file),
        )
        if heal_events:
            # 自愈结果已写进 elements,落库让下次登录定位更稳
            db.add(login_page)
            db.commit()
        ok = passed and pl.session_usable(login_page)
        pl.refresh_status(
            db,
            login_page,
            logged_in=ok,
            error="" if ok else "登录步骤执行未通过或未产生有效登录态",
        )
        return ok

    def _state_save_path(
        self, db: Session, page_object: Optional[PageObject], user_id: Optional[str]
    ) -> Optional[str]:
        """本次执行结束时要保存会话的位置。

        关联了登录页对象 → 存到它那里(登录态得以保持、供其他页面对象复用);
        自身是登录页对象 → 存到自己;都没有 → 该站点的通用缓存。
        """
        login_page = self._resolve_login_page(db, page_object, user_id)
        if login_page is not None:
            return str(pl.ensure_session_path(login_page))
        if page_object is not None and page_object.url:
            origin = _origin_of(_resolve_page_url(db, page_object.url))
            if origin:
                return str(ba._cache_path(origin))
        return None

    def _sync_login_status(
        self,
        db: Session,
        login_page: Optional[PageObject],
        *,
        logged_in: bool = False,
        error: str = "",
        origin: str = "",
    ) -> None:
        """把登录态文件实况回写登录页对象(计数/状态/时间)。失败只告警。"""
        if login_page is None:
            return
        try:
            pl.refresh_status(
                db, login_page, logged_in=logged_in, error=error, origin=origin
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[UIAuto] 登录态状态回写失败(忽略): {exc}")

    def _refresh_login_page_status(
        self, db: Session, run: UIAutomationRun, *, origin: str = ""
    ) -> None:
        """任务收尾:把本次用到的登录页对象的登录态实况回写 DB。"""
        page_object = db.get(PageObject, run.page_object_id) if run.page_object_id else None
        login_page = self._resolve_login_page(db, page_object, run.user_id)
        self._sync_login_status(db, login_page, origin=origin)

    async def _relogin(
        self,
        db: Session,
        target_url: str,
        task_id: Optional[str],
        page_object: Optional[PageObject],
        user_id: Optional[str],
    ) -> Optional[str]:
        """发现登录态实际已失效(被重定向到登录页)时的强制重登。

        返回新的 storage_state 路径;无法重登时返回 None,调用方保留原结果。
        本次显式不使用登录态(skip_login)时直接返回 None —— 不给匿名抽取偷偷登录。
        本次主体就是登录页对象自己时同样直接返回 None —— 它停在登录页是预期结果,
        重登录只会让页面被带进业务页。
        """
        if self.skip_login or _is_self_login_page(page_object):
            return None
        login_page = self._resolve_login_page(db, page_object, user_id)
        if login_page is not None:
            # 清掉本进程标记,确保这次真的重新执行登录步骤而不是复用旧会话
            ba._established_sessions.discard(str(pl.ensure_session_path(login_page)))
            ok = await self._bootstrap_login(db, login_page, task_id)
            if not ok:
                return None
            return str(pl.ensure_session_path(login_page))
        result = await ba.establish_login_session(
            db,
            target_url,
            self.login_overrides,
            agent_factory=self.agent_factory,
            task_id=task_id,
        )
        return (result.get("cache_path") or None) if result.get("ok") else None

    async def _ensure_session(
        self,
        db: Session,
        target_url: str,
        task_id: Optional[str],
        page_object: Optional[PageObject] = None,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """确保存在可用登录态后返回 storage_state 路径。

        优先级(前者可用则绝不重复登录):
        1. 关联的登录页对象:有登录态直接用;没有就先执行它的登录步骤完成登录并保持,
           再继续后续抽取/验证/执行;
        2. 该 origin 的通用登录态缓存(未关联时的原行为);
        3. 都没有 → 匿名访问(None)。

        例外一:`skip_login=True`(本次显式不选登录页对象)时直接返回 None —— 不查登录页
        对象、不读通用缓存、也不触发 AI 登录,全程匿名。

        例外二:本次主体**就是登录页对象自己**(执行/验证/抽取它的登录步骤)时同样直接
        返回 None —— 带上历史登录态会被站点直接带进业务页,登录页元素一个都不在。

        显式填写的登录信息(本次请求)在 AI 登录回退路径上覆盖全局参数。
        """
        if self.skip_login:
            _update_task(task_id, message="本次不使用登录态,以匿名身份访问")
            return None
        login_page = self._resolve_login_page(db, page_object, user_id)
        if login_page is not None:
            state_file = pl.ensure_session_path(login_page)
            db.add(login_page)
            db.commit()
            ba.restore_state_file_backup(state_file)
            ba._normalize_state_file_encoding(state_file)
            is_self = page_object is not None and page_object.id == login_page.id
            if is_self:
                # 本次的主体就是登录页对象本身(执行/验证/抽取它的登录步骤)。
                # 必须匿名打开:带上任何历史登录态,站点都会直接跳进业务页,登录页元素
                # 一个都不在,步骤必然全失效。实测该站点(http://192.168.13.178:8182/):
                #   带缓存 → #/home/workbench/workbench/index(工作台,input_5/input_7/button_8 全空)
                #   匿名   → /sso/?redirect=...(登录页,三个元素各命中 1 个)
                # 登录态不是从文件「读」出来的,而是这次跑完登录步骤后由会话落盘产生的
                # (见 _execute_in_browser 的 save_state_path)。所以这里恒返回 None。
                return None
            if pl.session_usable(login_page):
                _update_task(task_id, message=f"复用「{login_page.name}」的登录态")
                return str(state_file)
            ok = await self._bootstrap_login(db, login_page, task_id)
            if not ok:
                raise RuntimeError(
                    f"登录失败(登录页对象「{login_page.name}」):"
                    f"{login_page.last_login_error or '未知原因'}"
                )
            _update_task(task_id, message=f"已通过「{login_page.name}」建立登录态")
            # 顺带同步到该站点通用缓存:没关联登录页对象的动作也能复用同一登录态
            origin = _origin_of(target_url)
            if origin:
                ba.sync_state_file(state_file, ba._cache_path(origin))
            return str(state_file)

        from app.routes.proxy import build_param_map

        param_map = build_param_map(db, None, [])
        login_ctx, meta = ba.resolve_login_context(db, param_map, target_url, self.login_overrides)
        origin = meta.get("origin", "")
        if not origin:
            return None
        state_file = ba._cache_path(origin)
        # 恢复备份(避免一次失败执行清空登录态) + 归一化编码(browser-use 用 gbk 读)
        ba.restore_state_file_backup(state_file)
        ba._normalize_state_file_encoding(state_file)
        if ba.has_valid_session(origin) or ba.is_session_established(origin):
            return str(state_file)
        if not login_ctx.configured:
            # 未配置登录且无有效缓存:匿名访问(storage_state=None),避免 Playwright 因
            # storage_state 文件缺失报 ENOENT 导致抽取失败。
            return str(state_file) if state_file.exists() else None
        _update_task(task_id, phase="logging_in", progress=25, message="未检测到登录态,正在登录...")
        result = await ba.establish_login_session(
            db,
            target_url,
            self.login_overrides,
            agent_factory=self.agent_factory,
            task_id=task_id,
        )
        if not result.get("ok"):
            raise RuntimeError(f"登录失败,无法访问登录后页面:{result.get('reason') or '未知原因'}")
        _update_task(task_id, message="登录成功,已建立登录态")
        return result.get("cache_path") or str(state_file)


def _looks_like_login_page(raw_elements: list) -> bool:
    """页面采到密码输入框即视为登录页(过期登录态被重定向的强信号)。"""
    for item in raw_elements or []:
        if isinstance(item, dict) and (item.get("inputType") or "").strip().lower() == "password":
            return True
    return False


def _is_self_login_page(page_object: Optional[PageObject]) -> bool:
    """本次主体是否就是登录页对象本身(它本来就该停在登录页)。

    两种形态都算:自己勾了「作为登录页对象」,或自己关联了自己(历史数据形态)。
    """
    if page_object is None:
        return False
    if page_object.is_login_page:
        return True
    # 两个都是 None 不算「关联自己」——新建还没落库的对象 id 为 None
    return (
        page_object.login_page_object_id is not None
        and page_object.login_page_object_id == page_object.id
    )


def _diagnose_locator_failure(raw_elements: list, had_session: bool) -> str:
    """定位器全部失效时给出可操作的原因,而不是一句「都失效了」。

    判据只有两条:重扫到的页面是不是登录页(含密码框)、本次有没有携带登录态。
    组合出的两种典型场景都是真实踩过的:
      - 带登录态却没停在登录页 → 多半是把登录页对象当普通页面执行了(登录态直接把人
        带进了业务页),提示去勾「作为登录页对象」;
      - 带登录态却停在登录页 → 登录态已失效被踢回,提示刷新登录态。
    """
    base = "定位器全部失效且自愈失败"
    page_is_login = _looks_like_login_page(raw_elements)
    if had_session and not page_is_login:
        return (
            f"{base}:页面已带登录态打开,没有停在登录页 —— 若这个页面对象本身就是登录页"
            "(步骤是填账号密码后点登录),请到「编辑信息」勾选「作为登录页对象」,"
            "执行时会改为匿名打开登录页"
        )
    if had_session and page_is_login:
        return f"{base}:页面被重定向回登录页,登录态可能已失效 —— 请先刷新登录态再执行"
    if page_is_login:
        return f"{base}:匿名访问停在登录页 —— 请给该页面对象关联一个登录页对象,或把它自己标为登录页对象"
    return f"{base}:重扫页面未匹配到该元素,请重新抽取元素或核对目标地址"


def _default_page_object_name(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    path = (parsed.path or "").strip("/").split("/")[-1]
    fragment = (parsed.fragment or "").strip("/").split("/")[-1]
    tail = fragment or path or parsed.netloc or "page"
    return tail.replace("-", "_")


def _origin_of(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""


def _resolve_page_url(db: Session, url: str) -> str:
    """页面对象地址存原文(可含 {{var}});需要真实访问或算 origin 时按默认环境参数解析。

    不含占位符时原样返回(但仍归一化双斜杠);解析后折叠 path 中的重复斜杠,
    避免 {{web_url}}=/ + /#/x 拼出 http://host//#/x 导致 SPA 落回登录页。
    """
    from app.services.ui_steps import normalize_url

    if not url:
        return ""
    if "{{" not in url:
        return normalize_url(url)
    from app.routes.proxy import build_param_map, substitute_variables

    return normalize_url(substitute_variables(url, build_param_map(db, None, []), set()))


def _frame_for(page, element: dict):
    """元素记录了 frame(iframe/微前端正文)时返回对应 Frame,否则返回主页面。

    Frame 与 Page 同样暴露 .locator(),可直接传给 resolve_locator/validate_element。
    匹配不到时回退主页面(保持旧行为,不因 frame URL 变化而崩)。
    """
    frame_url = str(element.get("frame") or "").strip()
    if not frame_url:
        return page
    base = frame_url.split("?")[0]
    for frame in page.frames:
        if frame is page.main_frame:
            continue
        if frame.url == frame_url or frame.url.split("?")[0] == base:
            return frame
    return page


async def _wait_for_page_ready(page) -> None:
    """等待页面渲染稳定。

    与 `page_extractor.extract_elements` 保持一致:先等 networkidle,拿不到时退回
    固定短等待,避免 SPA / 客户端跳转尚未挂载 DOM 时把所有定位器统计成 0 命中。
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    try:
        await page.wait_for_timeout(500)
    except Exception:
        pass


# 抓取会话前的额外等待:前端把 token 写进 localStorage 通常发生在登录请求返回、
# 路由跳转之后,比最后一步点击晚一拍(实测约 1s)。
SNAPSHOT_SETTLE_MS = 1500


async def _settle_before_snapshot(page) -> None:
    """落盘登录态之前等页面安静下来。

    实测(鲁班平台 http://192.168.13.178:8182/):点完「登录」立刻
    `context.storage_state()`,落盘文件只有站点标题这类空壳(188 字节、无
    LBuilderToken) —— 登录态看着建立了,下一个页面对象复用时等于没登录,
    于是又去重新登录,永远停在「未登录」。等网络安静再补一段固定延迟即可拿到
    完整会话。
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    try:
        await page.wait_for_timeout(SNAPSHOT_SETTLE_MS)
    except Exception:
        pass


async def _validate_in_browser(url: str, elements: list[dict], storage_state: Optional[str]) -> list[dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            kwargs: dict = {}
            if storage_state:
                kwargs["storage_state"] = storage_state
            context = await browser.new_context(**kwargs)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await _wait_for_page_ready(page)
            results = []
            for element in elements:
                if not isinstance(element, dict):
                    continue
                results.append(await validate_element(_frame_for(page, element), element))
            return results
        finally:
            await browser.close()


async def _execute_in_browser(
    url: str,
    elements: list[dict],
    steps: list[dict],
    storage_state: Optional[str],
    trace: list[dict],
    heal_events: list[dict],
    task_id: Optional[str],
    save_state_path: Optional[str] = None,
) -> bool:
    """执行 PageObject 步骤;定位失败时先降级再 DOM 重扫自愈并回写元素。

    save_state_path 非空时,执行结束(无论成败)把最新会话落盘到该路径 ——
    「登录后保持登录状态」就靠这一步:执行过程中的登录/续期不会随浏览器关闭而丢失。
    落盘前先 `_settle_before_snapshot` 等页面安静(否则抓到的是没有 token 的空壳),
    落盘后再同步一份到该 origin 的通用缓存(供未关联登录页对象的旧链路复用)。
    """
    from playwright.async_api import async_playwright

    by_key = {e.get("key"): e for e in elements if isinstance(e, dict)}
    passed = True
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = None
        try:
            kwargs: dict = {}
            if storage_state:
                kwargs["storage_state"] = storage_state
            context = await browser.new_context(**kwargs)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await _wait_for_page_ready(page)

            for index, step in enumerate(steps, 1):
                element = by_key.get(step.get("element"))
                entry = {
                    "step_no": index,
                    "element": step.get("element"),
                    "action": step.get("action"),
                    "value": step.get("value"),
                    "status": "failed",
                    "healed": False,
                    "error": None,
                }
                if element is None:
                    entry["error"] = "元素不存在"
                    trace.append(entry)
                    passed = False
                    break

                resolved = await resolve_target(_frame_for(page, element), element)
                if resolved:
                    # 多候选降级命中:提升为主定位器(回写)
                    event = promote_locator(element, resolved["strategy"], resolved["value"])
                    if event:
                        event["step"] = index
                        heal_events.append(event)
                        entry["healed"] = True

                if resolved is None:
                    # 全部候选失败:重扫 DOM 相似度重匹配
                    raw = await extract_elements(url, storage_state=storage_state)
                    event = heal_from_candidates(element, raw)
                    if event:
                        event["step"] = index
                        heal_events.append(event)
                        entry["healed"] = True
                        resolved = await resolve_target(_frame_for(page, element), element)
                    if resolved is None:
                        entry["error"] = _diagnose_locator_failure(raw, bool(storage_state))[:300]
                        trace.append(entry)
                        passed = False
                        break

                # 隐藏控件的可见壳定位器交给执行层兜底(不参与候选降级,避免被误选为主定位器)
                resolved["trigger"] = element.get("trigger")
                try:
                    await _perform(resolved, step, page)
                    entry["status"] = "passed"
                except Exception as exc:  # noqa: BLE001
                    entry["error"] = str(exc)[:300]
                    passed = False
                    trace.append(entry)
                    break
                trace.append(entry)
                _update_task(task_id, progress=min(30 + index * 8, 95), message=f"执行步骤 {index}: {step.get('action')}")
            return passed
        finally:
            # 保持登录状态:执行中若完成登录/会话续期,关闭浏览器前落盘。
            # 失败只告警 —— 保存登录态不该让一次执行变红。
            if save_state_path and context is not None:
                try:
                    await _settle_before_snapshot(page)
                    # 空壳不落盘:匿名/登录失败会话的 storage_state 没有任何 cookie 或
                    # 有效 localStorage 条目,写下去会把已有的好登录态覆盖成空壳
                    # (实测 po514 被写成 {"cookies":[],"origins":[]},之后永远停在未登录)。
                    state = await context.storage_state()
                    if not ba.state_meaningful(state):
                        logger.warning(
                            f"[UIAuto] 本次会话无有效登录态(空壳),跳过保存: {save_state_path}"
                        )
                    else:
                        state_file = Path(save_state_path)
                        state_file.parent.mkdir(parents=True, exist_ok=True)
                        await context.storage_state(path=str(state_file))
                        ba._normalize_state_file_encoding(state_file)
                        # 顺带同步到该站点的通用缓存:没关联登录页对象的旧链路也能复用这次
                        # 新建立的登录态(实测登录页对象跑完登录后通用缓存里还是旧 token,
                        # 未关联的对象一用就被踢回登录页)。目标就是通用缓存本身时跳过。
                        origin = _origin_of(url)
                        if origin:
                            cache = ba._cache_path(origin)
                            if cache != state_file:
                                ba.sync_state_file(state_file, cache)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[UIAuto] 保存登录态失败({save_state_path}): {exc}")
            await browser.close()


# 上传动作(前端下拉用 upload_file,另两个是手写别名)
UPLOAD_ACTIONS = frozenset({"upload_file", "upload", "set_input_files"})

# 上传动作支持多文件:值里用换行 / 逗号 / 分号分隔
_UPLOAD_SPLIT_RE = re.compile(r"[\n,;]+")


def _upload_paths(raw: Any) -> list[str]:
    """动作值 → 待上传文件的绝对路径列表。

    可填单个路径,也可用换行/逗号分隔多个(multiple 上传框)。相对路径按后端
    进程工作目录解析。文件不存在时直接报错,避免静默跳过导致用例假通过。
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("上传动作缺少文件路径:请在步骤「值」里填待上传文件的绝对路径")
    paths: list[str] = []
    for part in _UPLOAD_SPLIT_RE.split(text):
        candidate = part.strip().strip('"').strip("'")
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.exists():
            raise ValueError(f"待上传文件不存在: {path}")
        if path.is_dir():
            raise ValueError(f"待上传的是目录而非文件: {path}")
        paths.append(str(path))
    if not paths:
        raise ValueError("上传动作缺少有效的文件路径")
    return paths


async def _perform_upload(target, value: Any, page=None) -> None:
    """上传文件。

    首选目标就是抽取到的 file input:Playwright 的 set_input_files 不要求元素
    可见,直接写值比模拟点击稳。只有当用户选中的是可见触发器(自定义 div/label
    包装,或 input 在点击后才动态注入)时,才退回「拦截文件选择器 + 点击」。
    """
    paths = _upload_paths(value)
    is_file_input = True
    try:
        is_file_input = bool(
            await target.evaluate(
                "el => el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'file'"
            )
        )
    except Exception:  # noqa: BLE001 - 判定失败按 file input 试,报错信息更直接
        pass
    if is_file_input:
        await target.set_input_files(paths)
        return
    if page is None:
        raise ValueError(
            "目标不是 file input 且拿不到页面对象,无法拦截文件选择器;"
            "请改选该上传框对应的 file input 元素(类型为 upload)"
        )
    async with page.expect_file_chooser(timeout=10000) as chooser_info:
        await target.click()
    chooser = await chooser_info.value
    await chooser.set_files(paths)


# 元素可操作性等待上限(按「可见」判定)
VISIBLE_WAIT_MS = 5000


async def _force_needed(target, *, handle: bool) -> bool:
    """元素是否不可见(需以 force 方式执行)。

    低代码平台把原生控件隐藏(display:none)、交互放到 label/div 壳上是常态,
    这类元素等 visible 必然超时。先试 visible,失败后退回 attached 只探测存在性
    并标记 force,让 check/click/select_option 跳过可操作性检查直接派发事件。

    注意 Playwright 的「可见」判定不含 opacity,所以被 opacity:0 盖住的输入框不
    会走这条分支,fill 依然正常。
    """
    wait = (
        (lambda state, timeout: target.wait_for_element_state(state, timeout=timeout))
        if handle
        else (lambda state, timeout: target.wait_for(state=state, timeout=timeout))
    )
    try:
        await wait("visible", VISIBLE_WAIT_MS)
        return False
    except Exception:  # noqa: BLE001 - 元素不可见属预期场景,退回存在性探测
        await wait("attached", VISIBLE_WAIT_MS)
        return True


async def _click_like(target, resolved: dict, action: str, *, force: bool, page) -> None:
    """执行 click / check / uncheck;原生节点点不动时改点它的可见壳。

    低代码平台把原生 radio/checkbox 藏起来(display:none),连 force 点击都可能报
    not visible —— 真实交互落在 label 壳上。用抽取时一并捕获的 trigger 定位器兜底。
    """
    try:
        if action == "click":
            await target.click(force=force)
        else:
            await getattr(target, action)(force=force)
        return
    except Exception as exc:  # noqa: BLE001 - 交给可见壳兜底
        shell = (resolved or {}).get("trigger")
        if not shell or page is None:
            raise
        logger.info(f"[UIAuto] 原生节点「{action}」失败({str(exc)[:80]}),改点可见壳 {shell.get('value')}")
    shell_target = playwright_locator(page, shell["strategy"], shell["value"]).first
    await shell_target.click()


async def _perform(resolved: dict, step: dict, page=None) -> None:
    """按解析结果执行动作;kind=locator 走 Playwright,kind=handle 走 ElementHandle。

    ElementHandle 路径服务于 closed shadow root 内嵌 iframe(选择器引擎不可用
    但 evaluate_handle 可拿到元素句柄,CDP 操作与 DOM 可达性无关)。

    page 供上传动作在「目标不是 file input」时拦截文件选择器使用。
    """
    action = (step.get("action") or "").strip().lower()
    value = step.get("value")
    is_upload = action in UPLOAD_ACTIONS
    if resolved.get("kind") == "handle":
        target = resolved["handle"]
        if is_upload:
            # 框架上传组件的 file input 天生隐藏,等 visible 必然超时
            await target.wait_for_element_state("attached", timeout=5000)
            await _perform_upload(target, value, page)
            return
        force = await _force_needed(target, handle=True)
        if action == "fill":
            await target.fill("" if value is None else str(value))
        elif action == "click":
            await target.click(force=force)
        elif action == "select_option":
            await target.select_option(str(value), force=force)
        elif action in ("check", "uncheck"):
            await getattr(target, action)(force=force)
        elif action == "press":
            await target.press(str(value or "Enter"))
        elif action == "assert_text":
            text = (await target.inner_text()) or ""
            if str(value or "") not in text:
                raise AssertionError(f"期望包含「{value}」,实际「{text[:120]}」")
        elif action == "assert_visible":
            if not await target.is_visible():
                raise AssertionError("元素不可见")
        elif action == "wait_for":
            await target.wait_for_element_state("visible", timeout=10000)
        else:
            raise ValueError(f"不支持的动作: {action}")
        return
    locator = resolved["locator"]
    target = locator.first
    if is_upload:
        await target.wait_for(state="attached", timeout=5000)
        await _perform_upload(target, value, page)
        return
    force = await _force_needed(target, handle=False)
    if action == "fill":
        await target.fill("" if value is None else str(value))
    elif action in ("click", "check", "uncheck"):
        await _click_like(target, resolved, action, force=force, page=page)
    elif action == "select_option":
        await target.select_option(str(value), force=force)
    elif action == "press":
        await target.press(str(value or "Enter"))
    elif action == "assert_text":
        text = (await target.inner_text()) or ""
        if str(value or "") not in text:
            raise AssertionError(f"期望包含「{value}」,实际「{text[:120]}」")
    elif action == "assert_visible":
        if not await target.is_visible():
            raise AssertionError("元素不可见")
    elif action == "wait_for":
        await target.wait_for(state="visible", timeout=10000)
    else:
        raise ValueError(f"不支持的动作: {action}")


def _persist_error(db, run, task_id, error_msg: str) -> None:
    run.status = "ERROR"
    run.error = error_msg[:2000]
    run.finished_at = datetime.now(tz=cn_tz)
    db.add(run)
    db.commit()
    _update_task(task_id, status="done", error=error_msg[:200], result_status="ERROR")


def _mark_stopped(db, run, task_id) -> None:
    if run.status != "RUNNING":
        _update_task(task_id, status="done", progress=100, result_status=run.status)
        return
    run.status = "STOPPED"
    run.summary = run.summary or "用户已停止执行"
    run.finished_at = datetime.now(tz=cn_tz)
    db.add(run)
    db.commit()
    _update_task(task_id, status="done", progress=100, result_status="STOPPED", message="用户已停止执行")


def cancel_po_task(task_id: str) -> bool:
    state = _po_tasks.get(task_id)
    if state is None or state.get("status") == "done":
        return False
    state["stop_requested"] = True
    state["message"] = "正在停止..."
    handle = _po_task_handles.get(task_id)
    if handle is not None and not handle.done():
        handle.cancel()
    return True


def _launch(run_id: int, runner: PageObjectRunner, task_id: str) -> None:
    async def _guarded():
        try:
            async with ba._EXECUTION_LOCK:
                await runner.run(run_id)
        except asyncio.CancelledError:
            # 排队阶段取消:兜底落 STOPPED
            db = runner._acquire_session()
            try:
                run = db.get(UIAutomationRun, run_id)
                if run is not None:
                    _mark_stopped(db, run, task_id)
            finally:
                runner._release_session(db)
            raise

    handle = asyncio.create_task(_guarded())
    _po_task_handles[task_id] = handle
    handle.add_done_callback(lambda _t: _po_task_handles.pop(task_id, None))


def fail_orphan_runs(session: Optional[Session] = None) -> int:
    """服务重启后把仍处于 RUNNING 的运行记录标记为 ERROR(内存任务已随进程丢失)。

    在应用启动时调用;避免前端轮询永远拿到 RUNNING,也避免任务ID回查无终态。
    session 仅测试注入;为 None 时自建会话。
    """
    own_session = session is None
    db = session
    if own_session:
        from db.db import engine
        db = Session(engine)
    try:
        rows = db.exec(select(UIAutomationRun).where(UIAutomationRun.status == "RUNNING")).all()
        for run in rows:
            run.status = "ERROR"
            run.error = run.error or "服务重启,任务已中断"
            run.finished_at = datetime.now(tz=cn_tz)
            db.add(run)
        if rows:
            db.commit()
        return len(rows)
    except Exception as exc:  # noqa: BLE001 - 清理失败不应阻塞启动
        logger.warning(f"[UIAuto] 清理中断任务失败(忽略): {exc}")
        return 0
    finally:
        if own_session and db is not None:
            db.close()


def _new_task(record_id: int, run_type: str) -> str:
    task_id = str(uuid.uuid4())
    _po_tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "phase": "queued",
        "progress": 0,
        "message": "排队等待中(串行执行)...",
        "record_id": record_id,
        "run_type": run_type,
        "result_status": None,
        "error": None,
        "page_object_id": None,
        "created_at": datetime.now(tz=cn_tz).isoformat(),
    }
    return task_id


def launch_extraction(
    record_id: int,
    request_db: Optional[Session] = None,
    login_overrides: Optional[dict] = None,
    login_page_object_id: Optional[int] = None,
    mark_login_page: bool = False,
    skip_login: bool = False,
) -> str:
    task_id = _new_task(record_id, "extract")
    _launch(
        record_id,
        PageObjectRunner(
            request_db,
            login_overrides,
            login_page_object_id=login_page_object_id,
            mark_login_page=mark_login_page,
            skip_login=skip_login,
        ),
        task_id,
    )
    return task_id


def launch_validation(
    record_id: int,
    request_db: Optional[Session] = None,
    login_overrides: Optional[dict] = None,
    login_page_object_id: Optional[int] = None,
) -> str:
    task_id = _new_task(record_id, "validate")
    _launch(record_id, PageObjectRunner(request_db, login_overrides, login_page_object_id=login_page_object_id), task_id)
    return task_id


def launch_execution(
    record_id: int,
    request_db: Optional[Session] = None,
    login_overrides: Optional[dict] = None,
    login_page_object_id: Optional[int] = None,
) -> str:
    task_id = _new_task(record_id, "execute")
    _launch(record_id, PageObjectRunner(request_db, login_overrides, login_page_object_id=login_page_object_id), task_id)
    return task_id
