from typing import List, Annotated, Optional

from fastapi import APIRouter, Query, status, Form, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlmodel import select, desc, func

from app.deps import SessionDep, CurrentUser
from app.permissions import Permission, get_user_permissions
from db.models import Session, TestCase, StatusValue, McpServer, TestCaseExecutionLog, ApiEndpoint, ApiProject, ApiScenario
from utils.base_response import Response
from app.services.testcase_service import (
    infer_endpoint_dependencies,
    _parse_endpoint_ids,
    parse_json_field,
)
import traceback
import json
from datetime import datetime
# NOTE: api_test_tool is imported lazily inside the functions that use it
# (run_scenario / run_endpoint_steps) to avoid a circular import: api_test_tool
# imports app.routes.proxy, which triggers app/routes/__init__.py to import this
# module before api_test_tool has finished initializing.

router = APIRouter(prefix="/testcases", tags=["testcases"])


class TestCasePage(BaseModel):
    items: List[dict]  # dict 而非 TestCase，因为每项附带了额外的 scenario_steps 字段
    totalNumber: int = Field(0, description="总条数")
    passed: int = Field(0, description="已通过的用例数")
    failed: int = Field(0, description="未通过的用例数")
    not_run: int = Field(0, description="未执行的用例数")
    totalBugs: int = Field(0, description="Bug数")
    model_config = {
        "arbitrary_types_allowed": True,
    }


MAX_TESTCASE_EXECUTION_LOGS = 10


