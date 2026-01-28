from typing import List

from fastapi import APIRouter, HTTPException
from sqlmodel import select
from starlette import status

from app.deps import SessionDep
from db.models import Module, TestCase
from utils.base_response import Response

router = APIRouter(prefix="/module", tags=["module"])


@router.get("/{session_id}/modules", response_model=Response[List[Module]])
def get_modules(session_id: int, session: SessionDep):
    """获取模块"""
    query = select(Module).where(Module.session_id == session_id)
    modules = session.exec(query).all()
    return Response(data=modules)


@router.get("/{module_id}", response_model=Response[Module])
def get_modules(module_id: int, session: SessionDep):
    """获取模块"""
    query = select(Module).where(Module.id == module_id)
    module = session.exec(query).first()
    return Response(data=module)


@router.post("/", response_model=Response[Module])
def create_module(module: Module, session: SessionDep):
    """创建模块"""
    # 创建新模块实例，不使用客户端提供的日期时间值
    new_module = Module(
        module_name=module.module_name,
        session_id=module.session_id
    )
    session.add(new_module)
    session.commit()
    session.refresh(new_module)
    return Response(data=new_module)


@router.put("/{module_id}", response_model=Response[Module])
def update_module(module_id: int, module: Module, session: SessionDep):
    """更新模块"""
    module_db = session.get(Module, module_id)
    if not module_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="模块不存在")
    module_db.module_name = module.module_name
    session.add(module_db)
    session.commit()
    session.refresh(module_db)
    return Response(data=module_db)

@router.delete("/{module_id}", response_model=Response)
def delete_module(module_id: int, session: SessionDep):
    """删除模块"""
    module_db = session.get(Module, module_id)
    if not module_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="模块不存在，删除失败！")
    result = session.exec(select(TestCase).where(
        TestCase.module_id == module_id,
        TestCase.status != "NOT_RUN"
    )).first()
    if result:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, message="模块下有已执行的用例，删除失败！")
    session.delete(module_db)
    session.commit()
    return Response()