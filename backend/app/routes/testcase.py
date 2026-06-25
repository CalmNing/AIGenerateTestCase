import json
import re
from typing import List, Annotated, Optional

from fastapi import APIRouter, Query, status, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlmodel import select, desc, func

from app.deps import SessionDep, CurrentUser
from app.permissions import Permission, get_user_permissions
from db.models import Session, TestCase, StatusValue, McpServer, TestCaseExecutionLog, ApiEndpoint, ApiProject
from utils.base_response import Response
import traceback
from app.services.api_test_tool import run_endpoint_steps

router = APIRouter(prefix="/testcases", tags=["testcases"])


def infer_endpoint_dependencies(
    session,
    selected_endpoint_ids: list[int],
    api_project_id: int | str | None,
) -> list[int]:
    """推断选中接口缺失的前置依赖接口，返回需要补全的接口 ID 列表。

    规则：
    1. 识别候选"新增"接口：POST 方法且路径不含 /{ 或 /:
    2. 识别"依赖方"接口：路径含 /{xxx} 且参数名含 id，或 body/parameters 含 id 字段但响应无该字段
    3. 如果存在依赖方但未选中任何候选新增接口，则按路径前缀匹配补全
    """
    if not api_project_id or not selected_endpoint_ids:
        return []

    try:
        project_id = int(api_project_id)
    except (ValueError, TypeError):
        return []

    # 加载同项目所有接口
    all_endpoints = session.exec(
        select(ApiEndpoint).where(ApiEndpoint.project_id == project_id)
    ).all()

    if not all_endpoints:
        return []

    selected_ids_set = set(selected_endpoint_ids)
    selected_eps = [ep for ep in all_endpoints if ep.id in selected_ids_set]
    non_selected_eps = [ep for ep in all_endpoints if ep.id not in selected_ids_set]

    # 1. 识别候选新增接口（POST + 非路径参数化）
    path_param_pattern = re.compile(r'/\{|/:')
    candidate_create_eps = [
        ep for ep in non_selected_eps
        if ep.method.upper() == 'POST' and not path_param_pattern.search(ep.path)
    ]

    if not candidate_create_eps:
        return []

    # 2. 检查是否已选中新增接口
    already_has_create = False
    for sel_ep in selected_eps:
        if sel_ep.method.upper() == 'POST' and not path_param_pattern.search(sel_ep.path):
            already_has_create = True
            break

    if already_has_create:
        return []

    # 3. 识别依赖方接口
    id_param_pattern = re.compile(r'\{[^}]*id[^}]*\}', re.IGNORECASE)
    has_dependency = False
    dependency_paths = []

    for ep in selected_eps:
        if id_param_pattern.search(ep.path):
            has_dependency = True
            dependency_paths.append(ep.path)
            continue

        has_id_field = False
        for field_list in [ep.parameters or [], _parse_body_fields(ep.body)]:
            for field in field_list:
                field_name = field.get('key', '') or field.get('name', '') or ''
                if 'id' in field_name.lower():
                    has_id_field = True
                    break
            if has_id_field:
                break

        if has_id_field:
            resp_fields = _get_response_top_fields(ep.response_schema)
            has_id_in_response = any('id' in f.lower() for f in resp_fields)
            if not has_id_in_response:
                has_dependency = True
                dependency_paths.append(ep.path)

    if not has_dependency:
        return []

    # 4. 为每个依赖方匹配最合适的候选新增接口
    result_ids = set()
    for dep_path in dependency_paths:
        best_match = _best_matching_create_endpoint(dep_path, candidate_create_eps)
        if best_match:
            result_ids.add(best_match.id)

    return list(result_ids)


def _parse_body_fields(body: str | None) -> list[dict]:
    """从 body JSON 字符串中解析顶层字段列表。"""
    if not body:
        return []
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            return [{'key': k} for k in parsed.keys()]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _get_response_top_fields(response_schema: dict | None) -> list[str]:
    """从响应 schema 中提取顶层字段名。"""
    if not response_schema:
        return []
    props = response_schema.get('properties', {})
    if not props:
        items = response_schema.get('items', {})
        props = items.get('properties', {})
    return list(props.keys())


def _best_matching_create_endpoint(
    dependency_path: str,
    candidates: list,
):
    """为依赖方接口选择最匹配的候选新增接口。"""
    def path_segments(path: str) -> list[str]:
        return [s for s in path.strip('/').split('/') if s]

    dep_segments = path_segments(dependency_path)
    dep_prefix = '/'.join(dep_segments[:-1]) if len(dep_segments) > 1 else ''

    best = None
    best_score = -1

    for ep in candidates:
        cand_segments = path_segments(ep.path)
        cand_prefix = '/'.join(cand_segments)

        if dep_prefix and cand_prefix == dep_prefix:
            return ep

        if dep_prefix and cand_prefix.startswith(dep_prefix):
            score = len(dep_prefix)
            if score > best_score:
                best_score = score
                best = ep
        elif dep_prefix and dep_prefix.startswith(cand_prefix):
            score = len(cand_prefix)
            if score > best_score:
                best_score = score
                best = ep

    if best:
        return best

    dep_len = len(dep_segments)
    candidates_sorted = sorted(candidates, key=lambda ep: abs(len(path_segments(ep.path)) - dep_len))
    return candidates_sorted[0] if candidates_sorted else None


