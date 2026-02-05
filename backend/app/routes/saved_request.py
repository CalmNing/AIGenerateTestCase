from typing import List, Optional

from fastapi import APIRouter, status
from sqlalchemy import delete
from sqlmodel import select

from app.deps import SessionDep
from db.models import SavedRequest
from utils.base_response import Response

router = APIRouter(prefix="/saved-requests", tags=["saved-requests"])


# 获取所有保存的请求配置
@router.get("", response_model=Response[List[SavedRequest]])
def get_saved_requests(session: SessionDep):
    """获取所有保存的请求配置"""
    saved_requests = session.exec(select(SavedRequest).order_by(SavedRequest.updated_at.desc())).all()
    return Response(data=saved_requests)


# 创建保存的请求配置
@router.post("", response_model=Response[SavedRequest])
def create_saved_request(session: SessionDep, saved_request: SavedRequest):
    """创建保存的请求配置"""
    session.add(saved_request)
    session.commit()
    session.refresh(saved_request)
    return Response(data=saved_request, message="请求配置已保存")


# 更新保存的请求配置
@router.put("/{saved_request_id}", response_model=Response[SavedRequest])
def update_saved_request(session: SessionDep, saved_request_id: int, saved_request: SavedRequest):
    """更新保存的请求配置"""
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


# 删除保存的请求配置
@router.delete("/{saved_request_id}", response_model=Response)
def delete_saved_request(session: SessionDep, saved_request_id: int):
    """删除保存的请求配置"""
    saved_request_db = session.get(SavedRequest, saved_request_id)
    if not saved_request_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="请求配置不存在")

    session.delete(saved_request_db)
    session.commit()
    return Response(message="请求配置已删除")
