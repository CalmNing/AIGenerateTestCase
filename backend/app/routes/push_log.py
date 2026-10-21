from typing import List

from fastapi import APIRouter, Query
from sqlalchemy import delete as sa_delete
from sqlmodel import select, func, desc

from app.deps import SessionDep, CurrentUser
from db.models import PushLog
from utils.base_response import Response

router = APIRouter(prefix="/push-logs", tags=["push-logs"])


@router.get("", response_model=Response[List[PushLog]])
def get_push_logs(
    session: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    method: str | None = Query(default=None),
    search: str | None = Query(default=None),
    mocked: bool | None = Query(default=None),
    failed: bool | None = Query(default=None),
    protocol: str | None = Query(default=None),
):
    """获取推送请求记录列表，支持分页和 method/search/mocked/failed/protocol 过滤"""
    filters = []
    if method:
        filters.append(PushLog.request_method == method.upper())
    if protocol:
        # 协议精确过滤：http | mqtt
        # 直接调用路由函数（非 HTTP 请求）时默认值仍是 Query 对象，需先归一化
        protocol_value = protocol.strip().lower() if isinstance(protocol, str) else None
        if protocol_value == "http":
            # 兼容历史数据：自动迁移补列时旧记录的 protocol 为空字符串，语义上都是 HTTP
            filters.append((PushLog.protocol == "http") | (PushLog.protocol == ""))
        elif protocol_value:
            filters.append(PushLog.protocol == protocol_value)
    if search:
        filters.append(
            (PushLog.final_url.contains(search)) | (PushLog.source_url.contains(search))
        )
    if mocked is not None:
        filters.append(PushLog.mocked == mocked)
    if failed is not None:
        # 失败 = 转发出错(error 非空) 或 HTTP 状态码 >= 400
        failure = (PushLog.error.is_not(None)) | (PushLog.response_status >= 400)
        success = (PushLog.error.is_(None)) & (PushLog.response_status < 400)
        filters.append(failure if failed else success)

    total = session.exec(
        select(func.count(PushLog.id)).where(*filters)
    ).one()

    query = select(PushLog).where(*filters).order_by(desc(PushLog.created_at))
    offset = (page - 1) * page_size
    logs = session.exec(query.offset(offset).limit(page_size)).all()

    return Response(data=logs, pagination={"total": total, "page": page, "page_size": page_size})


@router.delete("", response_model=Response)
def clear_push_logs(session: SessionDep, user: CurrentUser):
    """清空全部推送请求记录"""
    total = session.exec(select(func.count(PushLog.id))).one()
    session.execute(sa_delete(PushLog))
    session.commit()
    return Response(message=f"已清空 {total} 条记录")


@router.get("/{log_id}", response_model=Response[PushLog])
def get_push_log(session: SessionDep, user: CurrentUser, log_id: int):
    """获取单条推送请求记录详情（原始 + 解析后）"""
    log = session.get(PushLog, log_id)
    if not log:
        return Response(code=404, message="记录不存在")
    return Response(data=log)


@router.delete("/{log_id}", response_model=Response)
def delete_push_log(session: SessionDep, user: CurrentUser, log_id: int):
    """删除单条推送请求记录"""
    log = session.get(PushLog, log_id)
    if not log:
        return Response(code=404, message="记录不存在")
    session.delete(log)
    session.commit()
    return Response(message="记录已删除")
