from typing import List, Optional

from fastapi import APIRouter, status
from sqlalchemy import func
from sqlmodel import select

from app.deps import SessionDep, CurrentUser
from app.services.saved_request_layout import MAX_SORT_ORDER, LayoutError, apply_layout
from db.models import SavedRequestFolder, SavedRequest
from utils.base_response import Response

router = APIRouter(prefix="/saved-request-folders", tags=["saved-request-folders"])

# 同级顺序：先按拖拽得到的 sort_order，未拖拽过（NULL）的排在后面，再按创建时间兜底
_ORDER_KEY = func.coalesce(SavedRequestFolder.sort_order, MAX_SORT_ORDER)


def _build_tree(flat: List[SavedRequestFolder]) -> List[dict]:
    by_id: dict[int, dict] = {}
    for f in flat:
        by_id[f.id] = {"id": f.id, "name": f.name, "parent_id": f.parent_id, "children": []}
    roots: list[dict] = []
    for f in flat:
        node = by_id[f.id]
        if f.parent_id and f.parent_id in by_id:
            by_id[f.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


@router.get("", response_model=Response[List[dict]])
def get_folders(session: SessionDep, user: CurrentUser):
    flat = session.exec(
        select(SavedRequestFolder)
        .order_by(_ORDER_KEY, SavedRequestFolder.created_at)
    ).all()
    tree = _build_tree(flat)
    return Response(data=tree)


@router.post("", response_model=Response[SavedRequestFolder])
def create_folder(session: SessionDep, user: CurrentUser, folder: SavedRequestFolder):
    folder.user_id = user.user_id
    # 新文件夹排在同级末尾；同级没有任何显式顺序时保持 NULL（沿用创建时间排序）
    sibling_max = session.exec(
        select(func.max(SavedRequestFolder.sort_order)).where(
            SavedRequestFolder.parent_id == folder.parent_id
            if folder.parent_id is not None
            else SavedRequestFolder.parent_id.is_(None)
        )
    ).one()
    folder.sort_order = None if sibling_max is None else int(sibling_max) + 1
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return Response(data=folder, message="文件夹已创建")


@router.put("/layout", response_model=Response)
def update_layout(session: SessionDep, user: CurrentUser, payload: List[dict]):
    """批量保存侧栏拖拽结果。

    请求体是分组列表，每组给出一个父级（`parent_id` 为 null 表示根层 /
    未分类）下全部子项的完整顺序：

    ```json
    [{"parent_id": 3, "folder_ids": [7, 9], "request_ids": [12]}]
    ```

    后端按列表下标写回 `sort_order`，同时把这些节点移动到 `parent_id` 下，
    所以「拖进别的文件夹」和「同级换位置」共用同一个接口。
    """
    try:
        touched = apply_layout(session, payload)
    except LayoutError as error:
        return Response(code=status.HTTP_400_BAD_REQUEST, message=str(error))
    return Response(data={"updated": touched}, message="排序已保存")


@router.put("/{folder_id}", response_model=Response[SavedRequestFolder])
def update_folder(session: SessionDep, user: CurrentUser, folder_id: int, folder: SavedRequestFolder):
    folder_db = session.get(SavedRequestFolder, folder_id)
    if not folder_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="文件夹不存在")
    folder_data = folder.model_dump(exclude_unset=True)
    folder_data.pop("created_at", None)
    folder_data.pop("updated_at", None)
    folder_db.sqlmodel_update(folder_data)
    session.add(folder_db)
    session.commit()
    session.refresh(folder_db)
    return Response(data=folder_db, message="文件夹已更新")


@router.delete("/{folder_id}", response_model=Response)
def delete_folder(session: SessionDep, user: CurrentUser, folder_id: int):
    folder_db = session.get(SavedRequestFolder, folder_id)
    if not folder_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="文件夹不存在")

    # Check for child folders
    children = session.exec(
        select(SavedRequestFolder).where(SavedRequestFolder.parent_id == folder_id)
    ).all()
    if children:
        return Response(
            code=status.HTTP_400_BAD_REQUEST,
            message="该文件夹下存在子文件夹，请先删除子文件夹",
        )

    # Check for saved requests in this folder
    requests_in_folder = session.exec(
        select(SavedRequest).where(SavedRequest.folder_id == folder_id).limit(1)
    ).all()
    if requests_in_folder:
        return Response(
            code=status.HTTP_400_BAD_REQUEST,
            message="该文件夹下存在保存的请求，请先移动或删除这些请求",
        )

    session.delete(folder_db)
    session.commit()
    return Response(message="文件夹已删除")
