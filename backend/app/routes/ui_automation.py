"""UI 自动化工具路由:页面元素抽取 / PageObject 管理 / 登录态 / 定位器验证 / 自愈执行。"""
import logging
import re
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import select

from app.auth import UserInfo
from app.deps import CurrentUser, SessionDep
from app.services import page_login as pl
from app.services.page_object_codegen import render_page_object_code, suggest_file_name
from app.services.page_object_runner import (
    cancel_po_task,
    get_po_task,
    launch_execution,
    launch_extraction,
    launch_validation,
)
from app.services.ui_steps import build_target_url, normalize_url, normalize_url
from db.models import PageObject, UIAutomationRun
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ui-automation", tags=["ui-automation"])


class ExtractRequest(BaseModel):
    name: str = ""
    site_origin: str = ""
    page_path: str = ""
    url: str = ""
    description: str = ""
    use_llm_naming: bool = True
    # 本次抽取的显式登录配置(留空回退全局参数或页面对象关联的配置),只经内存传递不落库
    login_url: str = ""
    login_method: str = ""
    login_username: str = ""
    login_password: str = ""
    login_company: str = ""
    # 关联的登录页对象:有登录态直接复用,没有就先执行它的登录步骤再抽取
    login_page_object_id: Optional[int] = None
    # 本次抽取的页面直接标记为「登录页对象」(它的执行步骤即登录步骤)
    is_login_page: bool = False
    # 本次不使用登录态(前端没选登录页对象):不读该站点通用缓存、不触发登录,全程匿名
    skip_login: bool = False


class RegenerateRequest(BaseModel):
    use_llm_naming: bool = True
    merge: bool = True
    login_url: str = ""
    login_method: str = ""
    login_username: str = ""
    login_password: str = ""
    login_company: str = ""
    login_page_object_id: Optional[int] = None
    is_login_page: bool = False
    # 本次不使用登录态(前端没选登录页对象):不读该站点通用缓存、不触发登录,全程匿名
    skip_login: bool = False


class ValidateRequest(BaseModel):
    login_url: str = ""
    login_method: str = ""
    login_username: str = ""
    login_password: str = ""
    login_company: str = ""
    login_page_object_id: Optional[int] = None


class PageObjectPayload(BaseModel):
    name: str = ""
    site_origin: str = ""
    page_path: str = ""
    url: str = ""
    description: str = ""
    elements: List[dict] = []
    # 执行(自愈)步骤:保存在页面对象上,执行时无需每次重填;登录页对象据此完成登录
    steps: List[dict] = []
    # 是否作为登录页对象(其他页面对象可关联它复用登录态,并调用其步骤完成登录)
    is_login_page: bool = False
    # 关联的登录页对象ID:None=不改动;0=解除关联;正数=关联(校验归属)
    login_page_object_id: Optional[int] = None


class RunRequest(BaseModel):
    steps: List[dict] = []
    login_url: str = ""
    login_method: str = ""
    login_username: str = ""
    login_password: str = ""
    login_company: str = ""
    # 本次执行要复用的登录页对象(留空用页面对象自身关联的那个)
    login_page_object_id: Optional[int] = None


def _substitute(text: str, db, user: UserInfo) -> str:
    from app.routes.proxy import build_param_map, substitute_variables
    param_map = build_param_map(db, None, [])
    return substitute_variables(text or "", param_map, set())


_LEFTOVER_VAR = re.compile(r"\{\{([^}]+)\}\}")


def _resolve_or_400(text: str, db, user: UserInfo, field: str) -> str:
    """解析 {{var}} 变量(默认环境参数);解析后仍残留占位符 = 变量未定义,400 快速失败。

    URL 字段额外做 normalize_url:web_url 带尾斜杠 + url 也以 / 开头会拼出双斜杠。
    """
    resolved = normalize_url(_substitute(text, db, user))
    leftover = sorted(set(_LEFTOVER_VAR.findall(resolved)))
    if leftover:
        raise HTTPException(
            status_code=400, detail=f"{field}存在未定义的变量: {', '.join(leftover)}"
        )
    return resolved


