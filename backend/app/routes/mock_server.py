"""Mock 服务器路由 - 拦截匹配的请求并返回配置的响应"""
import copy
import json
import logging
import re
import subprocess
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Response as HttpResponse
from sqlmodel import Session, select

from db.db import engine
from db.models import MockConfig, GlobalParameter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mock", tags=["mock-server"])


def _substitute_builtins(text: str) -> str:
    """替换 {{$function}} 内置函数"""
    def _replace(match):
        expr = match.group(1).strip()

        # $timestamp - 毫秒时间戳
        if expr == "$timestamp":
            return str(int(datetime.now().timestamp() * 1000))

        # $now - 秒级时间戳
        if expr == "$now":
            return str(int(datetime.now().timestamp()))

        # $uuid - 生成UUID
        if expr == "$uuid":
            return str(uuid.uuid4())

        # $randomInt(min,max) - 指定范围随机整数
        rand_match = re.match(r"^\$randomInt\((\d+)\s*,\s*(\d+)\)$", expr)
        if rand_match:
            import random
            lo, hi = int(rand_match.group(1)), int(rand_match.group(2))
            return str(random.randint(lo, hi))

        # $randomInt - 0~100随机整数
        if expr == "$randomInt":
            import random
            return str(random.randint(0, 100))

        # $date(format) - 格式化日期
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

        # $date - 默认 YYYY-MM-DD
        if expr == "$date":
            return datetime.now().strftime("%Y-%m-%d")

        return match.group(0)

    return re.sub(r"\{\{(\$[^}]+)\}\}", _replace, text)


def _eval_js(expression: str) -> str | None:
    """通过 Node.js。执行 JS 表达式，返回字符串结果；失败返回 None"""
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


def _substitute_variables(text: str, env_id: Optional[int], path_params: dict = None) -> str:
    """替换文本中的变量占位符，支持多种格式：
    
    替换顺序:
      1. {{@expression}}`  - 通过 Node.js 执行 JS 表达式
      2. {{$function}}    - 内置函数（$timestamp, $now, $uuid, $randomInt, $date等）
      3. {{variable}}     - 路径参数（优先）或用户定义的环境变量
      4. ${variable}      - 路径参数（优先）或用户定义的环境变量
    
    Args:
        text: 要替换的文本
        env_id: 环境变量ID
        path_params: 路径参数字典（如 {'id': '123'}）
    """
    if not text:
        return text
    
    # 获取环境变量映射
    param_map = {}
    if env_id:
        with Session(engine) as session:
            env = session.exec(select(GlobalParameter).where(GlobalParameter.id == env_id)).first()
        if env:
            for p in env.parameters or []:
                if isinstance(p, dict) and p.get("key"):
                    param_map[p["key"]] = str(p.get("value", ""))
    
    # 合并路径参数（路径参数优先级更高）
    if path_params:
        param_map = {**param_map, **path_params}
    
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
        return param_map.get(key, match.group(0))
    
    result = re.sub(r"\{\{([^}$@][^}]*)\}\}", _replace_double, result)
    
    # 4. ${variable} 用户参数
    def _replace_dollar(match):
        key = match.group(1).strip()
        return param_map.get(key, match.group(0))
    
    result = re.sub(r"\$\{([^}]+)\}", _replace_dollar, result)
    
    return result


