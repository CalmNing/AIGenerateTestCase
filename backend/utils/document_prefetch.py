"""Shared Feishu/Lanhu document prefetch via MCP.

Single source of truth for the Lanhu/Feishu URL regexes and the prefetch
logic previously duplicated in utils/model_utils.py and
app/pipeline/pipeline_runner.py.

Feishu doc extraction covers three layers:
1. 正文 (body)        — `docx_v1_document_rawContent` via MCP.
2. 嵌入的表格 (sheet) — 通过飞书 docx blocks REST API（`tenant_access_token`）
                        发现嵌入块（block_type=30 sheet / 14 bitable）拿到子表 token，
                        再用 Sheets API 直连读取单元格。
                        关键：rawContent 是纯文本、**不含**嵌入表格引用，
                        lark-mcp 也未暴露 blocks 工具，故必须直连 REST API。
                        详见 docs/feishu-doc-extraction.md。
"""
import logging
import re

import httpx

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
    """读取多个画板并拼成 Markdown。"""
    blocks: list[str] = []
    for i, board in enumerate(boards):
        md = await fetch_feishu_board(board["token"], mcp_configs)
        if md:
            label = f"画板 {i + 1}"
            if board.get("width") and board.get("height"):
                label += f" ({board['width']}x{board['height']})"
            blocks.append(f"### {label}\n\n{md}")
    return "\n\n".join(blocks)




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


async def fetch_feishu_document(text: str, mcp_configs, section: str | None = None) -> "str | None":
    """Read a Feishu wiki/docx link's body + embedded sheets via MCP/API.

    When section is provided, return that heading and its nested content only.
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

            # 读取画板内容并追加到正文之后。
            board_tokens = await _discover_board_tokens(document_id, app_id, app_secret)
            if board_tokens:
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
