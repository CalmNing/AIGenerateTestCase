import json
import logging
import re
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlmodel import Session, select
import httpx

from app.deps import get_db
from db.models import GlobalParameter, MockConfig, PushLog
from app.auth import UserInfo
from app.deps import CurrentUser
from app.permissions import Permission, get_user_permissions
from utils.base_response import Response
from utils.js_expression import substitute_js_expressions
from utils.log_redact import redact_base64
from utils.template_builtins import substitute_builtins

router = APIRouter(prefix="/proxy", tags=["proxy"])
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 允许的域名白名单（可选，增强安全性）
ALLOWED_DOMAINS = [
    "example.com",
    "api.example.org",
]


class ProxyRequest(BaseModel):
    """代理请求模型"""
    url: str
    method: str = "GET"
    headers: Optional[dict] = None
    data: Optional[dict | List | str] = None
    params: Optional[dict] = None
    environment_id: Optional[int] = None  # 全局参数环境ID
    # 文件参数：[{key, fileId, fileName}]。fileId 会按 key 注入变量表，
    # 请求体里的 {{key}} 占位符解析成 fileId 值（与定时任务执行链路一致）
    file_params: Optional[List[dict]] = None
    # 前置步骤：先执行（发请求取变量 / 直接设置变量），再发主请求
    pre_steps: Optional[List[dict]] = None
    # 前置参数列表：列定义变量名，每行一组值
    batch_params: Optional[dict] = None


