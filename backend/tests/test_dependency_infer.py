"""Characterization tests for endpoint dependency inference.

infer_endpoint_dependencies auto-completes a missing prerequisite "create"
(POST) endpoint when a selected endpoint depends on an id it cannot produce.
Uses in-memory DB, no LLM.
"""
from app.routes.testcase import infer_endpoint_dependencies
from db.models import ApiProject, ApiEndpoint


def _project(db_session):
    p = ApiProject(name="proj")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _add_endpoint(db_session, project_id, **kwargs):
    ep = ApiEndpoint(project_id=project_id, **kwargs)
    db_session.add(ep)
    db_session.commit()
    db_session.refresh(ep)
    return ep


def test_returns_empty_without_project(db_session):
    assert infer_endpoint_dependencies(db_session, [1, 2], None) == []


def test_returns_empty_without_selected_ids(db_session):
    p = _project(db_session)
    assert infer_endpoint_dependencies(db_session, [], p.id) == []


def test_returns_empty_when_no_endpoints(db_session):
    p = _project(db_session)
    assert infer_endpoint_dependencies(db_session, [123], p.id) == []


def test_infers_missing_create_endpoint_for_path_id_dependency(db_session):
    p = _project(db_session)
    # A POST create endpoint (NOT selected) that the dependency could come from
    create_ep = _add_endpoint(
        db_session, p.id, name="创建订单", method="POST", path="/orders",
    )
    # A selected GET endpoint whose path needs {id} -> depends on a create
    get_ep = _add_endpoint(
        db_session, p.id, name="查询订单", method="GET", path="/orders/{id}",
    )

    result = infer_endpoint_dependencies(db_session, [get_ep.id], p.id)
    assert create_ep.id in result


def test_no_inference_when_create_already_selected(db_session):
    p = _project(db_session)
    create_ep = _add_endpoint(
        db_session, p.id, name="创建订单", method="POST", path="/orders",
    )
    get_ep = _add_endpoint(
        db_session, p.id, name="查询订单", method="GET", path="/orders/{id}",
    )
    # Both selected -> create already present -> nothing to infer
    result = infer_endpoint_dependencies(
        db_session, [create_ep.id, get_ep.id], p.id
    )
    assert result == []


def test_no_inference_when_no_candidate_create_exists(db_session):
    p = _project(db_session)
    get_ep = _add_endpoint(
        db_session, p.id, name="查询订单", method="GET", path="/orders/{id}",
    )
    # No POST create endpoint at all
    result = infer_endpoint_dependencies(db_session, [get_ep.id], p.id)
    assert result == []


def test_accepts_string_project_id(db_session):
    p = _project(db_session)
    create_ep = _add_endpoint(
        db_session, p.id, name="创建订单", method="POST", path="/orders",
    )
    get_ep = _add_endpoint(
        db_session, p.id, name="查询订单", method="GET", path="/orders/{id}",
    )
    # project id passed as a string should still resolve (int() coercion)
    result = infer_endpoint_dependencies(db_session, [get_ep.id], str(p.id))
    assert create_ep.id in result


def test_invalid_string_project_id_returns_empty(db_session):
    assert infer_endpoint_dependencies(db_session, [1], "not-a-number") == []
