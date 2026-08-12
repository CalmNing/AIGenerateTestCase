"""飞书文档内容读取 API

提供飞书文档内容读取能力，支持正文、嵌入表格和画板内容。
用于测试用例生成前的文档预取和预览。
"""
import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps import CurrentUser
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/document", tags=["document"])

_FEISHU_URL_RE = re.compile(
    r'https?://[a-zA-Z0-9-]+\.feishu\.cn/(?:wiki|docx)/([A-Za-z0-9_-]+)',
    re.IGNORECASE,
)


class FeishuContentRequest(BaseModel):
    url: str
    include_boards: bool = True


class BoardInfo(BaseModel):
    token: str
    block_id: str = ""
    width: float = 0
    height: float = 0
    content: str = ""


class FeishuContentResponse(BaseModel):
    text: str
    boards: list[BoardInfo] = []
    document_id: str = ""


@router.post("/feishu/content")
async def fetch_feishu_content(
    req: FeishuContentRequest,
    current_user: CurrentUser = None,
):
    """读取飞书文档内容，可选包含画板。

    返回文档正文、嵌入表格和画板内容。
    """
    match = _FEISHU_URL_RE.search(req.url)
    if not match:
        raise HTTPException(status_code=400, detail="无效的飞书文档 URL")

    token = match.group(1)

    # 获取用户的 MCP 配置
    try:
        from app.services.mcp_loader import load_user_mcp_configs
        from app.deps import get_db
        with next(get_db()) as session:
            mcp_configs = load_user_mcp_configs(session, current_user)
    except Exception:
        mcp_configs = []

    try:
        from utils.document_prefetch import (
            fetch_feishu_document,
            _discover_board_tokens,
            _resolve_feishu_credentials,
            _collect_board_markdown,
        )

        # 获取文档正文（含嵌入表格和画板）
        content = await fetch_feishu_document(req.url, mcp_configs)
        if not content:
            raise HTTPException(
                status_code=400,
                detail="飞书文档读取失败：未能通过 MCP 获取文档正文。"
                       "请检查 MCP 服务器配置、飞书应用权限或 access token 是否有效后重试。",
            )

        # 如果需要单独获取画板详情
        boards: list[BoardInfo] = []
        if req.include_boards and content:
            app_id, app_secret = _resolve_feishu_credentials(mcp_configs)
            board_tokens = await _discover_board_tokens(token, app_id, app_secret)
            for bt in board_tokens:
                boards.append(BoardInfo(
                    token=bt["token"],
                    block_id=bt.get("block_id", ""),
                    width=bt.get("width", 0),
                    height=bt.get("height", 0),
                ))

        return Response.success(FeishuContentResponse(
            text=content or "",
            boards=boards,
            document_id=token,
        ))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("飞书文档读取失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"文档读取失败: {str(e)}")


@router.post("/feishu/board/{board_token}")
async def fetch_board_content(
    board_token: str,
    current_user: CurrentUser = None,
):
    """单独读取飞书画板内容。"""
    try:
        from app.services.mcp_loader import load_user_mcp_configs
        from app.deps import get_db
        with next(get_db()) as session:
            mcp_configs = load_user_mcp_configs(session, current_user)
    except Exception:
        mcp_configs = []

    try:
        from utils.document_prefetch import fetch_feishu_board
        content = await fetch_feishu_board(board_token, mcp_configs)
        return Response.success({
            "token": board_token,
            "content": content or "",
        })
    except Exception as e:
        logger.error("画板读取失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"画板读取失败: {str(e)}")


@router.post("/feishu/boards")
async def fetch_boards_content(
    board_tokens: list[str],
    current_user: CurrentUser = None,
):
    """批量读取飞书画板内容。"""
    try:
        from app.services.mcp_loader import load_user_mcp_configs
        from app.deps import get_db
        with next(get_db()) as session:
            mcp_configs = load_user_mcp_configs(session, current_user)
    except Exception:
        mcp_configs = []

    try:
        from utils.document_prefetch import fetch_feishu_board
        results = []
        for token in board_tokens:
            content = await fetch_feishu_board(token, mcp_configs)
            results.append({
                "token": token,
                "content": content or "",
            })
        return Response.success(results)
    except Exception as e:
        logger.error("批量画板读取失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"画板读取失败: {str(e)}")
