# -*- coding: utf-8 -*-
"""前置参数列表（批量执行）服务。

设计：proxy.py 保持轻量，批量循环放在本模块，通过回调执行单次转发。

列（column）的类型：
- text（默认）：普通文本值，直接作为变量；
- image：该列的值是图片（base64 串 / data:image URL / http(s) 图片地址），
  循环时转成 base64 变量（可配置压缩与输出格式）；
- image_base64：该列的值已经是 base64 字符串，只需按图片校验并规范化；
- number：数值列，校验为合法数字（非法值跳过该变量）；
- boolean：布尔列，true/false/1/0/yes/no 归一化，非法值跳过该变量；
- json：JSON 列，校验并压缩为单行，非法值跳过该变量。
"""
import logging
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

# 失败策略
ON_ERROR_CONTINUE = "continue"
ON_ERROR_STOP = "stop"

# 单次批量最多执行多少行（防止误配打爆上游）
MAX_BATCH_ROWS = 200

# 列类型
COLUMN_TEXT = "text"
COLUMN_IMAGE = "image"
COLUMN_IMAGE_BASE64 = "image_base64"
COLUMN_NUMBER = "number"
COLUMN_BOOLEAN = "boolean"
COLUMN_JSON = "json"

# 图片类型列：图片地址下载的超时
IMAGE_FETCH_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _column_name(col: Any) -> str:
    """列名：兼容字符串列名与 {name, type} 结构。"""
    if isinstance(col, dict):
        return str(col.get("name") or col.get("key") or "").strip()
    return str(col or "").strip()


def _column_type(col: Any) -> str:
    """列类型：默认 text。"""
    if isinstance(col, dict):
        kind = str(col.get("type") or COLUMN_TEXT).strip().lower()
    else:
        kind = COLUMN_TEXT
    if kind not in (COLUMN_TEXT, COLUMN_IMAGE, COLUMN_IMAGE_BASE64, COLUMN_NUMBER, COLUMN_BOOLEAN, COLUMN_JSON):
        kind = COLUMN_TEXT
    return kind


def _column_options(col: Any) -> dict:
    """取图片类型列的选项：是否压缩、输出格式。"""
    if not isinstance(col, dict):
        return {"compress": True, "format": "raw"}
    fmt = str(col.get("format") or "raw").strip().lower()
    if fmt not in ("raw", "data_url"):
        fmt = "raw"
    return {"compress": col.get("compress", True) is not False, "format": fmt}


def normalize_batch_params(batch_params: Any) -> tuple[list[dict], list[list[str]], str]:
    """规整批量参数。

    返回 (列定义列表, 行值列表, 失败策略)。
    列定义统一成 {"name": str, "type": str, "compress": bool, "format": str}。
    """
    if not isinstance(batch_params, dict):
        return [], [], ON_ERROR_CONTINUE

    columns = batch_params.get("columns") or []
    if not isinstance(columns, list):
        return [], [], ON_ERROR_CONTINUE

    clean_columns: list[dict] = []
    seen: set[str] = set()
    for col in columns:
        name = _column_name(col)
        if not name or name in seen:
            continue
        seen.add(name)
        opts = _column_options(col)
        clean_columns.append({
            "name": name,
            "type": _column_type(col),
            "compress": opts["compress"],
            "format": opts["format"],
        })

    rows_raw = batch_params.get("rows") or []
    if not isinstance(rows_raw, list):
        rows_raw = []

    clean_rows: list[list[str]] = []
    for row in rows_raw:
        if not isinstance(row, list):
            continue
        values = [("" if v is None else str(v)) for v in row]
        if len(values) < len(clean_columns):
            values = values + [""] * (len(clean_columns) - len(values))
        elif len(values) > len(clean_columns):
            values = values[: len(clean_columns)]
        clean_rows.append(values)

    if len(clean_rows) > MAX_BATCH_ROWS:
        logger.warning("批量行数 %d 超过上限 %d，已截断", len(clean_rows), MAX_BATCH_ROWS)
        clean_rows = clean_rows[:MAX_BATCH_ROWS]

    on_error = str(batch_params.get("on_error") or ON_ERROR_CONTINUE).strip().lower()
    if on_error not in (ON_ERROR_CONTINUE, ON_ERROR_STOP):
        on_error = ON_ERROR_CONTINUE

    return clean_columns, clean_rows, on_error


