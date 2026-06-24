from typing import List, Optional

import httpx
from fastapi import APIRouter, File, Form, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.deps import CurrentUser, SessionDep
from app.services.api_test_tool import (
    create_unit_test_scenario,
    detect_base_url,
    endpoints_from_spec,
    generate_body_from_schema,
    infer_step_dependencies,
    parse_spec_text,
    run_endpoint,
    run_scenario,
    sync_project_from_spec,
)
from db.models import ApiEndpoint, ApiProject, ApiScenario, ApiScenarioResult
from utils.base_response import Response

router = APIRouter(prefix="/api-test", tags=["api-test"])
MAX_SCENARIO_RESULT_RECORDS = 10


class ImportResult(BaseModel):
    project: ApiProject
    endpoints: List[ApiEndpoint]


class ImportOpenApiRequest(BaseModel):
    name: str = ""
    url: str = ""


class RunEndpointRequest(BaseModel):
    base_url: Optional[str] = None
    project_headers: Optional[List[dict]] = None
    environment_id: Optional[int] = None
    variables: List[dict] = Field(default_factory=list)
    name: Optional[str] = None
    method: Optional[str] = None
    path: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[List[dict]] = None
    parameters: Optional[List[dict]] = None
    body: Optional[str] = None
    pre_actions: Optional[List[dict]] = None
    post_actions: Optional[List[dict]] = None
    assertions: Optional[List[dict]] = None


class GenerateBodyRequest(BaseModel):
    instruction: str = ""
    current_body: str = ""
    environment_id: Optional[int] = None
    model_type: str = "api"
    api_key: str = ""
    api_base_url: str = ""
    api_proxy_url: str = ""
    api_model: str = "deepseek-v4-flash"
    ollama_url: str = ""
    ollama_model: str = ""



class MatchEndpointRequest(BaseModel):
    requirement: str
    project_id: Optional[int] = None


def _calculate_match_score(requirement: str, endpoint: ApiEndpoint) -> float:
    """Calculate a relevance score between requirement text and an API endpoint."""
    score = 0.0
    req_lower = requirement.lower()

    # Exact match on endpoint name (highest)
    if endpoint.name and endpoint.name.lower() in req_lower:
        score += 50.0

    # Match on path segments
    if endpoint.path:
        path_segments = endpoint.path.strip("/").replace("-", " ").replace("_", " ").split("/")
        for seg in path_segments:
            if seg and seg in req_lower:
                score += 10.0

    # Match on method
    if endpoint.method and endpoint.method.lower() in req_lower:
        score += 5.0

    # Match on tags
    if endpoint.tags:
        for tag in endpoint.tags:
            if tag.lower() in req_lower:
                score += 15.0

    # Match on summary/description
    summary = endpoint.summary or endpoint.description or ""
    if summary:
        summary_lower = summary.lower()
        req_words = set(req_lower.split())
        summary_words = set(summary_lower.split())
        common = req_words & summary_words
        score += len(common) * 3.0

    return score


