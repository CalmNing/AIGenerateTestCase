"""
4阶段 Pipeline API 路由
提供深度生成、进度查询、中间产物管理等端点
"""
import json
import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Form
from pydantic import BaseModel
from sqlmodel import select

from app.deps import SessionDep, CurrentUser
from app.pipeline.pipeline_runner import PipelineRunner, get_pipeline_task
from db.models import RequirementPoint, TestPoint, TestCase, McpServer
from utils.base_response import Response
from config import config_manager
from app.permissions import Permission, get_user_permissions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/testcases", tags=["pipeline"])


class GenerateV2Request(BaseModel):
    """深度生成请求"""
    session_id: int
    requirement_text: str
    selected_skills: Optional[List[str]] = None


class UpdateRequirementPointRequest(BaseModel):
    """编辑需求点"""
    content: Optional[str] = None
    module: Optional[str] = None
    priority: Optional[str] = None


class UpdateTestPointRequest(BaseModel):
    """编辑测试点"""
    content: Optional[str] = None
    test_type: Optional[str] = None
    design_method: Optional[str] = None
    priority: Optional[str] = None


@router.post("/generate-v2")
async def generate_v2(
    session_id: int = Form(...),
    requirement_text: str = Form(...),
    selected_skills: Optional[str] = Form(None),
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """
    启动 4 阶段深度生成 pipeline

    返回 task_id，前端可通过 /pipeline/{task_id}/status 轮询进度
    """
    # 解析 selected_skills
    skills_list = None
    if selected_skills:
        try:
            skills_list = json.loads(selected_skills)
        except (json.JSONDecodeError, TypeError):
            skills_list = [s.strip() for s in selected_skills.split(",") if s.strip()]

    # 获取模型配置
    model_type = config_manager.get("model_type", "api")
    api_key = config_manager.get("api_key", "")
    api_base_url = config_manager.get("api_base_url", "https://api.deepseek.com")
    api_model = config_manager.get("api_model", "deepseek-v4-flash")
    api_proxy_url = config_manager.get("api_proxy_url", "")

    if model_type == "api" and not api_key:
        raise HTTPException(status_code=400, detail="API Key未配置，请先在配置页面设置")

    # 加载 MCP 服务器配置（用于飞书/蓝湖文档预取）
    mcp_configs = []
    if Permission.MCP_MANAGE in get_user_permissions(user):
        mcp_servers_db = session.exec(
            select(McpServer).where(
                McpServer.user_id == user.user_id,
                McpServer.enabled == True,
            )
        ).all()
        mcp_configs = [
            server.model_dump(exclude={"id", "created_at", "updated_at", "user_id"})
            for server in mcp_servers_db
        ]
        logger.info(f"Pipeline: 已加载 {len(mcp_configs)} 个 MCP 服务器配置")

    # 创建 pipeline runner
    runner = PipelineRunner(
        db_session=session,
        api_key=api_key,
        api_base_url=api_base_url,
        api_model=api_model,
        api_proxy_url=api_proxy_url or None,
        mcp_configs=mcp_configs,
    )

    # 启动 pipeline
    task_id = await runner.run(
        session_id=session_id,
        requirement_text=requirement_text,
        user_id=user.user_id if user else None,
        selected_skills=skills_list,
    )

    return Response(data={"task_id": task_id}, message="深度生成已启动")


@router.get("/pipeline/{task_id}/status")
async def get_pipeline_status(task_id: str):
    """查询 pipeline 任务进度"""
    task = get_pipeline_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return Response(data=task)


@router.get("/sessions/{session_id}/requirement-points")
async def get_requirement_points(
    session_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """获取会话的需求点列表"""
    points = session.exec(
        select(RequirementPoint)
        .where(RequirementPoint.session_id == session_id)
        .order_by(RequirementPoint.order_index)
    ).all()

    result = []
    for p in points:
        result.append({
            "id": p.id,
            "content": p.content,
            "module": p.module,
            "priority": p.priority,
            "order_index": p.order_index,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })

    return Response(data=result)


@router.get("/sessions/{session_id}/test-points")
async def get_test_points(
    session_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """获取会话的测试点列表"""
    points = session.exec(
        select(TestPoint)
        .where(TestPoint.session_id == session_id)
        .order_by(TestPoint.id)
    ).all()

    result = []
    for p in points:
        result.append({
            "id": p.id,
            "requirement_point_id": p.requirement_point_id,
            "content": p.content,
            "test_type": p.test_type,
            "design_method": p.design_method,
            "priority": p.priority,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })

    return Response(data=result)


@router.put("/requirement-points/{point_id}")
async def update_requirement_point(
    point_id: int,
    body: UpdateRequirementPointRequest,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """编辑需求点"""
    point = session.get(RequirementPoint, point_id)
    if not point:
        raise HTTPException(status_code=404, detail="需求点不存在")

    if body.content is not None:
        point.content = body.content
    if body.module is not None:
        point.module = body.module
    if body.priority is not None:
        point.priority = body.priority

    session.add(point)
    session.commit()
    session.refresh(point)

    return Response(message="需求点已更新", data={"id": point.id})


@router.put("/test-points/{point_id}")
async def update_test_point(
    point_id: int,
    body: UpdateTestPointRequest,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """编辑测试点"""
    point = session.get(TestPoint, point_id)
    if not point:
        raise HTTPException(status_code=404, detail="测试点不存在")

    if body.content is not None:
        point.content = body.content
    if body.test_type is not None:
        point.test_type = body.test_type
    if body.design_method is not None:
        point.design_method = body.design_method
    if body.priority is not None:
        point.priority = body.priority

    session.add(point)
    session.commit()
    session.refresh(point)

    return Response(message="测试点已更新", data={"id": point.id})


@router.post("/test-points/{point_id}/regenerate")
async def regenerate_test_point_cases(
    point_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """重新生成单个测试点的测试用例"""
    point = session.get(TestPoint, point_id)
    if not point:
        raise HTTPException(status_code=404, detail="测试点不存在")

    # 获取模型配置
    model_type = config_manager.get("model_type", "api")
    api_key = config_manager.get("api_key", "")
    api_base_url = config_manager.get("api_base_url", "https://api.deepseek.com")
    api_model = config_manager.get("api_model", "deepseek-v4-flash")
    api_proxy_url = config_manager.get("api_proxy_url", "")

    if model_type == "api" and not api_key:
        raise HTTPException(status_code=400, detail="API Key未配置")

    from app.pipeline.test_case_designer import TestCaseDesigner

    designer = TestCaseDesigner(
        api_key=api_key,
        api_base_url=api_base_url,
        api_model=api_model,
        api_proxy_url=api_proxy_url or None,
    )

    test_point_data = {
        "id": point.id,
        "content": point.content,
        "test_type": point.test_type,
        "design_method": point.design_method,
        "priority": point.priority,
    }

    try:
        cases_data = await designer.design_batch([test_point_data])

        if cases_data:
            case_data = cases_data[0]
            steps = case_data.get("test_steps", [])

            tc = TestCase(
                case_name=case_data.get("title", ""),
                steps=steps if isinstance(steps, list) else [],
                preset_conditions=[case_data.get("preconditions", "")] if case_data.get("preconditions") else [],
                expected_results=[s.get("expected", "") for s in steps] if steps else [],
                case_level=4,
                session_id=point.session_id,
                test_point_id=point.id,
                test_type=point.test_type,
                design_method=point.design_method,
                user_id=user.user_id if user else None,
            )
            session.add(tc)
            session.commit()
            session.refresh(tc)

            return Response(
                message="测试用例已重新生成",
                data={"testcase_id": tc.id, "case_name": tc.case_name},
            )
        else:
            raise HTTPException(status_code=500, detail="未生成任何测试用例")

    except Exception as e:
        logger.error(f"重新生成失败: {e}")
        raise HTTPException(status_code=500, detail=f"重新生成失败: {str(e)}")
