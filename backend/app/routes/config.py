import logging

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from config import config_manager
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


class LanhuCookieRequest(BaseModel):
    cookie: str


def _get_lanhu_mcp_base_url() -> str:
    """从配置的 MCP URL 推导 lanhu-mcp 服务的基础地址"""
    mcp_url = config_manager.get("mcp_server_url", "http://lanhu-mcp:8000/mcp")
    if mcp_url.endswith("/mcp"):
        return mcp_url[:-4]
    return mcp_url


async def _forward_cookie_to_lanhu_mcp(cookie: str) -> bool:
    """将 cookie 转发到 lanhu-mcp 服务的运行时配置端点"""
    base_url = _get_lanhu_mcp_base_url()
    urls = [base_url, "http://localhost:8002"]
    # 去重
    seen = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    for url in unique:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{url}/config/cookie",
                    json={"cookie": cookie},
                )
                if resp.status_code == 200:
                    logger.info(f"Cookie 已转发到 lanhu-mcp ({url})")
                    return True
                else:
                    logger.warning(f"转发 cookie 到 {url} 返回 {resp.status_code}")
        except Exception as e:
            logger.warning(f"转发 cookie 到 {url} 失败: {e}")
    return False


@router.get("/lanhu-cookie")
async def get_lanhu_cookie():
    """获取当前 LANHU_COOKIE 配置（脱敏）"""
    cookie = config_manager.get("lanhu_cookie", "")
    masked = (cookie[:20] + "...") if len(cookie) > 20 else ("***" if cookie else "")
    return Response(data={"cookie": masked, "has_cookie": bool(cookie)})


@router.post("/lanhu-cookie")
async def set_lanhu_cookie(req: LanhuCookieRequest):
    """设置 LANHU_COOKIE 并转发到运行中的 lanhu-mcp 服务"""
    config_manager.set("lanhu_cookie", req.cookie)

    forwarded = await _forward_cookie_to_lanhu_mcp(req.cookie)
    if forwarded:
        return Response(message="蓝湖 Cookie 已更新并同步到 MCP 服务")
    else:
        return Response(message="蓝湖 Cookie 已保存（MCP 服务未运行，重启后生效）")
