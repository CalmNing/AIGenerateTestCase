"""定时任务调度器服务 - 使用 APScheduler 管理定时任务"""
import json
import logging
import re
from datetime import datetime
from typing import Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from jsonpath_ng import parse
from sqlmodel import Session, select

from db.db import engine
from db.models import ScheduledTask, SavedRequest, GlobalParameter
from app.routes.proxy import (
    ProxyRequest, forward_core,
    build_param_map, substitute_variables, substitute_in_headers,
    substitute_in_data, substitute_in_params, is_valid_url,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 任务参数类型
TASK_PARAM_TYPE_TEXT = "text"
TASK_PARAM_TYPE_FILE = "file"
TASK_PARAM_TYPE_IMAGE_BASE64 = "image_base64"


def _task_param_entries(parameters: list) -> list:
    """挑出可以当普通变量注入的任务参数。

    - file 类型：值是 fileId，同样注入变量表 —— 请求参数里 {{变量名}}
      解析成 fileId 后会走 file_params 文件上传通道；
    - image_base64 类型：值已是 base64 字符串，正常注入；
    - 其余（text）：正常注入。

    不注入会导致请求参数里引用文件变量时报「变量未定义」。
    """
    entries = []
    for p in (parameters or []):
        if not isinstance(p, dict):
            continue
        if not p.get("key") or not p.get("value"):
            continue
        entries.append(p)
    return entries


def build_task_param_map(db, task) -> dict:
    """构建任务执行用的参数映射表。

    变量优先级（后者覆盖前者）：分组变量 < 环境变量 < 任务参数。

    环境取 task.environment_id；任务没选环境时回退到分组绑定的环境。
    任务参数只合并自身（不走 build_param_map 的环境降级），避免无关
    环境的变量混进来覆盖已选环境的值。
    """
    group_env_id, group_vars = _group_context(db, task)
    env_id = task.environment_id if task.environment_id is not None else group_env_id

    param_map: dict = {}
    for p in group_vars:
        if isinstance(p, dict) and p.get("key") and p.get("value") is not None:
            param_map[p["key"]] = p["value"]

    env_map = build_param_map(db, env_id, [])
    param_map.update(env_map)

    for p in _task_param_entries(task.parameters or []):
        if isinstance(p, dict) and p.get("key") and p.get("value") is not None:
            param_map[p["key"]] = p["value"]
    return param_map


def _group_context(db, task) -> tuple[Optional[int], list]:
    """取任务所属分组的上下文。

    返回 (环境ID, 分组变量列表)。任务本身没归组时返回 (None, [])。
    """
    group_id = getattr(task, "group_id", None)
    if not group_id:
        return None, []
    from db.models import TaskGroup

    group = db.get(TaskGroup, group_id)
    if not group:
        return None, []
    return group.environment_id, list(group.variables or [])


def _decode_task_image_base64(param: dict) -> str:
    """把 image_base64 类型的任务参数值规范化成 base64 字符串。

    前端存的是纯 base64（不含 data: 前缀），这里做一次兜底解析：
    去掉 data:image/...;base64, 前缀，并确认它是合法图片 base64；
    非法时回退为原始值，避免整个任务因一个参数而失败。
    """
    from app.services.image_base64 import decode_base64_image

    raw = str(param.get("value") or "")
    decoded, _mime = decode_base64_image(raw)
    if decoded is None:
        logger.warning(
            "任务参数 %s 的 image_base64 值不是合法图片 base64，按原值注入", param.get("key")
        )
        return raw
    return raw

scheduler = AsyncIOScheduler()


def strip_json_comments(text: str) -> str:
    """去除 JSON 字符串中的注释（支持 // 和 /* */）"""
    result = []
    i = 0
    length = len(text)
    in_string = False
    string_char = ''
    while i < length:
        if in_string:
            if text[i] == '\\' and i + 1 < length:
                result.append(text[i])
                result.append(text[i + 1])
                i += 2
                continue
            if text[i] == string_char:
                in_string = False
            result.append(text[i])
            i += 1
        elif text[i] in ('"', "'"):
            in_string = True
            string_char = text[i]
            result.append(text[i])
            i += 1
        elif text[i] == '/' and i + 1 < length and text[i + 1] == '/':
            while i < length and text[i] != '\n':
                i += 1
        elif text[i] == '/' and i + 1 < length and text[i + 1] == '*':
            i += 2
            while i < length and not (text[i] == '*' and i + 1 < length and text[i + 1] == '/'):
                i += 1
            if i < length:
                i += 2
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)


