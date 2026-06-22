import base64
from typing import List, Annotated, Optional

from fastapi import APIRouter, Query, status, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlmodel import select, desc, func

from app.deps import SessionDep, CurrentUser
from app.permissions import Permission, get_user_permissions
from db.models import Session, TestCase, StatusValue, McpServer
from utils.base_response import Response
import traceback
from app.services.api_test_tool import run_endpoint
from db.models import ApiEndpoint, ApiProject

router = APIRouter(prefix="/testcases", tags=["testcases"])


class TestCasePage(BaseModel):
    items: List[TestCase]
    totalNumber: int = Field(int, description="总条数")
    passed: int = Field(int, description="已通过的用例数")
    failed: int = Field(int, description="未通过的用例数")
    not_run: int = Field(int, description="未执行的用例数")
    totalBugs: int = Field(int, description="Bug数")
    model_config = {
        "arbitrary_types_allowed": True,
    }


# 测试用例管理API
@router.get("/{session_id}/testcases", response_model=Response[TestCasePage])
def get_testcases(
        session_id: int,
        session: SessionDep,
        user: CurrentUser,
        module_id: int = None,
        offset: int = 0,
        limit: Annotated[int, Query(le=1000)] = 1000,
        case_name: str = None,
        status: str = None,
        bug_id: str = None,
        exist_bug: bool = False
):
    """获取会话的测试用例"""
    query = select(TestCase).where(TestCase.session_id == session_id).order_by(TestCase.case_level,
                                                                               desc(TestCase.created_at))
    if module_id:
        query = query.where(TestCase.module_id == module_id)

    if case_name:
        query = query.where(TestCase.case_name.contains(case_name))
    if status:
        query = query.where(TestCase.status.contains(status))
    if bug_id:
        query = query.where(TestCase.bug_id.contains(bug_id))
    if exist_bug:
        query = query.where(TestCase.bug_id != None)

    testcases_db = session.exec(query.offset(offset).limit(limit)).all()
    for testcase in testcases_db:
        testcase.session_id = int(testcase.session_id) if testcase.session_id else None

    # 构建基础查询条件
    count_query_base = [TestCase.session_id == session_id]
    if module_id is not None:
        count_query_base.append(TestCase.module_id == module_id)
    totalNumber = session.scalar(select(func.count()).where(*count_query_base))

    passed_query = [TestCase.session_id == session_id, TestCase.status == StatusValue.PASSED]
    if module_id is not None:
        passed_query.append(TestCase.module_id == module_id)
    passed = session.scalar(select(func.count()).where(*passed_query))

    failed_query = [TestCase.session_id == session_id, TestCase.status == StatusValue.FAILED]
    if module_id is not None:
        failed_query.append(TestCase.module_id == module_id)
    failed = session.scalar(select(func.count()).where(*failed_query))

    not_run_query = [TestCase.session_id == session_id, TestCase.status == StatusValue.NOT_RUN]
    if module_id is not None:
        not_run_query.append(TestCase.module_id == module_id)
    not_run = session.scalar(select(func.count()).where(*not_run_query))

    total_bugs_query = [TestCase.session_id == session_id, TestCase.bug_id != None]
    if module_id is not None:
        total_bugs_query.append(TestCase.module_id == module_id)
    totalBugs = session.scalar(select(func.count()).where(*total_bugs_query))

    testcases = TestCasePage(
        items=testcases_db,
        totalNumber=totalNumber,
        passed=passed,
        failed=failed,
        not_run=not_run,
        totalBugs=totalBugs
    )
    return Response(data=testcases)


# 请求模型
class GenerateTestcasesRequest(BaseModel):
    model_type: str = "api"
    api_key: str = ""
    api_base_url: str = ""
    api_proxy_url: str = ""
    ollama_url: str = ""
    ollama_model: str = ""
    requirement: Optional[str]


