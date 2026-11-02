"""
4阶段 Pipeline API 路由
提供深度生成、进度查询、中间产物管理等端点
支持功能测试和接口测试两种模式
"""
import asyncio
import json
import logging
from typing import Optional, List, Annotated

from fastapi import APIRouter, HTTPException, Form, Query, File, UploadFile
from pydantic import BaseModel
from sqlmodel import select, func

from app.deps import SessionDep, CurrentUser
from app.pipeline.pipeline_runner import PipelineRunner, get_pipeline_task
from db.models import RequirementPoint, TestPoint, TestCase, Module, McpServer, ApiEndpoint, ApiProject, SystemConfig
from utils.base_response import Response
from config import config_manager
from app.permissions import Permission, get_user_permissions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/testcases", tags=["pipeline"])

# 保存 fire-and-forget 后台重生成任务的引用，避免被垃圾回收（"Task was destroyed but it is pending"）
# 或异常未被检索的告警；done_callback 在任务完成后自动移除引用。
_BACKGROUND_TASKS: set = set()


def _schedule_background(coro):
    """把重生成协程丢到线程池子线程执行（不阻塞主事件循环），并跟踪其引用。"""
    bg = asyncio.create_task(asyncio.to_thread(asyncio.run, coro))
    _BACKGROUND_TASKS.add(bg)
    bg.add_done_callback(_BACKGROUND_TASKS.discard)
    return bg


class GenerateV2Request(BaseModel):
    """深度生成请求"""
    session_id: int
    requirement_text: str
    selected_skills: Optional[List[str]] = None


class UpdateRequirementPointRequest(BaseModel):
    """编辑需求点"""
    content: Optional[str] = None
    module_id: Optional[int] = None
    priority: Optional[str] = None


class UpdateTestPointRequest(BaseModel):
    """编辑测试点"""
    content: Optional[str] = None
    test_type: Optional[str] = None
    design_method: Optional[str] = None
    priority: Optional[str] = None


class CreateRequirementPointRequest(BaseModel):
    """手动新增需求点"""
    session_id: int
    content: str
    module_id: Optional[int] = None
    priority: Optional[str] = "medium"
    api_project_id: Optional[int] = None
    api_endpoint_ids: Optional[str] = None  # 逗号分隔


class CreateTestPointRequest(BaseModel):
    """手动新增测试点"""
    session_id: int
    requirement_point_id: int
    content: str
    test_type: Optional[str] = "functional"
    design_method: Optional[str] = ""
    priority: Optional[str] = "medium"
    api_project_id: Optional[int] = None
    api_endpoint_ids: Optional[str] = None


def _parse_endpoint_ids(endpoint_id_str: str | int | None) -> list[int]:
    """Deprecated shim — use app.services.api_context.parse_endpoint_ids."""
    from app.services.api_context import parse_endpoint_ids
    return parse_endpoint_ids(endpoint_id_str)


def _build_api_context(
    session,
    api_endpoint_ids: list[int],
    api_project_id: int | None,
    endpoint_overrides: dict[int, dict],
) -> tuple[str, dict[int, int]]:
    """Deprecated shim — use app.services.api_context.build_api_context."""
    from app.services.api_context import build_api_context
    return build_api_context(session, api_endpoint_ids, api_project_id, endpoint_overrides)


def _get_user_config(session, user_id: Optional[str] = None) -> dict:
    """从数据库获取用户配置，如果不存在则返回默认值"""
    statement = select(SystemConfig).where(SystemConfig.user_id == user_id)
    config = session.exec(statement).first()

    if config:
        return {
            "model_type": config.model_type,
            "api_key": config.api_key or "",
            "api_base_url": config.api_base_url,
            "api_model": config.api_model,
            "api_proxy_url": config.api_proxy_url or "",
            "ollama_url": config.ollama_url,
            "ollama_model": config.ollama_model,
        }

    # 返回默认配置
    return {
        "model_type": "api",
        "api_key": "",
        "api_base_url": "https://api.deepseek.com",
        "api_model": "deepseek-v4-flash",
        "api_proxy_url": "",
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3",
    }