def _resolve_page_target_url(db, user: UserInfo, page_object: PageObject) -> str:
    """解析页面对象目标地址:优先 site_origin+page_path 拼接(去尾斜杠),否则解析 url。

    这是 regenerate/validate/run 的统一入口 —— 旧代码直接 _resolve_or_400(url)
    在 web_url=http://host/ 且 url={{web_url}}/#/... 时得到 http://host//#/...,
    path 变成 //,SPA 会 404/重定向到登录页,抽取到的是登录页元素。
    """
    origin_raw = (page_object.site_origin or "").strip()
    path_raw = (page_object.page_path or "").strip()
    if origin_raw and path_raw:
        origin = _resolve_or_400(origin_raw, db, user, "站点地址").strip()
        path = _resolve_or_400(path_raw, db, user, "页面路径").strip()
        return build_target_url(origin, path)
    if not page_object.url:
        raise HTTPException(status_code=400, detail="页面对象缺少目标地址,无法解析")
    return _resolve_or_400(page_object.url, db, user, "目标地址")


def _resolve_target_url(db, user: UserInfo, page_object: PageObject) -> str:
    """解析页面对象目标地址:优先 site_origin+page_path 拼接(去重斜杠),否则解析 url。

    优先拼接是为了修「web_url 带尾斜杠 + path 以 / 开头 → 双斜杠」:
    {{web_url}}=http://host/ + /#/home 拼出 http://host//#/home,path 变 //,
    SPA 路由异常会导致抽取落回登录页。url 原文路径也做 normalize 兜底。
    """
    origin_raw = (page_object.site_origin or "").strip()
    path_raw = (page_object.page_path or "").strip()
    if origin_raw and path_raw:
        origin = _resolve_or_400(origin_raw, db, user, "站点地址").strip()
        path = _resolve_or_400(path_raw, db, user, "页面路径").strip()
        return normalize_url(build_target_url(origin, path))
    if not page_object.url:
        raise HTTPException(status_code=400, detail="页面对象缺少目标地址,无法解析")
    return normalize_url(_resolve_or_400(page_object.url, db, user, "目标地址").strip())


def _login_overrides(body, db=None, user: UserInfo = None) -> dict:
    """本次执行的显式登录配置(弹窗填写),字段名与 LoginContext 对齐。

    login_url 支持 {{var}} 替换(传入 db/user 时解析),其余按原样透传;仅经内存交给
    runner,不落库、不写入任务状态(避免经 /tasks 泄露凭据)。
    """
    login_url = (getattr(body, "login_url", "") or "").strip()
    if db is not None and user is not None and login_url:
        login_url = normalize_url(_resolve_or_400(login_url, db, user, "登录入口").strip())
    return {
        "login_url": login_url,
        "login_method": (getattr(body, "login_method", "") or "").strip(),
        "username": getattr(body, "login_username", "") or "",
        "password": getattr(body, "login_password", "") or "",
        "company": (getattr(body, "login_company", "") or "").strip(),
    }


def _get_owned_page_object(db, page_object_id: int, user: UserInfo) -> PageObject:
    page_object = db.get(PageObject, page_object_id)
    if page_object is None or (page_object.user_id and page_object.user_id != user.user_id):
        raise HTTPException(status_code=404, detail="页面对象不存在")
    return page_object


# ---------------------------------------------------------------------------
# 鲁班平台菜单代理(按菜单生成目标 URL)
# ---------------------------------------------------------------------------

class LubanProxyRequest(BaseModel):
    base_url: str = ""
    access_token: str = ""
    res_id: str = ""


class LubanMenusRequest(LubanProxyRequest):
    sys_module_code: str = ""


def _luban_headers(body: LubanProxyRequest) -> dict:
    base = body.base_url.strip().rstrip("/")
    token = body.access_token.strip()
    if not base or not token:
        raise HTTPException(status_code=400, detail="base_url 与 access-token 不能为空")
    headers = {"access-token": token, "User-Agent": "ai-testcase-ui-automation"}
    if body.res_id.strip():
        headers["resId"] = body.res_id.strip()
    return headers


