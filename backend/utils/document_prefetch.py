"""Shared Feishu/Lanhu document prefetch via MCP.

Single source of truth for the Lanhu/Feishu URL regexes and the prefetch
logic previously duplicated in utils/model_utils.py and
app/pipeline/pipeline_runner.py.

Feishu doc extraction covers four layers:
1. 正文 (body)        — `docx_v1_document_rawContent` via MCP.
2. 嵌入的表格 (sheet) — 通过飞书 docx blocks REST API（`tenant_access_token`）
                        发现嵌入块（block_type=30 sheet / 14 bitable）拿到子表 token，
                        再用 Sheets API 直连读取单元格。
                        关键：rawContent 是纯文本、**不含**嵌入表格引用，
                        lark-mcp 也未暴露 blocks 工具，故必须直连 REST API。
                        详见 docs/feishu-doc-extraction.md。
3. 画板 (board)       — MCP 工具 `board_v1_whiteboardNode_list` 读取节点并转 Markdown。
4. 内嵌图片 (image)   — docx blocks block_type=27 → `image.token` → 下载素材 →
                        多模态模型转文字描述，追加到正文末尾（可选，失败静默跳过）。
                        详见 docs/feishu-image-extraction.md。
"""
import asyncio
import base64
import io
import json
import logging
import re

import httpx

from config import config_manager
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

# 裸图片引用行（rawContent 中图片位置的占位，如 image.png）
_IMAGE_LINE_RE = re.compile(r'^(image\d*\.(png|jpe?g|gif|bmp|webp)|图片\d*\.png)$', re.IGNORECASE)

# 飞书 token 前缀：电子表格(sheet) 与 多维表格(bitable) 各自的专属前缀。
# 嵌入块 token 形如 `spreadsheetToken_sheetId`（下划线前是表格、后是子表）。
_FEISHU_TOKEN_PREFIXES = (
    "shtcn", "sht", "NR", "basc", "NLWU", "Vl", "BI", "Sg", "Gt", "st",
)
# 从文档正文中抽取可能存在的嵌入表格 token（启发式，宁可漏不可错；
# 命中后真正读 API 时若令牌无效会被 try/except 静默跳过）。
FEISHU_SHEET_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _FEISHU_TOKEN_PREFIXES) +
    r")[A-Za-z0-9_]{4,}(?:_[A-Za-z0-9]{2,})?",
)

_FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# 飞书画板在 docx blocks 中的 block_type
_BOARD_BLOCK_TYPE = 43
_ADD_ONS_BLOCK_TYPE = 40

# ---------------------------------------------------------------------------
# 文档内嵌图片（docx image block）
# ---------------------------------------------------------------------------
# 飞书 docx 图片块 block_type=27，block["image"]["token"] 即素材 token。
# 下载素材需要文档资源权限：应用需被加为文档协作者（可编辑），且文档
# 「谁可以创建副本、打印和下载」允许该权限级别；否则下载返回 403。
# 下载后交给多模态模型转成文字描述，追加到正文末尾供测试用例生成使用。
# 详见 docs/feishu-image-extraction.md。
_IMAGE_BLOCK_TYPE = 27
_IMAGE_MAX_BYTES = 8 * 1024 * 1024
_IMAGE_RAW_MAX_BYTES = 2 * 1024 * 1024
_IMAGE_CONTEXT_CHARS = 160
_VISION_MAX_SIDE = 1568
_VISION_CONCURRENCY = 3
_VISION_TIMEOUT = 90.0
_VISION_MAX_TOKENS = 768
_IMAGE_MAX_PIXELS = 40_000_000
_IMAGE_TOTAL_TIMEOUT = 90.0

# 进程内负缓存：某个 (base_url, model) 已判定不支持图片后，本进程不再重复尝试。
_VISION_UNSUPPORTED_CACHE: set = set()

_VISION_PROMPT = (
    "你是需求文档截图解析助手。请用简洁的中文描述这张图片，"
    "重点提取对测试设计有用的信息："
    "① 界面/页面名称与所属功能；② 关键字段、表头、按钮、状态及可见取值；"
    "③ 若是流程图/审批流，描述节点、分支条件与箭头关系；"
    "④ 若是弹窗/提示，写出提示文案与选项；"
    "⑤ 若是表格，列出表头与代表性数据。"
    "只描述图中可见内容，不要臆测，不要输出 Markdown 表格。"
)

_TEXT_BLOCK_FIELDS = (
    "text", "heading1", "heading2", "heading3", "heading4", "heading5",
    "heading6", "heading7", "heading8", "heading9",
    "bullet", "ordered", "code", "quote", "todo",
)


class _VisionUnavailable(RuntimeError):
    """视觉模型不可用（未配置或模型不支持图片），用于快速中止后续解析。"""


