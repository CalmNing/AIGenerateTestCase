"""UI 自动化执行路由(spec §9 API 契约)。"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.auth import UserInfo
from app.deps import CurrentUser, SessionDep
from app.services.browser_agent import _ui_tasks, get_ui_task, launch_ui_execution
from app.services.ui_steps import build_target_url, normalize_ui_steps
from db.models import TestCase, UIExecutionRecord
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
    testcase_id: Optional[int] = None,
    db: SessionDep = None,
    user: CurrentUser = None,
):
    if testcase_id is None:
        raise HTTPException(status_code=400, detail="缺少 testcase_id 参数")
    records = db.exec(
        select(UIExecutionRecord)
        .where(UIExecutionRecord.testcase_id == testcase_id)
        .order_by(UIExecutionRecord.id.desc())
    ).all()
    return Response(data=records)
