from typing import List, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from starlette import status

from app.deps import SessionDep
from db.models import Session, TestCase, StatusValue
from utils.base_response import Response


# 请求模型
class CreateSessionRequest(BaseModel):
    name: str


class UpdateSessionRequest(CreateSessionRequest):
    ...


router = APIRouter(prefix="/sessions", tags=["session"])


# 会话管理API
@router.get("/", response_model=Response[List[Session]])
def get_sessions(session: SessionDep):
    """获取所有会话"""
    sessions = session.exec(select(Session)).all()
    return Response(data=sessions)


@router.post("/", response_model=Response)
def create_session(create: CreateSessionRequest, session: SessionDep):
    """创建新会话"""
    new_session = Session(name=create.name)
    session.add(new_session)
    session.commit()
    session.refresh(new_session)
    return Response(data=new_session)


@router.put("/{session_id}", response_model=Response[int])
def update_session(session_id: int, update: UpdateSessionRequest, session: SessionDep):
    """更新会话"""
    session_db = session.get(Session, session_id)
    if not session_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message='会话不存在！')
    session_data = update.model_dump(exclude_unset=True)
    session_db.sqlmodel_update(session_data)
    session.add(session_db)
    session.commit()
    session.refresh(session_db)
    return Response(data=session_id)


@router.delete("/{session_id}", response_model=Response[Union[int, str]])
def delete_session(session_id: int, session: SessionDep):
    """删除会话"""
    session_status = session.exec(
        select(TestCase.status).where(
        TestCase.session_id == session_id,
            TestCase.status != StatusValue.NOT_RUN
        )
    ).first()
    if session_status:
        return Response(code=status.HTTP_400_BAD_REQUEST,message="当前会话下存在已执行的测试用例，删除失败！")
    sess = session.get(Session, session_id)
    if not sess:
        return Response(code=status.HTTP_404_NOT_FOUND,message="会话不存在！")
    session.delete(sess)
    session.commit()
    return Response(data=session_id)