async def _get_feishu_tenant_token(app_id: str, app_secret: str) -> "str | None":
    """用应用身份换取 tenant_access_token（无需用户授权）。"""
    if not app_id or not app_secret:
        return None
    try:
        async with httpx.AsyncClient(timeout=15, verify=False) as client:
            resp = await client.post(
                f"{_FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = resp.json()
            if data.get("code") == 0 and data.get("tenant_access_token"):
                return data["tenant_access_token"]
            logger.warning("飞书 tenant_access_token 获取失败: %s", data)
    except Exception as e:
        logger.warning("飞书 tenant_access_token 请求异常: %s", e)
    return None


def _split_sheet_token(token: str) -> "tuple[str, str | None]":
    """`spreadsheetToken_sheetId` -> (spreadsheetToken, sheetId)。"""
    if "_" in token:
        spread, sheet_id = token.rsplit("_", 1)
        return spread, sheet_id
    return token, None


def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_rows_to_markdown(rows: list) -> str:
    """把飞书 Sheets API 返回的二维数组转成 Markdown 表格。"""
    if not rows:
        return ""
    # 去掉整行全空的行；并裁掉每行尾部的空单元格（电子表格常有多余空列）
    cleaned = []
    for r in rows:
        if not any(c is not None and str(c).strip() != "" for c in r):
            continue
        # 去掉尾部空单元格
        while r and (r[-1] is None or str(r[-1]).strip() == ""):
            r = r[:-1]
        if r:
            cleaned.append(r)
    if not cleaned:
        return ""
    ncols = max(len(r) for r in cleaned)
    out = []
    for i, row in enumerate(cleaned):
        cells = [(str(c).replace("\n", " ") if c is not None else "")
                 for c in (list(row) + [""] * (ncols - len(row)))]
        out.append("| " + " | ".join(cells) + " |")
        if i == 0:
            out.append("| " + " | ".join(["---"] * ncols) + " |")
    return "\n".join(out)


async def fetch_feishu_sheet(
    spreadsheet_token: str, sheet_id: str | None,
    app_id: str, app_secret: str,
) -> "str | None":
    """读取单个嵌入电子表格/多维表格的子表内容，返回 Markdown 表格或 None。"""
    token = await _get_feishu_tenant_token(app_id, app_secret)
    if not token:
        return None
    sheet_part = sheet_id or ""
    rng = f"{sheet_part}!A1:Z300" if sheet_part else "A1:Z300"
    url = (f"{_FEISHU_API_BASE}/sheets/v2/spreadsheets/{spreadsheet_token}"
           f"/values/{rng}")
    try:
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {token}"})
            data = resp.json()
        if data.get("code") != 0:
            logger.warning("飞书表格读取失败 token=%s: %s", spreadsheet_token, data)
            return None
        values = data.get("data", {}).get("valueRange", {}).get("values", [])
        md = _sheet_rows_to_markdown(values)
        return md or None
    except Exception as e:
        logger.warning("飞书表格读取异常 token=%s: %s", spreadsheet_token, e)
        return None


def _resolve_feishu_credentials(mcp_configs) -> "tuple[str, str]":
    """优先用 config 中的 feishu_app_id/feishu_app_secret；
    其次尝试从 Feishu MCP(stdio lark-mcp) 配置的 -a/-s 参数中提取。"""
    try:
        from config import config_manager
        aid = config_manager.get_feishu_app_id()
        asec = config_manager.get_feishu_app_secret()
        if aid and asec:
            return aid, asec
    except Exception:
        pass

    for config in (mcp_configs or []):
        args = config.get("args") or []
        if not isinstance(args, list):
            continue
        # lark-mcp 参数形如: -a <app_id> -s <app_secret>
        for idx, a in enumerate(args):
            if a in ("-a", "--app-id", "app_id") and idx + 1 < len(args):
                cand_id = args[idx + 1]
            elif a in ("-s", "--app-secret", "app_secret") and idx + 1 < len(args):
                cand_sec = args[idx + 1]
        if cand_id and cand_sec:
            return cand_id, cand_sec
    return "", ""


async def _discover_board_tokens(
    document_id: str, app_id: str, app_secret: str,
) -> "list[dict]":
    """通过飞书 docx blocks REST API 发现文档中嵌入的画板 block。

    画板特征：block_type=43 -> block['board']['token']。
    返回 [{token, block_id, width, height}, ...]。
    """
    token = await _get_feishu_tenant_token(app_id, app_secret)
    if not token or not document_id:
        return []
    url = f"{_FEISHU_API_BASE}/docx/v1/documents/{document_id}/blocks"
    try:
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={"page_size": 500},
            )
            data = resp.json()
        if data.get("code") != 0:
            logger.warning("飞书 blocks 读取失败（画板检测）doc=%s: %s", document_id, data)
            return []
        items = (data.get("data", {}) or {}).get("items", []) or []
        boards = []
        for b in items:
            if b.get("block_type") == _BOARD_BLOCK_TYPE and isinstance(b.get("board"), dict):
                t = b["board"].get("token")
                if t:
                    boards.append({
                        "token": t,
                        "block_id": b.get("block_id", ""),
                        "width": b["board"].get("width", 0),
                        "height": b["board"].get("height", 0),
                    })
        if boards:
            logger.info("发现 %d 个画板 block: doc=%s", len(boards), document_id)
        return boards
    except Exception as e:
        logger.warning("飞书 blocks 读取异常（画板检测）doc=%s: %s", document_id, e)
        return []


async def fetch_feishu_board(board_token: str, mcp_configs) -> "str | None":
    """通过 MCP 调用 board.v1.whiteboardNode.list 读取画板内容。

    返回解析后的 Markdown 文本，或 None。
    """
    try:
        from utils.lanhu_mcp_adapter import connect_single_server, _MAX_TOOL_RESPONSE
    except Exception:
        logger.warning("画板读取不可用: adapter import failed", exc_info=True)
        return None

    # 画板内容通常较大，临时增加响应限制
    _BOARD_MAX_RESPONSE = 200 * 1024  # 200KB

    enabled_configs = [c for c in mcp_configs if c.get("enabled", True)]
    for config in enabled_configs:
        server_name, client, error = await connect_single_server(config)
        if error or client is None:
            continue
        try:
            tool_names = {t.get("name") for t in client.tools}
            if "board_v1_whiteboardNode_list" not in tool_names:
                continue

            # 临时调整响应限制
            import utils.lanhu_mcp_adapter as adapter
            original_limit = adapter._MAX_TOOL_RESPONSE
            adapter._MAX_TOOL_RESPONSE = _BOARD_MAX_RESPONSE
            try:
                raw = await client.call_tool(
                    "board_v1_whiteboardNode_list",
                    {"path": {"whiteboard_id": board_token}},
                )
            finally:
                adapter._MAX_TOOL_RESPONSE = original_limit

            data = _load_json_object(raw) or {}
            nodes = data.get("nodes", [])
            if not nodes:
                logger.info("画板无节点: token=%s", board_token)
                return None

            from utils.board_parser import parse_board_nodes
            md = parse_board_nodes(nodes)
            if md.strip():
                logger.info("已读取画板内容: token=%s nodes=%d size=%d",
                           board_token, len(nodes), len(md))
                return md
            return None
        except Exception as e:
            logger.warning("画板读取失败 server=%s token=%s: %s", server_name, board_token, e)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    return None