@router.post("/{session_id}/testcases")
async def generate_testcases(
        session: SessionDep,
        user: CurrentUser,
        session_id: int,
        requirement: Optional[str] = Form(None),
        model_type: str = Form("api"),
        api_key: str = Form(""),
        api_base_url: str = Form(""),
        api_proxy_url: str = Form(""),
        ollama_url: str = Form(""),
        ollama_model: str = Form(""),
        module_id: Optional[int|str] = Form(""),
        mcp_servers: Optional[str] = Form(""),
        selected_skills: Optional[str] = Form(""),
        api_endpoint_id: Optional[str] = Form(""),
        api_project_id: Optional[str] = Form(""),
):
    """生成测试用例"""
    from utils.model_utils import ModelServiceUnavailableError, generate_testcases

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"接收到生成测试用例请求")
    logger.info(f"  session_id: {session_id}")
    logger.info(f"  module_id: {module_id}")
    logger.info(f"  requirement: {'有值' if requirement else 'None'} (长度: {len(requirement) if requirement else 0})")
    logger.info(f"  model_type: {model_type}")
    logger.info(f"  api_key: {'有值' if api_key else 'None'}")

    has_valid_input = False

    if requirement and requirement.strip():
        has_valid_input = True
        logger.info(f"  有有效的requirement")

    logger.info(f"  验证结果: has_valid_input={has_valid_input}")

    if not has_valid_input:
        logger.error(f"  验证失败: 请输入requirement")
        return Response(code=status.HTTP_400_BAD_REQUEST, data="请输入requirement")

    api_key = api_key.strip() if api_key else ""
    api_base_url = api_base_url.strip() if api_base_url else ""
    api_proxy_url = api_proxy_url.strip() if api_proxy_url else ""
    ollama_url = ollama_url.strip() if ollama_url else ""
    ollama_model = ollama_model.strip() if ollama_model else ""

    if model_type == "api" and not api_key:
        return Response(code=status.HTTP_400_BAD_REQUEST, data="api_key 未提供!")
    if model_type == "ollama" and (not ollama_url or not ollama_model):
        return Response(code=status.HTTP_400_BAD_REQUEST,
                        data="Ollama 配置不完整（ollama_url/ollama_model）")

    # MCP 服务器配置只允许从服务端持久化配置读取，避免生成接口绕过 /api/mcp 的权限控制。
    import json
    mcp_configs = []
    if mcp_servers and mcp_servers.strip():
        logger.info("忽略客户端传入的 MCP 服务器配置，改用服务端已保存配置")

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
        logger.info(f"已加载 {len(mcp_configs)} 个服务端 MCP 服务器配置")
    else:
        logger.info("当前用户无 mcp:manage 权限，生成用例时不加载自定义 MCP 工具")

    # 解析选中的技能名称
    selected_skill_names = []
    if selected_skills and selected_skills.strip():
        try:
            parsed_skills = json.loads(selected_skills)
            if isinstance(parsed_skills, list):
                selected_skill_names = parsed_skills
                logger.info(f"接收到 {len(selected_skill_names)} 个选中技能: {selected_skill_names}")
        except json.JSONDecodeError:
            logger.warning(f"Skills 配置解析失败: {selected_skills[:200]}")

    # ?? API ???? Schema ???????
        api_context = ""
    api_endpoint_ids = []
    if api_endpoint_id:
        for part in api_endpoint_id.split(','):
            part = part.strip()
            if part:
                try:
                    api_endpoint_ids.append(int(part))
                except ValueError:
                    pass

    if api_endpoint_ids:
        api_sections = []
        for eid in api_endpoint_ids:
            try:
                endpoint_db = session.get(ApiEndpoint, eid)
                if not endpoint_db:
                    continue
                project_db = session.get(ApiProject, api_project_id and int(api_project_id) or endpoint_db.project_id)
                ep_lines = []
                ep_lines.append('===== ' + endpoint_db.name + ' =====')
                ep_lines.append('??: ' + (project_db.name if project_db else '??'))
                ep_lines.append('??: ' + endpoint_db.method)
                ep_lines.append('??: ' + endpoint_db.path)
                if endpoint_db.headers:
                    ep_lines.append('???: ' + json.dumps(endpoint_db.headers, ensure_ascii=False, indent=2))
                if endpoint_db.parameters:
                    ep_lines.append('??: ' + json.dumps(endpoint_db.parameters, ensure_ascii=False, indent=2))
                if endpoint_db.request_schema:
                    ep_lines.append('请求 Schema:\n' + json.dumps(endpoint_db.request_schema, ensure_ascii=False, indent=2))
                if endpoint_db.response_schema:
                    ep_lines.append('响应 Schema:\n' + json.dumps(endpoint_db.response_schema, ensure_ascii=False, indent=2))
                api_sections.append('\n'.join(ep_lines))
                logger.info('已加载 API 端点: ' + endpoint_db.name + ' (' + endpoint_db.method + ' ' + endpoint_db.path + ')')
            except Exception as e:
                logger.warning('加载端点 ' + str(eid) + ' 失败: ' + str(e))

        if api_sections:
            all_api_info = '\n\n'.join(api_sections)
            api_context = '\n\n===== 以下 API 接口信息 =====\n' + all_api_info

    effective_requirement = requirement
    if api_context:
        effective_requirement = requirement + api_context

    try:
        testcases, effective_req = await generate_testcases(
            db_session=session,
            requirement=effective_requirement,
            session_id=session_id,
            module_id=module_id,
            model_type=model_type,
            api_key=api_key,
            api_base_url=api_base_url,
            api_proxy_url=api_proxy_url,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            mcp_configs=mcp_configs,
            selected_skill_names=selected_skill_names,
            user_id=user.user_id,
        )
    except ModelServiceUnavailableError as e:
        logger.error(f"生成测试用例失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=Response(code=status.HTTP_503_SERVICE_UNAVAILABLE, message=str(e)).model_dump(),
        )
    except ValueError as e:
        logger.error(f"生成测试用例失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=Response(code=status.HTTP_400_BAD_REQUEST, message=str(e)).model_dump(),
        )
    # 自动填充 user_id
    for tc in testcases:
        tc.user_id = user.user_id
        if api_endpoint_id:
            # Save as comma-separated string for multi-endpoint support
            tc.api_endpoint_id = api_endpoint_id
        if api_project_id:
            try:
                tc.api_project_id = int(api_project_id)
            except (ValueError, TypeError):
                pass
    session.add_all(testcases)
    session.commit()
    return Response(message="生成测试用例成功")


