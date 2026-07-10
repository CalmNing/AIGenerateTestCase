"""TDD for endpoint dependency -> create mapping and pre-step injection.

Uses in-memory DB (db_session fixture), no LLM.
"""
from app.services.testcase_service import map_endpoint_dependencies
from db.models import ApiProject, ApiEndpoint


def _project(db_session):
    p = ApiProject(name="proj")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _ep(db_session, project_id, **kwargs):
    ep = ApiEndpoint(project_id=project_id, **kwargs)
    db_session.add(ep)
    db_session.commit()
    db_session.refresh(ep)
    return ep


def test_map_returns_create_for_path_id_dependency(db_session):
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders",
                 response_schema={"properties": {"data": {"properties": {"id": {"type": "integer"}}}}})
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    mapping = map_endpoint_dependencies(db_session, [create.id, detail.id], p.id)
    assert mapping.get(detail.id) == create.id


def test_map_empty_when_no_candidate_create(db_session):
    p = _project(db_session)
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    assert map_endpoint_dependencies(db_session, [detail.id], p.id) == {}


def test_map_empty_for_list_collection(db_session):
    # 列表查询（无 {id}）不应被判定为依赖
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders")
    lst = _ep(db_session, p.id, name="订单列表", method="GET", path="/orders")
    assert map_endpoint_dependencies(db_session, [create.id, lst.id], p.id) == {}
