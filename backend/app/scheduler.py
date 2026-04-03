"""定时任务调度器服务 - 使用 APScheduler 管理定时任务"""
import json
import logging
from datetime import datetime

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from jsonpath_ng import parse
from sqlmodel import Session, select

from db.db import engine
from db.models import ScheduledTask, SavedRequest, GlobalParameter
from app.routes.proxy import (
    build_param_map, substitute_variables, substitute_in_headers,
    substitute_in_data, substitute_in_params, is_valid_url,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def execute_scheduled_task(task_id: int):
    """执行定时任务：按顺序执行所有关联的请求"""
    with Session(engine) as db:
        task = db.get(ScheduledTask, task_id)
        if not task or not task.enabled:
            return

        logger.info("Executing scheduled task [%s] (id=%d)", task.name, task_id)
        results = []

        # 构建环境参数映射
        param_map = build_param_map(db, task.environment_id, [])
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
                # 变量替换
                final_url = substitute_variables(saved_req.url, param_map, unresolved)
                headers_dict = {h["key"]: h["value"] for h in saved_req.headers if h.get("key") and h.get("value")}
                final_headers = substitute_in_headers(headers_dict, param_map, unresolved)
                params_dict = {p["key"]: p["value"] for p in saved_req.parameters if p.get("key") and p.get("value")}
                final_params = substitute_in_params(params_dict, param_map, unresolved)

                # 解析 body
                request_data = None
                if saved_req.body and saved_req.method in ("POST", "PUT", "PATCH"):
                    try:
                        request_data = json.loads(saved_req.body)
                    except json.JSONDecodeError:
                        request_data = saved_req.body
                final_data = substitute_in_data(request_data, param_map, unresolved)

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

                # 发送请求
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
                        }
                    }

                    # 后置提取：从响应中提取变量并保存到环境参数
                    if saved_req.post_extractions and task.environment_id and isinstance(response_data, (dict, list)):
                        env = db.get(GlobalParameter, task.environment_id)
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

        # 更新任务状态
        task.last_run_at = datetime.now()
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