@router.put("/{session_id}/testcases/{testcase_id}", response_model=Response)
def update_testcase(
        session: SessionDep,
        user: CurrentUser,
        session_id: int,
        testcase_id: int,
        testcase: TestCase
):
    """更新测试用例"""
    testcase_db = session.get(TestCase, testcase_id)
    if not testcase_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="测试用例不存在")

    testcase_data = testcase.model_dump(exclude_unset=True)
    testcase_data.pop("created_at")
    testcase_data.pop("updated_at")
    testcase_db.sqlmodel_update(testcase_data)
    session.add(testcase_db)
    session.commit()
    session.refresh(testcase_db)
    return Response(message="更新测试用例成功")


@router.delete("/{session_id}/testcases", response_model=Response[str])
def delete_testcase(
        session: SessionDep,
        user: CurrentUser,
        session_id: int,
        testcases: List[int]
):
    # 检查测试用例是否存在
    user_testcases = session.exec(
        select(TestCase).where(
            TestCase.id.in_(testcases)
        )
    ).all()
    if len(user_testcases) != len(testcases):
        return Response(code=status.HTTP_404_NOT_FOUND, message="部分测试用例不存在")

    testcase_status = session.exec(
        select(TestCase.status)
        .where(
            TestCase.id.in_(testcases),
            TestCase.status != StatusValue.NOT_RUN
        )
    ).first()
    if testcase_status:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="存在已执行的用例，删除失败！")

    # 批量删除测试用例
    statement = delete(TestCase).where(
        TestCase.id.in_(testcases),
        TestCase.session_id == session_id
    )
    session.exec(statement)
    session.commit()
    return Response(message="删除测试用例成功")


# 移动测试用例请求模型
class MoveTestcaseRequest(BaseModel):
    session_id: int
    module_id: Optional[int] = None


@router.post("/{testcase_id}/move", response_model=Response)
def move_testcase(
        session: SessionDep,
        user: CurrentUser,
        testcase_id: int,
        request: MoveTestcaseRequest
):
    """移动测试用例到指定会话和模块"""
    testcase_db = session.get(TestCase, testcase_id)
    if not testcase_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="测试用例不存在")
    
    testcase_db.session_id = request.session_id
    testcase_db.module_id = request.module_id
    
    session.add(testcase_db)
    session.commit()
    session.refresh(testcase_db)
    return Response(message="移动测试用例成功")


# 批量移动测试用例请求模型
class BatchMoveTestcaseRequest(BaseModel):
    testcase_ids: List[int]
    session_id: int
    module_id: Optional[int] = None