async def _collect_board_markdown(
    boards: list[dict], mcp_configs,
) -> str:
    """读取多个画板并拼成 Markdown（不带画板编号标题，内容直接拼接）。"""
    blocks: list[str] = []
    for board in boards:
        md = await fetch_feishu_board(board["token"], mcp_configs)
        if md:
            blocks.append(md)
    return "\n\n".join(blocks)




def _block_text_content(block: dict) -> str:
    """提取文本类 block 的纯文本（用于给图片补充上下文）。"""
    if not isinstance(block, dict):
        return ""
    for field in _TEXT_BLOCK_FIELDS:
        payload = block.get(field)
        if not isinstance(payload, dict):
            continue
        elements = payload.get("elements")
        if not isinstance(elements, list):
            continue
        parts = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            text_run = element.get("text_run")
            if isinstance(text_run, dict) and isinstance(text_run.get("content"), str):
                parts.append(text_run["content"])
        text = "".join(parts).strip()
        if text:
            return text
    return ""


def _extract_image_blocks(items: list, document_id: str = "") -> list[dict]:
    """按文档顺序从 blocks 里提取图片块，并附带其前面最近一段正文。"""
    blocks = [b for b in (items or []) if isinstance(b, dict)]
    by_id = {b.get("block_id"): b for b in blocks}

    ordered_ids: list = []
    seen = set()

    def walk(block_id):
        if not block_id or block_id in seen:
            return
        seen.add(block_id)
        block = by_id.get(block_id)
        if not block:
            return
        ordered_ids.append(block_id)
        for child in block.get("children") or []:
            walk(child)

    if document_id and document_id in by_id:
        walk(document_id)
    if not ordered_ids:
        ordered_ids = [b.get("block_id") for b in blocks]

    records: list = []
    last_text = ""
    index = 0
    for block_id in ordered_ids:
        block = by_id.get(block_id)
        if not block:
            continue
        if block.get("block_type") == _IMAGE_BLOCK_TYPE:
            image = block.get("image")
            if not isinstance(image, dict):
                continue
            token = image.get("token")
            if not token:
                continue
            index += 1
            records.append({
                "index": index,
                "block_id": block_id,
                "token": token,
                "width": image.get("width") or 0,
                "height": image.get("height") or 0,
                "context": last_text[-_IMAGE_CONTEXT_CHARS:],
            })
        else:
            text = _block_text_content(block)
            if text:
                last_text = text
    return records


async def _discover_image_blocks(document_id: str, app_id: str, app_secret: str) -> list:
    """通过 docx blocks REST API 按文档顺序发现图片块。"""
    tenant_token = await _get_feishu_tenant_token(app_id, app_secret)
    if not tenant_token or not document_id:
        return []
    items: list = []
    page_token = ""
    try:
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            while True:
                params = {"page_size": 500}
                if page_token:
                    params["page_token"] = page_token
                resp = await client.get(
                    f"{_FEISHU_API_BASE}/docx/v1/documents/{document_id}/blocks",
                    headers={"Authorization": f"Bearer {tenant_token}"},
                    params=params,
                )
                data = resp.json()
                if data.get("code") != 0:
                    logger.warning("飞书 blocks 读取失败（图片检测）doc=%s: %s", document_id, data)
                    break
                page_data = data.get("data") or {}
                items.extend(page_data.get("items") or [])
                if not page_data.get("has_more"):
                    break
                page_token = page_data.get("page_token") or ""
                if not page_token:
                    break
    except Exception as e:
        logger.warning("飞书 blocks 读取异常（图片检测）doc=%s: %s", document_id, e)
    return _extract_image_blocks(items, document_id)


async def _download_image_bytes(file_token: str, tenant_token: str) -> "bytes | None":
    """流式下载 docx 内嵌图片素材；无权限/超限时返回 None（失败原因由调用方汇总）。"""
    if not file_token or not tenant_token:
        return None
    url = f"{_FEISHU_API_BASE}/drive/v1/medias/{file_token}/download"
    try:
        async with httpx.AsyncClient(timeout=60, verify=False) as client:
            async with client.stream(
                "GET", url, headers={"Authorization": f"Bearer {tenant_token}"}
            ) as resp:
                if resp.status_code != 200:
                    logger.debug("飞书图片下载失败 token=%s status=%s", file_token, resp.status_code)
                    return None
                chunks: list = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > _IMAGE_MAX_BYTES:
                        logger.info("飞书图片过大已跳过 token=%s size>%s", file_token, _IMAGE_MAX_BYTES)
                        return None
                    chunks.append(chunk)
        return b"".join(chunks) if chunks else None
    except Exception as e:
        logger.debug("飞书图片下载异常 token=%s: %s", file_token, e)
        return None