def _save_execution_log(db_session, testcase: TestCase, result: dict, passed: bool, status: str) -> TestCaseExecutionLog:
    """保存测试用例执行日志，并清理超出限制的旧日志。"""
    # 移除超出限制的旧日志
    existing_logs = db_session.exec(
        select(TestCaseExecutionLog)
        .where(TestCaseExecutionLog.testcase_id == testcase.id)
        .order_by(desc(TestCaseExecutionLog.created_at))
    ).all()
    if len(existing_logs) >= MAX_TESTCASE_EXECUTION_LOGS:
        for old_log in existing_logs[MAX_TESTCASE_EXECUTION_LOGS - 1:]:
            db_session.delete(old_log)

    log = TestCaseExecutionLog(
        testcase_id=testcase.id,
        session_id=testcase.session_id,
        case_name=testcase.case_name,
        passed=passed,
        status=status,
        result=result,
        user_id=testcase.user_id,
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log



# NOTE: _parse_endpoint_ids 已下沉至 app.services.testcase_service，路由通过顶部导入复用。


async def _run_validation(db, tc: TestCase, env_id: Optional[int] = None) -> dict:
    """手动「验证」：真实执行用例并回写结果（复用 scenario_runner.run_scenario）。

    优先复用用例已有的 ApiScenario；否则基于 api_endpoint_id + steps 临时构建。
    """
    from app.services.scenario_runner import run_scenario
    from db.models import ApiScenario, ApiEndpoint as ApiEndpointModel, ApiProject as ApiProjectModel

    project = db.get(ApiProjectModel, tc.api_project_id)
    if not project:
        raise ValueError("关联 API 项目不存在")
    env_id = env_id or project.environment_id

    scenario = None
    if tc.scenario_id:
        scenario = db.get(ApiScenario, tc.scenario_id)
    if scenario is None:
        endpoint_ids = [int(x) for x in str(tc.api_endpoint_id).split(",") if x.strip().isdigit()]
        index_to_id = {i + 1: eid for i, eid in enumerate(endpoint_ids)}
        scenario_steps = []
        for step in (tc.steps or []):
            if not isinstance(step, dict):
                continue
            ref = step.get("endpoint_ref") or step.get("endpoint_id")
            eid = index_to_id.get(ref) if isinstance(ref, int) else None
            ep = db.get(ApiEndpointModel, eid) if eid else None
            if not ep:
                continue
            scenario_steps.append({
                "endpoint_id": ep.id,
                "description": step.get("description", ""),
                "headers": step.get("headers"),
                "parameters": step.get("parameters"),
                "body": step.get("body"),
                "assertions": step.get("assertions") or tc.assertions,
                "post_actions": step.get("post_actions"),
            })
        scenario = ApiScenario(
            name=f"verify-{tc.id}",
            project_id=project.id,
            environment_id=env_id,
            steps=scenario_steps,
            user_id=tc.user_id,
        )
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        tc.scenario_id = scenario.id

    result = await run_scenario(db, scenario, project)
    passed = bool(result.get("passed"))
    tc.validation_status = "passed" if passed else "failed"
    tc.validation_detail = json.dumps(result, ensure_ascii=False)
    tc.validation_at = datetime.now()
    db.add(tc)
    db.commit()
    db.refresh(tc)
    return {
        "validation_status": tc.validation_status,
        "validation_detail": tc.validation_detail,
        "validation_at": str(tc.validation_at),
    }


def _session_api_overrides(db_session, session_id: int) -> dict:
    """Get session-level API config overrides."""
    session_db = db_session.get(Session, session_id)
    if session_db and session_db.api_config:
        api_config = session_db.api_config
        return {
            "headers": api_config.get("headers"),
            "environment_id": api_config.get("environment_id"),
        }
    return {}


def _testcase_step_snapshot(step: dict, index: int) -> dict:
    """Create a snapshot dict for a step."""
    return {"index": index, "type": step.get("type") or "api_endpoint",
            "name": step.get("name") or step.get("description") or "", "content": step.get("body")}


def _api_call_overrides(step: dict, fallback: dict, test_case_assertions) -> dict:
    """Extract API call overrides from a step dict, merged with session fallback."""
    # 合并 session headers 和 step headers，step headers 优先
    session_headers = fallback.get("headers") or []
    step_headers = step.get("headers") or []

    # 将 session headers 转换为 dict 以便合并
    merged_headers = {}
    for h in session_headers:
        if isinstance(h, dict) and h.get("key"):
            merged_headers[h["key"].lower()] = h

    # step headers 覆盖 session headers
    for h in step_headers:
        if isinstance(h, dict) and h.get("key"):
            merged_headers[h["key"].lower()] = h

    # 转换回列表
    headers = list(merged_headers.values()) if merged_headers else (step_headers or session_headers or None)

    overrides = {
        "headers": headers,
        "parameters": step.get("parameters"),
        "body": step.get("body"),
        "variables": step.get("variables", []),
        "pre_actions": step.get("pre_actions"),
        "post_actions": step.get("post_actions"),
        "assertions": step.get("assertions") or test_case_assertions,
        "environment_id": step.get("environment_id") or fallback.get("environment_id"),
    }
    return overrides


def _endpoint_plan_item(db_session, testcase, endpoint_id: int, overrides: dict, snapshot: dict) -> tuple[dict | None, dict | None]:
    """Build a single plan item for an endpoint."""
    endpoint = db_session.get(ApiEndpoint, endpoint_id)
    if not endpoint:
        return None, {"index": snapshot.get("index"), "status": "error", "detail": f"API 接口 {endpoint_id} 不存在"}
    project = db_session.get(ApiProject, testcase.api_project_id or endpoint.project_id)
    if not project:
        return None, {"index": snapshot.get("index"), "status": "error", "detail": f"API 项目不存在"}
    return {
        "endpoint": endpoint,
        "project": project,
        "overrides": overrides,
        "testcase_step": snapshot,
    }, None


def _build_testcase_execution_plan(db_session, testcase, session_id):
    """Build execution plan from test case steps and preset_conditions."""
    endpoint_ids = _parse_endpoint_ids(testcase.api_endpoint_id)
    fallback = _session_api_overrides(db_session, session_id)
    plan: list[dict] = []
    errors: list[dict] = []
    fallback_endpoint_index = 0

    def _process_api_step(step: dict, index: int) -> None:
        nonlocal fallback_endpoint_index
        explicit_ids = _parse_endpoint_ids(step.get("endpoint_id") or step.get("api_endpoint_id"))
        endpoint_id = explicit_ids[0] if explicit_ids else None
        if endpoint_id is None and fallback_endpoint_index < len(endpoint_ids):
            endpoint_id = endpoint_ids[fallback_endpoint_index]
            fallback_endpoint_index += 1
        snapshot = _testcase_step_snapshot(step, index)
        if endpoint_id is None:
            errors.append({"index": index, "status": "error", "detail": "API 调用步骤未关联接口", "testcase_step": snapshot})
            return
        item, error = _endpoint_plan_item(db_session, testcase, endpoint_id,
                                          _api_call_overrides(step, fallback, testcase.assertions), snapshot)
        if error:
            errors.append(error)
        else:
            plan.append(item)

    next_index = 1

    # Phase 1: preset_conditions api_call steps
    for cond in (testcase.preset_conditions or []):
        if isinstance(cond, dict) and cond.get("type") == "api_call":
            _process_api_step(cond, next_index)
            next_index += 1

    # Phase 2: main steps api_call steps
    api_steps = [step for step in (testcase.steps or [])
                 if isinstance(step, dict) and step.get("type") == "api_call"]

    if api_steps:
        for step in api_steps:
            _process_api_step(step, next_index)
            next_index += 1
        return plan, errors

    # Legacy: no api_call steps, generate one step per endpoint_id
    if not plan:
        tc_overrides = dict(fallback)
        if testcase.assertions:
            tc_overrides["assertions"] = testcase.assertions
        for index, eid in enumerate(endpoint_ids, 1):
            snapshot = {"index": index, "type": "api_endpoint", "name": f"API 接口 {eid}", "content": None}
            item, error = _endpoint_plan_item(db_session, testcase, eid, dict(tc_overrides), snapshot)
            if error:
                errors.append(error)
            else:
                plan.append(item)

    return plan, errors

@router.get("/{session_id}/testcases/{testcase_id}/execution-logs", response_model=Response[List[TestCaseExecutionLog]])
async def get_execution_logs(
    session: SessionDep,
    user: CurrentUser,
    session_id: int,
    testcase_id: int,
):
    """获取测试用例的执行日志列表。"""
    testcase = session.get(TestCase, testcase_id)
    if not testcase or int(testcase.session_id) != session_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="测试用例不存在")

    logs = session.exec(
        select(TestCaseExecutionLog)
        .where(TestCaseExecutionLog.testcase_id == testcase_id)
        .order_by(desc(TestCaseExecutionLog.created_at))
        .limit(MAX_TESTCASE_EXECUTION_LOGS)
    ).all()
    return Response(data=list(logs))


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
        exist_bug: bool = False,
        test_point_id: int = None
):
    """获取会话的测试用例"""
    query = select(TestCase).where(TestCase.session_id == session_id).order_by(TestCase.case_level,
                                                                               desc(TestCase.created_at))
    if module_id:
        query = query.where(TestCase.module_id == module_id)
    if test_point_id:
        query = query.where(TestCase.test_point_id == test_point_id)

    if case_name:
        query = query.where(TestCase.case_name.contains(case_name))
    if status:
        query = query.where(TestCase.status.contains(status))
    if bug_id:
        query = query.where(TestCase.bug_id.contains(bug_id))
    if exist_bug:
        query = query.where(TestCase.bug_id != None)

    testcases_db = session.exec(query.offset(offset).limit(limit)).all()

    # 批量加载关联场景的步骤摘要
    scenario_ids = [tc.scenario_id for tc in testcases_db if tc.scenario_id]
    scenario_steps_map: dict[int, list[dict]] = {}
    if scenario_ids:
        scenarios = session.exec(
            select(ApiScenario).where(ApiScenario.id.in_(scenario_ids))
        ).all()
        for sc in scenarios:
            steps_summary = []
            for step in (sc.steps or []):
                if isinstance(step, dict):
                    steps_summary.append({
                        "method": step.get("method", ""),
                        "path": step.get("path") or step.get("url", ""),
                        "name": step.get("name") or step.get("endpoint_name", ""),
                    })
            scenario_steps_map[sc.id] = steps_summary

    # 附加场景步骤摘要到用例
    tc_items = []
    for tc in testcases_db:
        tc_dict = tc.model_dump()
        tc_dict["scenario_steps"] = scenario_steps_map.get(tc.scenario_id, []) if tc.scenario_id else []
        tc_items.append(tc_dict)

    # 统计各状态计数：一次分组查询 + 一次 bug 计数（替代原 5 次 count 往返）
    status_count_base = [TestCase.session_id == session_id]
    if module_id is not None:
        status_count_base.append(TestCase.module_id == module_id)
    if test_point_id is not None:
        status_count_base.append(TestCase.test_point_id == test_point_id)

    status_rows = session.exec(
        select(TestCase.status, func.count())
        .where(*status_count_base)
        .group_by(TestCase.status)
    ).all()
    status_counts = {row[0]: row[1] for row in status_rows}

    passed = status_counts.get(StatusValue.PASSED.value, 0)
    failed = status_counts.get(StatusValue.FAILED.value, 0)
    not_run = status_counts.get(StatusValue.NOT_RUN.value, 0)
    totalNumber = sum(status_counts.values())

    total_bugs_query = [TestCase.session_id == session_id, TestCase.bug_id != None]
    if module_id is not None:
        total_bugs_query.append(TestCase.module_id == module_id)
    if test_point_id is not None:
        total_bugs_query.append(TestCase.test_point_id == test_point_id)
    totalBugs = session.scalar(select(func.count()).where(*total_bugs_query))

    testcases = TestCasePage(
        items=tc_items,
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
        api_model: str = Form("deepseek-v4-flash"),
        api_proxy_url: str = Form(""),
        ollama_url: str = Form(""),
        ollama_model: str = Form(""),
        module_id: Optional[int|str] = Form(""),
        mcp_servers: Optional[str] = Form(""),
        selected_skills: Optional[str] = Form(""),
        api_endpoint_id: Optional[str] = Form(""),
        api_project_id: Optional[str] = Form(""),
        api_endpoint_overrides: Optional[str] = Form(default=None),
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
    from app.services.mcp_loader import load_user_mcp_configs
    if mcp_servers and mcp_servers.strip():
        logger.info("忽略客户端传入的 MCP 服务器配置，改用服务端已保存配置")
    mcp_configs = load_user_mcp_configs(session, user)

    # 解析选中的技能名称
    selected_skill_names = []
    if selected_skills and selected_skills.strip():
        parsed_skills = parse_json_field(selected_skills)
        if isinstance(parsed_skills, list):
            selected_skill_names = parsed_skills
            logger.info(f"接收到 {len(selected_skill_names)} 个选中技能: {selected_skill_names}")
        else:
            logger.warning(f"Skills 配置解析失败: {selected_skills[:200]}")

    # 解析 API 端点 Schema 和定义
    from app.services.api_context import build_api_context, parse_endpoint_ids
    api_context = ""
    api_endpoint_ids = parse_endpoint_ids(api_endpoint_id)

    # 自动推断依赖接口：补全缺失的前置"新增"接口
    dependency_labels: dict[int, str] = {}  # {endpoint_id: "依赖补全"}
    if api_endpoint_ids and api_project_id:
        extra_ids = infer_endpoint_dependencies(session, api_endpoint_ids, api_project_id)
        if extra_ids:
            new_ids = [eid for eid in extra_ids if eid not in api_endpoint_ids]
            for eid in new_ids:
                api_endpoint_ids.append(eid)
                dependency_labels[eid] = "依赖补全"
            if new_ids:
                logger.info(f"依赖推断：自动补全 {len(new_ids)} 个前置接口 {new_ids}")

    # 解析 API 端点覆盖配置（用户编辑过的 body/headers/parameters）
    endpoint_overrides: dict[int, dict] = {}
    if api_endpoint_overrides:
        raw_overrides = parse_json_field(api_endpoint_overrides)
        if isinstance(raw_overrides, dict):
            try:
                for eid_str, override in raw_overrides.items():
                    eid = int(eid_str)
                    if eid in api_endpoint_ids:
                        endpoint_overrides[eid] = override
            except (ValueError, TypeError):
                logger.warning("端点覆盖配置解析失败，继续使用数据库默认值")
        else:
            logger.warning("端点覆盖配置解析失败，继续使用数据库默认值")

    _proj_id_int = None
    if api_project_id:
        try:
            _proj_id_int = int(api_project_id)
        except (ValueError, TypeError):
            _proj_id_int = None
    api_context, endpoint_index_to_id = build_api_context(
        session, api_endpoint_ids, _proj_id_int, endpoint_overrides,
        dependency_labels=dependency_labels,
    )

    if api_context:
        effective_requirement = api_context + "\n\n## 用户需求\n\n" + requirement
    else:
        effective_requirement = requirement

    try:
        # Convert api_project_id to int once for downstream usage
        api_project_id_int: int | None = None
        if api_project_id:
            try:
                api_project_id_int = int(api_project_id)
            except (ValueError, TypeError):
                api_project_id_int = None

        testcases, effective_req = await generate_testcases(
            db_session=session,
            requirement=effective_requirement,
            session_id=session_id,
            module_id=module_id,
            model_type=model_type,
            api_key=api_key,
            api_base_url=api_base_url,
            api_model=api_model,
            api_proxy_url=api_proxy_url,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            mcp_configs=mcp_configs,
            selected_skill_names=selected_skill_names,
            user_id=user.user_id,
            endpoint_index_to_id=endpoint_index_to_id,
            api_project_id=api_project_id_int,
            is_api_test=bool(api_context),
        )
    except ModelServiceUnavailableError as e:
        logger.error(f"生成测试用例失败: {e}")
        return Response(code=status.HTTP_503_SERVICE_UNAVAILABLE, message=str(e))
    except ValueError as e:
        logger.error(f"生成测试用例失败: {e}")
        return Response(code=status.HTTP_400_BAD_REQUEST, message=str(e))
    # 自动填充 user_id
    for tc in testcases:
        tc.user_id = user.user_id
        if api_endpoint_id:
            # Save as comma-separated string for multi-endpoint support
            tc.api_endpoint_id = api_endpoint_id
        if api_project_id_int is not None:
            tc.api_project_id = api_project_id_int
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
    """执行测试用例关联的 API 接口并保存执行日志。"""
    testcase = session.get(TestCase, testcase_id)
    if not testcase or int(testcase.session_id) != session_id or (testcase.user_id and testcase.user_id != user.user_id):
        return Response(code=status.HTTP_404_NOT_FOUND, message="测试用例不存在")

    # 优先使用关联的场景执行（保持参数同步）
    if testcase.scenario_id:
        scenario = session.get(ApiScenario, testcase.scenario_id)
        if scenario and scenario.user_id == user.user_id:
            project = session.get(ApiProject, scenario.project_id)
            if project and project.user_id == user.user_id:
                try:
                    from app.services.api_test_tool import run_scenario
                    # 获取 session headers
                    fallback = _session_api_overrides(session, session_id)
                    session_headers = fallback.get("headers") or []

                    result = await run_scenario(session, scenario, project, session_headers=session_headers)
                    passed = result.get("passed", False)
                    testcase.status = "PASSED" if passed else "FAILED"
                    session.add(testcase)
                    session.commit()
                    log = _save_execution_log(session, testcase, result, passed, testcase.status)
                    return Response(data={
                        "passed": passed,
                        "status": testcase.status,
                        "result": result,
                        "log_id": log.id,
                    })
                except Exception as e:
                    logger = __import__("logging").getLogger(__name__)
                    logger.error(f"场景执行失败，回退到用例步骤: {e}")

    has_api_presets = any(
        isinstance(c, dict) and c.get("type") == "api_call"
        for c in (testcase.preset_conditions or [])
    )
    has_api_steps = any(
        isinstance(s, dict) and s.get("type") == "api_call"
        for s in (testcase.steps or [])
    )
    if not testcase.api_endpoint_id and not has_api_presets and not has_api_steps:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="该测试用例未关联 API 接口")

    plan, preflight_errors = _build_testcase_execution_plan(session, testcase, session_id)

    try:
        if plan:
            from app.services.api_test_tool import run_endpoint_steps
            result = await run_endpoint_steps(session, plan)
            if preflight_errors:
                result["steps"] = preflight_errors + result.get("steps", [])
                result["passed"] = False
        else:
            result = {"passed": False, "variables": {}, "steps": list(preflight_errors)}

        passed = result.get("passed", False)
        testcase.status = "PASSED" if passed else "FAILED"
        session.add(testcase)
        session.commit()

        log = _save_execution_log(session, testcase, result, passed, testcase.status)

        return Response(data={
            "passed": passed,
            "status": testcase.status,
            "result": result,
            "log_id": log.id,
        })
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"执行测试用例 {testcase_id} 失败: {e}\n{traceback.format_exc()}")

        error_result = {"steps": [{"index": 1, "status": "error", "detail": str(e)}]}
        try:
            _save_execution_log(session, testcase, error_result, False, "FAILED")
        except Exception:
            pass

        return Response(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"执行失败: {str(e)}")


