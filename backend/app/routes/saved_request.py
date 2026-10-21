from typing import List, Optional

from fastapi import APIRouter, Query, status
from sqlalchemy import func
from sqlmodel import select

from app.deps import SessionDep, CurrentUser
from app.services.saved_request_layout import MAX_SORT_ORDER
from db.models import SavedRequest, ScheduledTask
from utils.base_response import Response

router = APIRouter(prefix="/saved-requests", tags=["saved-requests"])

# 侧栏顺序：先按拖拽得到的 sort_order，未拖拽过（NULL）的排在后面，同组内保持原来的更新时间倒序
_ORDER_KEY = func.coalesce(SavedRequest.sort_order, MAX_SORT_ORDER)


@router.get("", response_model=Response[List[SavedRequest]])
def get_saved_requests(
    session: SessionDep,
    user: CurrentUser,
    folder_id: Optional[int] = Query(None, description="按文件夹ID筛选，不传返回全部，-1返回未分类"),
):
    query = select(SavedRequest).order_by(_ORDER_KEY, SavedRequest.updated_at.desc())
    if folder_id is not None:
        if folder_id == -1:
            query = query.where(SavedRequest.folder_id.is_(None))
        else:
            query = query.where(SavedRequest.folder_id == folder_id)
    saved_requests = session.exec(query).all()
    return Response(data=saved_requests)


@router.post("", response_model=Response[SavedRequest])
def create_saved_request(session: SessionDep, user: CurrentUser, saved_request: SavedRequest):
    saved_request.user_id = user.user_id
    # 新请求排在所属分组末尾；分组内没有任何显式顺序时保持 NULL（沿用更新时间排序）
    sibling_max = session.exec(
        select(func.max(SavedRequest.sort_order)).where(
            SavedRequest.folder_id == saved_request.folder_id
            if saved_request.folder_id is not None
            else SavedRequest.folder_id.is_(None)
        )
    ).one()
    saved_request.sort_order = None if sibling_max is None else int(sibling_max) + 1
    session.add(saved_request)
    session.commit()
    session.refresh(saved_request)
    return Response(data=saved_request, message="请求配置已保存")


@router.put("/{saved_request_id}", response_model=Response[SavedRequest])
def update_saved_request(session: SessionDep, user: CurrentUser, saved_request_id: int, saved_request: SavedRequest):
    saved_request_db = session.get(SavedRequest, saved_request_id)
    if not saved_request_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="请求配置不存在")

    saved_request_data = saved_request.model_dump(exclude_unset=True)
    saved_request_data.pop("created_at", None)
    saved_request_data.pop("updated_at", None)
    saved_request_db.sqlmodel_update(saved_request_data)
    session.add(saved_request_db)
    session.commit()
    session.refresh(saved_request_db)
    return Response(data=saved_request_db, message="请求配置已更新")


@router.delete("/{saved_request_id}", response_model=Response)
def delete_saved_request(session: SessionDep, user: CurrentUser, saved_request_id: int):
    saved_request_db = session.get(SavedRequest, saved_request_id)
    if not saved_request_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="请求配置不存在")

    scheduled_tasks = session.exec(select(ScheduledTask)).all()
    referenced_tasks = []
    for task in scheduled_tasks:
        if saved_request_id in task.request_ids:
            referenced_tasks.append(task.name)

    if referenced_tasks:
        return Response(
            code=status.HTTP_400_BAD_REQUEST, 
            message="无法删除该请求配置，它被以下定时任务引用: {task_names}".format(
                task_names="\n".join(f"{index + 1}. {task}" for index, task in enumerate(referenced_tasks))
            )
        )

    session.delete(saved_request_db)
    session.commit()
    return Response(message="请求配置已删除")
