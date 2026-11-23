# -*- coding: utf-8 -*-
"""定时任务分组路由。

分组支持多级嵌套（parent_id 自引用），并可：
- 绑定一个执行环境（environment_id），组内任务默认继承；
- 配置分组级变量（variables），组内任务共享。
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func
from sqlmodel import select

from app.deps import SessionDep, CurrentUser
from db.models import TaskGroup, ScheduledTask
from utils.base_response import Response

router = APIRouter(prefix="/task-groups", tags=["task-groups"])

# 同级顺序：先按 sort_order（未设置排后面），再按创建时间
_ORDER_KEY = func.coalesce(TaskGroup.sort_order, 1000000)


def _build_tree(flat: List[TaskGroup]) -> List[dict]:
    """扁平分组 -> 树形结构。"""
    by_id: dict[int, dict] = {}
    for g in flat:
        by_id[g.id] = {
            "id": g.id,
            "name": g.name,
            "parent_id": g.parent_id,
            "environment_id": g.environment_id,
            "variables": g.variables or [],
            "children": [],
        }

    roots: list[dict] = []
    for g in flat:
        node = by_id[g.id]
        if g.parent_id and g.parent_id in by_id:
            by_id[g.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _collect_descendant_ids(session, group_id: int) -> list[int]:
    """收集一个分组及其所有子孙分组 id。"""
    result: list[int] = []
    frontier = [group_id]
    while frontier:
        current = frontier.pop()
        if current in result:
            continue
        result.append(current)
        children = session.exec(
            select(TaskGroup).where(TaskGroup.parent_id == current)
        ).all()
        frontier.extend(c.id for c in children)
    return result


@router.get("", response_model=Response[List[dict]])
def get_task_groups(session: SessionDep, user: CurrentUser):
    """获取分组树（含每个分组的任务数）。"""
    flat = session.exec(
        select(TaskGroup).order_by(_ORDER_KEY, TaskGroup.created_at)
    ).all()
    tree = _build_tree(flat)

    # 统计每个分组的直属任务数
    task_counts: dict[int, int] = {}
    for task in session.exec(select(ScheduledTask)).all():
        if task.group_id:
            task_counts[task.group_id] = task_counts.get(task.group_id, 0) + 1

    def _attach(nodes: list[dict]) -> None:
        for node in nodes:
            node["task_count"] = task_counts.get(node["id"], 0)
            _attach(node["children"])

    _attach(tree)
    return Response(data=tree)


@router.post("", response_model=Response[TaskGroup])
def create_task_group(session: SessionDep, user: CurrentUser, group: TaskGroup):
    """创建分组。"""
    if not (group.name or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="分组名称不能为空")
    if group.parent_id is not None and session.get(TaskGroup, group.parent_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父分组不存在")

    group.user_id = user.user_id
    group.name = group.name.strip()
    sibling_max = session.exec(
        select(func.max(TaskGroup.sort_order)).where(
            TaskGroup.parent_id == group.parent_id
            if group.parent_id is not None
            else TaskGroup.parent_id.is_(None)
        )
    ).one()
    group.sort_order = None if sibling_max is None else int(sibling_max) + 1

    session.add(group)
    session.commit()
    session.refresh(group)
    return Response(data=group, message="分组已创建")


@router.put("/{group_id}", response_model=Response[TaskGroup])
def update_task_group(session: SessionDep, user: CurrentUser, group_id: int, group: TaskGroup):
    """更新分组（名称/父分组/绑定环境/变量）。"""
    db_group = session.get(TaskGroup, group_id)
    if not db_group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")

    data = group.model_dump(exclude_unset=True)
    for key in ("id", "created_at", "updated_at", "user_id", "sort_order"):
        data.pop(key, None)

    if "parent_id" in data and data["parent_id"] is not None:
        if data["parent_id"] == group_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父分组不能是自己")
        if session.get(TaskGroup, data["parent_id"]) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父分组不存在")
        # 新父分组不能是自己或自己的子孙分组，否则会形成环
        descendants = _collect_descendant_ids(session, group_id)
        if data["parent_id"] in descendants:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父分组不能是自己的子分组")

    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="分组名称不能为空")
        data["name"] = name

    db_group.sqlmodel_update(data)
    session.add(db_group)
    session.commit()
    session.refresh(db_group)
    return Response(data=db_group, message="分组已更新")


@router.delete("/{group_id}", response_model=Response)
def delete_task_group(session: SessionDep, user: CurrentUser, group_id: int):
    """删除分组。分组下的任务与子分组一并处理。"""
    db_group = session.get(TaskGroup, group_id)
    if not db_group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")

    descendant_ids = _collect_descendant_ids(session, group_id)

    # 组内任务移出分组（不删除任务本身）
    for task in session.exec(select(ScheduledTask)).all():
        if task.group_id in descendant_ids:
            task.group_id = None
            session.add(task)

    # 删除子分组（先子后父，避免外键问题）
    for gid in sorted(descendant_ids, reverse=True):
        node = session.get(TaskGroup, gid)
        if node:
            session.delete(node)

    session.commit()
    return Response(message="分组已删除")
