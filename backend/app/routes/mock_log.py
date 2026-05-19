from typing import List

from fastapi import APIRouter, Query
from sqlmodel import select, func, desc

from app.deps import SessionDep, CurrentUser
from db.models import MockLog
from utils.base_response import Response

router = APIRouter(prefix="/mock-logs", tags=["mock-logs"])


@router.get("", response_model=Response[List[MockLog]])
def get_mock_logs(
    session: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    config_id: int | None = Query(default=None),
    matched: bool | None = Query(default=None),
):
    """获取Mock请求日志列表，支持分页和按config_id/matched过滤"""
    filters = []
    if config_id is not None:
        filters.append(MockLog.config_id == config_id)
    if matched is not None:
        filters.append(MockLog.matched == matched)

    total = session.exec(
        select(func.count(MockLog.id)).where(*filters)
    ).one()

    query = select(MockLog).where(*filters).order_by(desc(MockLog.created_at))
    offset = (page - 1) * page_size
    logs = session.exec(query.offset(offset).limit(page_size)).all()

    return Response(data=logs, pagination={"total": total, "page": page, "page_size": page_size})


@router.get("/{log_id}", response_model=Response[MockLog])
def get_mock_log(session: SessionDep, user: CurrentUser, log_id: int):
    """获取单条Mock请求日志详情"""
    log = session.get(MockLog, log_id)
    if not log:
        return Response(code=404, message="日志不存在")
    return Response(data=log)


@router.delete("/{log_id}", response_model=Response)
def delete_mock_log(session: SessionDep, user: CurrentUser, log_id: int):
    """删除Mock请求日志"""
    log = session.get(MockLog, log_id)
    if not log:
        return Response(code=404, message="日志不存在")
    session.delete(log)
    session.commit()
    return Response(message="日志已删除")
