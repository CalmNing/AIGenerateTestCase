"""Characterization tests for the API-context builder.

Phase 1.2 extracts these into app/services/api_context.py; the assertions
here must hold before and after that move. Uses in-memory DB, no LLM.
"""
from app.services.api_context import parse_endpoint_ids as _parse_endpoint_ids, build_api_context as _build_api_context
from db.models import ApiProject, ApiEndpoint


def test_parse_endpoint_ids_variants():
    assert _parse_endpoint_ids(None) == []
    assert _parse_endpoint_ids("") == []
    assert _parse_endpoint_ids(5) == [5]
    assert _parse_endpoint_ids("1,2,3") == [1, 2, 3]
    assert _parse_endpoint_ids(" 1 , 2 ,bad, 3 ") == [1, 2, 3]  # bad parts skipped
    assert _parse_endpoint_ids("nope") == []


def _make_project_with_endpoints(db_session):
    project = ApiProject(name="商城")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    ep1 = ApiEndpoint(
        project_id=project.id, name="创建用户", method="POST", path="/users",
        parameters=[{"key": "name", "value": "", "in": "query"}],
        body='{"name": "demo"}',
    )
    ep2 = ApiEndpoint(
        project_id=project.id, name="查询用户", method="GET", path="/users/{id}",
    )
    db_session.add(ep1)
    db_session.add(ep2)
    db_session.commit()
    db_session.refresh(ep1)
    db_session.refresh(ep2)
    return project, ep1, ep2


def test_build_api_context_empty_ids_returns_empty(db_session):
    ctx, index_map = _build_api_context(db_session, [], None, {})
    assert ctx == ""
    assert index_map == {}


def test_build_api_context_builds_sections_and_index_map(db_session):
    project, ep1, ep2 = _make_project_with_endpoints(db_session)

    ctx, index_map = _build_api_context(
        db_session, [ep1.id, ep2.id], project.id, {}
    )

    # index map is 1-based ordinal -> endpoint id
    assert index_map == {1: ep1.id, 2: ep2.id}
    # context contains the header and both endpoint sections with ordinals
    assert "API 接口信息" in ctx
    assert "[1] 创建用户" in ctx
    assert "[2] 查询用户" in ctx
    assert "请求方法: POST" in ctx
    assert "请求路径: /users/{id}" in ctx
    assert "所属项目: 商城" in ctx


def test_build_api_context_skips_missing_endpoint(db_session):
    project, ep1, _ = _make_project_with_endpoints(db_session)
    # 999999 does not exist -> skipped. ep1 is first and keeps ordinal 1.
    ctx, index_map = _build_api_context(
        db_session, [ep1.id, 999999], project.id, {}
    )
    assert index_map == {1: ep1.id}
    assert "[1] 创建用户" in ctx


def test_build_api_context_applies_overrides(db_session):
    project, ep1, _ = _make_project_with_endpoints(db_session)
    overrides = {ep1.id: {"body": '{"name": "overridden"}'}}
    ctx, _ = _build_api_context(db_session, [ep1.id], project.id, overrides)
    assert "overridden" in ctx
    assert "demo" not in ctx  # original body replaced


def test_build_api_context_includes_request_skeleton(db_session):
    from app.services.swagger_parser import build_body_from_schema
    project, ep1, _ = _make_project_with_endpoints(db_session)
    # 给 ep1 一个 request_schema
    ep1.request_schema = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "status": {"type": "string", "enum": ["active", "disabled"]},
        },
    }
    db_session.add(ep1)
    db_session.commit()
    ctx, _ = _build_api_context(db_session, [ep1.id], project.id, {})
    assert "请求骨架" in ctx
    assert "必填字段" in ctx
    assert "name" in ctx
    assert "enum" in ctx or "active" in ctx
