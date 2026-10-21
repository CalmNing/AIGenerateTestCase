"""保存的请求侧栏拖拽布局：sort_order 写回、跨文件夹移动、成环保护。

使用内存 SQLite（conftest.db_session），直接调用服务函数与路由函数，
覆盖 apply_layout 的核心语义而不是 HTTP 层。
"""
import pytest
from sqlmodel import select

import app.routes.saved_request as saved_request_mod
import app.routes.saved_request_folder as folder_mod
from app.services.saved_request_layout import LayoutError, apply_layout
from db.models import SavedRequest, SavedRequestFolder


def _folder(session, name: str, parent_id=None) -> SavedRequestFolder:
    folder = SavedRequestFolder(name=name, parent_id=parent_id)
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return folder


def _request(session, name: str, folder_id=None) -> SavedRequest:
    request = SavedRequest(name=name, folder_id=folder_id)
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


def _order(session, model, parent_field, parent_id):
    """取出某个父级下的 id 列表，顺序与 GET 接口一致：sort_order 升序，NULL 排最后。"""
    column = getattr(model, parent_field)
    condition = column.is_(None) if parent_id is None else column == parent_id
    rows = session.exec(select(model).where(condition)).all()
    rows.sort(key=lambda row: (row.sort_order is None, row.sort_order or 0))
    return [row.id for row in rows]


class TestApplyLayout:
    def test_reorders_folders_by_index(self, db_session):
        a = _folder(db_session, "A")
        b = _folder(db_session, "B")
        c = _folder(db_session, "C")

        apply_layout(db_session, [{"parent_id": None, "folder_ids": [c.id, a.id, b.id]}])

        db_session.expire_all()
        assert _order(db_session, SavedRequestFolder, "parent_id", None) == [c.id, a.id, b.id]
        assert (db_session.get(SavedRequestFolder, c.id).sort_order, db_session.get(SavedRequestFolder, a.id).sort_order) == (0, 1)

    def test_reorders_requests_inside_folder(self, db_session):
        folder = _folder(db_session, "目录")
        first = _request(db_session, "一", folder.id)
        second = _request(db_session, "二", folder.id)

        apply_layout(db_session, [{"parent_id": folder.id, "request_ids": [second.id, first.id]}])

        db_session.expire_all()
        assert _order(db_session, SavedRequest, "folder_id", folder.id) == [second.id, first.id]

    def test_moves_request_to_another_folder(self, db_session):
        source = _folder(db_session, "源")
        target = _folder(db_session, "目标")
        moved = _request(db_session, "请求", source.id)
        stay = _request(db_session, "另一个", source.id)

        apply_layout(db_session, [
            {"parent_id": source.id, "request_ids": [stay.id]},
            {"parent_id": target.id, "request_ids": [moved.id]},
        ])

        db_session.expire_all()
        assert db_session.get(SavedRequest, moved.id).folder_id == target.id
        assert db_session.get(SavedRequest, moved.id).sort_order == 0
        assert db_session.get(SavedRequest, stay.id).sort_order == 0

    def test_moves_request_back_to_root(self, db_session):
        folder = _folder(db_session, "目录")
        request = _request(db_session, "请求", folder.id)

        apply_layout(db_session, [{"parent_id": None, "request_ids": [request.id]}])

        db_session.expire_all()
        assert db_session.get(SavedRequest, request.id).folder_id is None

    def test_moves_folder_into_parent(self, db_session):
        parent = _folder(db_session, "父")
        child = _folder(db_session, "子")

        apply_layout(db_session, [{"parent_id": parent.id, "folder_ids": [child.id]}])

        db_session.expire_all()
        assert db_session.get(SavedRequestFolder, child.id).parent_id == parent.id

    def test_rejects_folder_into_own_subtree(self, db_session):
        outer = _folder(db_session, "外层")
        inner = _folder(db_session, "内层", outer.id)

        with pytest.raises(LayoutError):
            apply_layout(db_session, [{"parent_id": inner.id, "folder_ids": [outer.id]}])

        db_session.expire_all()
        assert db_session.get(SavedRequestFolder, outer.id).parent_id is None

    def test_rejects_folder_into_itself(self, db_session):
        folder = _folder(db_session, "自己")

        with pytest.raises(LayoutError):
            apply_layout(db_session, [{"parent_id": folder.id, "folder_ids": [folder.id]}])

    def test_rejects_unknown_parent(self, db_session):
        folder = _folder(db_session, "目录")

        with pytest.raises(LayoutError):
            apply_layout(db_session, [{"parent_id": 9999, "folder_ids": [folder.id]}])

    def test_rolls_back_every_group_on_error(self, db_session):
        folder_a = _folder(db_session, "A")
        folder_b = _folder(db_session, "B")
        moved = _folder(db_session, "B-子", folder_b.id)

        # 第一组合法、第二组非法：整体必须回滚，第一组的顺序不能落库
        with pytest.raises(LayoutError):
            apply_layout(db_session, [
                {"parent_id": None, "folder_ids": [folder_b.id, folder_a.id]},
                {"parent_id": moved.id, "folder_ids": [folder_b.id]},
            ])

        db_session.expire_all()
        assert db_session.get(SavedRequestFolder, folder_a.id).sort_order is None
        assert db_session.get(SavedRequestFolder, folder_b.id).sort_order is None

    def test_same_payload_twice_is_idempotent(self, db_session):
        a = _folder(db_session, "A")
        b = _folder(db_session, "B")
        payload = [{"parent_id": None, "folder_ids": [b.id, a.id]}]

        apply_layout(db_session, payload)
        apply_layout(db_session, payload)

        db_session.expire_all()
        assert _order(db_session, SavedRequestFolder, "parent_id", None) == [b.id, a.id]