def _sniff_image_mime(data: bytes) -> "str | None":
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def _encode_image(data: bytes) -> "tuple[str, str] | None":
    """把图片压缩到适合多模态模型的大小，返回 (mime, base64)。"""
    if not data or len(data) > _IMAGE_MAX_BYTES:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > _IMAGE_MAX_PIXELS:
                logger.info("飞书图片像素过大已跳过 width=%s height=%s", image.width, image.height)
                return None
            if image.mode in ("RGBA", "LA", "P"):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((_VISION_MAX_SIDE, _VISION_MAX_SIDE))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82)
        return "image/jpeg", base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        # Pillow 缺失或图片无法解码时，小图直接按原始字节发送。
        pass
    mime = _sniff_image_mime(data)
    if not mime or len(data) > _IMAGE_RAW_MAX_BYTES:
        return None
    return mime, base64.b64encode(data).decode("ascii")


def _resolve_vision_config(explicit: "dict | None" = None) -> "dict | None":
    """解析视觉模型配置：config.json 的 vision_* 优先，其次调用方传入的模型配置。"""
    explicit = explicit or {}
    api_key = (
        config_manager.get_vision_api_key()
        or explicit.get("api_key")
        or config_manager.get_api_key()
        or ""
    ).strip()
    base_url = (
        config_manager.get_vision_api_base_url()
        or explicit.get("api_base_url")
        or config_manager.get_api_base_url()
        or ""
    ).strip()
    model = (
        config_manager.get_vision_model()
        or explicit.get("api_model")
        or config_manager.get_api_model()
        or ""
    ).strip()
    if not api_key or not model:
        return None
    return {
        "api_key": api_key,
        "api_base_url": base_url or "https://api.deepseek.com",
        "api_model": model,
        "api_proxy_url": (explicit.get("api_proxy_url") or "").strip(),
    }


def _is_deepseek_endpoint(base_url: str) -> bool:
    """判断是否 DeepSeek 端点（用于关闭 thinking，见 _build_vision_payload）。"""
    try:
        from urllib.parse import urlparse

        host = (urlparse(base_url or "").hostname or "").lower()
    except Exception:
        host = (base_url or "").lower()
    return "deepseek" in host


def _build_vision_payload(config: dict, mime: str, data_b64: str, prompt: str) -> dict:
    """构造视觉请求体。

    DeepSeek（deepseek-v4-flash 等）默认开启 thinking，推理 token 会占满
    max_tokens，导致 message.content 返回空串；这里显式关闭 thinking，
    与 utils.llm_factory.build_chat_model 的行为保持一致。
    """
    payload = {
        "model": config["api_model"],
        "temperature": 0,
        "max_tokens": _VISION_MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data_b64}"}},
            ],
        }],
    }
    if _is_deepseek_endpoint(config.get("api_base_url", "")):
        payload["thinking"] = {"type": "disabled"}
    return payload


async def _post_vision_chat(config: dict, mime: str, data_b64: str, prompt: str) -> str:
    """调用 OpenAI 兼容的视觉接口，返回图片描述文本。"""
    url = config["api_base_url"].rstrip("/") + "/chat/completions"
    payload = _build_vision_payload(config, mime, data_b64, prompt)
    client_kwargs = {"timeout": _VISION_TIMEOUT}
    if config.get("api_proxy_url"):
        client_kwargs["proxy"] = config["api_proxy_url"]
    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {config['api_key']}"},
            json=payload,
        )
    if resp.status_code != 200:
        logger.warning("视觉模型调用失败 status=%s body=%s", resp.status_code, resp.text[:300])
        if resp.status_code in (400, 401, 403, 404, 422):
            raise _VisionUnavailable(f"HTTP {resp.status_code}")
        return ""
    try:
        data = resp.json()
    except Exception:
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts = [
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("text")
        ]
        if "".join(parts).strip():
            return "".join(parts).strip()
    logger.warning(
        "视觉模型返回空内容（finish_reason=%s, reasoning_len=%s）；"
        "若是 DeepSeek thinking 模型，请确认已关闭 thinking",
        choices[0].get("finish_reason"), len(message.get("reasoning_content") or ""),
    )
    return ""


def _format_image_markdown(records: list, max_chars: int = 0) -> str:
    """把图片描述渲染成可直接拼进需求的 Markdown 段落。"""
    records = [r for r in (records or []) if (r.get("description") or "").strip()]
    if not records:
        return ""
    parts = [f"## 文档图片（共 {len(records)} 张，按正文出现顺序）"]
    for record in records:
        description = record["description"].strip()
        if max_chars and len(description) > max_chars:
            description = description[:max_chars].rstrip() + "…"
        title = f"### 图片 {record.get('index', '?')}"
        width, height = record.get("width") or 0, record.get("height") or 0
        if width and height:
            title += f"（{width}×{height}）"
        context = (record.get("context") or "").strip()
        if context:
            title += f"\n\n> 图片前的正文：{context[-120:]}"
        parts.append(f"{title}\n\n{description}")
    return "\n\n".join(parts)


async def _collect_image_markdown(
    document_id: str,
    app_id: str,
    app_secret: str,
    vision_config: "dict | None" = None,
) -> str:
    """读取文档内嵌图片并转成文字描述；任何失败都静默返回空串。"""
    if not document_id or not app_id or not app_secret:
        return ""
    if not config_manager.is_feishu_image_enabled():
        return ""
    resolved = _resolve_vision_config(vision_config)
    if not resolved:
        logger.info("未配置视觉模型，跳过飞书文档图片解析")
        return ""
    cache_key = (resolved["api_base_url"].rstrip("/"), resolved["api_model"])
    if cache_key in _VISION_UNSUPPORTED_CACHE:
        logger.info("视觉模型此前已判定不支持图片，跳过: %s", resolved["api_model"])
        return ""
    tenant_token = await _get_feishu_tenant_token(app_id, app_secret)
    if not tenant_token:
        return ""
    try:
        return await asyncio.wait_for(
            _collect_image_markdown_impl(
                document_id, app_id, app_secret, tenant_token, resolved, cache_key),
            timeout=_IMAGE_TOTAL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "飞书文档图片解析超时（>%.0fs），已跳过 doc=%s", _IMAGE_TOTAL_TIMEOUT, document_id)
        return ""
    except Exception as e:
        logger.warning("飞书文档图片解析失败 doc=%s: %s", document_id, e)
        return ""