async def _fetch_remote_image(url: str):
    """下载远程图片。失败返回 None。"""
    try:
        async with httpx.AsyncClient(timeout=IMAGE_FETCH_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                return None
            return response.content
    except Exception as exc:  # noqa: BLE001 - 取图失败不阻断整批
        logger.warning("下载图片失败 [%s]: %s", url, exc)
        return None


async def _column_value_to_base64(value: str, column: dict):
    """把图片类型列的单元格值转成 base64 变量值。

    - http(s) 开头：先下载图片再编码；
    - 其余：当作 base64 / data:image URL 处理。

    失败返回 None（该变量不注入）。
    """
    from app.services.image_base64 import (
        decode_base64_image,
        encode_image,
        format_output,
    )

    raw = (value or "").strip()
    if not raw:
        return None

    compress = column.get("compress", True) is not False
    fmt = column.get("format") or "raw"

    if raw.lower().startswith("http://") or raw.lower().startswith("https://"):
        data = await _fetch_remote_image(raw)
        if not data:
            return None
        b64, mime = encode_image(data, compress=compress)
        if b64 is None:
            return None
        return format_output(b64, mime, fmt)

    # 已经是 base64 或 data:image URL
    decoded, mime_hint = decode_base64_image(raw)
    if decoded is None:
        logger.warning("图片列 [%s] 的值不是有效的 base64 图片，跳过", column.get("name"))
        return None
    b64, mime = encode_image(decoded, compress=compress)
    if b64 is None:
        return None
    return format_output(b64, mime or mime_hint, fmt)


def _convert_typed_value(kind: str, value: str, name: str):
    """按列类型转换单元格值；非法值返回 None（该变量不注入）。"""
    raw = (value or "").strip()
    if kind == COLUMN_NUMBER:
        try:
            float(raw)
        except ValueError:
            logger.warning("数值列 [%s] 的值不是合法数字: %r，跳过该变量", name, value)
            return None
        return raw
    if kind == COLUMN_BOOLEAN:
        low = raw.lower()
        if low in ("true", "1", "yes", "y", "是"):
            return "true"
        if low in ("false", "0", "no", "n", "否"):
            return "false"
        logger.warning("布尔列 [%s] 的值无法识别: %r，跳过该变量", name, value)
        return None
    if kind == COLUMN_JSON:
        import json as _json
        try:
            return _json.dumps(_json.loads(raw), ensure_ascii=False, separators=(",", ":"))
        except (ValueError, TypeError):
            logger.warning("JSON 列 [%s] 的值不是合法 JSON，跳过该变量", name)
            return None
    return value

async def row_to_variables(columns: list[dict], values: list[str]) -> dict:
    """把一行值转成变量表（列名 -> 值）。

    图片类型列的值会先转成 base64；转换失败则不产生该变量。
    空值的列不产生变量。
    """
    variables: dict[str, str] = {}
    for column, value in zip(columns, values):
        name = column["name"]
        if not name or value == "":
            continue
        kind = column.get("type")
        if kind in (COLUMN_NUMBER, COLUMN_BOOLEAN, COLUMN_JSON):
            converted = _convert_typed_value(kind, value, name)
            if converted is not None:
                variables[name] = converted
            continue
        if kind == COLUMN_TEXT:
            variables[name] = value
            continue
        converted = await _column_value_to_base64(value, column)
        if converted is not None:
            variables[name] = converted
    return variables


def has_batch(batch_params: Any) -> bool:
    """列和行都有才走批量。"""
    columns, rows, _ = normalize_batch_params(batch_params)
    return bool(columns) and bool(rows)


async def run_batch(
    batch_params: Any,
    base_param_map: dict,
    forward_one: Callable[[dict], Awaitable[dict]],
) -> dict:
    """按行循环调用 forward_one，返回批量汇总。

    forward_one 接收本行的变量表并执行一次转发，返回 {status_code, data, ...}。
    行内变量只在该次循环内生效（不跨行泄漏）。
    """
    columns, rows, on_error = normalize_batch_params(batch_params)

    batch_results: list[dict] = []
    passed = 0
    failed = 0
    stopped = False

    for index, values in enumerate(rows):
        row_vars = await row_to_variables(columns, values)
        row_param_map = dict(base_param_map)
        row_param_map.update(row_vars)
        try:
            one = await forward_one(row_param_map)
            status_code = int(one.get("status_code") or 0)
            ok = 200 <= status_code < 400
            batch_results.append({
                "index": index,
                "variables": row_vars,
                "status_code": status_code,
                "ok": ok,
                "data": one.get("data"),
            })
            if ok:
                passed += 1
            else:
                failed += 1
                if on_error == ON_ERROR_STOP:
                    stopped = True
                    break
        except Exception as exc:  # noqa: BLE001 - 单行异常不中断批量
            failed += 1
            status_code = int(getattr(exc, "status_code", 0) or 0)
            detail = getattr(exc, "detail", None) or str(exc)
            batch_results.append({
                "index": index,
                "variables": row_vars,
                "status_code": status_code,
                "ok": False,
                "error": detail,
            })
            if on_error == ON_ERROR_STOP:
                stopped = True
                break

    return {
        "total": len(rows),
        "executed": len(batch_results),
        "passed": passed,
        "failed": failed,
        "stopped": stopped,
        "results": batch_results,
    }
