"""前置步骤执行器。

前置步骤用于 IoT 数据推送平台的请求链路，三种类型：

1. set_variable —— 直接设置变量，不发请求；
2. request      —— 先发一个 HTTP 请求，再按 JSONPath 从响应里提取变量。
   来源两种（source）：saved（关联已有请求，执行时后端按 id 现取最新配置，
   前端只需传 id）与 custom（前端内联配置）；
3. params       —— 一次性注入一组参数变量，不发请求。参数统一在这里设置，
   其余步骤类型不再单独配置参数。

提取出的变量只在本请求链路内生效（不写入环境参数），依次注入到后续
前置步骤与主请求中，因此主请求可以用 {{var}} 占位符引用前置步骤取到的值。

步骤之间按顺序串行执行；后面的步骤能引用前面步骤产生的变量。
"""
import logging
from typing import Any, Optional

import httpx
from jsonpath_ng import parse as parse_jsonpath
from sqlmodel import Session

from app.services.image_base64 import (
    FORMAT_RAW,
    to_base64_variable,
)

logger = logging.getLogger(__name__)

# 前置步骤类型
STEP_SET_VARIABLE = "set_variable"
STEP_REQUEST = "request"
STEP_PARAMS = "params"

# request 步骤来源
SOURCE_CUSTOM = "custom"
SOURCE_SAVED = "saved"

# 提取规则类型：普通取值 / 图片 base64
EXTRACT_JSON = "json"
EXTRACT_IMAGE_BASE64 = "image_base64"

# 前置步骤请求超时
PRE_STEP_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


def _extract_by_jsonpath(response_data: Any, jsonpath_expr: str) -> Optional[str]:
    """按 JSONPath 从响应里取值，取不到返回 None。"""
    if not jsonpath_expr or not isinstance(response_data, (dict, list)):
        return None
    try:
        matches = parse_jsonpath(jsonpath_expr).find(response_data)
    except Exception as exc:  # noqa: BLE001 - 表达式非法不应中断整条链路
        logger.warning("前置步骤 JSONPath 解析失败 [%s]: %s", jsonpath_expr, exc)
        return None
    if not matches:
        return None
    value = matches[0].value
    return value if isinstance(value, str) else str(value)


def _substitute(text: str, variables: dict, unresolved: set) -> str:
    """在字符串里替换 {{var}} 与美元花括号占位符。

    unresolved 只用于记录，不阻断请求（与 proxy 的严格模式不同）。
    """
    import re

    if not text:
        return text

    def _replace(match):
        key = match.group(1).strip()
        if key in variables:
            return str(variables[key])
        unresolved.add(key)
        return match.group(0)

    result = re.sub(r"\{\{([^}$@][^}]*)\}\}", _replace, text)
    result = re.sub(r"\$\{([^}]+)\}", _replace, result)
    return result


def _substitute_deep(value, variables: dict, unresolved: set):
    """递归替换 dict / list / str 里的变量。"""
    if isinstance(value, str):
        return _substitute(value, variables, unresolved)
    if isinstance(value, dict):
        return {k: _substitute_deep(v, variables, unresolved) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_deep(v, variables, unresolved) for v in value]
    return value


def _normalize_kv_entries(raw: Any) -> dict:
    """把请求头 / 查询参数规整成 {key: value}。

    兼容两种形态：{k: v} 字典，以及 [{key, value, enabled}] 列表
    （前端 PreStepKv 结构，enabled 为 False 的条目不参与发送）。
    """
    result: dict = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            name = str(k or "").strip()
            if name:
                result[name] = v
        return result
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("key") or item.get("name") or "").strip()
            if not name:
                continue
            if item.get("enabled", True) is False:
                continue
            result[name] = item.get("value", "")
    return result


