from typing import List, Optional

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.deps import CurrentUser, SessionDep
from db.models import NavCategory, NavLink
from utils.base_response import Response

router = APIRouter(prefix="/nav-links", tags=["nav-links"])


# ===== 只读：分类 + 链接树（全员可用） =====

@router.get("")
def get_nav_tree(session: SessionDep, user: CurrentUser):
    """返回所有分类及其下属链接，分类与链接均按 sort_order 升序。

    返回结构：[{ "category": NavCategory, "links": [NavLink, ...] }, ...]
    """
    categories = session.exec(select(NavCategory).order_by(NavCategory.sort_order)).all()
    tree = []
    for cat in categories:
        links = session.exec(
            select(NavLink).where(NavLink.category_id == cat.id).order_by(NavLink.sort_order)
        ).all()
        tree.append({"category": cat, "links": links})
    return Response(data=tree)


# ===== 分类管理（需 CONFIG_MANAGE） =====

@router.post("/categories", response_model=Response[NavCategory])
def create_category(payload: NavCategory, session: SessionDep, user: CurrentUser):
    session.add(payload)
    session.commit()
    session.refresh(payload)
    return Response(data=payload)


@router.put("/categories/{category_id}", response_model=Response[NavCategory])
def update_category(category_id: int, payload: NavCategory, session: SessionDep, user: CurrentUser):
    db = session.get(NavCategory, category_id)
    if not db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="分类不存在")
    db.name = payload.name
    if payload.icon is not None:
        db.icon = payload.icon
    db.sort_order = payload.sort_order
    session.add(db)
    session.commit()
    session.refresh(db)
    return Response(data=db)


@router.delete("/categories/{category_id}", response_model=Response)
def delete_category(category_id: int, session: SessionDep, user: CurrentUser):
    db = session.get(NavCategory, category_id)
    if not db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="分类不存在")
    # 级联删除下属链接（关系上配置了 cascade="all, delete-orphan"）
    session.delete(db)
    session.commit()
    return Response()


# ===== 链接管理（需 CONFIG_MANAGE） =====

@router.post("/links", response_model=Response[NavLink])
def create_link(payload: NavLink, session: SessionDep, user: CurrentUser):
    cat = session.get(NavCategory, payload.category_id)
    if not cat:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="所属分类不存在")
    session.add(payload)
    session.commit()
    session.refresh(payload)
    return Response(data=payload)


@router.put("/links/{link_id}", response_model=Response[NavLink])
def update_link(link_id: int, payload: NavLink, session: SessionDep, user: CurrentUser):
    db = session.get(NavLink, link_id)
    if not db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="链接不存在")
    cat = session.get(NavCategory, payload.category_id)
    if not cat:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="所属分类不存在")
    db.category_id = payload.category_id
    db.title = payload.title
    db.url = payload.url
    if payload.description is not None:
        db.description = payload.description
    if payload.icon is not None:
        db.icon = payload.icon
    db.open_type = payload.open_type
    db.sort_order = payload.sort_order
    session.add(db)
    session.commit()
    session.refresh(db)
    return Response(data=db)


@router.delete("/links/{link_id}", response_model=Response)
def delete_link(link_id: int, session: SessionDep, user: CurrentUser):
    db = session.get(NavLink, link_id)
    if not db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="链接不存在")
    session.delete(db)
    session.commit()
    return Response()