async def execute_scheduled_task(task_id: int, *, force_run: bool = False):
    """执行定时任务：按顺序执行所有关联的请求

    Args:
        force_run: 手动执行时为 True，忽略 enabled 检查
    """
    with Session(engine) as db:
        task = db.get(ScheduledTask, task_id)
        if not task:
            return
        if not force_run and not task.enabled:
            return

        logger.info("Executing scheduled task [%s] (id=%d)", task.name, task_id)
        results = []

        # 变量优先级（后者覆盖前者）：
        #   分组变量 < 环境变量 < 任务参数
        # 统一走 build_task_param_map：环境取任务自身绑定，任务参数只合并自身，
        # 不触发 build_param_map 的环境降级（否则会把无关环境的变量混进来）。
        param_map = build_task_param_map(db, task)
        group_env_id, _group_vars = _group_context(db, task)
        env_id = task.environment_id if task.environment_id is not None else group_env_id
        logger.info("Task [%s] param_map (env=%s, task_params=%s): %s",
                     task.name, task.environment_id, task.parameters, param_map)
        unresolved: set[str] = set()

        for req_id in task.request_ids:
            saved_req = db.get(SavedRequest, req_id)
            if not saved_req:
                results.append({
                    "request_id": req_id,
                    "request_name": f"Unknown(ID:{req_id})",
                    "status": "error",
                    "detail": "请求配置不存在"
                })
                continue

            try:
                # 前置步骤：先执行（关联请求按 id 现取最新配置 / 自定义请求），
                # 产出的变量并入 param_map（仅本次请求生效，不写环境）
                if saved_req.pre_steps:
                    from app.services.pre_steps import run_pre_steps

                    try:
                        param_map, _pre_step_results = await run_pre_steps(
                            saved_req.pre_steps, dict(param_map), db=db,
                        )
                    except Exception as pre_exc:  # noqa: BLE001 - 前置失败不阻断主请求
                        logger.warning("Task [%s] request %s pre_steps error: %s", task.name, req_id, pre_exc)

                # 变量替换
                final_url = substitute_variables(saved_req.url, param_map, unresolved)
                headers_dict = {h["key"]: h["value"] for h in saved_req.headers if h.get("key") and h.get("value")}
                final_headers = substitute_in_headers(headers_dict, param_map, unresolved)

                # 解析 body（去除注释后再解析）
                request_data = None
                if saved_req.body and saved_req.method in ("POST", "PUT", "PATCH"):
                    try:
                        clean_body = strip_json_comments(saved_req.body)
                        request_data = json.loads(clean_body)
                    except json.JSONDecodeError:
                        request_data = saved_req.body
                final_data = substitute_in_data(request_data, param_map, unresolved)

                # 处理请求参数：分离文件类型参数和文本参数（支持任务参数优先级）
                final_request_params = {}
                file_params = []
                for p in (saved_req.parameters or []):
                    if not p.get("key"):
                        continue
                    key = p["key"]
                    raw_value = p.get("value", "")

                    # 文件类型参数：提取为 file_params。值为空时回退同名任务变量
                    # （任务参数里的 file 类型 value 即 fileId），拿到 fileId 才走文件上传
                    if p.get("type") == "file" and not raw_value:
                        raw_value = str(param_map.get(key) or "")
                    if p.get("type") == "file" and raw_value:
                        resolved_value = substitute_variables(str(raw_value), param_map, unresolved)
                        # 如果解析成功（不再是模板占位符），作为文件参数
                        if resolved_value and "{{" not in resolved_value:
                            file_params.append({
                                "key": key,
                                "fileId": resolved_value,
                                "fileName": p.get("fileName") or f"{key}.dat",
                            })
                            continue

                    # 文本参数：正常变量替换
                    resolved = substitute_variables(str(raw_value), param_map, unresolved)
                    final_request_params[key] = resolved

                # 对最终文本参数再做一次未解析变量检查
                final_params = substitute_in_params(final_request_params, param_map, unresolved)

                if unresolved:
                    results.append({
                        "request_id": req_id,
                        "request_name": saved_req.name,
                        "status": "error",
                        "detail": f"变量未定义: {', '.join(sorted(unresolved))}",
                        "request": {
                            "url": final_url,
                            "method": saved_req.method,
                            "headers": final_headers,
                            "params": final_params,
                            "body": final_data
                        }
                    })
                    unresolved.clear()
                    continue

                if not is_valid_url(final_url):
                    results.append({
                        "request_id": req_id,
                        "request_name": saved_req.name,
                        "status": "error",
                        "detail": "无效的URL",
                        "request": {
                            "url": final_url,
                            "method": saved_req.method,
                            "headers": final_headers,
                            "params": final_params,
                            "body": final_data
                        }
                    })
                    continue

                # 发送请求（如果有文件参数，走代理核心以支持文件上传）
                if file_params:
                    # 进程内直调代理核心（forward_core）：等价于带认证的
                    # 代理转发接口，但不需要 Bearer token，也不依赖后端
                    # 监听地址/端口。user=None 时环境权限按无权限降级。
                    try:
                        proxy_result = await forward_core(
                            ProxyRequest(
                                url=final_url,
                                method=saved_req.method,
                                headers=final_headers,
                                params=final_params,
                                data=final_data,
                                file_params=file_params,
                                environment_id=env_id,
                            ),
                            user=None,
                            db=db,
                        )

                        # forward_core 失败时抛 HTTPException，由外层 except
                        # 捕获后写入 status=error 的结果；正常返回必然带
                        # status_code，非 2xx 记 warning 便于排查。
                        response_data = proxy_result.get("data")
                        status_code = proxy_result.get("status_code", 500)
                        if not (200 <= status_code < 300):
                            logger.warning(
                                "Task [%s] request %s 上游返回 %s: %s",
                                task.name, req_id, status_code,
                                json.dumps(response_data, ensure_ascii=False)[:300],
                            )

                        result_entry = {
                            "request_id": req_id,
                            "request_name": saved_req.name,
                            "status": "success" if 200 <= status_code < 300 else "failed",
                            "status_code": status_code,
                            "request": {
                                "url": final_url,
                                "method": saved_req.method,
                                "headers": final_headers,
                                "params": final_params,
                                "body": final_data,
                                "file_params": file_params,
                            },
                            "response": {
                                "status_code": status_code,
                                "body": response_data,
                            }
                        }

                        # 后置提取（与下方逻辑一致）
                        if saved_req.post_extractions and env_id and isinstance(response_data, (dict, list)):
                            env = db.get(GlobalParameter, env_id)
                            if env:
                                params = list(env.parameters)
                                param_index = {p.get("key"): i for i, p in enumerate(params) if isinstance(p, dict) and p.get("key")}
                                extracted = {}
                                for rule in saved_req.post_extractions:
                                    if not rule.get("variable") or not rule.get("jsonpath"):
                                        continue
                                    try:
                                        jsonpath_expr = parse(rule["jsonpath"])
                                        matches = jsonpath_expr.find(response_data)
                                        if matches:
                                            value = matches[0].value
                                            val_str = str(value) if not isinstance(value, str) else value
                                            extracted[rule["variable"]] = val_str
                                            if rule["variable"] in param_index:
                                                idx = param_index[rule["variable"]]
                                                params[idx] = {**params[idx], "value": val_str}
                                            else:
                                                params.append({"key": rule["variable"], "value": val_str})
                                                param_index[rule["variable"]] = len(params) - 1
                                    except Exception:
                                        pass
                                if extracted:
                                    env.parameters = params
                                    db.add(env)
                                    db.commit()
                                    param_map.update(extracted)
                                    result_entry["extracted"] = extracted

                        results.append(result_entry)
                        continue
                    except Exception as e:
                        results.append({
                            "request_id": req_id,
                            "request_name": saved_req.name,
                            "status": "error",
                            "detail": str(e),
                            "request": {
                                "url": final_url,
                                "method": saved_req.method,
                                "headers": final_headers,
                                "params": final_params,
                                "body": final_data,
                                "file_params": file_params,
                            }
                        })
                        continue

                # 无文件参数时直接发送
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        method=saved_req.method,
                        url=final_url,
                        headers=final_headers,
                        json=final_data if isinstance(final_data, (dict, list)) else None,
                        content=final_data if isinstance(final_data, str) else None,
                        params=final_params,
                        timeout=30.0
                    )
                    try:
                        response_data = response.json()
                    except Exception:
                        response_data = response.text

                    result_entry = {
                        "request_id": req_id,
                        "request_name": saved_req.name,
                        "status": "success" if 200 <= response.status_code < 300 else "failed",
                        "status_code": response.status_code,
                        "request": {
                            "url": final_url,
                            "method": saved_req.method,
                            "headers": final_headers,
                            "params": final_params,
                            "body": final_data
                        },
                        "response": {
                            "status_code": response.status_code,
                            "headers": dict(response.headers),
                            "body": response_data,
                        }
                    }

                    # 后置提取：从响应中提取变量并保存到环境参数
                    if saved_req.post_extractions and env_id and isinstance(response_data, (dict, list)):
                        env = db.get(GlobalParameter, env_id)
                        if env:
                            params = list(env.parameters)
                            param_index = {p.get("key"): i for i, p in enumerate(params) if isinstance(p, dict) and p.get("key")}
                            extracted = {}
                            for rule in saved_req.post_extractions:
                                if not rule.get("variable") or not rule.get("jsonpath"):
                                    continue
                                try:
                                    jsonpath_expr = parse(rule["jsonpath"])
                                    matches = jsonpath_expr.find(response_data)
                                    if matches:
                                        value = matches[0].value
                                        val_str = str(value) if not isinstance(value, str) else value
                                        extracted[rule["variable"]] = val_str
                                        if rule["variable"] in param_index:
                                            idx = param_index[rule["variable"]]
                                            params[idx] = {**params[idx], "value": val_str}
                                        else:
                                            params.append({"key": rule["variable"], "value": val_str})
                                            param_index[rule["variable"]] = len(params) - 1
                                except Exception:
                                    pass
                            if extracted:
                                env.parameters = params
                                db.add(env)
                                db.commit()
                                param_map.update(extracted)
                                result_entry["extracted"] = extracted

                    results.append(result_entry)

            except Exception as e:
                results.append({
                    "request_id": req_id,
                    "request_name": saved_req.name,
                    "status": "error",
                    "detail": str(e)
                })

        # 更新任务状态。last_run_at 必须与其他时间字段同用 Asia/Shanghai：
        # 若进程时区是 UTC，naive datetime.now() 会存成 UTC 值，
        # 前端按本地解析后显示错 8 小时，让用户误以为执行发生在编辑之前。
        from db.models import cn_tz

        task.last_run_at = datetime.now(tz=cn_tz)
        task.last_run_result = json.dumps(results, ensure_ascii=False, default=str)
        db.add(task)
        db.commit()

        logger.info("Scheduled task [%s] completed, %d requests executed", task.name, len(results))


