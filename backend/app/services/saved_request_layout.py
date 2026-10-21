"""保存的请求侧栏（文件夹树 + 请求）的拖拽布局持久化。

侧栏是一棵树：每个文件夹内部先列请求、再列子文件夹。因此「顺序」天然分成
两条互不干扰的列表——同级文件夹的顺序、同级请求的顺序，分别落在两张表各自的
`sort_order` 列上；而「归属」落在 `parent_id` / `folder_id` 上。

前端每次拖放提交「受影响父级的完整顺序」：

```json
[{"parent_id": 3, "folder_ids": [7, 9], "request_ids": [12]}]
```

后端按列表下标写回 `sort_order`，同时把这些节点移动到 `parent_id` 下，
所以「拖进别的文件夹」和「同级换位置」共用同一个接口。提交的是完整顺序而不是
「插到第几位」，同一份布局重复提交结果一致，前端重试也不会把顺序写乱。

`sort_order` 为空表示该行从未被拖拽过，读取时由路由层用
`coalesce(sort_order, MAX_SORT_ORDER)` 排到显式顺序之后。
"""
from typing import List, Optional, Sequence

from sqlmodel import Session

from db.models import SavedRequest, SavedRequestFolder

# 未拖拽过的行（sort_order 为 NULL）在 SQL 排序时统一排到显式顺序之后。
MAX_SORT_ORDER = 2**31 - 1


class LayoutError(ValueError):
    """布局非法：目标文件夹不存在，或把文件夹拖进了它自己的子树。"""


def _get(source: object, key: str, default=None):
    """同时兼容 dict 请求体与 Pydantic 对象。"""
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _is_in_subtree(session: Session, node_id: Optional[int], ancestor_id: int) -> bool:
    """node_id 是否位于 ancestor_id 的子树内（沿 parent 链上溯，不遍历全表）。"""
    seen = set()
    current = node_id
    while current is not None and current not in seen:
        if current == ancestor_id:
            return True
        seen.add(current)
        node = session.get(SavedRequestFolder, current)
        if node is None:
            return False
        current = node.parent_id
    return False


def _require_parent(session: Session, parent_id: Optional[int]) -> None:
    if parent_id is not None and session.get(SavedRequestFolder, parent_id) is None:
        raise LayoutError("目标文件夹不存在")


def apply_folder_order(session: Session, parent_id: Optional[int], ordered_ids: Sequence[int]) -> int:
    """把 parent_id 下的子文件夹顺序写为 ordered_ids，并把这些文件夹移动到 parent_id 下。"""
    folders: List[SavedRequestFolder] = []
    for index, folder_id in enumerate(ordered_ids):
        folder = session.get(SavedRequestFolder, folder_id)
        if folder is None:
            raise LayoutError(f"文件夹 {folder_id} 不存在")
        # 把文件夹拖进它自己的子树会形成环，树的渲染会无限递归，直接拒绝
        if parent_id is not None and _is_in_subtree(session, parent_id, folder_id):
            raise LayoutError("不能把文件夹移动到它自己或它的子文件夹内")
        folder.parent_id = parent_id
        folder.sort_order = index
        folders.append(folder)

    for folder in folders:
        session.add(folder)
    return len(folders)


def apply_request_order(session: Session, parent_id: Optional[int], ordered_ids: Sequence[int]) -> int:
    """把 parent_id 下的请求顺序写为 ordered_ids，并把这些请求移动到 parent_id 下。"""
    requests: List[SavedRequest] = []
    for index, request_id in enumerate(ordered_ids):
        request = session.get(SavedRequest, request_id)
        if request is None:
            raise LayoutError(f"请求 {request_id} 不存在")
        request.folder_id = parent_id
        request.sort_order = index
        requests.append(request)

    for request in requests:
        session.add(request)
    return len(requests)


def apply_group(session: Session, group: object) -> int:
    """应用一个父级的布局：先校验父级，再写文件夹顺序与请求顺序。"""
    parent_id = _get(group, "parent_id")
    _require_parent(session, parent_id)

    touched = 0
    folder_ids = _get(group, "folder_ids")
    if folder_ids is not None:
        touched += apply_folder_order(session, parent_id, folder_ids)
    request_ids = _get(group, "request_ids")
    if request_ids is not None:
        touched += apply_request_order(session, parent_id, request_ids)
    return touched


def apply_layout(session: Session, groups: Sequence[object]) -> int:
    """按顺序应用每个父级的布局；任何一组非法则整批回滚。"""
    touched = 0
    try:
        for group in groups:
            touched += apply_group(session, group)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return touched