async def _luban_get(url: str, headers: dict, params: dict) -> list:
    try:
        # 内网环境常见自签证书,与前端 NODE_TLS_REJECT_UNAUTHORIZED=0 对齐
        async with httpx.AsyncClient(timeout=15, verify=False) as client:
            resp = await client.get(url, headers=headers, params=params)
        payload = resp.json()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"请求鲁班平台失败: {exc}")
    if not payload.get("success"):
        raise HTTPException(
            status_code=502,
            detail=f"鲁班平台返回错误: {payload.get('msg') or payload.get('code')}",
        )
    data = payload.get("data")
    return data if isinstance(data, list) else []


@router.post("/luban/modules", response_model=Response)
async def luban_modules(body: LubanProxyRequest, db: SessionDep, user: CurrentUser):
    """代理 GET /service/acl/biz/modules?type=web(系统模块列表)。base_url 支持 {{var}} 变量。"""
    body.base_url = _resolve_or_400(body.base_url, db, user, "base_url")
    headers = _luban_headers(body)
    base = body.base_url.strip().rstrip("/")
    data = await _luban_get(f"{base}/service/acl/biz/modules", headers, {"type": "web"})
    return Response(data=data)


@router.post("/luban/menus", response_model=Response)
async def luban_menus(body: LubanMenusRequest, db: SessionDep, user: CurrentUser):
    """代理 GET /service/acl/biz/module/menus?sysModuleCode=...(模块菜单扁平树)。base_url 支持 {{var}} 变量。"""
    body.base_url = _resolve_or_400(body.base_url, db, user, "base_url")
    headers = _luban_headers(body)
    if not body.sys_module_code.strip():
        raise HTTPException(status_code=400, detail="sys_module_code 不能为空")
    base = body.base_url.strip().rstrip("/")
    data = await _luban_get(
        f"{base}/service/acl/biz/module/menus",
        headers,
        {"sysModuleCode": body.sys_module_code.strip()},
    )
    return Response(data=data)


# ---------------------------------------------------------------------------
# 页面对象登录态:登录 = 一个配了执行步骤的页面对象,其他页面对象关联它复用
# ---------------------------------------------------------------------------

def _validated_login_page_object_id(db, user: UserInfo, raw: Optional[int]) -> Optional[int]:
    """关联校验:0/None 表示不关联;正数必须是本人的页面对象。"""
    if not raw:
        return None
    row = db.get(PageObject, raw)
    if row is None or (row.user_id and row.user_id != user.user_id):
        raise HTTPException(status_code=404, detail="登录页对象不存在")
    return int(raw)


def _resolve_login_page_object_id(
    db, user: UserInfo, explicit_id: Optional[int], page_object: Optional[PageObject] = None
) -> Optional[int]:
    """本次使用的登录页对象:请求显式指定 > 页面对象自身已关联。"""
    candidate = explicit_id if explicit_id is not None else (
        page_object.login_page_object_id if page_object else None
    )
    if not candidate:
        return None
    row = db.get(PageObject, candidate)
    if row is None or (row.user_id and row.user_id != user.user_id):
        raise HTTPException(status_code=404, detail="登录页对象不存在")
    return int(candidate)


@router.get("/page-objects/{page_object_id}/session", response_model=Response)
def get_page_object_session(page_object_id: int, db: SessionDep, user: CurrentUser):
    """该页面对象的登录态状态(不含 cookie/token 明文)。"""
    return Response(data=pl.session_view(_get_owned_page_object(db, page_object_id, user)))


@router.post("/page-objects/{page_object_id}/session/clear", response_model=Response)
def clear_page_object_session(page_object_id: int, db: SessionDep, user: CurrentUser):
    """清除该页面对象的登录态;下次使用时重新执行登录步骤建立。"""
    return Response(data=pl.clear_session(db, _get_owned_page_object(db, page_object_id, user)))


# ---------------------------------------------------------------------------
# 抽取
# ---------------------------------------------------------------------------

