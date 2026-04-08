"""定时任务管理路由"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.deps import SessionDep
from db.models import ScheduledTask, SavedRequest
from utils.base_response import Response

router = APIRouter(prefix="/scheduled-tasks", tags=["scheduled-tasks"])


@router.get("", response_model=Response[List[ScheduledTask]])
def get_scheduled_tasks(session: SessionDep):
    """获取所有定时任务"""
    tasks = session.exec(select(ScheduledTask).order_by(ScheduledTask.updated_at.desc())).all()
    # 附加每个任务的请求名称信息
    result = []
    for task in tasks:
        task_dict = task.model_dump()
        request_names = []
        for rid in task.request_ids:
            sr = session.get(SavedRequest, rid)
            request_names.append({"id": rid, "name": sr.name if sr else f"Unknown({rid})"})
        task_dict["request_names"] = request_names
        result.append(task_dict)
    return Response(data=result)


@router.post("", response_model=Response[ScheduledTask])
def create_scheduled_task(session: SessionDep, task: ScheduledTask):
    """创建定时任务"""
    session.add(task)
    session.commit()
    session.refresh(task)

    # 注册到调度器
    from app.scheduler import add_job
    add_job(task)

    return Response(data=task, message="定时任务已创建")


@router.put("/{task_id}", response_model=Response[ScheduledTask])
def update_scheduled_task(session: SessionDep, task_id: int, task: ScheduledTask):
    """更新定时任务"""
    db_task = session.get(ScheduledTask, task_id)
    if not db_task:
        return Response(code=404, message="定时任务不存在")

    task_data = task.model_dump(exclude_unset=True)
    task_data.pop("created_at", None)
    task_data.pop("updated_at", None)
    task_data.pop("last_run_at", None)  # 避免前端传递的字符串格式导致错误
    db_task.sqlmodel_update(task_data)
    session.add(db_task)
    session.commit()
    session.refresh(db_task)

    # 更新调度器
    from app.scheduler import add_job, remove_job
    if db_task.enabled:
        add_job(db_task)
    else:
        remove_job(db_task.id)

    return Response(data=db_task, message="定时任务已更新")


@router.delete("/{task_id}", response_model=Response)
def delete_scheduled_task(session: SessionDep, task_id: int):
    """删除定时任务"""
    db_task = session.get(ScheduledTask, task_id)
    if not db_task:
        return Response(code=404, message="定时任务不存在")

    session.delete(db_task)
    session.commit()

    # 从调度器移除
    from app.scheduler import remove_job
    remove_job(task_id)

    return Response(message="定时任务已删除")


@router.post("/{task_id}/run", response_model=Response)
async def run_task_now(session: SessionDep, task_id: int):
    """手动触发一次定时任务"""
    db_task = session.get(ScheduledTask, task_id)
    if not db_task:
        return Response(code=404, message="定时任务不存在")

    from app.scheduler import execute_scheduled_task
    import asyncio
    asyncio.create_task(execute_scheduled_task(task_id, force_run=True))

    return Response(message="任务已触发执行")