@router.post("/match-endpoint", response_model=Response)
def match_endpoint(
    session: SessionDep,
    user: CurrentUser,
    request: MatchEndpointRequest,
):
    """Smart-match requirement text to API endpoints using keyword scoring."""
    query = select(ApiEndpoint)
    if request.project_id:
        project = session.get(ApiProject, request.project_id)
        if not project or project.user_id != user.user_id:
            return Response(code=status.HTTP_404_NOT_FOUND, message="项目不存在")
        query = query.where(ApiEndpoint.project_id == request.project_id)
    else:
        projects = session.exec(
            select(ApiProject).where(ApiProject.user_id == user.user_id)
        ).all()
        project_ids = [p.id for p in projects]
        if not project_ids:
            return Response(data={"matches": []})
        query = query.where(ApiEndpoint.project_id.in_(project_ids))

    endpoints = session.exec(query).all()
    scored = []
    for ep in endpoints:
        score = _calculate_match_score(request.requirement, ep)
        if score > 0:
            scored.append({
                "endpoint_id": ep.id,
                "project_id": ep.project_id,
                "score": round(score, 1),
                "name": ep.name,
                "method": ep.method,
                "path": ep.path,
                "tags": ep.tags,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return Response(data={
        "matches": scored[:50],
        "total_matches": len(scored),
    })



@router.get("/projects", response_model=Response[List[ApiProject]])
def list_projects(session: SessionDep, user: CurrentUser):
    projects = session.exec(
        select(ApiProject)
        .where(ApiProject.user_id == user.user_id)
        .order_by(ApiProject.updated_at.desc())
    ).all()
    return Response(data=projects)


@router.put("/projects/{project_id}", response_model=Response[ApiProject])
def update_project(project_id: int, project: ApiProject, session: SessionDep, user: CurrentUser):
    db_project = session.get(ApiProject, project_id)
    if not db_project or db_project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    data = project.model_dump(exclude_unset=True)
    data.pop("id", None)
    data.pop("created_at", None)
    data.pop("updated_at", None)
    data.pop("user_id", None)
    db_project.sqlmodel_update(data)
    session.add(db_project)
    session.commit()
    session.refresh(db_project)
    return Response(data=db_project, message="接口项目已更新")


@router.delete("/projects/{project_id}", response_model=Response)
def delete_project(project_id: int, session: SessionDep, user: CurrentUser):
    db_project = session.get(ApiProject, project_id)
    if not db_project or db_project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    endpoints = session.exec(select(ApiEndpoint).where(ApiEndpoint.project_id == project_id)).all()
    scenarios = session.exec(select(ApiScenario).where(ApiScenario.project_id == project_id)).all()
    results = session.exec(select(ApiScenarioResult).where(ApiScenarioResult.project_id == project_id)).all()
    for item in results:
        session.delete(item)
    for item in endpoints + scenarios:
        session.delete(item)
    session.delete(db_project)
    session.commit()
    return Response(message="接口项目已删除")


@router.post("/import", response_model=Response[ImportResult])
async def import_openapi(
    session: SessionDep,
    user: CurrentUser,
    name: str = Form(""),
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    source_url = url.strip() if url and url.strip() else None
    if file:
        raw = (await file.read()).decode("utf-8")
        source_type = "upload"
        source_url = None
        project_name = name.strip() or (file.filename or "导入接口项目")
    elif source_url:
        async with httpx.AsyncClient() as client:
            resp = await client.get(source_url, timeout=30.0)
            resp.raise_for_status()
            raw = resp.text
        source_type = "url"
        project_name = name.strip() or url.strip().rstrip("/").split("/")[-1] or "导入接口项目"
    else:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="请上传文件或填写 OpenAPI URL")

    try:
        spec = parse_spec_text(raw)
    except ValueError as exc:
        return Response(code=status.HTTP_400_BAD_REQUEST, message=str(exc))

    project = ApiProject(
        name=project_name,
        base_url=detect_base_url(spec),
        source_type=source_type,
        source_url=source_url,
        raw_spec=raw,
        user_id=user.user_id,
    )
    try:
        session.add(project)
        session.flush()
        endpoints = endpoints_from_spec(spec, project.id, user.user_id)
        session.add_all(endpoints)
        session.commit()
        session.refresh(project)
        for endpoint in endpoints:
            session.refresh(endpoint)
    except RecursionError:
        session.rollback()
        return Response(code=status.HTTP_400_BAD_REQUEST, message="接口文档 schema 存在循环引用，导入示例生成失败")
    except Exception as exc:
        session.rollback()
        return Response(code=status.HTTP_400_BAD_REQUEST, message=f"接口文档解析失败: {exc}")

    return Response(data=ImportResult(project=project, endpoints=endpoints), message="导入成功")


@router.post("/projects/{project_id}/sync", response_model=Response[dict])
async def sync_openapi_project(project_id: int, session: SessionDep, user: CurrentUser):
    project = session.get(ApiProject, project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="Project not found")
    if project.source_type != "url" or not project.source_url:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="Only URL imported projects can be synced")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(project.source_url, timeout=30.0)
            resp.raise_for_status()
            raw = resp.text
        result = sync_project_from_spec(session, project, raw, user.user_id)
    except RecursionError:
        session.rollback()
        return Response(code=status.HTTP_400_BAD_REQUEST, message="OpenAPI schema contains circular refs")
    except ValueError as exc:
        session.rollback()
        return Response(code=status.HTTP_400_BAD_REQUEST, message=str(exc))
    except Exception as exc:
        session.rollback()
        return Response(code=status.HTTP_400_BAD_REQUEST, message=f"Sync failed: {exc}")
    return Response(data=result, message="Sync completed")


@router.get("/projects/{project_id}/endpoints", response_model=Response[List[ApiEndpoint]])
def list_endpoints(project_id: int, session: SessionDep, user: CurrentUser):
    project = session.get(ApiProject, project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    endpoints = session.exec(
        select(ApiEndpoint)
        .where(ApiEndpoint.project_id == project_id)
        .order_by(ApiEndpoint.id)
    ).all()
    return Response(data=endpoints)


@router.post("/projects/{project_id}/endpoints", response_model=Response[ApiEndpoint])
def create_endpoint(project_id: int, endpoint: ApiEndpoint, session: SessionDep, user: CurrentUser):
    project = session.get(ApiProject, project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    endpoint.id = None
    endpoint.project_id = project_id
    endpoint.user_id = user.user_id
    session.add(endpoint)
    session.commit()
    session.refresh(endpoint)
    return Response(data=endpoint, message="接口已创建")


@router.put("/endpoints/{endpoint_id}", response_model=Response[ApiEndpoint])
def update_endpoint(endpoint_id: int, endpoint: ApiEndpoint, session: SessionDep, user: CurrentUser):
    db_endpoint = session.get(ApiEndpoint, endpoint_id)
    if not db_endpoint or db_endpoint.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口不存在")
    data = endpoint.model_dump(exclude_unset=True)
    for key in ("id", "created_at", "updated_at", "user_id"):
        data.pop(key, None)
    db_endpoint.sqlmodel_update(data)
    session.add(db_endpoint)
    session.commit()
    session.refresh(db_endpoint)
    return Response(data=db_endpoint, message="接口已更新")


def _endpoint_inference_step(endpoint: ApiEndpoint) -> dict:
    return {
        "endpoint_id": endpoint.id,
        "name": endpoint.name,
        "headers": list(endpoint.headers or []),
        "parameters": list(endpoint.parameters or []),
        "body": endpoint.body,
        "post_actions": list(endpoint.post_actions or []),
        "request_schema": endpoint.request_schema or {},
        "response_schema": endpoint.response_schema or {},
    }


def _apply_inference_step_to_endpoint(endpoint: ApiEndpoint, step: dict) -> None:
    endpoint.headers = step.get("headers") or []
    endpoint.parameters = step.get("parameters") or []
    endpoint.body = step.get("body") or ""
    endpoint.post_actions = step.get("post_actions") or []


def _scenario_inference_step(step: dict, endpoint: ApiEndpoint | None) -> dict:
    return {
        **step,
        "headers": step["headers"] if "headers" in step else list(getattr(endpoint, "headers", []) or []),
        "parameters": step["parameters"] if "parameters" in step else list(getattr(endpoint, "parameters", []) or []),
        "body": step.get("body") if "body" in step else getattr(endpoint, "body", None),
        "post_actions": step["post_actions"] if "post_actions" in step else list(getattr(endpoint, "post_actions", []) or []),
        "request_schema": getattr(endpoint, "request_schema", {}) or {},
        "response_schema": getattr(endpoint, "response_schema", {}) or {},
    }


@router.post("/projects/{project_id}/infer-dependencies", response_model=Response[dict])
def infer_project_dependencies(project_id: int, session: SessionDep, user: CurrentUser):
    project = session.get(ApiProject, project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    endpoints = session.exec(
        select(ApiEndpoint)
        .where(ApiEndpoint.project_id == project_id)
        .where(ApiEndpoint.user_id == user.user_id)
        .order_by(ApiEndpoint.id)
    ).all()
    steps = [_endpoint_inference_step(endpoint) for endpoint in endpoints]
    summary = infer_step_dependencies(steps)
    for endpoint, step in zip(endpoints, steps):
        _apply_inference_step_to_endpoint(endpoint, step)
        session.add(endpoint)
    session.commit()
    return Response(data={**summary, "target": "project", "project_id": project_id}, message="依赖推断完成")


@router.post("/scenarios/{scenario_id}/infer-dependencies", response_model=Response[dict])
def infer_scenario_dependencies(scenario_id: int, session: SessionDep, user: CurrentUser):
    scenario = session.get(ApiScenario, scenario_id)
    if not scenario or scenario.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="场景不存在")
    endpoints = {
        endpoint.id: endpoint
        for endpoint in session.exec(
            select(ApiEndpoint)
            .where(ApiEndpoint.project_id == scenario.project_id)
            .where(ApiEndpoint.user_id == user.user_id)
        ).all()
    }
    original_steps = []
    inference_steps = []
    for raw_step in scenario.steps or []:
        if not isinstance(raw_step, dict) or raw_step.get("enabled", True) is False:
            continue
        endpoint = endpoints.get(raw_step.get("endpoint_id"))
        original_steps.append(raw_step)
        inference_steps.append(_scenario_inference_step(raw_step, endpoint))
    summary = infer_step_dependencies(inference_steps)
    for original, inferred in zip(original_steps, inference_steps):
        for key in ("headers", "parameters", "body", "post_actions"):
            if key in inferred:
                original[key] = inferred[key]
    scenario.steps = list(scenario.steps or [])
    session.add(scenario)
    session.commit()
    session.refresh(scenario)
    return Response(data={**summary, "target": "scenario", "scenario_id": scenario_id}, message="依赖推断完成")


@router.delete("/endpoints/{endpoint_id}", response_model=Response)
def delete_endpoint(endpoint_id: int, session: SessionDep, user: CurrentUser):
    db_endpoint = session.get(ApiEndpoint, endpoint_id)
    if not db_endpoint or db_endpoint.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口不存在")
    session.delete(db_endpoint)
    session.commit()
    return Response(message="接口已删除")


@router.post("/endpoints/{endpoint_id}/run", response_model=Response[dict])
async def run_api_endpoint(endpoint_id: int, request: RunEndpointRequest, session: SessionDep, user: CurrentUser):
    endpoint = session.get(ApiEndpoint, endpoint_id)
    if not endpoint or endpoint.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口不存在")
    project = session.get(ApiProject, endpoint.project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    result = await run_endpoint(session, project, endpoint, request.model_dump(exclude_unset=True))
    return Response(data=result, message="接口调试完成")


@router.post("/endpoints/{endpoint_id}/generate-body", response_model=Response[dict])
async def generate_endpoint_body(endpoint_id: int, request: GenerateBodyRequest, session: SessionDep, user: CurrentUser):
    endpoint = session.get(ApiEndpoint, endpoint_id)
    if not endpoint or endpoint.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口不存在")
    if not endpoint.request_schema:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="当前接口没有可用的 request schema")
    result = await generate_body_from_schema(
        db=session,
        schema=endpoint.request_schema,
        current_body=request.current_body,
        instruction=request.instruction,
        environment_id=request.environment_id,
        model_type=request.model_type,
        api_key=request.api_key,
        api_base_url=request.api_base_url,
        api_proxy_url=request.api_proxy_url,
        api_model=request.api_model,
        ollama_url=request.ollama_url,
        ollama_model=request.ollama_model,
    )
    return Response(data=result, message=result.get("message") or "请求体已生成")


@router.post("/endpoints/{endpoint_id}/generate-scenario-tests", response_model=Response[ApiScenario])
def generate_endpoint_scenario_tests(endpoint_id: int, session: SessionDep, user: CurrentUser):
    endpoint = session.get(ApiEndpoint, endpoint_id)
    if not endpoint or endpoint.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="Endpoint not found")
    project = session.get(ApiProject, endpoint.project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="Project not found")
    scenario = create_unit_test_scenario(session, project, endpoint, user.user_id)
    return Response(data=scenario, message="Scenario tests generated")


@router.get("/projects/{project_id}/scenarios", response_model=Response[List[ApiScenario]])
def list_scenarios(project_id: int, session: SessionDep, user: CurrentUser):
    project = session.get(ApiProject, project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    scenarios = session.exec(
        select(ApiScenario)
        .where(ApiScenario.project_id == project_id)
        .order_by(ApiScenario.updated_at.desc())
    ).all()
    return Response(data=scenarios)


@router.post("/projects/{project_id}/scenarios", response_model=Response[ApiScenario])
def create_scenario(project_id: int, scenario: ApiScenario, session: SessionDep, user: CurrentUser):
    project = session.get(ApiProject, project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    scenario.id = None
    scenario.project_id = project_id
    scenario.user_id = user.user_id
    session.add(scenario)
    session.commit()
    session.refresh(scenario)
    return Response(data=scenario, message="场景已创建")


@router.put("/scenarios/{scenario_id}", response_model=Response[ApiScenario])
def update_scenario(scenario_id: int, scenario: ApiScenario, session: SessionDep, user: CurrentUser):
    db_scenario = session.get(ApiScenario, scenario_id)
    if not db_scenario or db_scenario.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="场景不存在")
    data = scenario.model_dump(exclude_unset=True)
    for key in ("id", "created_at", "updated_at", "user_id"):
        data.pop(key, None)
    db_scenario.sqlmodel_update(data)
    session.add(db_scenario)
    session.commit()
    session.refresh(db_scenario)
    return Response(data=db_scenario, message="场景已更新")


@router.delete("/scenarios/{scenario_id}", response_model=Response)
def delete_scenario(scenario_id: int, session: SessionDep, user: CurrentUser):
    db_scenario = session.get(ApiScenario, scenario_id)
    if not db_scenario or db_scenario.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="场景不存在")
    results = session.exec(select(ApiScenarioResult).where(ApiScenarioResult.scenario_id == scenario_id)).all()
    for item in results:
        session.delete(item)
    session.delete(db_scenario)
    session.commit()
    return Response(message="场景已删除")


@router.get("/scenarios/{scenario_id}/results", response_model=Response[List[ApiScenarioResult]])
def list_scenario_results(
    scenario_id: int,
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(MAX_SCENARIO_RESULT_RECORDS, ge=1, le=50),
):
    scenario = session.get(ApiScenario, scenario_id)
    if not scenario or scenario.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="场景不存在")
    results = session.exec(
        select(ApiScenarioResult)
        .where(ApiScenarioResult.scenario_id == scenario_id)
        .where(ApiScenarioResult.user_id == user.user_id)
        .order_by(ApiScenarioResult.created_at.desc(), ApiScenarioResult.id.desc())
        .limit(limit)
    ).all()
    return Response(data=results)


@router.post("/scenarios/{scenario_id}/run", response_model=Response[ApiScenarioResult])
async def run_api_scenario(scenario_id: int, session: SessionDep, user: CurrentUser):
    scenario = session.get(ApiScenario, scenario_id)
    if not scenario or scenario.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="场景不存在")
    project = session.get(ApiProject, scenario.project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    result = await run_scenario(session, scenario, project)
    record = ApiScenarioResult(
        scenario_id=scenario.id,
        project_id=project.id,
        scenario_name=scenario.name,
        passed=bool(result.get("passed")),
        result=result,
        user_id=user.user_id,
    )
    session.add(record)
    session.flush()

    records = session.exec(
        select(ApiScenarioResult)
        .where(ApiScenarioResult.scenario_id == scenario.id)
        .where(ApiScenarioResult.user_id == user.user_id)
        .order_by(ApiScenarioResult.created_at.desc(), ApiScenarioResult.id.desc())
    ).all()
    for old_record in records[MAX_SCENARIO_RESULT_RECORDS:]:
        session.delete(old_record)

    session.commit()
    session.refresh(record)
    return Response(data=record, message="场景执行完成")