class TestRoutesReadOrder:
    def test_get_requests_orders_by_sort_order_then_updated_at(self, db_session):
        first = _request(db_session, "first")
        second = _request(db_session, "second")
        apply_layout(db_session, [{"parent_id": None, "request_ids": [second.id, first.id]}])
        db_session.expire_all()

        res = saved_request_mod.get_saved_requests(session=db_session, user=None, folder_id=None)

        assert [row.id for row in res.data] == [second.id, first.id]

    def test_get_folders_orders_siblings_by_sort_order(self, db_session):
        a = _folder(db_session, "A")
        b = _folder(db_session, "B")
        apply_layout(db_session, [{"parent_id": None, "folder_ids": [b.id, a.id]}])
        db_session.expire_all()

        res = folder_mod.get_folders(session=db_session, user=None)

        assert [node["id"] for node in res.data] == [b.id, a.id]

    def test_create_request_appends_to_sorted_group(self, db_session):
        existing = _request(db_session, "旧")
        apply_layout(db_session, [{"parent_id": None, "request_ids": [existing.id]}])
        db_session.expire_all()

        created = saved_request_mod.create_saved_request(
            session=db_session,
            user=type("U", (), {"user_id": "u1"})(),
            saved_request=SavedRequest(name="新"),
        )

        assert created.data.sort_order == 1

    def test_create_folder_appends_to_sorted_group(self, db_session):
        existing = _folder(db_session, "旧")
        apply_layout(db_session, [{"parent_id": None, "folder_ids": [existing.id]}])
        db_session.expire_all()

        created = folder_mod.create_folder(
            session=db_session,
            user=type("U", (), {"user_id": "u1"})(),
            folder=SavedRequestFolder(name="新"),
        )

        assert created.data.sort_order == 1

    def test_create_request_without_layout_keeps_null_sort_order(self, db_session):
        created = saved_request_mod.create_saved_request(
            session=db_session,
            user=type("U", (), {"user_id": "u1"})(),
            saved_request=SavedRequest(name="新"),
        )

        # 未参与过拖拽的分组保持 NULL，继续沿用 updated_at 排序，不会被打乱
        assert created.data.sort_order is None