def _normalize_step(step: dict) -> Optional[dict]:
    """把前端传来的步骤规整成内部结构，非法步骤返回 None。"""
    if not isinstance(step, dict):
        return None

    kind = step.get("type") or STEP_SET_VARIABLE

    if kind == STEP_SET_VARIABLE:
        key = (step.get("key") or step.get("variable") or "").strip()
        if not key:
            return None
        return {
            "type": STEP_SET_VARIABLE,
            "key": key,
            "value": step.get("value", ""),
            "enabled": step.get("enabled", True) is not False,
        }

    if kind == STEP_PARAMS:
        # 参数步骤：一次性注入一组变量。兼容 List[{key,value}] 与 {k:v} 两种形态。
        entries = []
        raw_params = step.get("params") if step.get("params") is not None else step.get("param_items")
        if isinstance(raw_params, dict):
            for k, v in raw_params.items():
                name = str(k or "").strip()
                if name:
                    entries.append((name, v))
        elif isinstance(raw_params, list):
            for item in raw_params:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("key") or item.get("name") or "").strip()
                if not name or item.get("enabled", True) is False:
                    continue
                entries.append((name, item.get("value", "")))
        else:
            # 顶层直接是 {key,value} 列表形态
            name = str(step.get("key") or step.get("variable") or "").strip()
            if name:
                entries.append((name, step.get("value", "")))
        if not entries:
            return None
        return {
            "type": STEP_PARAMS,
            "params": entries,
            "enabled": step.get("enabled", True) is not False,
        }

    if kind == STEP_REQUEST:
        source = str(step.get("source") or SOURCE_CUSTOM).strip().lower()
        if source not in (SOURCE_CUSTOM, SOURCE_SAVED):
            source = SOURCE_CUSTOM
        if source == SOURCE_SAVED:
            # 关联已有请求：url/method 允许为空，执行时按 saved_request_id 现取最新配置
            try:
                saved_id = int(step.get("saved_request_id"))
            except (TypeError, ValueError):
                return None
            return {
                "type": STEP_REQUEST,
                "source": SOURCE_SAVED,
                "saved_request_id": saved_id,
                "name": str(step.get("name") or "").strip() or ("关联请求#" + str(saved_id)),
                # 用户在前置步骤里配置的提取规则（执行时优先于关联请求的后置提取）
                "extracts": step.get("extracts") or [],
                "enabled": step.get("enabled", True) is not False,
            }
        url = (step.get("url") or "").strip()
        if not url:
            return None
        return {
            "type": STEP_REQUEST,
            "source": SOURCE_CUSTOM,
            "name": step.get("name") or url,
            "method": (step.get("method") or "GET").upper(),
            "url": url,
            "headers": _normalize_kv_entries(step.get("headers")),
            "params": _normalize_kv_entries(step.get("params")),
            "body": step.get("body"),
            "extracts": [
                {
                    "key": (e.get("key") or e.get("variable") or "").strip(),
                    "jsonpath": (e.get("jsonpath") or "").strip(),
                    # 提取类型：json（普通取值）/ image_base64（图片转 base64）
                    "type": (e.get("type") or EXTRACT_JSON),
                    # 图片 base64 是否压缩（仅 image_base64 有效）
                    "compress": e.get("compress", True) is not False,
                    # 输出格式：raw（纯 base64）/ data_url（完整 data:image URL）
                    "format": (e.get("format") or FORMAT_RAW),
                }
                for e in (step.get("extracts") or [])
                if isinstance(e, dict)
            ],
            "enabled": step.get("enabled", True) is not False,
        }

    return None


def _load_saved_request(db: Optional[Session], saved_request_id: int) -> Optional[dict]:
    """按 id 读取已保存请求，转成 request 步骤的内部结构。不存在返回 None。"""
    if db is None:
        return None
    try:
        from db.models import SavedRequest

        row = db.get(SavedRequest, saved_request_id)
    except Exception as exc:  # noqa: BLE001 - 读取失败不中断链路
        logger.warning("前置步骤读取关联请求 %s 失败: %s", saved_request_id, exc)
        return None
    if row is None:
        return None

    headers: dict[str, str] = {}
    for item in row.headers or []:
        if isinstance(item, dict) and item.get("key") and item.get("enabled", True) is not False:
            headers[str(item["key"])] = str(item.get("value") or "")

    params: dict[str, str] = {}
    for item in row.parameters or []:
        if isinstance(item, dict) and item.get("key") and item.get("enabled", True) is not False:
            if item.get("type") == "file":
                # 文件参数走主链路的 file_params 通道，前置请求暂不支持
                continue
            params[str(item["key"])] = str(item.get("value") or "")

    extracts = [
        {
            "key": (e.get("variable") or e.get("key") or "").strip(),
            "jsonpath": (e.get("jsonpath") or "").strip(),
            "type": EXTRACT_JSON,
        }
        for e in (row.post_extractions or [])
        if isinstance(e, dict) and (e.get("variable") or e.get("key"))
    ]

    return {
        "type": STEP_REQUEST,
        "source": SOURCE_SAVED,
        "saved_request_id": saved_request_id,
        "name": row.name or ("关联请求#" + str(saved_request_id)),
        "method": (row.method or "GET").upper(),
        "url": row.url or "",
        "headers": headers,
        "params": params,
        "body": row.body,
        "extracts": extracts,
        "enabled": True,
    }


