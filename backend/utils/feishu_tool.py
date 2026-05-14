"""Feishu (飞书) document tool — read-only, full-content fetch.

Provides a single `feishu_doc` LangChain tool wrapping the Feishu Open API.
Only supports reading the full document content in one request via the
raw_content endpoint. No block-level operations.

Configuration:
    The tool reads credentials from the global ConfigManager:
    - feishu_app_id     -> Feishu App ID
    - feishu_app_secret -> Feishu App Secret

Required Feishu app permissions:
    - docx:document
    - docx:document:readonly
    - docx:document.block:convert
    - drive:drive
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

import httpx
from langchain.tools import tool
from pydantic import BaseModel, Field

from config import config_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feishu Open API endpoints
# ---------------------------------------------------------------------------
_BASE_URL = "https://open.feishu.cn/open-apis"
_AUTH_URL = f"{_BASE_URL}/auth/v3/tenant_access_token/internal"
_DOC_URL = f"{_BASE_URL}/docx/v1/documents"

# Token cache
_token_cache: dict[str, Any] = {}


def _get_access_token() -> str:
    """Get or refresh a Feishu tenant_access_token (cached for 1.5 h)."""
    now = time.time()
    if _token_cache.get("expires_at", 0) > now + 60:
        return _token_cache["token"]

    app_id = config_manager.get_feishu_app_id()
    app_secret = config_manager.get_feishu_app_secret()

    if not app_id or not app_secret:
        raise ValueError("飞书配置未完成：请在配置页面设置 feishu_app_id 和 feishu_app_secret")

    resp = httpx.post(_AUTH_URL, json={
        "app_id": app_id,
        "app_secret": app_secret,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书token失败: {data.get('msg', data)}")

    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expires_at"] = now + data.get("expire", 1800)
    return _token_cache["token"]


def _feishu_headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_access_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _extract_doc_token(url_or_token: str) -> str:
    """Extract the doc_token from a Feishu URL or return the token as-is.

    Supports both /docx/ and /wiki/ URL patterns."""
    m = re.search(r"/(?:docx|wiki)/([A-Za-z0-9_-]+)", url_or_token)
    return m.group(1) if m else url_or_token


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def _read_doc(doc_token: str) -> str:
    """Read the full raw content of a Feishu document in one request."""
    r = httpx.get(
        f"{_DOC_URL}/{doc_token}/raw_content",
        headers=_feishu_headers(),
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"读取飞书文档失败: {data.get('msg', data)}")

    content = data.get("data", {}).get("content", "")
    title = _read_doc_title(doc_token)
    return f"# {title}\n\n{content}"


def _read_doc_title(doc_token: str) -> str:
    """Read document metadata to get the title."""
    try:
        r = httpx.get(f"{_DOC_URL}/{doc_token}", headers=_feishu_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("document", {}).get("title", "无标题")
    except Exception:
        pass
    return "无标题"


# ---------------------------------------------------------------------------
# Pydantic schema for the tool
# ---------------------------------------------------------------------------

class FeishuDocInput(BaseModel):
    """飞书文档工具参数"""
    action: str = Field(description='操作名称: "read"（仅只读，一次性获取全文）')
    doc_token: Optional[str] = Field(default=None, description="文档token，从飞书URL中 /docx/XXXX 部分提取")


# ---------------------------------------------------------------------------
# The main tool
# ---------------------------------------------------------------------------

@tool(args_schema=FeishuDocInput)
def feishu_doc(
    action: str = "",
    doc_token: str = "",
) -> str:
    """飞书文档只读工具。一次性获取文档全文，不分块。

    action 仅支持 "read": 读取文档全文 (需要 doc_token)。

    从URL提取doc_token: URL中 /docx/XXXX 的 XXXX 部分即为 doc_token。
    """
    try:
        doc_token = _extract_doc_token(doc_token)

        if action == "read":
            return _read_doc(doc_token)

        else:
            return f'不支持的操作: {action}。仅支持 read（一次性获取全文）。'

    except ValueError as e:
        return f"参数错误: {e}"
    except httpx.HTTPStatusError as e:
        logger.error("Feishu API HTTP error: %s %s", e.response.status_code, e.response.text[:500])
        return f"飞书 API 请求失败 (HTTP {e.response.status_code})"
    except Exception as e:
        logger.error("Feishu tool error: %s", e)
        return f"飞书操作失败: {e}"
