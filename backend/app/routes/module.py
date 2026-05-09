from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from starlette import status

from app.deps import SessionDep, CurrentUser
from db.models import Module, TestCase
from utils.base_response import Response

router = APIRouter(prefix="/module", tags=["module"])


# 树形模块响应模型
class ModuleTree(BaseModel):
    """树形模块结构"""
    id: int
    module_name: str
    session_id: int
    parent_id: Optional[int] = None
    children: List["ModuleTree"] = []

    class Config:
        from_attributes = True


ModuleTree.model_rebuild()


def build_module_tree(modules: List[Module]) -> List[ModuleTree]:
    """构建模块树形结构"""
    module_map = {m.id: ModuleTree(
        id=m.id,
        module_name=m.module_name,
        session_id=m.session_id,
        parent_id=m.parent_id,
        children=[]
    ) for m in modules}
    
    root_modules = []
    for module in modules:
        module_tree = module_map[module.id]
        if module.parent_id is None:
            root_modules.append(module_tree)
        else:
            parent = module_map.get(module.parent_id)
            if parent:
                parent.children.append(module_tree)
    
    return root_modules


@router.get("/{session_id}/modules", response_model=Response[List[Module]])
def get_modules(session_id: int, session: SessionDep, user: CurrentUser):
    """获取模块（平铺列表）"""
    query = select(Module).where(Module.session_id == session_id)
    modules = session.exec(query).all()
    return Response(data=modules)


@router.get("/{session_id}/modules/tree", response_model=Response[List[ModuleTree]])
def get_modules_tree(session_id: int, session: SessionDep, user: CurrentUser):
    """获取模块树形结构"""
    query = select(Module).where(Module.session_id == session_id)
    modules = session.exec(query).all()
    tree = build_module_tree(list(modules))
    return Response(data=tree)


@router.get("/{module_id}", response_model=Response[Module])
def get_module(module_id: int, session: SessionDep, user: CurrentUser):
    """获取单个模块"""
    query = select(Module).where(Module.id == module_id)
    module = session.exec(query).first()
    return Response(data=module)


@router.post("/", response_model=Response[Module])
def create_module(module: Module, session: SessionDep, user: CurrentUser):
    """创建模块"""
    if module.parent_id:
        parent = session.get(Module, module.parent_id)
        if not parent:
            return Response(code=status.HTTP_400_BAD_REQUEST, message="父模块不存在")
        if parent.session_id != module.session_id:
            return Response(code=status.HTTP_400_BAD_REQUEST, message="父模块不属于当前会话")
    
    new_module = Module(
        module_name=module.module_name,
        session_id=module.session_id,
        parent_id=module.parent_id,
        user_id=user.user_id
    )
    session.add(new_module)
    session.commit()
    session.refresh(new_module)
    return Response(data=new_module)


@router.put("/{module_id}", response_model=Response[Module])
def update_module(module_id: int, module: Module, session: SessionDep, user: CurrentUser):
    """更新模块"""
    module_db = session.get(Module, module_id)
    if not module_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="模块不存在")
    
    if module.parent_id is not None:
        if module.parent_id == module_id:
            return Response(code=status.HTTP_400_BAD_REQUEST, message="不能将模块设为自己的子模块")
        if module.parent_id != 0:
            parent = session.get(Module, module.parent_id)
            if not parent:
                return Response(code=status.HTTP_400_BAD_REQUEST, message="父模块不存在")
            if parent.session_id != module_db.session_id:
                return Response(code=status.HTTP_400_BAD_REQUEST, message="父模块不属于当前会话")
    
    module_db.module_name = module.module_name
    if module.parent_id is not None:
        module_db.parent_id = module.parent_id if module.parent_id != 0 else None
    
    session.add(module_db)
    session.commit()
    session.refresh(module_db)
    return Response(data=module_db)


@router.delete("/{module_id}", response_model=Response)
def delete_module(module_id: int, session: SessionDep, user: CurrentUser):
    """删除模块"""
    module_db = session.get(Module, module_id)
    if not module_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="模块不存在，删除失败！")
    
    children = session.exec(select(Module).where(Module.parent_id == module_id)).first()
    if children:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模块下有子模块，请先删除子模块！")
    
    result = session.exec(select(TestCase).where(
        TestCase.module_id == module_id,
        TestCase.status != "NOT_RUN"
    )).first()
    if result:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模块下有已执行的用例，删除失败！")
    
    session.delete(module_db)
    session.commit()
    return Response()
