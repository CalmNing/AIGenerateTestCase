"""UI 执行的 steps 归一化与目标 URL 拼接(纯函数,spec §4/§5)。

数据库中 steps 有三种形态:
  - 纯字符串(单次生成路径功能用例)          → 原样可用
  - {step, action, expected} dict(4阶段流水线) → 取 action 字段
  - {type:"api_call", ...} dict(接口用例)     → UI 执行不支持,跳过并打标志
"""
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _is_api_call_step(step: Any) -> bool:
    """判定一个 step 是否为接口调用步骤(与 pipeline_runner._is_pipeline_api_call_step 同口径)。"""
    if not isinstance(step, dict):
        return False
    return (
        step.get("type") == "api_call"
        or step.get("endpoint_id") is not None
        or step.get("endpoint_ref") is not None
    )


def has_api_call_step(steps: list) -> bool:
    """任一步骤为接口调用则 True。"""
    return any(_is_api_call_step(s) for s in (steps or []))


def normalize_ui_steps(steps: list) -> tuple[list[str], bool]:
    """归一化 steps 为 Agent 可执行的文本动作列表。

    Returns:
        (动作文本列表, 是否含接口调用步骤)。
    """
    actions: list[str] = []
    has_api = False
    for step in (steps or []):
        if _is_api_call_step(step):
            has_api = True
            continue
        if isinstance(step, str):
            text = step.strip()
            if text:
                actions.append(text)
        elif isinstance(step, dict) and step.get("action"):
            text = str(step["action"]).strip()
            if text:
                actions.append(text)
    return actions, has_api


def build_target_url(site_origin: str, page_path: str) -> str:
    """拼接目标页面地址:origin(去尾斜杠) + hash/普通路径。"""
    origin = (site_origin or "").strip().rstrip("/")
    path = (page_path or "").strip()
    if not path:
        return origin
    if path.startswith("#") or path.startswith("/"):
        return origin + path
    return origin + "/" + path


def normalize_url(url: str) -> str:
    """折叠 URL 路径中的重复斜杠(保留 scheme:// 与 query/hash)。

    典型场景:全局参数 web_url 带尾斜杠 + 页面对象 url 也以 / 开头,
    拼出 http://host//#/path,path 变成 //,SPA 路由会异常。
    """
    if not url:
        return url
    # 仅折叠 scheme 之后的重复斜杠;不动 query/hash(路径里的 // 才有害)
    if "://" not in url:
        return re.sub(r"(?<!:)/{2,}", "/", url)
    scheme, _, rest = url.partition("://")
    # hash 不参与 path 折叠(hash 内的 // 可能是有意的)
    hash_idx = rest.find("#")
    if hash_idx >= 0:
        before_hash, hash_part = rest[:hash_idx], rest[hash_idx:]
    else:
        before_hash, hash_part = rest, ""
    # query 也不参与(少数接口路径在 query 里)
    q_idx = before_hash.find("?")
    if q_idx >= 0:
        path_part, query_part = before_hash[:q_idx], before_hash[q_idx:]
    else:
        path_part, query_part = before_hash, ""
    path_part = re.sub(r"/{2,}", "/", path_part)
    return f"{scheme}://{path_part}{query_part}{hash_part}"


def normalize_url(url: str) -> str:
    """折叠 URL 路径中的重复斜杠(保留 scheme:// 与 query/hash)。

    典型场景:全局参数 `web_url=http://host/`(带尾斜杠)+ 页面对象
    `url={{web_url}}/#/home`(路径也以 / 开头)拼出 `http://host//#/home`,
    path 变成 `//`,SPA 路由/服务端可能异常。只折叠 path,不动 hash。
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme and not parts.netloc:
        return url
    path = re.sub(r"/{2,}", "/", parts.path)
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
