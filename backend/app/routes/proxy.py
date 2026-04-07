import json
import logging
import re
import subprocess
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlmodel import Session, select
import httpx

from db.db import get_db
from db.models import GlobalParameter, MockConfig

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


def is_valid_url(url: str) -> bool:
    """验证URL是否安全"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not parsed.scheme in ["http", "https"]:
        return False
    return True


def _substitute_builtins(text: str) -> str:
    """替换 {{$function}} 内置函数"""
    def _replace(match):
        expr = match.group(1).strip()

        # $timestamp
        if expr == "$timestamp":
            return str(int(datetime.now().timestamp() * 1000))

        # $now
        if expr == "$now":
            return str(int(datetime.now().timestamp()))

        # $uuid
        if expr == "$uuid":
            return str(uuid.uuid4())

        # $randomInt(min,max)
        rand_match = re.match(r"^\$randomInt\((\d+)\s*,\s*(\d+)\)$", expr)
        if rand_match:
            import random
            lo, hi = int(rand_match.group(1)), int(rand_match.group(2))
            return str(random.randint(lo, hi))

        # $randomInt (0~100)
        if expr == "$randomInt":
            import random
            return str(random.randint(0, 100))

        # $date(format)
        date_match = re.match(r"^\$date\((.+)\)$", expr)
        if date_match:
            fmt = date_match.group(1).strip().strip("'\"")
            now = datetime.now()
            fmt_map = {
                "YYYY": str(now.year),
                "MM": f"{now.month:02d}",
                "DD": f"{now.day:02d}",
                "HH": f"{now.hour:02d}",
                "mm": f"{now.minute:02d}",
                "ss": f"{now.second:02d}",
                "SSS": f"{now.microsecond // 1000:03d}",
            }
            result = fmt
            for key, val in fmt_map.items():
                result = result.replace(key, val)
            return result

        # $date (默认 YYYY-MM-DD)
        if expr == "$date":
            return datetime.now().strftime("%Y-%m-%d")

        return match.group(0)

    return re.sub(r"\{\{(\$[^}]+)\}\}", _replace, text)


def _eval_js(expression: str) -> str | None:
    """通过 Node.js 执行 JS 表达式，返回字符串结果；失败返回 None"""
    try:
        proc = subprocess.run(
            ["node", "-e", f"console.log(({expression}))"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception as e:
        logger.warning("JS expression eval failed: %s, error: %s", expression, e)
    return None


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
    def _replace_js(match):
        expr = match.group(1).strip()
        result = _eval_js(expr)
        return result if result is not None else match.group(0)

    result = re.sub(r"\{\{@([^}]+)\}\}", _replace_js, text)

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
    """构建参数映射表：环境参数 + 本地参数（本地优先级更高）"""
    param_map: dict = {}

    if environment_id:
        global_param = db.get(GlobalParameter, environment_id)
        if global_param:
            for p in global_param.parameters:
                if isinstance(p, dict) and p.get("key") and p.get("value"):
                    param_map[p["key"]] = p["value"]

    for p in (local_parameters or []):
        if isinstance(p, dict) and p.get("key") and p.get("value"):
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



@router.post("/forward")
async def forward_request(
    request: ProxyRequest,
    db: Session = Depends(get_db),
):
    """转发请求到目标URL，支持参数变量替换"""
    # 构建参数映射表
    param_map = build_param_map(db, request.environment_id, [])
    logger.info("param_map: %s", param_map)

    # 收集未解析的用户变量
    unresolved: set[str] = set()

    # 对各部分进行变量替换
    final_url = substitute_variables(request.url, param_map, unresolved)
    final_headers = substitute_in_headers(request.headers, param_map, unresolved)
    final_params = substitute_in_params(request.params, param_map, unresolved)
    final_data = substitute_in_data(request.data, param_map, unresolved)

    # 检查是否有未解析的用户变量
    if unresolved:
        unresolved_list = ", ".join(sorted(unresolved))
        raise HTTPException(
            status_code=400,
            detail=f"以下变量未在环境参数中定义: {unresolved_list}",
        )
    logger.info("Proxy request: url=%s, method=%s, headers=%s, params=%s, json=%s",
                     final_url, request.method, final_headers, final_params, final_data)
    logger.info("final_data_type: %s", type(final_data))
    if not is_valid_url(final_url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    # 检查是否有匹配的 Mock 配置，优先返回 Mock 响应
    mock_result = try_mock_response(db, request.method, request.url, param_map, unresolved)
    if mock_result:
        return mock_result

    async with httpx.AsyncClient() as client:
        
        try:
            response = await client.request(
                method=request.method,
                url=final_url,
                headers=final_headers,
                json=None if not isinstance(final_data, dict) else final_data,
                data=None if not isinstance(final_data, str) else final_data,
                params=final_params,
                timeout=30.0
            )
            # 尝试解析响应为JSON
            try:
                response_data = response.json()
                logger.info("response_data: %s", response_data)
            except Exception:
                response_data = response.text

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "data": response_data
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")