def add_job(task: ScheduledTask):
    """根据任务配置添加 APScheduler job"""
    job_id = f"scheduled_task_{task.id}"
    # 先移除已有的
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    if not task.enabled:
        return

    if task.schedule_type == "cron" and task.cron_expression:
        parts = task.cron_expression.strip().split()
        if len(parts) == 6:
            # 6部分表达式: 秒 分 时 日 月 星期
            trigger = CronTrigger(
                second=parts[0],
                minute=parts[1],
                hour=parts[2],
                day=parts[3],
                month=parts[4],
                day_of_week=parts[5],
            )
        else:
            # 5部分表达式: 分 时 日 月 星期
            trigger = CronTrigger(
                minute=parts[0] if len(parts) > 0 else "*",
                hour=parts[1] if len(parts) > 1 else "*",
                day=parts[2] if len(parts) > 2 else "*",
                month=parts[3] if len(parts) > 3 else "*",
                day_of_week=parts[4] if len(parts) > 4 else "*",
            )
    else:
        trigger = IntervalTrigger(seconds=max(task.interval_seconds, 1))

    scheduler.add_job(execute_scheduled_task, trigger, args=[task.id], id=job_id, replace_existing=True)


def remove_job(task_id: int):
    """移除 APScheduler job"""
    job_id = f"scheduled_task_{task_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def load_all_jobs():
    """启动时加载所有启用的任务"""
    with Session(engine) as db:
        tasks = db.exec(select(ScheduledTask).where(ScheduledTask.enabled == True)).all()
        for task in tasks:
            add_job(task)
        logger.info("Loaded %d scheduled tasks", len(tasks))
