"""TDD for endpoint dependency -> create mapping and pre-step injection.

Uses in-memory DB (db_session fixture), no LLM.
"""
from app.services.testcase_service import map_endpoint_dependencies, ensure_prerequisite_steps
from db.models import ApiProject, ApiEndpoint
from utils.models import TestCase, ApiCallStep, ApiCallKeyValue, PostAction


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


def _build_case(ep_by_ordinal, *, with_create_preset=False):
    """endpoint_index_to_id = {1: create, 2: detail}。"""
    create, detail = ep_by_ordinal[1], ep_by_ordinal[2]
    endpoint_index_to_id = {1: create.id, 2: detail.id}
    presets = []
    if with_create_preset:
        presets.append(ApiCallStep(
            endpoint_ref=1, description="已存在的新增前置",
            post_actions=[PostAction(type="extract_jsonpath", key="record_id", jsonpath="$.data.id")],
        ))
    tc = TestCase(
        case_name="查询订单-成功",
        api_endpoint_ref=[2],
        steps=[ApiCallStep(endpoint_ref=2, description="查询订单")],
        preset_conditions=presets,
        expected_results=["返回订单详情"],
    )
    return tc, endpoint_index_to_id


def test_injects_create_preset_and_binds_id(db_session):
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders",
                 response_schema={"properties": {"data": {"properties": {"id": {"type": "integer"}}}}})
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    tc, e2i = _build_case({1: create, 2: detail})

    out = ensure_prerequisite_steps([tc], endpoint_index_to_id=e2i,
                                    db_session=db_session, api_project_id=p.id)
    case = out[0]
    api_call_presets = [s for s in case.preset_conditions if isinstance(s, ApiCallStep)]
    assert len(api_call_presets) == 1
    preset = api_call_presets[0]
    assert preset.endpoint_ref == 1
    assert preset.post_actions[0].key == "created_id"
    assert preset.post_actions[0].jsonpath == "$.data.id"
    dep_step = case.steps[0]
    id_param = next(x for x in dep_step.parameters if x.key == "id")
    assert id_param.value == "{{created_id}}"
    assert id_param.in_field == "path"


def test_no_injection_when_create_already_present(db_session):
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders",
                 response_schema={"properties": {"data": {"properties": {"id": {"type": "integer"}}}}})
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    tc, e2i = _build_case({1: create, 2: detail}, with_create_preset=True)

    out = ensure_prerequisite_steps([tc], endpoint_index_to_id=e2i,
                                    db_session=db_session, api_project_id=p.id)
    case = out[0]
    api_call_presets = [s for s in case.preset_conditions if isinstance(s, ApiCallStep)]
    # 不重复注入（仍只有模型已有的 1 个）
    assert len(api_call_presets) == 1
    # 绑定复用已有变量名 record_id
    dep_step = case.steps[0]
    id_param = next(x for x in dep_step.parameters if x.key == "id")
    assert id_param.value == "{{record_id}}"


def test_no_injection_for_create_own_case(db_session):
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders")
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    tc = TestCase(case_name="创建订单-成功", api_endpoint_ref=[1],
                  steps=[ApiCallStep(endpoint_ref=1, description="创建")],
                  preset_conditions=[], expected_results=["创建成功"])
    e2i = {1: create.id, 2: detail.id}
    out = ensure_prerequisite_steps([tc], endpoint_index_to_id=e2i,
                                    db_session=db_session, api_project_id=p.id)
    assert out[0].preset_conditions == []


def test_no_injection_when_no_candidate_create(db_session):
    p = _project(db_session)
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    tc = TestCase(case_name="查询订单-成功", api_endpoint_ref=[2],
                  steps=[ApiCallStep(endpoint_ref=2, description="查询")],
                  preset_conditions=[], expected_results=["返回详情"])
    e2i = {2: detail.id}
    out = ensure_prerequisite_steps([tc], endpoint_index_to_id=e2i,
                                    db_session=db_session, api_project_id=p.id)
    assert out[0].preset_conditions == []
