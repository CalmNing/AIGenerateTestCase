"""Shared Feishu/Lanhu document prefetch via MCP.

Single source of truth for the Lanhu/Feishu URL regexes and the prefetch
logic previously duplicated in utils/model_utils.py and
app/pipeline/pipeline_runner.py.
"""
import logging
import re

from utils.helpers import _load_json_object

logger = logging.getLogger(__name__)

LANHU_URL_RE = re.compile(
    r'https?://lanhuapp\.com/web/#/item/project/[a-z]+[^"\s]*',
    re.IGNORECASE,
)
FEISHU_URL_RE = re.compile(
    r'https?://[a-zA-Z0-9-]+\.feishu\.cn/(?:wiki|docx)/([A-Za-z0-9_-]+)',
    re.IGNORECASE,
)


async def fetch_feishu_document(text: str, mcp_configs) -> "str | None":
    """Read a Feishu wiki/docx link's body via MCP. Returns content or None."""
    match = FEISHU_URL_RE.search(text or "")
    if not match or not mcp_configs:
        return None

    token = match.group(1)
    try:
        from utils.lanhu_mcp_adapter import connect_single_server
    except Exception:
        logger.warning("Feishu MCP prefetch unavailable: adapter import failed", exc_info=True)
        return None

    enabled_configs = [c for c in mcp_configs if c.get("enabled", True)]
    for config in enabled_configs:
        server_name, client, error = await connect_single_server(config)
        if error or client is None:
            logger.info("Feishu MCP prefetch skipped unavailable server %s: %s", server_name, error)
            continue
        try:
            tool_names = {t.get("name") for t in client.tools}
            document_id = token
            if "wiki_v2_space_getNode" in tool_names and "/wiki/" in match.group(0).lower():
                node_raw = await client.call_tool("wiki_v2_space_getNode", {"params": {"token": token}})
                node_data = _load_json_object(node_raw) or {}
                # Feishu API envelope: {code, msg, data: {node: {...}}}
                node = node_data.get("node")
                if not isinstance(node, dict):
                    inner = node_data.get("data")
                    node = inner.get("node") if isinstance(inner, dict) else None
                if isinstance(node, dict):
                    document_id = node.get("obj_token") or document_id
                else:
                    logger.warning("飞书 wiki getNode 响应无 node 字段: %s", str(node_data)[:300])

            if "docx_v1_document_rawContent" not in tool_names:
                continue
            raw = await client.call_tool("docx_v1_document_rawContent", {"path": {"document_id": document_id}})
            data = _load_json_object(raw) or {}
            # Feishu API envelope: {code, msg, data: {content: "..."}}
            content = data.get("content")
            if not isinstance(content, str) or not content.strip():
                inner = data.get("data")
                if isinstance(inner, dict):
                    content = inner.get("content")
            if isinstance(content, str) and content.strip():
                logger.info("已预取飞书文档正文: server=%s document_id=%s size=%s", server_name, document_id, len(content))
                return content
            logger.warning("飞书 rawContent 响应无有效 content: %s", str(data)[:300])
        except Exception as e:
            logger.warning("Feishu MCP prefetch failed on server %s: %s", server_name, e)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    return None


async def fetch_lanhu_document(text: str, mcp_configs) -> "str | None":
    """Read a Lanhu document link's content via MCP. Returns content or None."""
    match = LANHU_URL_RE.search(text or "")
    if not match or not mcp_configs:
        return None
    try:
        from utils.lanhu_mcp_adapter import connect_single_server
    except Exception:
        logger.warning("Lanhu MCP prefetch unavailable: adapter import failed", exc_info=True)
        return None

    enabled_configs = [c for c in mcp_configs if c.get("enabled", True)]
    for config in enabled_configs:
        server_name, client, error = await connect_single_server(config)
        if error or client is None:
            continue
        try:
            tool_names = {t.get("name") for t in client.tools}
            if "get_lanhu_content" not in tool_names:
                continue
            raw = await client.call_tool("get_lanhu_content", {"url": match.group(0)})
            data = _load_json_object(raw) or {}
            content = data.get("content") or data.get("text")
            if isinstance(content, str) and content.strip():
                logger.info("已预取蓝湖文档 server=%s size=%s", server_name, len(content))
                return content
        except Exception as e:
            logger.warning("蓝湖预取失败 server=%s: %s", server_name, e)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
    return None


async def prefetch_document(text: str, mcp_configs) -> str:
    """Replace a Feishu/Lanhu link in `text` with its fetched content.

    Tries Feishu first, then Lanhu. Returns the fetched content if found,
    otherwise the original text unchanged (matching pipeline_runner's
    previous behavior of returning replacement-or-original).
    """
    feishu = await fetch_feishu_document(text, mcp_configs)
    if feishu:
        return feishu
    lanhu = await fetch_lanhu_document(text, mcp_configs)
    if lanhu:
        return lanhu
    return text