async def _collect_image_markdown_impl(
    document_id: str,
    app_id: str,
    app_secret: str,
    tenant_token: str,
    resolved: dict,
    cache_key: tuple,
) -> str:
    async def _describe(record, data):
        try:
            encoded = await asyncio.to_thread(_encode_image, data)
            if not encoded:
                return
            mime, data_b64 = encoded
            prompt = _VISION_PROMPT
            context = (record.get("context") or "").strip()
            if context:
                prompt += f"\n\n这张图片在文档中的前一段文字：{context[-_IMAGE_CONTEXT_CHARS:]}"
            description = await _post_vision_chat(resolved, mime, data_b64, prompt)
            if description:
                record["description"] = description
        except _VisionUnavailable:
            raise
        except Exception as e:
            logger.warning("单张图片视觉解析失败 block=%s: %s", record.get("block_id"), e)

    records = await _discover_image_blocks(document_id, app_id, app_secret)
    if not records:
        logger.info("飞书文档未发现图片: doc=%s", document_id)
        return ""
    max_images = max(1, config_manager.get_feishu_image_max())
    if len(records) > max_images:
        logger.warning(
            "飞书文档图片 %s 张，超过上限 %s，仅解析前 %s 张（可调 feishu_image_max）",
            len(records), max_images, max_images,
        )
        records = records[:max_images]
    logger.info(
        "开始解析飞书文档图片: doc=%s count=%s model=%s",
        document_id, len(records), resolved["api_model"],
    )

    # 1) 先下载并解析第一张：探测视觉模型是否支持图片；文本模型场景下
    #    只需下载 1 张、调用 1 次即可整体跳过，不会把整篇图片都拉下来。
    first_data = await _download_image_bytes(records[0]["token"], tenant_token)
    if not first_data:
        logger.warning(
            "飞书文档图片下载失败，已跳过图片解析（应用需被加为文档协作者且允许下载）doc=%s",
            document_id,
        )
        return ""
    try:
        await _describe(records[0], first_data)
    except _VisionUnavailable:
        _VISION_UNSUPPORTED_CACHE.add(cache_key)
        logger.warning("当前模型不支持图片输入，已跳过飞书文档图片解析")
        return ""

    # 2) 并发下载其余图片；失败数量汇总成一条日志，避免逐张刷屏。
    rest = records[1:]
    if rest:
        download_sem = asyncio.Semaphore(4)

        async def _download(record):
            async with download_sem:
                return await _download_image_bytes(record["token"], tenant_token)

        datas = await asyncio.gather(*[_download(r) for r in rest])
        ready = [(r, d) for r, d in zip(rest, datas) if d]
        if len(ready) < len(rest):
            logger.warning(
                "飞书文档图片下载失败 %s/%s 张（应用需被加为文档协作者且允许下载）",
                len(rest) - len(ready), len(rest),
            )
        # 3) 并发解析其余图片；单张失败只跳过该张。
        tasks = [asyncio.create_task(_describe(record, data)) for record, data in ready]
        if tasks:
            try:
                await asyncio.gather(*tasks)
            except _VisionUnavailable:
                _VISION_UNSUPPORTED_CACHE.add(cache_key)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.warning("部分图片视觉解析失败，保留已解析结果")

    markdown = _format_image_markdown(records, config_manager.get_feishu_image_max_chars())
    if markdown:
        parsed = sum(1 for r in records if (r.get("description") or "").strip())
        logger.info("已解析飞书文档图片: doc=%s parsed=%s", document_id, parsed)
    return markdown


async def _collect_image_records(
    document_id: str,
    app_id: str,
    app_secret: str,
    vision_config: "dict | None" = None,
) -> list:
    """读取文档内嵌图片并返回结构化描述记录（按正文出现顺序）。

    与 _collect_image_markdown 相同链路，但返回 records 列表
    （含 index/context/description），供按位置内嵌到正文。失败返回空列表。
    """
    markdown = await _collect_image_markdown(
        document_id, app_id, app_secret, vision_config)
    if not markdown:
        return []
    # _collect_image_markdown 内部完成了解析；records 无法直接透传，
    # 这里从 markdown 反解出 {index, description}（width/height/title 不需要）。
    records: list = []
    current: dict | None = None
    for line in markdown.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        m = re.match(r'^###\s*图片\s*(\d+)', s)
        if m:
            current = {"index": int(m.group(1)), "description": ""}
            records.append(current)
            continue
        if current is not None:
            if s.startswith("###") or s.startswith("##"):
                current = None
                continue
            if s.startswith("> 图片前的正文"):
                continue
            if s:
                current["description"] = (current["description"] + "\n" + s).strip()
    return records


def _inline_image_descriptions(content: str, records: list) -> str:
    """把图片描述按出现顺序内嵌到正文对应的 image 引用行位置。

    rawContent 中图片位置是裸 image.png 行；按 records 顺序逐个替换该类行，
    插入【图片 N：描述】。描述缺失的图片保留占位。任何情况下不改变非图片行。
    """
    if not records:
        return content
    lines = content.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    ri = 0  # 下一个待插入的图片记录下标
    for line in lines:
        s = line.strip()
        if _IMAGE_LINE_RE.match(s):
            if ri < len(records):
                record = records[ri]
                ri += 1
                idx = record.get("index", ri)
                desc = (record.get("description") or "").strip()
                if desc:
                    out.append(f"【图片 {idx}】{desc}")
                else:
                    out.append(f"【图片 {idx}】（未识别内容）")
            else:
                # 超出识别上限/未识别的图片：保留占位提示，避免裸 image.png 困惑
                out.append("【图片】（未识别内容：超出单次识别上限或视觉模型未识别）")
        else:
            out.append(line)
    # 正文 image 行少于记录时（引用行被裁剪），剩余描述按顺序追加
    if ri < len(records):
        for record in records[ri:]:
            idx = record.get("index", ri + 1)
            desc = (record.get("description") or "").strip()
            if desc:
                out.append(f"【图片 {idx}】{desc}")
    return "\n".join(out)


