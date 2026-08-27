from typing import List

from fastapi import APIRouter, Query
from sqlalchemy import and_, delete as sa_delete, or_
from sqlmodel import select, func, desc

from app.deps import SessionDep, CurrentUser
from db.models import MockLog
from utils.base_response import Response

router = APIRouter(prefix="/mock-logs", tags=["mock-logs"])

# 与前端结果标签一致的来源分类（按 config_name 前缀推导，优先级同前端）
_PROXY_MOCK_PREFIX = "[代理→Mock%"
_PROXY_PASS_PREFIXES = ["[代理透传%", "[代理→透传%"]
_PROJECT_PREFIX = "[项目Mock]%"


def _source_condition(source: str):
    if source == "proxy_mock":
        return MockLog.config_name.like(_PROXY_MOCK_PREFIX)
    if source == "passthrough":
        return or_(*[MockLog.config_name.like(p) for p in _PROXY_PASS_PREFIXES])
    if source == "project_mock":
        return MockLog.config_name.like(_PROJECT_PREFIX)
    if source == "missed":
        return and_(
            MockLog.matched == False,
            ~MockLog.config_name.like(_PROXY_MOCK_PREFIX),
            *[~MockLog.config_name.like(p) for p in _PROXY_PASS_PREFIXES],
        )
    if source == "mock":
        return and_(
            MockLog.matched == True,
            ~MockLog.config_name.like(_PROXY_MOCK_PREFIX),
            ~MockLog.config_name.like(_PROJECT_PREFIX),
        )
    return None


@router.get("", response_model=Response[List[MockLog]])
def get_mock_logs(
    session: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    config_id: int | None = Query(default=None),
    matched: bool | None = Query(default=None),
    method: str | None = Query(default=None),
    search: str | None = Query(default=None),
    source: str | None = Query(default=None),
):
    """获取Mock请求日志列表，支持分页和按 config_id/matched/method/search/source 过滤"""
    filters = []
    if config_id is not None:
        filters.append(MockLog.config_id == config_id)
    if matched is not None:
        filters.append(MockLog.matched == matched)
    if method:
        filters.append(MockLog.request_method == method.upper())
    if search:
        filters.append(MockLog.request_path.contains(search))
    source_cond = _source_condition(source) if source else None
    if source_cond is not None:
        filters.append(source_cond)

    total = session.exec(
        select(func.count(MockLog.id)).where(*filters)
    ).one()

    query = select(MockLog).where(*filters).order_by(desc(MockLog.created_at))
    offset = (page - 1) * page_size
    logs = session.exec(query.offset(offset).limit(page_size)).all()

    return Response(data=logs, pagination={"total": total, "page": page, "page_size": page_size})


@router.delete("", response_model=Response)
def clear_mock_logs(session: SessionDep, user: CurrentUser):
    """清空全部Mock请求日志"""
    total = session.exec(select(func.count(MockLog.id))).one()
    session.execute(sa_delete(MockLog))
    session.commit()
    return Response(message=f"已清空 {total} 条日志")


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