def is_valid_url(url: str) -> bool:
    """验证URL是否安全"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not parsed.scheme in ["http", "https"]:
        return False
    return True


def _substitute_builtins(text: str) -> str:
    """替换 {{$function}} 内置函数

    实现收敛在 utils.template_builtins，Mock 服务器共用同一份逻辑。
    """
    return substitute_builtins(text)


def substitute_variables(text: str, param_map: dict, unresolved: set) -> str:
    """在字符串中替换所有变量占位符，未找到的变量名记录到 unresolved 集合

    替换顺序:
      1. {{@expression}}  - 通过 Node.js 执行 JS 表达式
      2. {{$function}}    - 内置函数
      3. {{variable}}     - 用户定义的参数
      4. ${variable}      - 用户定义的参数
    """
    if not text:
        return text

    # 1. {{@expression}} — 通过 Node.js 执行 JS 表达式
    result = substitute_js_expressions(text)

    # 2. {{$function}} 内置函数
    result = _substitute_builtins(result)

    # 3. {{variable}} 用户参数
    def _replace_double(match):
        key = match.group(1).strip()
        if key in param_map:
            return str(param_map[key])
        unresolved.add(key)
        return match.group(0)

    result = re.sub(r"\{\{([^}$@][^}]*)\}\}", _replace_double, result)

    # 4. ${variable} 用户参数
    def _replace_dollar(match):
        key = match.group(1).strip()
        if key in param_map:
            return str(param_map[key])
        unresolved.add(key)
        return match.group(0)

    result = re.sub(r"\$\{([^}]+)\}", _replace_dollar, result)

    return result


def substitute_in_headers(headers: dict | None, param_map: dict, unresolved: set) -> dict:
    """对 headers 中的值进行变量替换"""
    if not headers:
        return headers
    return {k: substitute_variables(str(v), param_map, unresolved) for k, v in headers.items()}


def substitute_in_data(data, param_map: dict, unresolved: set):
    """对 data 进行递归变量替换"""
    if data is None:
        return None
    if isinstance(data, str):
        return substitute_variables(data, param_map, unresolved)
    if isinstance(data, dict):
        return {k: substitute_in_data(v, param_map, unresolved) for k, v in data.items()}
    if isinstance(data, list):
        return [substitute_in_data(item, param_map, unresolved) for item in data]
    return data


def substitute_in_params(params: dict | None, param_map: dict, unresolved: set) -> dict | None:
    """对 URL params 进行变量替换"""
    if not params:
        return params
    return {k: substitute_variables(str(v), param_map, unresolved) for k, v in params.items()}


def build_param_map(db: Session, environment_id: Optional[int], local_parameters: list) -> dict:
    """构建参数映射表：环境参数 + 本地参数（本地优先级更高）

    当 environment_id 未指定时，自动降级：
      1. 查找标记为默认的环境参数
      2. 查找任意一个环境参数
      3. 返回空映射
    """
    param_map: dict = {}

    # 1. 尝试指定环境
    global_param = db.get(GlobalParameter, environment_id) if environment_id else None
    # 2. 尝试默认环境
    if global_param is None:
        global_param = db.exec(select(GlobalParameter).where(GlobalParameter.is_default == True)).first()
    # 3. 尝试任意环境
    if global_param is None:
        global_param = db.exec(select(GlobalParameter)).first()

    if global_param:
        for p in global_param.parameters:
            if isinstance(p, dict) and p.get("key") and p.get("value"):
                param_map[p["key"]] = p["value"]

    for p in (local_parameters or []):
        if not isinstance(p, dict) or not p.get("key") or not p.get("value"):
            continue
        # file 类型参数的 value 是 fileId，同样注入变量表：
        # 请求参数/请求体里的 {{变量名}} 解析成 fileId 后，scheduler 据此
        # 把该参数归入 file_params 走文件上传通道；不注入会报「变量未定义」
        param_map[p["key"]] = p["value"]

    return param_map


def _mock_match_url(config: MockConfig, request_path: str) -> bool:
    """将配置的 url_path 与请求路径进行匹配，支持 {param} 通配符"""
    pattern = re.escape(config.url_path)
    pattern = pattern.replace(r'\{', '(?P<param_[^}]+>[^/]+)').replace(r'\}', '')
    try:
        return re.fullmatch(pattern, request_path) is not None
    except re.error:
        return config.url_path == request_path


def _mock_substitute(text: str, env_id: Optional[int]) -> str:
    """替换文本中的 {{variable}} 占位符为环境变量值"""
    if not env_id or not text:
        return text
    from db.db import engine as _engine
    from sqlmodel import Session as _Session
    with _Session(_engine) as session:
        env = session.get(GlobalParameter, env_id)
    if not env:
        return text
    param_map = {}
    for p in env.parameters or []:
        if isinstance(p, dict) and p.get("key"):
            param_map[p["key"]] = str(p.get("value", ""))

    def replacer(match):
        return param_map.get(match.group(1), match.group(0))

    return re.sub(r'\{\{(\w+)\}\}', replacer, text)


def try_mock_response(db: Session, method: str, url: str, param_map: dict, unresolved: set):
    """检查是否有匹配的 Mock 配置，如果有则返回 mock 响应字典，否则返回 None。

    使用与 proxy 相同的变量替换逻辑处理 url 中的变量，以便正确匹配。
    """
    enabled_mocks = db.exec(select(MockConfig).where(MockConfig.enabled == True)).all()
    if not enabled_mocks:
        return None

    # 对 url 中的变量进行替换（但不检查 unresolved，因为 mock 匹配优先）
    resolved_url = substitute_variables(url, param_map, set())

    # 提取 URL 的 path 部分
    from urllib.parse import urlparse
    parsed = urlparse(resolved_url)
    request_path = parsed.path

    for config in enabled_mocks:
        if config.method.upper() == method.upper() and _mock_match_url(config, request_path):
            # 匹配成功，构建 mock 响应
            resp_headers = {}
            for h in (config.response_headers or []):
                if isinstance(h, dict) and h.get("key"):
                    resp_headers[h["key"]] = _mock_substitute(str(h["value"]), config.environment_id)

            body = None
            if config.response_body:
                body = _mock_substitute(config.response_body, config.environment_id)
                try:
                    body = json.loads(body)
                except (json.JSONDecodeError, TypeError):
                    pass

            # 对 body 也进行代理层面的变量替换
            import json as _json
            if isinstance(body, str):
                body = substitute_variables(body, param_map, unresolved)
            elif isinstance(body, (dict, list)):
                body = substitute_in_data(body, param_map, unresolved)

            logger.info("Mock intercepted: %s %s -> %d", method, request_path, config.status_code)
            return {
                "status_code": config.status_code,
                "headers": resp_headers,
                "data": body,
                "mocked": True,
            }

    return None



class TestVariableRequest(BaseModel):
    """测试变量请求模型"""
    expression: str
    environment_id: Optional[int] = None


def _body_to_text(data) -> Optional[str]:
    """把请求/响应体统一序列化为文本，便于落库展示

    体里常见的图片/文件 base64 串会被替换成占位符，避免日志与
    推送记录被撑到几十万字符；需要原始数据时从业务侧重新生成。
    """
    if data is None:
        return None
    text = data if isinstance(data, str) else None
    if text is None:
        try:
            text = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(data)
    return redact_base64(text)



_PUSHLOG_PRE_STEPS_READY = False


def _ensure_pushlog_pre_steps_column(db: Session) -> None:
    """惰性补列：pushlog.pre_steps 列不存在时实时补上。

    模型新增列依赖启动时的 _migrate_missing_columns；对未重启的长驻进程，
    第一次写推送日志前在这里补列，避免前置步骤记录静默丢失。
    """
    global _PUSHLOG_PRE_STEPS_READY
    if _PUSHLOG_PRE_STEPS_READY:
        return
    try:
        from sqlalchemy import inspect, text

        insp = inspect(db.get_bind())
        columns = {col["name"].lower() for col in insp.get_columns("pushlog")}
        if "pre_steps" not in columns:
            db.exec(text('ALTER TABLE pushlog ADD COLUMN pre_steps JSON DEFAULT "[]"'))
            db.commit()
            logger.info("自动补列: pushlog.pre_steps (JSON)")
        _PUSHLOG_PRE_STEPS_READY = True
    except Exception as exc:  # noqa: BLE001 - 补列失败不阻断推送
        logger.warning("pushlog.pre_steps 补列失败（前置步骤记录可能缺失）: %s", exc)


def _trim_pre_step_results(results: list) -> list:
    """把前置步骤执行结果裁剪成适合落库展示的形态。

    保留每个步骤的请求方法/地址/状态码/提取变量/错误，以及响应体摘要
    （超长截断），方便在推送记录里回看前置请求的响应内容。

    提取变量与响应体先做 base64 脱敏：前置步骤经常提取图片 base64，
    原样落库会把单条记录撑到几十万字符。
    """
    trimmed: list = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        entry = {
            "index": item.get("index"),
            "type": item.get("type"),
            "name": item.get("name"),
            "method": item.get("method"),
            "url": item.get("url"),
            "status_code": item.get("status_code"),
            "extracted": redact_base64(item.get("extracted") or {}),
            "ok": item.get("ok"),
            "error": item.get("error"),
        }
        if item.get("type") == "params":
            entry["params"] = redact_base64(item.get("params") or {})
        elif item.get("type") == "set_variable":
            entry["key"] = item.get("key")
            value = item.get("value")
            entry["value"] = value if (value is None or len(str(value)) <= 500) else str(value)[:500] + "…(截断)"
        response_data = item.get("response_data")
        if response_data is not None:
            entry["response_body"] = _body_to_text(response_data)
            text_body = entry.get("response_body") or ""
            if len(text_body) > 2000:
                entry["response_body"] = text_body[:2000] + "…(截断)"
        trimmed.append(entry)
    return trimmed


def _save_push_log(
    db: Session,
    *,
    user: Optional[UserInfo],
    request: ProxyRequest,
    environment_id: Optional[int],
    final_url: str,
    final_headers: Optional[dict],
    final_params: Optional[dict],
    final_data,
    duration_ms: int,
    response_status: int = 0,
    response_headers: Optional[dict] = None,
    response_body=None,
    mocked: bool = False,
    error: Optional[str] = None,
    pre_step_results: Optional[list] = None,
):
    """持久化一条推送请求记录（原始 + 解析后），失败只告警不阻断转发"""
    # pushlog.pre_steps 列不存在时惰性补上（兼容未重启的旧进程）
    _ensure_pushlog_pre_steps_column(db)
    try:
        log = PushLog(
            request_method=(request.method or "GET").upper(),
            source_url=request.url or "",
            final_url=final_url or "",
            request_headers=request.headers or {},
            final_headers=final_headers or {},
            request_params=request.params or {},
            final_params=final_params or {},
            request_body=_body_to_text(request.data),
            final_body=_body_to_text(final_data),
            response_status=response_status,
            response_headers=response_headers or {},
            response_body=_body_to_text(response_body),
            mocked=mocked,
            error=error,
            duration_ms=duration_ms,
            environment_id=environment_id,
            user_id=getattr(user, "user_id", None),
            pre_steps=_trim_pre_step_results(pre_step_results or []),
        )
        db.add(log)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 记录失败不影响转发
        db.rollback()
        logger.warning("保存推送请求记录失败: %s", exc)


async def _forward_once(
    request: ProxyRequest,
    user: Optional[UserInfo],
    db: Session,
    environment_id: Optional[int],
    param_map: dict,
    pre_step_results: list,
    start_time: float,
) -> dict:
    # 收集未解析的用户变量
    unresolved: set[str] = set()

    # 对各部分进行变量替换
    final_url = substitute_variables(request.url, param_map, unresolved)
    final_headers = substitute_in_headers(request.headers, param_map, unresolved)
    final_params = substitute_in_params(request.params, param_map, unresolved)
    final_data = substitute_in_data(request.data, param_map, unresolved)

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - start_time) * 1000)

    # 检查是否有未解析的用户变量
    if unresolved:
        unresolved_list = ", ".join(sorted(unresolved))
        _save_push_log(
            db, user=user, request=request, environment_id=environment_id,
            final_url=final_url, final_headers=final_headers, final_params=final_params,
            final_data=final_data, duration_ms=_elapsed_ms(),
            error=f"以下变量未在环境参数中定义: {unresolved_list}",
            pre_step_results=pre_step_results,
        )
        raise HTTPException(
            status_code=400,
            detail=f"以下变量未在环境参数中定义: {unresolved_list}",
        )
    logger.info("Proxy request: url=%s, method=%s, headers=%s, params=%s, json=%s",
                     final_url, request.method, redact_base64(final_headers),
                     redact_base64(final_params), redact_base64(final_data))
    logger.info("final_data_type: %s", type(final_data))
    if not is_valid_url(final_url):
        _save_push_log(
            db, user=user, request=request, environment_id=environment_id,
            final_url=final_url, final_headers=final_headers, final_params=final_params,
            final_data=final_data, duration_ms=_elapsed_ms(), error="Invalid URL",
            pre_step_results=pre_step_results,
        )
        raise HTTPException(status_code=400, detail="Invalid URL")

    # 检查是否有匹配的 Mock 配置，优先返回 Mock 响应
    mock_result = try_mock_response(db, request.method, request.url, param_map, unresolved)
    if mock_result:
        _save_push_log(
            db, user=user, request=request, environment_id=environment_id,
            final_url=final_url, final_headers=final_headers, final_params=final_params,
            final_data=final_data, duration_ms=_elapsed_ms(),
            response_status=mock_result.get("status_code", 0),
            response_headers=mock_result.get("headers") or {},
            response_body=mock_result.get("data"),
            mocked=True,
            pre_step_results=pre_step_results,
        )
        if pre_step_results:
            mock_result["pre_steps"] = pre_step_results
        return mock_result

    async with httpx.AsyncClient() as client:

        try:
            response = await client.request(
                method=request.method,
                url=final_url,
                headers=final_headers,
                json=None if not isinstance(final_data, dict | list) else final_data,
                data=None if not isinstance(final_data, str) else final_data,
                params=final_params,
                timeout=30.0
            )
            # 尝试解析响应为JSON
            try:
                response_data = response.json()
                logger.info("response_data: %s", redact_base64(response_data))
            except Exception:
                response_data = response.text

            _save_push_log(
                db, user=user, request=request, environment_id=environment_id,
                final_url=final_url, final_headers=final_headers, final_params=final_params,
                final_data=final_data, duration_ms=_elapsed_ms(),
                response_status=response.status_code,
                response_headers=dict(response.headers),
                response_body=response_data,
                pre_step_results=pre_step_results,
            )
            result = {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "data": response_data
            }
            if pre_step_results:
                result["pre_steps"] = pre_step_results
            return result
        except Exception as e:
            _save_push_log(
                db, user=user, request=request, environment_id=environment_id,
                final_url=final_url, final_headers=final_headers, final_params=final_params,
                final_data=final_data, duration_ms=_elapsed_ms(),
                error=f"Proxy error: {str(e)}",
                pre_step_results=pre_step_results,
            )
            raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")




async def forward_core(
    request: ProxyRequest,
    user: Optional[UserInfo],
    db: Session,
) -> dict:
    """转发核心实现，路由层与定时任务调度器共用。

    user 传 None 表示内部链路（定时任务调度器直调，无 HTTP 认证上下文）；
    权限校验按「无用户=无 GLOBAL_PARAMETER_MANAGE 权限」降级，与
    mqtt/websocket 路由的内部执行路径一致。
    """
    start_time = time.perf_counter()
    # 构建参数映射表
    environment_id = request.environment_id
    if environment_id and (
        user is None or Permission.GLOBAL_PARAMETER_MANAGE not in get_user_permissions(user)
    ):
        environment_id = None
    param_map = build_param_map(db, environment_id, [])
    logger.info("param_map: %s", redact_base64(param_map))

    # 文件参数：fileId 按 key 注入变量表（与定时任务执行链路一致），
    # 请求体/请求头里的 {{key}} 占位符即可解析成 fileId 值
    for fp in (request.file_params or []):
        if isinstance(fp, dict) and fp.get("key") and fp.get("fileId"):
            param_map[str(fp["key"])] = str(fp["fileId"])

    # 前置步骤：先执行一次，产出的变量并入 param_map（仅本次链路生效，不写环境）
    pre_step_results: list[dict] = []
    if request.pre_steps:
        from app.services.pre_steps import run_pre_steps

        try:
            param_map, pre_step_results = await run_pre_steps(
                request.pre_steps, dict(param_map), db=db,
                user_id=getattr(user, "user_id", None),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("pre_steps error: %s", exc)
            pre_step_results = [{"type": "error", "ok": False, "error": str(exc)}]

    # ---- 批量模式：按前置参数列表逐行循环 ----
    from app.services.batch_params import has_batch, run_batch

    if has_batch(request.batch_params):
        async def _forward_one(row_param_map: dict) -> dict:
            return await _forward_once(
                request, user, db, environment_id, row_param_map, pre_step_results, start_time,
            )

        batch = await run_batch(request.batch_params, param_map, _forward_one)
        logger.info("batch done: total=%s passed=%s failed=%s", batch["total"], batch["passed"], batch["failed"])
        return {
            "status_code": 200 if not batch["failed"] else 207,
            "headers": {},
            "data": None,
            "batch": batch,
        }

    # ---- normal single forward ----
    return await _forward_once(
        request, user, db, environment_id, param_map, pre_step_results, start_time,
    )


@router.post("/forward")
async def forward_request(
    request: ProxyRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    """转发请求到目标URL，支持参数变量替换"""
    return await forward_core(request, user, db)

@router.post("/test-variable")
async def test_variable(
    request: TestVariableRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    """测试变量表达式，返回替换后的值"""
    # 构建参数映射表
    environment_id = request.environment_id
    if environment_id and Permission.GLOBAL_PARAMETER_MANAGE not in get_user_permissions(user):
        environment_id = None
    param_map = build_param_map(db, environment_id, [])

    # 收集未解析的变量
    unresolved: set[str] = set()

    # 执行变量替换
    result = substitute_variables(request.expression, param_map, unresolved)

    return Response(data={
        "expression": request.expression,
        "result": result,
        "unresolved": list(unresolved) if unresolved else [],
    })
