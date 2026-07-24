from typing import List, Optional

from fastapi import APIRouter, status
from sqlmodel import select

from app.deps import SessionDep, CurrentUser
from db.models import SavedRequestFolder, SavedRequest
from utils.base_response import Response

router = APIRouter(prefix="/saved-request-folders", tags=["saved-request-folders"])


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
        .order_by(SavedRequestFolder.created_at)
    ).all()
    tree = _build_tree(flat)
    return Response(data=tree)


@router.post("", response_model=Response[SavedRequestFolder])
def create_folder(session: SessionDep, user: CurrentUser, folder: SavedRequestFolder):
    folder.user_id = user.user_id
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return Response(data=folder, message="文件夹已创建")


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