@router.post("/page-objects/extract", response_model=Response)
async def extract_page_object(body: ExtractRequest, db: SessionDep, user: CurrentUser):
    # 站点地址/页面路径支持 {{var}} 变量:执行用解析后的 URL,落库(新建页面对象)保留原文
    raw_origin = body.site_origin.strip()
    raw_path = body.page_path.strip()
    raw_url = body.url.strip() or build_target_url(raw_origin, raw_path)
    site_origin = _resolve_or_400(body.site_origin, db, user, "站点地址").strip()
    page_path = _resolve_or_400(body.page_path, db, user, "页面路径").strip()
    # origin+path 都有时优先拼接(自动去尾斜杠),避免 {{web_url}}=/ + path=/#/x 出双斜杠
    if site_origin and page_path:
        url = normalize_url(build_target_url(site_origin, page_path))
    else:
        url = normalize_url(_resolve_or_400(body.url, db, user, "页面地址").strip())
    if not url:
        raise HTTPException(status_code=400, detail="页面地址不能为空")

    run = UIAutomationRun(
        user_id=user.user_id,
        run_type="extract",
        target_url=url,
        status="RUNNING",
        result={
            "request": {
                "name": body.name.strip(),
                # 原文(可含 {{var}}):新建页面对象时按原文落库,换环境只需改全局参数
                "site_origin": raw_origin,
                "page_path": raw_path,
                "url": raw_url,
                "description": body.description.strip(),
                "use_llm_naming": body.use_llm_naming,
            }
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # 关联的登录配置:有可用登录态直接复用,没有就先登录再抽取(显式填写字段优先)
    # 本次显式不使用登录态(没选登录页对象)时不解析、不复用任何登录态,直接匿名访问
    login_page_object_id = (
        None
        if body.skip_login
        else _resolve_login_page_object_id(db, user, body.login_page_object_id)
    )
    if login_page_object_id:
        # 记录本次关联的登录页对象(只存 id,不存凭据),便于前端显示与回溯
        run.result = {**(run.result or {}), "login_page_object_id": login_page_object_id}
        db.add(run)
        db.commit()
    task_id = launch_extraction(
        run.id,
        login_overrides=_login_overrides(body, db, user),
        login_page_object_id=login_page_object_id,
        mark_login_page=bool(body.is_login_page),
        skip_login=bool(body.skip_login),
    )
    run.task_id = task_id
    db.add(run)
    db.commit()
    logger.info(f"[UIAuto] 用户 {user.username} 发起元素抽取 run={run.id} url={url}")
    return Response(data={"task_id": task_id, "run_id": run.id})


@router.post("/page-objects/{page_object_id}/regenerate", response_model=Response)
async def regenerate_page_object(page_object_id: int, body: RegenerateRequest, db: SessionDep, user: CurrentUser):
    """对已有页面对象重新抽取元素:刷新定位器,合并时保留用户编辑与自愈历史。"""
    page_object = _get_owned_page_object(db, page_object_id, user)
    if not page_object.url and not (page_object.site_origin and page_object.page_path):
        raise HTTPException(status_code=400, detail="页面对象缺少目标地址,无法重新生成")

    run = UIAutomationRun(
        page_object_id=page_object.id,
        user_id=user.user_id,
        run_type="extract",
        # 页面对象地址存原文(可含 {{var}}),执行时才按默认环境参数解析;优先 origin+path 拼接去双斜杠
        target_url=_resolve_target_url(db, user, page_object),
        status="RUNNING",
        result={
            "request": {
                "use_llm_naming": body.use_llm_naming,
                "merge": body.merge,
            }
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # 本次没选登录页对象(显式不使用登录态)时:既不回退页面对象自身已关联的登录页对象,
    # 也不复用该站点的通用缓存,直接匿名抽取
    login_page_object_id = (
        None
        if body.skip_login
        else _resolve_login_page_object_id(db, user, body.login_page_object_id, page_object)
    )
    task_id = launch_extraction(
        run.id,
        login_overrides=_login_overrides(body, db, user),
        login_page_object_id=login_page_object_id,
        mark_login_page=bool(body.is_login_page),
        skip_login=bool(body.skip_login),
    )
    run.task_id = task_id
    db.add(run)
    db.commit()
    logger.info(f"[UIAuto] 用户 {user.username} 重新生成页面对象 page_object={page_object.id} run={run.id}")
    return Response(data={"task_id": task_id, "run_id": run.id})


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------

def _task_from_run(run: UIAutomationRun) -> dict:
    """内存任务缺失(如服务重启)时,用 DB 运行记录还原任务终态供前端收尾。"""
    return {
        "task_id": run.task_id,
        "status": "done",
        "phase": "done",
        "progress": 100,
        "message": run.summary or run.error or "任务已结束",
        "record_id": run.id,
        "run_type": run.run_type,
        "result_status": run.status,
        "error": run.error,
        "page_object_id": run.page_object_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("/tasks/{task_id}", response_model=Response)
def get_automation_task(task_id: str, db: SessionDep, user: CurrentUser):
    task = get_po_task(task_id)
    if task is None:
        run = db.exec(
            select(UIAutomationRun).where(UIAutomationRun.task_id == task_id)
        ).first()
        if run is None or (run.user_id and run.user_id != user.user_id):
            raise HTTPException(status_code=404, detail="任务不存在或已过期")
        task = _task_from_run(run)
    return Response(data=task)


@router.post("/tasks/{task_id}/stop", response_model=Response)
def stop_automation_task(task_id: str, db: SessionDep, user: CurrentUser):
    task = get_po_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    run = db.get(UIAutomationRun, task.get("record_id"))
    if run is None or (run.user_id and run.user_id != user.user_id):
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if not cancel_po_task(task_id):
        return Response(data={"stopped": False, "message": "任务已结束"})
    return Response(data={"stopped": True, "message": "已请求停止"})


# ---------------------------------------------------------------------------
# PageObject CRUD
# ---------------------------------------------------------------------------

@router.get("/page-objects", response_model=Response)
def list_page_objects(db: SessionDep, user: CurrentUser):
    rows = db.exec(
        select(PageObject)
        .where(PageObject.user_id == user.user_id)
        .order_by(PageObject.id.desc())
    ).all()
    return Response(data=rows)


@router.post("/page-objects", response_model=Response)
def create_page_object(body: PageObjectPayload, db: SessionDep, user: CurrentUser):
    url = body.url.strip() or build_target_url(body.site_origin, body.page_path)
    # 幂等去重:同用户同 URL 已存在时直接返回已有记录,避免重复扫描产生重复页面对象
    existing = db.exec(
        select(PageObject)
        .where(PageObject.user_id == user.user_id)
        .where(PageObject.url == url)
    ).first()
    if existing is not None:
        return Response(data=existing)
    page_object = PageObject(
        name=body.name.strip() or "PageObject",
        site_origin=body.site_origin.strip(),
        page_path=body.page_path.strip(),
        url=url,
        description=body.description.strip(),
        elements=body.elements or [],
        steps=body.steps or [],
        is_login_page=bool(body.is_login_page),
        login_page_object_id=_validated_login_page_object_id(db, user, body.login_page_object_id),
        user_id=user.user_id,
    )
    db.add(page_object)
    db.commit()
    db.refresh(page_object)
    return Response(data=page_object)


@router.get("/page-objects/{page_object_id}", response_model=Response)
def get_page_object(page_object_id: int, db: SessionDep, user: CurrentUser):
    return Response(data=_get_owned_page_object(db, page_object_id, user))


@router.put("/page-objects/{page_object_id}", response_model=Response)
def update_page_object(page_object_id: int, body: PageObjectPayload, db: SessionDep, user: CurrentUser):
    page_object = _get_owned_page_object(db, page_object_id, user)
    page_object.name = body.name.strip() or page_object.name
    page_object.site_origin = body.site_origin.strip() or page_object.site_origin
    page_object.page_path = body.page_path.strip() or page_object.page_path
    page_object.url = body.url.strip() or page_object.url
    page_object.description = body.description.strip()
    if body.elements is not None:
        page_object.elements = body.elements
    # 登录配置关联:None=不改动;0=解除关联;正数=关联(校验归属)
    page_object.steps = body.steps or []
    page_object.is_login_page = bool(body.is_login_page)
    if body.login_page_object_id is not None:
        page_object.login_page_object_id = _validated_login_page_object_id(
            db, user, body.login_page_object_id
        )
    db.add(page_object)
    db.commit()
    db.refresh(page_object)
    return Response(data=page_object)


@router.delete("/page-objects/{page_object_id}", response_model=Response)
def delete_page_object(page_object_id: int, db: SessionDep, user: CurrentUser):
    page_object = _get_owned_page_object(db, page_object_id, user)
    db.delete(page_object)
    db.commit()
    return Response(data={"deleted": True})


# ---------------------------------------------------------------------------
# 验证 / 执行 / 导出
# ---------------------------------------------------------------------------

@router.post("/page-objects/{page_object_id}/validate", response_model=Response)
async def validate_page_object(
    page_object_id: int,
    db: SessionDep,
    user: CurrentUser,
    body: Optional[ValidateRequest] = None,
):
    page_object = _get_owned_page_object(db, page_object_id, user)
    if not page_object.url and not (page_object.site_origin and page_object.page_path):
        raise HTTPException(status_code=400, detail="页面对象缺少目标地址,无法验证")
    run = UIAutomationRun(
        page_object_id=page_object.id,
        user_id=user.user_id,
        run_type="validate",
        # 页面对象地址存原文(可含 {{var}}),执行时才按默认环境参数解析;优先 origin+path 拼接去双斜杠
        target_url=_resolve_target_url(db, user, page_object),
        status="RUNNING",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    login_page_object_id = _resolve_login_page_object_id(
        db, user, getattr(body, "login_page_object_id", None) if body else None, page_object
    )
    task_id = launch_validation(
        run.id,
        login_overrides=_login_overrides(body, db, user) if body else {},
        login_page_object_id=login_page_object_id,
    )
    run.task_id = task_id
    db.add(run)
    db.commit()
    return Response(data={"task_id": task_id, "run_id": run.id})


@router.post("/page-objects/{page_object_id}/run", response_model=Response)
async def run_page_object(page_object_id: int, body: RunRequest, db: SessionDep, user: CurrentUser):
    page_object = _get_owned_page_object(db, page_object_id, user)
    if not page_object.url and not (page_object.site_origin and page_object.page_path):
        raise HTTPException(status_code=400, detail="页面对象缺少目标地址,无法执行")
    # 步骤来源:本次提交的 > 页面对象上已保存的(登录页对象就是被这样「调用」的)
    steps = body.steps or list(page_object.steps or [])
    if not steps:
        raise HTTPException(status_code=400, detail="执行步骤不能为空")
    run = UIAutomationRun(
        page_object_id=page_object.id,
        user_id=user.user_id,
        run_type="execute",
        # 页面对象地址存原文(可含 {{var}}),执行时才按默认环境参数解析;优先 origin+path 拼接去双斜杠
        target_url=_resolve_target_url(db, user, page_object),
        status="RUNNING",
        steps=steps,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    login_page_object_id = _resolve_login_page_object_id(
        db, user, body.login_page_object_id, page_object
    )
    task_id = launch_execution(
        run.id,
        login_overrides=_login_overrides(body, db, user),
        login_page_object_id=login_page_object_id,
    )
    run.task_id = task_id
    db.add(run)
    db.commit()
    logger.info(f"[UIAuto] 用户 {user.username} 发起执行 page_object={page_object.id} run={run.id}")
    return Response(data={"task_id": task_id, "run_id": run.id})


@router.get("/page-objects/{page_object_id}/export")
def export_page_object(page_object_id: int, db: SessionDep, user: CurrentUser):
    page_object = _get_owned_page_object(db, page_object_id, user)
    code = render_page_object_code({
        "name": page_object.name,
        "url": page_object.url,
        "elements": page_object.elements or [],
    })
    file_name = suggest_file_name(page_object.name)
    return PlainTextResponse(
        content=code,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get("/runs", response_model=Response)
def list_runs(
    page_object_id: Optional[int] = None,
    db: SessionDep = None,
    user: CurrentUser = None,
):
    if page_object_id is None:
        raise HTTPException(status_code=400, detail="缺少 page_object_id 参数")
    rows = db.exec(
        select(UIAutomationRun)
        .where(UIAutomationRun.page_object_id == page_object_id)
        .where(UIAutomationRun.user_id == user.user_id)
        .order_by(UIAutomationRun.id.desc())
    ).all()
    return Response(data=rows)


@router.get("/runs/{run_id}", response_model=Response)
def get_run(run_id: int, db: SessionDep, user: CurrentUser):
    run = db.get(UIAutomationRun, run_id)
    if run is None or (run.user_id and run.user_id != user.user_id):
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return Response(data=run)