class TestCasePage(BaseModel):
    items: List[TestCase]
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



def _parse_endpoint_ids(endpoint_id_str: str | int | None) -> list[int]:
    """Parse comma-separated endpoint IDs string into int list."""
    if not endpoint_id_str:
        return []
    if isinstance(endpoint_id_str, int):
        return [endpoint_id_str]
    ids = []
    for part in endpoint_id_str.split(","):
        part = part.strip()
        if part:
            try:
                ids.append(int(part))
            except ValueError:
                pass
    return ids


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
    overrides = {
        "headers": step.get("headers", fallback.get("headers")),
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
    if not testcase or testcase.session_id != session_id:
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

    # 解析 API 端点 Schema 和定义
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
        try:
            raw_overrides = json.loads(api_endpoint_overrides)
            for eid_str, override in raw_overrides.items():
                eid = int(eid_str)
                if eid in api_endpoint_ids:
                    endpoint_overrides[eid] = override
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("端点覆盖配置解析失败，继续使用数据库默认值")

    if api_endpoint_ids:
        api_sections = []
        endpoint_index_to_id = {}
        for idx, eid in enumerate(api_endpoint_ids, 1):
            try:
                endpoint_db = session.get(ApiEndpoint, eid)
                if not endpoint_db:
                    continue
                endpoint_index_to_id[idx] = eid
                project_db = session.get(ApiProject, api_project_id and int(api_project_id) or endpoint_db.project_id)
                ep_lines = []
                dep_label = f" [{dependency_labels[eid]}]" if eid in dependency_labels else ""
                ep_lines.append(f'===== [{idx}] {endpoint_db.name}{dep_label} =====')
                ep_lines.append('所属项目: ' + (project_db.name if project_db else '未知'))
                ep_lines.append('请求方法: ' + endpoint_db.method)
                ep_lines.append('请求路径: ' + endpoint_db.path)
                if endpoint_db.tags:
                    ep_lines.append('标签: ' + ', '.join(endpoint_db.tags))
                if endpoint_db.assertions:
                    ep_lines.append('默认断言: ' + json.dumps(endpoint_db.assertions, ensure_ascii=False, indent=2))
                # 使用用户编辑的值优先，否则使用数据库写入
                override = endpoint_overrides.get(eid, {})
                ep_headers = override.get("headers") if override.get("headers") is not None else endpoint_db.headers
                ep_parameters = override.get("parameters") if override.get("parameters") is not None else endpoint_db.parameters
                ep_body = override.get("body") if override.get("body") is not None else endpoint_db.body
                if ep_headers:
                    ep_lines.append('请求头: ' + json.dumps(ep_headers, ensure_ascii=False, indent=2))
                if ep_parameters:
                    ep_lines.append('请求参数: ' + json.dumps(ep_parameters, ensure_ascii=False, indent=2))
                if endpoint_db.request_schema:
                    ep_lines.append('请求 Schema:\n' + json.dumps(endpoint_db.request_schema, ensure_ascii=False, indent=2))
                if endpoint_db.response_schema:
                    ep_lines.append('响应 Schema:\n' + json.dumps(endpoint_db.response_schema, ensure_ascii=False, indent=2))
                if ep_body:
                    ep_lines.append('请求体示例:\n' + ep_body)
                if endpoint_db.pre_actions:
                    ep_lines.append('前置操作: ' + json.dumps(endpoint_db.pre_actions, ensure_ascii=False, indent=2))
                if endpoint_db.post_actions:
                    ep_lines.append('后置提取: ' + json.dumps(endpoint_db.post_actions, ensure_ascii=False, indent=2))
                api_sections.append('\n'.join(ep_lines))
                logger.info('已加载 API 端点: ' + endpoint_db.name + ' (' + endpoint_db.method + ' ' + endpoint_db.path + ')')
            except Exception as e:
                logger.warning('加载端点 ' + str(eid) + ' 失败: ' + str(e))

        if api_sections:
            all_api_info = '\n\n'.join(api_sections)
            api_context = '\n\n===== 以下 API 接口信息（每个接口前的[数字]为接口编号）=====\n' + all_api_info

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
            api_proxy_url=api_proxy_url,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            mcp_configs=mcp_configs,
            selected_skill_names=selected_skill_names,
            user_id=user.user_id,
            endpoint_index_to_id=endpoint_index_to_id,
            api_project_id=api_project_id_int,
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
    if not testcase or testcase.session_id != session_id or (testcase.user_id and testcase.user_id != user.user_id):
        return Response(code=status.HTTP_404_NOT_FOUND, message="测试用例不存在")

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