class DocumentSectionNotFoundError(ValueError):
    pass


def _document_heading(line: str) -> tuple[int, str] | None:
    line = line.strip()
    markdown = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
    if markdown:
        return len(markdown.group(1)), markdown.group(2).strip()

    chinese_number = re.match(r"^([一二三四五六七八九十百]+)[、.．]\s*(.+?)\s*$", line)
    if chinese_number:
        return 1, chinese_number.group(2).strip()

    arabic_number = re.match(r"^(\d+(?:\.\d+)*)(?:[、.．]|\s+)\s*(.+?)\s*$", line)
    if arabic_number:
        return arabic_number.group(1).count(".") + 2, arabic_number.group(2).strip()

    chapter = re.match(r"^第[一二三四五六七八九十百\d]+(章|节|部分)\s*(.+?)\s*$", line)
    if chapter:
        level = {"章": 1, "部分": 1, "节": 2}[chapter.group(1)]
        return level, chapter.group(2).strip()
    return None


def extract_document_section(content: str, section: str) -> str | None:
    """Return a heading and its descendants from Feishu plain text or Markdown."""
    target = section.strip().lstrip("#").strip()
    if not target:
        return content

    lines = content.splitlines()
    start = None
    level = 0
    for index, line in enumerate(lines):
        heading = _document_heading(line)
        if heading and (heading[1].casefold() == target.casefold() or line.strip().casefold() == target.casefold()):
            start = index
            level = heading[0]
            break

    if start is None:
        return None

    end = len(lines)
    for index in range(start + 1, len(lines)):
        heading = _document_heading(lines[index])
        if heading and heading[0] <= level:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


async def fetch_feishu_document(
    text: str,
    mcp_configs,
    section: str | None = None,
    vision_config: "dict | None" = None,
) -> "str | None":
    """Read a Feishu wiki/docx link's body + embedded sheets/images via MCP/API.

    When section is provided, return that heading and its nested content only
    (images are skipped for section-scoped reads).
    """
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
    app_id, app_secret = _resolve_feishu_credentials(enabled_configs)

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
            if not (isinstance(content, str) and content.strip()):
                logger.warning("飞书 rawContent 响应无有效 content: %s", str(data)[:300])
                continue

            logger.info("已预取飞书文档正文: server=%s document_id=%s size=%s", server_name, document_id, len(content))

            # blocks API 重建带标题级别的正文（rawContent 纯文本丢失了标题级别）；
            # 成功则替换 content，失败保留纯文本并走启发式解析。
            if app_id and app_secret:
                try:
                    blocks_md = await _build_markdown_from_blocks(document_id, app_id, app_secret)
                    if blocks_md:
                        content = blocks_md
                except Exception as e:
                    logger.warning("blocks 重建正文失败（保留 rawContent）doc=%s: %s", document_id, e)

            # 读取画板内容：按 blocks 出现顺序内嵌到正文占位行位置（替代尾部附录）。
            board_tokens = await _discover_board_tokens(document_id, app_id, app_secret)
            if board_tokens and "<BOARD_TOKEN=" in content:
                boards_md = await _collect_board_markdown(board_tokens, enabled_configs) or ""
                # 按空行切分为每板一段（顺序与 board_tokens 一致），直接内嵌不加分组标题
                parts = [p.strip() for p in boards_md.split("\n\n") if p.strip()]
                lines = content.split("\n")
                bi = 0
                for li, line in enumerate(lines):
                    if "<BOARD_TOKEN=" in line:
                        if bi < len(parts):
                            lines[li] = parts[bi]
                        else:
                            lines[li] = "【画板】（画板内容获取失败，请重新同步）"
                        bi += 1
                content = "\n".join(lines)
                # 未被占位行消耗的剩余画板段落追加到尾部
                if bi < len(parts):
                    content = content + "\n\n" + "\n\n".join(parts[bi:])
            elif board_tokens and "<BOARD_TOKEN=" not in content:
                # rawContent 降级路径:保留原尾部追加行为
                boards_md = await _collect_board_markdown(board_tokens, enabled_configs)
                if boards_md:
                    content = content + "\n\n## 画板内容\n\n" + boards_md

            # 读取嵌入的电子表格/多维表格内容并追加到正文之后。
            # 1) 优先从块结构发现 token（rawContent 不携带表格引用）；
            # 2) 兜底再从正文正则抽（个别文档 rawContent 会带引用）。
            tokens: list = []
            if document_id:
                tokens += await _discover_embedded_sheet_tokens(
                    document_id, app_id, app_secret)
            tokens += FEISHU_SHEET_TOKEN_RE.findall(content)
            sheets_md = await _collect_sheet_markdown(tokens, app_id, app_secret)
            if sheets_md:
                content = content + "\n\n" + sheets_md

            # 解析内嵌图片：按需求补充多模态描述（未配置视觉模型/无权限时静默跳过）。
            # 图片描述按出现顺序内嵌到正文对应 image 引用行的位置，替代原附录追加。
            # 指定 section 的读取无法判断图片归属，故不内嵌图片，避免混入其他章节。
            if not section:
                try:
                    image_records = await _collect_image_records(
                        document_id, app_id, app_secret, vision_config)
                    if image_records:
                        content = _inline_image_descriptions(content, image_records)
                except Exception as e:
                    logger.warning("飞书文档图片内嵌失败 doc=%s: %s", document_id, e)

            if section:
                content = extract_document_section(content, section)
                if content is None:
                    raise DocumentSectionNotFoundError(f"飞书文档中未找到目录：{section}")
            return content
        except DocumentSectionNotFoundError:
            raise
        except Exception as e:
            logger.warning("Feishu MCP prefetch failed on server %s: %s", server_name, e)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    return None


