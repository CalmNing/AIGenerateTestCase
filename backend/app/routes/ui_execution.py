"""UI 自动化执行路由(spec §9 API 契约)。

端点:
  POST /ui-execution/run               发起执行(建记录 + 后台任务)
  GET  /ui-execution/tasks/{task_id}   查询任务状态(前端轮询)
  POST /ui-execution/tasks/{task_id}/stop  停止执行(仅发起人)
  GET  /ui-execution/records           某用例的历史执行记录
"""
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import UserInfo
from app.deps import CurrentUser, SessionDep
from app.routes.proxy import build_param_map, substitute_variables
from app.services.browser_agent import (
    MAX_STEPS,
    MAX_STEPS_LIMIT,
    _ui_tasks,
    cancel_ui_execution,
    get_ui_task,
    launch_ui_execution,
)
from app.services.ui_steps import build_target_url, normalize_ui_steps, normalize_url, normalize_url
from db.models import TestCase, UIExecutionRecord
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ui-execution", tags=["ui-execution"])


class RunRequest(BaseModel):
    testcase_id: int
    site_origin: str = ""
    page_path: str = ""
    # 本次执行的显式登录配置(执行弹窗直接填写);留空字段回退到全局参数
    login_url: str = ""
    login_method: str = ""
    login_username: str = ""
    login_password: str = ""
    login_company: str = ""
    # 步数预算:0/空 回退后端默认 MAX_STEPS;超过 MAX_STEPS_LIMIT 由后端裁剪
    max_steps: int = 0


# ---------------------------------------------------------------------------
# 参数与鉴权助手
# ---------------------------------------------------------------------------

def _substitute(text: str, db: Session) -> str:
    """对输入做 {{var}} 变量替换(默认环境参数)。"""
    return substitute_variables(text or "", build_param_map(db, None, []), set())


_LEFTOVER_VAR = re.compile(r"\{\{([^}]+)\}\}")


def _resolve_or_400(text: str, db: Session, field: str) -> str:
    """解析 {{var}} 变量(默认环境参数);解析后仍残留占位符 = 变量未定义,400 快速失败。

    URL 字段额外做 normalize_url:web_url 带尾斜杠 + path 以 / 开头会拼出双斜杠。
    """
    resolved = normalize_url(_substitute(text, db))
    leftover = sorted(set(_LEFTOVER_VAR.findall(resolved)))
    if leftover:
        raise HTTPException(
            status_code=400, detail=f"{field}存在未定义的变量: {', '.join(leftover)}"
        )
    return resolved


def _require_owned_testcase(db: Session, user: UserInfo, testcase_id: int) -> TestCase:
    """取用例;不存在一律 404(不泄露资源是否存在)。有权限即可访问,不校验归属。"""
    testcase = db.get(TestCase, testcase_id)
    if testcase is None:
        raise HTTPException(status_code=404, detail="测试用例不存在")
    return testcase


def _require_owned_record(db: Session, user: UserInfo, record_id: Any) -> UIExecutionRecord:
    """取执行记录;失败统一按「任务不存在或已过期」处理。有权限即可访问。"""
    record = db.get(UIExecutionRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return record


# ---------------------------------------------------------------------------
# 请求归一化
# ---------------------------------------------------------------------------

def _validate_ui_case(testcase: TestCase) -> None:
    """前置校验:必须是含可执行 UI 步骤的用例(接口用例不支持 UI 执行)。"""
    actions, has_api = normalize_ui_steps(testcase.steps or [])
    if has_api:
        raise HTTPException(status_code=400, detail="接口测试用例不支持 UI 执行")
    if not actions:
        raise HTTPException(status_code=400, detail="用例无可执行的 UI 步骤")


def _build_login_overrides(body: RunRequest, db: Session) -> dict:
    """本次执行的显式登录配置(弹窗填写),字段名与 LoginContext 对齐。

    login_url 支持 {{var}} 替换,其余按原样透传;仅经内存交给 runner,不落库、
    不写入任务状态(避免经 /tasks 泄露凭据)。
    """
    return {
        "login_url": normalize_url(_resolve_or_400(body.login_url, db, "登录入口").strip()),
        "login_method": (body.login_method or "").strip(),
        "username": body.login_username or "",
        "password": body.login_password or "",
        "company": (body.login_company or "").strip(),
    }


def _resolve_max_steps(requested: int) -> int:
    """步数预算:0/空 回退后端默认;超过上限则裁剪。"""
    if not requested or requested <= 0:
        return MAX_STEPS
    return min(requested, MAX_STEPS_LIMIT)


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.post("/run", response_model=Response)
async def run_ui_execution(body: RunRequest, db: SessionDep, user: CurrentUser):
    testcase = _require_owned_testcase(db, user, body.testcase_id)
    _validate_ui_case(testcase)

    site_origin = _resolve_or_400(body.site_origin, db, "站点地址").strip()
    if not site_origin:
        raise HTTPException(status_code=400, detail="站点地址不能为空")
    target_url = normalize_url(
        build_target_url(site_origin, _resolve_or_400(body.page_path, db, "页面路径").strip())
    )

    record = UIExecutionRecord(
        testcase_id=testcase.id,
        user_id=user.user_id,
        target_url=target_url,
        status="RUNNING",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    # 凭据只经内存传给 runner,不落库、不写入任务状态(避免经 /tasks 泄露)
    login_overrides = _build_login_overrides(body, db)
    max_steps = _resolve_max_steps(body.max_steps)
    task_id = launch_ui_execution(
        db, record.id, login_overrides=login_overrides, max_steps=max_steps
    )
    logger.info(
        f"[UIExec] 用户 {user.username} 发起执行 record={record.id} url={target_url} "
        f"max_steps={max_steps} login_url={login_overrides['login_url'] or '(全局参数)'}"
    )
    return Response(data={"task_id": task_id, "record_id": record.id})


@router.get("/tasks/{task_id}", response_model=Response)
def get_ui_execution_task(task_id: str, db: SessionDep, user: CurrentUser):
    """查询任务状态;db/user 仅作鉴权依赖,不参与查询逻辑。"""
    task = get_ui_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return Response(data=task)


@router.post("/tasks/{task_id}/stop", response_model=Response)
def stop_ui_execution_task(task_id: str, db: SessionDep, user: CurrentUser):
    """停止正在执行的 UI 任务(仅发起人本人可停止)。"""
    task = get_ui_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    _require_owned_record(db, user, task.get("record_id"))

    if not cancel_ui_execution(task_id):
        return Response(data={"stopped": False, "message": "任务已结束"})
    logger.info(f"[UIExec] 用户 {user.username} 请求停止 task={task_id}")
    return Response(data={"stopped": True, "message": "已请求停止"})


@router.get("/records", response_model=Response)
def list_ui_execution_records(
    db: SessionDep, user: CurrentUser, testcase_id: Optional[int] = None
):
    """某用例的历史执行记录,按 id 倒序。有权限即可查看全部。"""
    if testcase_id is None:
        raise HTTPException(status_code=400, detail="缺少 testcase_id 参数")
    records = db.exec(
        select(UIExecutionRecord)
        .where(UIExecutionRecord.testcase_id == testcase_id)
        .order_by(UIExecutionRecord.id.desc())
    ).all()
    return Response(data=records)