def _match_url_path(config: MockConfig, request_path: str) -> tuple[bool, dict]:
    """将配置的 url_path 与请求路径进行匹配，支持 {param} 通配符
    
    支持两种配置方式：
    1. 用户配置 /users/{id}，匹配 /api/mock/users/123（自动去掉 /api/mock 前缀）
    2. 用户配置 /api/mock/users/{id}，匹配 /api/mock/users/123（完整路径匹配）
    
    返回: (是否匹配, 路径参数字典)
    """
    config_path = config.url_path
    
    # 如果配置路径不包含 /api/mock 前缀，则从请求路径中去掉该前缀
    if not config_path.startswith('/api/mock'):
        if request_path.startswith('/api/mock'):
            request_path = request_path[len('/api/mock'):]
            if not request_path.startswith('/'):
                request_path = '/' + request_path
    
    # 将路径参数 {param} 转换为正则表达式
    # 先将 {param} 替换为占位符，然后转义其他字符，最后再替换回正则表达式
    parts = []
    i = 0
    while i < len(config_path):
        if config_path[i] == '{':
            # 找到对应的 }
            j = config_path.find('}', i)
            if j != -1:
                # 提取参数名并转换为正则表达式
                param_name = config_path[i+1:j]
                parts.append(r'(?P<param_' + re.escape(param_name) + r'>[^/]+)')
                i = j + 1
            else:
                # 没有找到对应的 }，转义 {
                parts.append(re.escape(config_path[i]))
                i += 1
        else:
            # 普通字符，转义
            parts.append(re.escape(config_path[i]))
            i += 1
    
    pattern = ''.join(parts)
    logger.debug("Path matching: config_path=%s, request_path=%s, pattern=%s", config_path, request_path, pattern)
    
    try:
        match = re.fullmatch(pattern, request_path)
        if match:
            # 提取路径参数，去掉 param_ 前缀
            path_params = {}
            for key, value in match.groupdict().items():
                if key.startswith('param_'):
                    param_name = key[6:]  # 去掉 'param_' 前缀
                    path_params[param_name] = value
            logger.debug("Path match result: True, params=%s", path_params)
            return True, path_params
        else:
            logger.debug("Path match result: False")
            return False, {}
    except re.error as e:
        logger.warning("Regex error: %s, falling back to exact match", e)
        return config_path == request_path, {}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def mock_handler(request: Request, path: str):
    """拦截所有请求，匹配已启用的Mock配置并返回预设响应"""
    request_path = f"/{path}"
    request_method = request.method

    with Session(engine) as session:
        configs = session.exec(
            select(MockConfig).where(MockConfig.enabled == True)
        ).all()

    matched_config = None
    path_params = {}
    logger.info("Mock matching: method=%s, path=%s, configs_count=%d", request_method, request_path, len(configs))
    
    for config in configs:
        is_match, params = _match_url_path(config, request_path)
        logger.info("Checking config: %s %s -> match=%s, params=%s", config.method, config.url_path, is_match, params)
        
        if config.method.upper() == request_method.upper() and is_match:
            matched_config = config
            path_params = params
            logger.info("Mock matched: %s %s, path_params=%s", config.method, config.url_path, path_params)
            break

    if not matched_config:
        return HttpResponse(
            content=json.dumps({"error": "No matching mock configuration", "path": request_path, "method": request_method}, ensure_ascii=False),
            status_code=404,
            media_type="application/json"
        )

    resp_headers = {}
    for h in (matched_config.response_headers or []):
        if isinstance(h, dict) and h.get("key"):
            resp_headers[h["key"]] = _substitute_variables(str(h["value"]), matched_config.environment_id, path_params)

    body = None
    if matched_config.response_body:
        
        body = _substitute_variables(matched_config.response_body, matched_config.environment_id, path_params)
        logger.info("Original body: %s", body)
        logger.info("Body type: %s", type(body))
        
        # 处理分页逻辑
        if matched_config.response_count > 1:
            try:
                # 解析响应体
                parsed_body = json.loads(body)
                logger.info("Parsed response body: %s", parsed_body)
                logger.info("Parsed body type: %s", type(parsed_body))
                
                # 支持两种格式：
                # 1. 直接数组：[1, 2, 3]
                # 2. 嵌套对象：{ "items": [1, 2, 3] }
                # 3. 深层嵌套：{ "data": { "items": [1, 2, 3] } }
                data_array = None
                if isinstance(parsed_body, list):
                    data_array = parsed_body
                elif isinstance(parsed_body, dict) and 'items' in parsed_body and isinstance(parsed_body['items'], list):
                    data_array = parsed_body['items']
                elif isinstance(parsed_body, dict) and 'data' in parsed_body and isinstance(parsed_body['data'], dict) and 'items' in parsed_body['data'] and isinstance(parsed_body['data']['items'], list):
                    data_array = parsed_body['data']['items']
                
                # 只有当找到数组时才进行分页
                if data_array is not None:
                    # 如果配置的 response_count 大于数组长度，则扩展数组
                    target_count = matched_config.page_size
                    if len(data_array) < target_count:
                        # 重新生成数组元素直到达到目标数量
                        original_len = len(data_array)
                        response_body_template = json.loads(matched_config.response_body)
                        item_template = response_body_template['data']['items'][0]
                        start_time = datetime.now()
                        for _ in range(original_len, target_count):
                            # 重新生成元素并替换变量
                            # 获取原始模板
                      
                            # original_item = data_array[i % original_len]
                            # 转换为字符串并重新替换变量
                            item_str = json.dumps(item_template)
                            item_str = _substitute_variables(item_str, matched_config.environment_id, path_params)
                            data_array.append(json.loads(item_str))
                        end_time = datetime.now()
                        logger.info("Pagination time: %s", end_time - start_time)
                    logger.info("Extended data_array length: %d", len(data_array))
                    # 获取分页参数
                    query_params = dict(request.query_params)
                    page = int(query_params.get('page', 1))
                    page_size = matched_config.page_size or int(query_params.get('page_size', 10))
                    
                    # 计算分页
                    total = matched_config.response_count
                    start = (page - 1) * page_size
                    end = start + page_size
                    page_data = data_array[start:end]
                    
                    # 构建分页响应
                    body = json.dumps({
                        "code": 200,
                        "message": "success",
                        "data": page_data,
                        "pagination": {
                            "total": total,
                            "page": page,
                            "page_size": page_size,
                            "total_pages": (total + page_size - 1) // page_size
                        }
                    }, ensure_ascii=False)
                    
                    logger.info("Mock pagination: total=%d, page=%d, page_size=%d, returned=%d", 
                               total, page, page_size, len(page_data))
                else:
                    # 响应体不是数组或嵌套对象格式，不进行分页，保持原样
                    logger.warning("Response body is not an array or nested object with items array, skipping pagination")
                    try:
                        json.loads(body)
                    except json.JSONDecodeError:
                        pass
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning("Pagination failed: %s, returning original body", e)
                try:
                    json.loads(body)
                except json.JSONDecodeError:
                    pass

    logger.info("Mock matched: %s %s -> %d", request_method, request_path, matched_config.status_code)

    return HttpResponse(
        content=body,
        status_code=matched_config.status_code,
        headers=resp_headers,
        media_type="application/json"
    )