async def _collect_sheet_markdown(tokens: list, app_id: str, app_secret: str) -> str:
    """把一组嵌入表格 token 逐个读取并拼成 Markdown。"""
    if not (app_id and app_secret) or not tokens:
        return ""
    seen = set()
    blocks = []
    for tok in dict.fromkeys(tokens):  # 保序去重
        if tok in seen:
            continue
        seen.add(tok)
        spread, sheet_id = _split_sheet_token(tok)
        md = await fetch_feishu_sheet(spread, sheet_id, app_id, app_secret)
        if md:
            blocks.append(md)
    return "\n\n".join(blocks)


async def _discover_embedded_sheet_tokens(
    document_id: str, app_id: str, app_secret: str
) -> list:
    """通过飞书 docx blocks REST API 发现文档中嵌入的电子表格/多维表格块 token。

    关键：``docx_v1_document_rawContent`` 是纯文本，**不含**嵌入表格的引用，
    必须从块结构里才能拿到子表 token。lark-mcp 未暴露 blocks 工具，故直连 REST API。
    嵌入块特征：block_type=30(sheet) -> block['sheet']['token']；
    block_type=14(bitable) -> block['bitable']['token'] + ['table_id']。
    """
    token = await _get_feishu_tenant_token(app_id, app_secret)
    if not token or not document_id:
        return []
    url = f"{_FEISHU_API_BASE}/docx/v1/documents/{document_id}/blocks"
    try:
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={"page_size": 500},
            )
            data = resp.json()
        if data.get("code") != 0:
            logger.warning("飞书 blocks 读取失败 doc=%s: %s", document_id, data)
            return []
        items = (data.get("data", {}) or {}).get("items", []) or []
        tokens = []
        for b in items:
            bt = b.get("block_type")
            if bt == 30 and isinstance(b.get("sheet"), dict):
                t = b["sheet"].get("token")
                if t:
                    tokens.append(t)
            elif bt == 14 and isinstance(b.get("bitable"), dict):
                t = b["bitable"].get("token")
                tid = b["bitable"].get("table_id")
                if t and tid:
                    tokens.append(f"{t}_{tid}")
        return tokens
    except Exception as e:
        logger.warning("飞书 blocks 读取异常 doc=%s: %s", document_id, e)
        return []


# 飞书 docx 标题块类型：heading1..heading9 → block_type 3..11
# 文本所在字段名与 block_type 同族：heading1..heading9 / text / ordered（bullet）
_HEADING_BLOCK_TYPES = {3: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 8, 11: 9}
_TEXT_BLOCK_TYPE = 2
_BULLET_BLOCK_TYPE = 13
_ORDERED_BLOCK_TYPE = 12
_IMAGE_BLOCK_TYPE = 27
_QUOTE_BLOCK_TYPE = 15
_TODO_BLOCK_TYPE = 17
_DIVIDER_BLOCK_TYPE = 22
_TABLE_BLOCK_TYPE = 31
_TABLE_CELL_BLOCK_TYPE = 32
_CONTAINER_BLOCK_TYPES = {24, 25, 33}  # grid / grid_column / view：仅递归 children


def _block_text(block: dict) -> str:
    """提取块的纯文本（按 block_type 从对应字段取 elements[].text_run.content）。"""
    bt = block.get("block_type")
    if bt in _HEADING_BLOCK_TYPES:
        payload = block.get(f"heading{_HEADING_BLOCK_TYPES[bt]}") or {}
    elif bt == _BULLET_BLOCK_TYPE:
        payload = block.get("bullet") or block.get("ordered") or {}
    elif bt == _ORDERED_BLOCK_TYPE:
        payload = block.get("ordered") or block.get("bullet") or {}
    elif bt == _QUOTE_BLOCK_TYPE:
        payload = block.get("quote") or block.get("quote_container") or {}
    elif bt == _TODO_BLOCK_TYPE:
        payload = block.get("todo") or {}
    else:
        payload = block.get("text") or {}
    elements = payload.get("elements") or []
    parts = []
    for el in elements:
        tr = el.get("text_run") or {}
        c = tr.get("content")
        if isinstance(c, str):
            parts.append(c)
    return "".join(parts)