@router.post("/move", response_model=Response)
def batch_move_testcase(
        session: SessionDep,
        user: CurrentUser,
        request: BatchMoveTestcaseRequest
):
    """批量移动测试用例到指定会话和模块"""
    testcases_db = session.exec(
        select(TestCase).where(
            TestCase.id.in_(request.testcase_ids)
        )
    ).all()
    
    if not testcases_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="测试用例不存在")

    if len(testcases_db) != len(request.testcase_ids):
        return Response(code=status.HTTP_404_NOT_FOUND, message="部分测试用例不存在")
    
    for testcase_db in testcases_db:
        testcase_db.session_id = request.session_id
        testcase_db.module_id = request.module_id
        session.add(testcase_db)
    
    session.commit()
    return Response(message=f"成功移动 {len(testcases_db)} 个测试用例")


@router.post("/{session_id}/testcases/create", response_model=Response[TestCase])
def create_testcase(
        session: SessionDep,
        user: CurrentUser,
        session_id: int,
        testcase: TestCase
):
    """创建测试用例"""
    testcase_db = TestCase(
        case_name=testcase.case_name,
        case_level=testcase.case_level,
        preset_conditions=testcase.preset_conditions,
        steps=testcase.steps,
        expected_results=testcase.expected_results,
        session_id=session_id,
        module_id=testcase.module_id,
        status=testcase.status or StatusValue.NOT_RUN,
        user_id=user.user_id
    )
    
    session.add(testcase_db)
    session.commit()
    session.refresh(testcase_db)
    return Response(data=testcase_db, message="创建测试用例成功")


@router.post("/{session_id}/testcases/{testcase_id}/execute", response_model=Response)
async def execute_testcase(
    session: SessionDep,
    user: CurrentUser,
    session_id: int,
    testcase_id: int,
):
    """?????? API ??????"""
    testcase = session.get(TestCase, testcase_id)
    if not testcase or testcase.session_id != session_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="测试用例不存在")

    if not testcase.api_endpoint_id:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="\u8be5\u6d4b\u8bd5\u7528\u4f8b\u672a\u5173\u8054 API \u63a5\u53e3")

    # Support comma-separated multiple endpoint IDs; use first for execution
    first_id = testcase.api_endpoint_id
    if isinstance(first_id, str) and "," in first_id:
        first_id = first_id.split(",")[0].strip()
    endpoint = session.get(ApiEndpoint, int(first_id))
    if not endpoint:
        return Response(code=status.HTTP_404_NOT_FOUND, message="\u5173\u8054\u7684 API \u63a5\u53e3\u4e0d\u5b58\u5728")

    project = session.get(ApiProject, testcase.api_project_id or endpoint.project_id)
    if not project:
        return Response(code=status.HTTP_404_NOT_FOUND, message="关联的 API 项目不存在")

    # 从 steps 中提取 api_call 配置作为 overrides
    # 如果测试用例没有自己的 api_call 配置，则使用会话级别的配置
    overrides = {}
    has_testcase_api_config = False

    # 首先检查测试用例自己的 api_call 配置
    for step in testcase.steps:
        if isinstance(step, dict) and step.get("type") == "api_call":
            overrides = {
                "headers": step.get("headers"),
                "parameters": step.get("parameters"),
                "body": step.get("body"),
                "variables": step.get("variables", []),
                "environment_id": step.get("environment_id"),
                "base_url": step.get("base_url"),
            }
            has_testcase_api_config = True
            # 只取第一个 api_call 配置
            break

    # 如果测试用例没有自己的配置，使用会话级别的配置
    if not has_testcase_api_config:
        session_db = session.get(Session, session_id)
        if session_db and session_db.api_config:
            api_config = session_db.api_config
            overrides = {
                "headers": api_config.get("headers"),
                "environment_id": api_config.get("environment_id"),
            }

    try:
        result = await run_endpoint(session, project, endpoint, overrides)
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"执行测试用例 {testcase_id} 失败: {e}\n{traceback.format_exc()}")
        return Response(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"执行失败: {str(e)}")

    # 根据执行结果更新用例状态??????
    passed = result.get("passed", False)
    testcase.status = "PASSED" if passed else "FAILED"
    session.add(testcase)
    session.commit()
    session.refresh(testcase)

    return Response(
        data={
            "passed": passed,
            "status": testcase.status,
            "result": result,
        },
        message="执行通过" if passed else "执行失败",
    )