async def run_pre_steps(
    steps: list | None,
    variables: dict,
    client: Optional[httpx.AsyncClient] = None,
    db: Optional[Session] = None,
    user_id: Optional[str] = None,
) -> tuple[dict, list[dict]]:
    """执行前置步骤，把产生的变量合并进 variables。

    返回 (变量表, 每个步骤的执行结果)。变量表是原地更新的，
    便于调用方接着用它去解析主请求。

    saved 来源的 request 步骤在执行时按 saved_request_id 现取最新配置，
    保证关联请求保存后无需同步步骤。
    """
    results: list[dict] = []
    if not steps:
        return variables, results

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=PRE_STEP_TIMEOUT)

    try:
        for index, raw_step in enumerate(steps):
            step = _normalize_step(raw_step)
            if step is None:
                continue
            if not step.get("enabled", True):
                continue

            if step["type"] == STEP_SET_VARIABLE:
                unresolved: set = set()
                value = _substitute(str(step.get("value", "")), variables, unresolved)
                variables[step["key"]] = value
                results.append({
                    "index": index,
                    "type": STEP_SET_VARIABLE,
                    "key": step["key"],
                    "value": value,
                    "ok": True,
                })
                continue

            if step["type"] == STEP_PARAMS:
                injected = {}
                for key, raw_value in step["params"]:
                    unresolved = set()
                    value = _substitute_deep(raw_value, variables, unresolved)
                    variables[key] = value
                    injected[key] = value
                results.append({
                    "index": index,
                    "type": STEP_PARAMS,
                    "params": injected,
                    "ok": True,
                })
                continue

            # request 类型：关联请求先取最新配置
            if step.get("source") == SOURCE_SAVED:
                loaded = _load_saved_request(db, step["saved_request_id"])
                if loaded is None:
                    results.append({
                        "index": index,
                        "type": STEP_REQUEST,
                        "name": step.get("name"),
                        "ok": False,
                        "error": "关联的请求不存在或已被删除",
                    })
                    continue
                saved_name = step.get("name")
                # 用户在前置步骤里配置的提取规则优先；未配置时沿用关联请求的后置提取规则
                user_extracts = step.get("extracts")
                step = {**loaded, "name": loaded["name"] if loaded["name"] else saved_name}
                if user_extracts:
                    step["extracts"] = user_extracts

            # 发请求并提取变量
            unresolved = set()
            url = _substitute(step["url"], variables, unresolved)
            headers = _substitute_deep(step.get("headers") or {}, variables, unresolved)
            params = _substitute_deep(step.get("params") or {}, variables, unresolved)
            body = _substitute_deep(step.get("body"), variables, unresolved)

            entry = {
                "index": index,
                "type": STEP_REQUEST,
                "name": step["name"],
                "method": step["method"],
                "url": url,
                "status_code": 0,
                "extracted": {},
                "ok": False,
                "error": None,
            }

            try:
                response = await client.request(
                    method=step["method"],
                    url=url,
                    headers=headers,
                    params=params,
                    json=body if isinstance(body, (dict, list)) else None,
                    data=None if isinstance(body, (dict, list)) else body,
                )
                entry["status_code"] = response.status_code
                entry["ok"] = 200 <= response.status_code < 300
                try:
                    response_data = response.json()
                except Exception:
                    response_data = response.text

                extracted = {}
                for rule in step.get("extracts") or []:
                    key = rule.get("key")
                    if not key:
                        continue
                    raw_value = _extract_by_jsonpath(response_data, rule.get("jsonpath") or "")
                    if raw_value is None:
                        continue

                    if rule.get("type") == EXTRACT_IMAGE_BASE64:
                        # 图片转 base64：失败则不写入变量，避免把脏值带进后续步骤
                        converted = to_base64_variable(
                            raw_value,
                            compress=rule.get("compress", True) is not False,
                            fmt=rule.get("format") or FORMAT_RAW,
                        )
                        if converted is None:
                            logger.warning(
                                "前置步骤图片 base64 转换失败 [%s]，跳过该变量", key
                            )
                            continue
                        value = converted
                    else:
                        value = raw_value

                    variables[key] = value
                    extracted[key] = value
                entry["extracted"] = extracted
                entry["response_data"] = response_data
            except Exception as exc:  # noqa: BLE001 - 单个前置步骤失败不阻断主请求
                logger.warning("前置步骤执行失败 [%s]: %s", step["name"], exc)
                entry["error"] = str(exc)

            results.append(entry)
    finally:
        if owns_client:
            await client.aclose()

    return variables, results