@router.post("/{session_id}/testcases/{testcase_id}/infer-dependencies", response_model=Response)
def infer_testcase_dependencies(
    session: SessionDep,
    user: CurrentUser,
    session_id: int,
    testcase_id: int,
):
    """推断测试用例的接口依赖关系，返回需要补全的接口 ID 列表。"""
    testcase = session.get(TestCase, testcase_id)
    if not testcase or int(testcase.session_id) != session_id or (testcase.user_id and testcase.user_id != user.user_id):
        return Response(code=status.HTTP_404_NOT_FOUND, message="测试用例不存在")

    if not testcase.api_endpoint_id or not testcase.api_project_id:
        return Response(data={"added_post_actions": 0, "replaced_fields": 0, "extra_endpoint_ids": []})

    # 解析关联的接口 ID
    endpoint_ids = []
    for part in str(testcase.api_endpoint_id).split(','):
        part = part.strip()
        if part:
            try:
                endpoint_ids.append(int(part))
            except ValueError:
                pass

    if not endpoint_ids:
        return Response(data={"added_post_actions": 0, "replaced_fields": 0, "extra_endpoint_ids": []})

    # 调用依赖推断
    extra_ids = infer_endpoint_dependencies(session, endpoint_ids, testcase.api_project_id)

    return Response(data={
        "added_post_actions": 0,
        "replaced_fields": 0,
        "extra_endpoint_ids": extra_ids,
    })


@router.post("/{testcase_id}/validate", response_model=Response)
async def validate_testcase(
    testcase_id: int,
    payload: Optional[dict] = None,
    user: CurrentUser = None,
    db: SessionDep = None,
):
    """手动触发真实执行验证：复用 scenario_runner 实跑并回写结果。"""
    tc = db.get(TestCase, testcase_id)
    if not tc:
        raise HTTPException(status_code=404, detail="测试用例不存在")
    if not tc.api_project_id or not tc.api_endpoint_id:
        raise HTTPException(status_code=400, detail="仅接口测试用例支持验证")

    env_id = None
    if isinstance(payload, dict):
        env_id = payload.get("environment_id")
    try:
        data = await _run_validation(db, tc, env_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return Response(data=data)