async def _build_markdown_from_blocks(
    document_id: str,
    app_id: str,
    app_secret: str,
) -> "str | None":
    """用 docx blocks API 重建带标题级别的 Markdown 正文。

    rawContent 是纯文本，丢失了标题级别；blocks API 的 block_type 明确
    标注 heading1..9。按块树顺序生成：标题块加 # 前缀，bullet 块加 - ，
    文本块原样，图片块留 image.png 占位行（供图片描述内嵌），
    容器块（grid/view）递归 children。失败返回 None。
    """
    tenant_token = await _get_feishu_tenant_token(app_id, app_secret)
    if not tenant_token:
        return None
    try:
        items: list = []
        page_token = ""
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            while True:
                params = {"page_size": 500}
                if page_token:
                    params["page_token"] = page_token
                resp = await client.get(
                    f"{_FEISHU_API_BASE}/docx/v1/documents/{document_id}/blocks",
                    headers={"Authorization": f"Bearer {tenant_token}"},
                    params=params,
                )
                data = resp.json()
                if data.get("code") != 0:
                    logger.warning("飞书 blocks 读取失败 doc=%s: %s", document_id, data)
                    return None
                d = data.get("data", {}) or {}
                items.extend(d.get("items", []) or [])
                if d.get("has_more") and d.get("page_token"):
                    page_token = d["page_token"]
                else:
                    break

        blocks_by_id = {b.get("block_id"): b for b in items}
        root_id = document_id
        # 文档根的直接子块顺序（没有 parent 顺序信息时按 API 返回顺序兜底）
        roots = [b for b in items if b.get("parent_id") == root_id]
        if not roots:
            roots = items

        out: list[str] = []

        def emit_table(block: dict) -> None:
            """31 table → 按 table.cells(扁平顺序)+ column_size 切行，重建 Markdown 表格。"""
            tp = block.get("table") or {}
            cell_ids = tp.get("cells") or []
            col_size = int((tp.get("property") or {}).get("column_size") or 0)
            if not cell_ids or col_size <= 0:
                return
            md_rows: list[list[str]] = []
            current: list[str] = []
            for cid in cell_ids:
                cell = blocks_by_id.get(cid)
                texts: list[str] = []
                for tcv in (cell or {}).get("children") or []:
                    tchild = blocks_by_id.get(tcv)
                    if tchild:
                        t = _block_text(tchild).strip()
                        if t:
                            texts.append(t)
                current.append(" ".join(texts).replace("\n", " ").replace("|", "\\|"))
                if len(current) == col_size:
                    md_rows.append(current)
                    current = []
            if current:
                current += [""] * (col_size - len(current))
                md_rows.append(current)
            if not md_rows:
                return
            for i, r in enumerate(md_rows):
                out.append("| " + " | ".join(r) + " |")
                if i == 0:
                    out.append("|" + "---|" * col_size)

        def emit(block: dict) -> None:
            bt = block.get("block_type")
            if bt in _HEADING_BLOCK_TYPES:
                text = _block_text(block).strip()
                if text:
                    level = _HEADING_BLOCK_TYPES[bt]
                    out.append("#" * min(level, 6) + " " + text)
                # heading 的 children（其下的正文块）也要输出
                for cid in block.get("children") or []:
                    child = blocks_by_id.get(cid)
                    if child:
                        emit(child)
            elif bt in (_TEXT_BLOCK_TYPE, _BULLET_BLOCK_TYPE, _ORDERED_BLOCK_TYPE):
                text = _block_text(block).strip()
                if text:
                    out.append(text)
                # bullet/ordered 的嵌套子块无条件递归（保持文档顺序）
                for cid in block.get("children") or []:
                    child = blocks_by_id.get(cid)
                    if child:

                        emit(child)
            elif bt == _QUOTE_BLOCK_TYPE:
                text = _block_text(block).strip()
                if text:
                    out.append("> " + text)
                for cid in block.get("children") or []:
                    child = blocks_by_id.get(cid)
                    if child:
                        emit(child)
            elif bt == _TODO_BLOCK_TYPE:
                text = _block_text(block).strip()
                if text:
                    out.append("- [ ] " + text)
                for cid in block.get("children") or []:
                    child = blocks_by_id.get(cid)
                    if child:
                        emit(child)
            elif bt == _DIVIDER_BLOCK_TYPE:
                out.append("---")
            elif bt == _TABLE_BLOCK_TYPE:
                emit_table(block)
            elif bt == _IMAGE_BLOCK_TYPE:
                out.append("image.png")
            elif bt == _BOARD_BLOCK_TYPE:
                btoken = ((block.get("board") or {}).get("token") or "").strip()
                if btoken:
                    out.append("<BOARD_TOKEN=" + btoken + ">")
            elif bt == _ADD_ONS_BLOCK_TYPE:
                # 插件块：流程图类插件的 Mermaid 源码存在 add_ons.record（JSON 字符串）.data
                ao = block.get("add_ons") or {}
                rec = ao.get("record")
                diagram_text = ""
                if isinstance(rec, str) and rec.strip():
                    try:
                        parsed = json.loads(rec)
                        diagram_text = (parsed.get("data") or "").strip()
                    except Exception:
                        diagram_text = rec.strip()
                if diagram_text:
                    out.append("【流程图】")
                    for dline in diagram_text.split("\n"):
                        dline = dline.strip()
                        if dline:
                            out.append(dline)
            elif bt in _CONTAINER_BLOCK_TYPES:
                for cid in block.get("children") or []:
                    child = blocks_by_id.get(cid)
                    if child:
                        emit(child)
            elif block.get("children"):
                for cid in block.get("children") or []:
                    child = blocks_by_id.get(cid)
                    if child:
                        emit(child)

        for b in roots:
            emit(b)

        markdown = "\n".join(out).strip()
        if markdown:
            logger.info("已用 blocks API 重建带标题级别正文: doc=%s lines=%s", document_id, len(out))
            return markdown
        return None
    except Exception as e:
        logger.warning("blocks API 重建正文失败 doc=%s: %s", document_id, e)
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


async def prefetch_document(text: str, mcp_configs, vision_config: "dict | None" = None) -> str:
    """Replace a Feishu/Lanhu link in `text` with its fetched content.

    Tries Feishu first, then Lanhu. Returns the fetched content if found,
    otherwise the original text unchanged (matching pipeline_runner's
    previous behavior of returning replacement-or-original).
    """
    feishu = await fetch_feishu_document(text, mcp_configs, vision_config=vision_config)
    if feishu:
        return feishu
    lanhu = await fetch_lanhu_document(text, mcp_configs)
    if lanhu:
        return lanhu
    return text