def _check_session_owner(session, session_id: int, user: "CurrentUser"):
    """校验 session 归属当前用户（user_id 匹配或管理员）。"""
    from db.models import Session as SessionModel
    s = session.get(SessionModel, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 仅当用户登录且 session 绑定了 user_id 时校验；管理员/未绑定不强制
    if user and s.user_id and user.user_id and s.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="无权操作该会话的数据")


@router.post("/generate-v2")
async def generate_v2(
    session_id: int = Form(...),
    requirement_text: Optional[str] = Form(None),
    module_id: Optional[int] = Form(None),
    selected_skills: Optional[str] = Form(None),
    api_endpoint_id: Optional[str] = Form(None),
    api_project_id: Optional[str] = Form(None),
    api_endpoint_overrides: Optional[str] = Form(default=None),
    pipeline_mode: str = Form("auto"),
    requested_stop_after_stage: Optional[int] = Form(None, alias="stop_after_stage"),
    feishu_section: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """
    启动 4 阶段深度生成 pipeline

    返回 task_id，前端可通过 /pipeline/{task_id}/status 轮询进度
    支持功能测试（无接口）和接口测试（有关联接口）两种模式

    pipeline_mode: 兼容旧客户端，"auto"=4阶段全部执行，"manual"=执行前两阶段。
    stop_after_stage: 新客户端指定执行至第1-4阶段；后续阶段将跳过。
    文件上传：上传 .docx/.txt/.md 需求文档时，文件内容作为生成依据主体，
    文本框内已输入的文字作为「补充背景」拼接在文件内容之后。
    """
    # 解析上传的需求文件：文件内容作为主体，文本框文字作为补充背景
    from utils.file_parser import parse_uploaded_file
    if file is not None:
        try:
            file_content = await parse_uploaded_file(file)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if file_content:
            if requirement_text and requirement_text.strip():
                requirement_text = (
                    file_content
                    + "\n\n---\n## 补充背景（用户额外说明）\n"
                    + requirement_text.strip()
                )
            else:
                requirement_text = file_content

    # 既无文件又无需求文本时拒绝空生成
    if not requirement_text or not requirement_text.strip():
        raise HTTPException(status_code=400, detail="请输入需求描述或上传需求文件")
    if feishu_section and feishu_section.strip():
        from utils.document_prefetch import FEISHU_URL_RE
        if not FEISHU_URL_RE.search(requirement_text):
            raise HTTPException(status_code=400, detail="指定飞书目录时，请同时提供飞书文档链接")

    # 解析 selected_skills
    skills_list = None
    if selected_skills:
        try:
            skills_list = json.loads(selected_skills)
        except (json.JSONDecodeError, TypeError):
            skills_list = [s.strip() for s in selected_skills.split(",") if s.strip()]

    # 从数据库获取模型配置
    user_id = user.user_id if user else None
    user_config = _get_user_config(session, user_id)
    model_type = user_config["model_type"]
    api_key = user_config["api_key"]
    api_base_url = user_config["api_base_url"]
    api_model = user_config["api_model"]
    api_proxy_url = user_config["api_proxy_url"]

    if model_type == "api" and not api_key:
        raise HTTPException(status_code=400, detail="API Key未配置，请先在配置页面设置")

    # 加载 MCP 服务器配置（用于飞书/蓝湖文档预取）
    from app.services.mcp_loader import load_user_mcp_configs
    mcp_configs = load_user_mcp_configs(session, user)

    # 解析 API 端点参数
    api_endpoint_ids = _parse_endpoint_ids(api_endpoint_id)
    api_project_id_int = None
    if api_project_id:
        try:
            api_project_id_int = int(api_project_id)
        except (ValueError, TypeError):
            pass

    user_selected_endpoint_ids = list(api_endpoint_ids)
    if api_endpoint_ids and api_project_id_int:
        from app.routes.testcase import infer_endpoint_dependencies
        extra_ids = infer_endpoint_dependencies(session, api_endpoint_ids, api_project_id_int)
        for eid in extra_ids:
            if eid not in api_endpoint_ids:
                api_endpoint_ids.append(eid)
        if extra_ids:
            logger.info(f"Pipeline: inferred dependency endpoints {extra_ids}")

    # 解析接口参数覆盖配置
    endpoint_overrides: dict[int, dict] = {}
    if api_endpoint_overrides:
        try:
            raw_overrides = json.loads(api_endpoint_overrides)
            for eid_str, override in raw_overrides.items():
                eid = int(eid_str)
                if eid in api_endpoint_ids:
                    endpoint_overrides[eid] = override
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("Pipeline: 端点覆盖配置解析失败，继续使用数据库默认值")

    # 构建 API 接口信息文本
    api_context = ""
    endpoint_index_to_id = {}
    if api_endpoint_ids:
        api_context, endpoint_index_to_id = _build_api_context(
            session, api_endpoint_ids, api_project_id_int, endpoint_overrides,
        )

    # P5：核心接口 = 用户显式选中的接口（依赖补全接口可被压缩）
    core_endpoint_ids = {
        idx for idx, eid in endpoint_index_to_id.items()
        if eid in set(user_selected_endpoint_ids)
    }

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
    # 新客户端指定截止阶段；未传时兼容旧 auto/manual 模式。
    if requested_stop_after_stage is not None and requested_stop_after_stage not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="stop_after_stage 必须为 1 到 4")
    stop_after_stage = requested_stop_after_stage or (2 if pipeline_mode == "manual" else 4)
    task_id = await runner.run(
        session_id=session_id,
        requirement_text=requirement_text,
        user_id=user.user_id if user else None,
        selected_skills=skills_list,
        module_id=module_id,
        api_endpoint_ids=api_endpoint_ids or None,
        api_project_id=api_project_id_int,
        endpoint_index_to_id=endpoint_index_to_id,
        api_context=api_context,
        core_endpoint_ids=core_endpoint_ids,
        stop_after_stage=stop_after_stage,
        feishu_section=feishu_section.strip() if feishu_section and feishu_section.strip() else None,
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
    offset: int = 0,
    limit: Annotated[int, Query(le=200, gt=0)] = 50,
    module_id: Optional[int] = None,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """获取会话的需求点列表（分页，可按模块过滤，附带每个需求点的测试点计数）"""
    base = [RequirementPoint.session_id == session_id]
    if module_id is not None:
        base.append(RequirementPoint.module_id == module_id)
    total = session.scalar(select(func.count()).where(*base))

    points = session.exec(
        select(RequirementPoint)
        .where(*base)
        .order_by(RequirementPoint.order_index)
        .offset(offset)
        .limit(limit)
    ).all()

    # 解析模块名（一次查询，避免逐条 join）
    module_name_map = {
        m.id: m.module_name
        for m in session.exec(select(Module).where(Module.session_id == session_id)).all()
    }

    # 每个需求点的测试点计数（一次分组查询，避免逐条 count）
    tp_counts = session.exec(
        select(TestPoint.requirement_point_id, func.count())
        .where(TestPoint.session_id == session_id)
        .group_by(TestPoint.requirement_point_id)
    ).all()
    tp_count_map = {row[0]: row[1] for row in tp_counts}

    result = []
    for p in points:
        result.append({
            "id": p.id,
            "content": p.content,
            "module_id": p.module_id,
            "module": module_name_map.get(p.module_id, "") if p.module_id else "",
            "priority": p.priority,
            "order_index": p.order_index,
            "test_point_count": tp_count_map.get(p.id, 0),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        })

    return Response(data={"items": result, "totalNumber": total or 0})


@router.get("/sessions/{session_id}/test-points")
async def get_test_points(
    session_id: int,
    requirement_point_id: Optional[int] = None,
    offset: int = 0,
    limit: Annotated[int, Query(le=200, gt=0)] = 50,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """获取会话的测试点列表（可按需求点过滤、分页，附带每个测试点的用例计数）"""
    base = [TestPoint.session_id == session_id]
    if requirement_point_id is not None:
        base.append(TestPoint.requirement_point_id == requirement_point_id)

    total = session.scalar(select(func.count()).where(*base))

    points = session.exec(
        select(TestPoint)
        .where(*base)
        .order_by(TestPoint.id)
        .offset(offset)
        .limit(limit)
    ).all()

    # 每个测试点的用例计数（一次分组查询）
    tc_counts = session.exec(
        select(TestCase.test_point_id, func.count())
        .where(TestCase.session_id == session_id)
        .group_by(TestCase.test_point_id)
    ).all()
    tc_count_map = {row[0]: row[1] for row in tc_counts}

    result = []
    for p in points:
        result.append({
            "id": p.id,
            "requirement_point_id": p.requirement_point_id,
            "content": p.content,
            "test_type": p.test_type,
            "design_method": p.design_method,
            "priority": p.priority,
            "case_count": tc_count_map.get(p.id, 0),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        })

    return Response(data={"items": result, "totalNumber": total or 0})


@router.post("/requirement-points")
async def create_requirement_point(
    body: CreateRequirementPointRequest,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """手动新增需求点"""
    _check_session_owner(session, body.session_id, user)
    rp = RequirementPoint(
        session_id=body.session_id,
        content=body.content,
        module_id=body.module_id,
        priority=body.priority or "medium",
        order_index=0,
        user_id=user.user_id if user else None,
        api_project_id=body.api_project_id,
        api_endpoint_ids=body.api_endpoint_ids,
    )
    session.add(rp)
    session.commit()
    session.refresh(rp)
    return Response(message="需求点已创建", data={"id": rp.id})


@router.post("/test-points")
async def create_test_point(
    body: CreateTestPointRequest,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """手动新增测试点（校验父需求点存在且同 session）"""
    _check_session_owner(session, body.session_id, user)
    parent = session.get(RequirementPoint, body.requirement_point_id)
    if not parent or parent.session_id != body.session_id:
        raise HTTPException(status_code=400, detail="父需求点不存在或不属于该会话")
    tp = TestPoint(
        requirement_point_id=body.requirement_point_id,
        session_id=body.session_id,
        content=body.content,
        test_type=body.test_type or "functional",
        design_method=body.design_method or "",
        priority=body.priority or "medium",
        user_id=user.user_id if user else None,
        api_project_id=body.api_project_id,
        api_endpoint_ids=body.api_endpoint_ids,
    )
    session.add(tp)
    session.commit()
    session.refresh(tp)
    return Response(message="测试点已创建", data={"id": tp.id})


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
    if body.module_id is not None:
        point.module_id = body.module_id
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


@router.delete("/requirement-points/{point_id}")
async def delete_requirement_point(
    point_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """级联删除需求点及其下所有测试点、用例与孤儿场景"""
    from db.models import TestPoint as TPModel, TestCase as TCModel, ApiScenario
    rp = session.get(RequirementPoint, point_id)
    if not rp:
        raise HTTPException(status_code=404, detail="需求点不存在")
    if user and rp.user_id and user.user_id and rp.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="无权删除该需求点")

    tps = session.exec(select(TPModel).where(TPModel.requirement_point_id == point_id)).all()
    for tp in tps:
        cases = session.exec(select(TCModel).where(TCModel.test_point_id == tp.id)).all()
        for c in cases:
            if c.scenario_id:
                sc = session.get(ApiScenario, c.scenario_id)
                if sc:
                    session.delete(sc)
            session.delete(c)
        session.delete(tp)
    session.delete(rp)
    session.commit()
    return Response(message="需求点及其下游已级联删除")


@router.delete("/test-points/{point_id}")
async def delete_test_point(
    point_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """级联删除测试点及其下所有用例与孤儿场景"""
    from db.models import TestCase as TCModel, ApiScenario
    tp = session.get(TestPoint, point_id)
    if not tp:
        raise HTTPException(status_code=404, detail="测试点不存在")
    if user and tp.user_id and user.user_id and tp.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="无权删除该测试点")

    cases = session.exec(select(TCModel).where(TCModel.test_point_id == point_id)).all()
    for c in cases:
        if c.scenario_id:
            sc = session.get(ApiScenario, c.scenario_id)
            if sc:
                session.delete(sc)
        session.delete(c)
    session.delete(tp)
    session.commit()
    return Response(message="测试点及其用例已级联删除")


@router.post("/test-points/{point_id}/regenerate")
async def regenerate_test_point_cases(
    point_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """重新生成单个测试点的测试用例（就地更新，异步任务）。"""
    tp = session.get(TestPoint, point_id)
    if not tp:
        raise HTTPException(status_code=404, detail="测试点不存在")
    # 继承父需求点的 module_id，确保级联生成的用例模块归属不丢
    parent_rp = session.get(RequirementPoint, tp.requirement_point_id) if tp.requirement_point_id else None
    parent_module_id = parent_rp.module_id if parent_rp else None

    user_id = user.user_id if user else None
    user_config = _get_user_config(session, user_id)
    model_type = user_config["model_type"]
    api_key = user_config["api_key"]
    if model_type == "api" and not api_key:
        raise HTTPException(status_code=400, detail="API Key未配置，请先在配置页面设置")

    runner = PipelineRunner(
        db_session=session,
        api_key=api_key,
        api_base_url=user_config["api_base_url"],
        api_model=user_config["api_model"],
        api_proxy_url=user_config["api_proxy_url"] or None,
        mcp_configs=[],
    )
    task_id = runner._create_task()
    _schedule_background(
        runner.regenerate_test_point(
            task_id, point_id,
            user_id=user_id,
            module_id=parent_module_id,
        )
    )
    return Response(data={"task_id": task_id}, message="测试点用例重生成已启动")


@router.post("/requirement-points/{id}/regenerate")
async def regenerate_requirement_point_points(
    id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """重新生成需求点下的测试点（一步到位级联到用例，异步任务）。"""
    rp = session.get(RequirementPoint, id)
    if not rp:
        raise HTTPException(status_code=404, detail="需求点不存在")

    user_id = user.user_id if user else None
    user_config = _get_user_config(session, user_id)
    model_type = user_config["model_type"]
    api_key = user_config["api_key"]
    if model_type == "api" and not api_key:
        raise HTTPException(status_code=400, detail="API Key未配置，请先在配置页面设置")

    runner = PipelineRunner(
        db_session=session,
        api_key=api_key,
        api_base_url=user_config["api_base_url"],
        api_model=user_config["api_model"],
        api_proxy_url=user_config["api_proxy_url"] or None,
        mcp_configs=[],
    )
    task_id = runner._create_task()
    _schedule_background(
        runner.regenerate_requirement_point(
            task_id, id,
            user_id=user_id,
            module_id=rp.module_id,
        )
    )
    return Response(data={"task_id": task_id}, message="需求点测试点重生成已启动")


@router.post("/sessions/{session_id}/batch-generate-cases")
async def batch_generate_cases(
    session_id: int,
    module_id: Optional[int] = Query(None),
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """批量为本会话所有测试点生成测试用例（Stage 3，异步任务）。

    用于「手动模式」深度生成（pipeline_mode=manual，只产出需求点+测试点）之后，
    用户审阅/编辑测试点，再一键为全部测试点生成用例。可按 module_id 限定范围。
    """
    _check_session_owner(session, session_id, user)
    user_id = user.user_id if user else None
    user_config = _get_user_config(session, user_id)
    model_type = user_config["model_type"]
    api_key = user_config["api_key"]
    if model_type == "api" and not api_key:
        raise HTTPException(status_code=400, detail="API Key未配置，请先在配置页面设置")

    runner = PipelineRunner(
        db_session=session,
        api_key=api_key,
        api_base_url=user_config["api_base_url"],
        api_model=user_config["api_model"],
        api_proxy_url=user_config["api_proxy_url"] or None,
        mcp_configs=[],
    )
    task_id = runner._create_task()
    _schedule_background(
        runner.batch_generate_cases(
            task_id,
            session_id,
            user_id=user_id,
            module_id=module_id,
        )
    )
    return Response(data={"task_id": task_id}, message="批量生成用例已启动")
