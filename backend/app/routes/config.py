"""
系统配置管理路由
提供配置的CRUD接口，配置存储在数据库中
"""
import asyncio
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from sqlmodel import select

from app.deps import SessionDep, CurrentUser
from db.models import SystemConfig
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    model_type: Optional[str] = None
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None
    api_model: Optional[str] = None
    api_proxy_url: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    lanhu_cookie: Optional[str] = None
    bug_link_template: Optional[str] = None


def _get_or_create_config(session, user_id: Optional[str] = None) -> SystemConfig:
    """获取或创建用户配置"""
    # 尝试获取现有配置
    statement = select(SystemConfig).where(SystemConfig.user_id == user_id)
    config = session.exec(statement).first()

    if not config:
        # 创建默认配置
        config = SystemConfig(
            user_id=user_id,
            model_type="api",
            api_key="",
            api_base_url="https://api.deepseek.com",
            api_model="deepseek-v4-flash",
            api_proxy_url="",
            ollama_url="http://localhost:11434",
            ollama_model="llama3",
        )
        session.add(config)
        session.commit()
        session.refresh(config)

    return config


@router.get("")
async def get_config(
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """获取当前用户的系统配置"""
    user_id = user.user_id if user else None
    config = _get_or_create_config(session, user_id)

    return Response(data={
        "model_type": config.model_type,
        "api_key": config.api_key or "",
        "api_base_url": config.api_base_url,
        "api_model": config.api_model,
        "api_proxy_url": config.api_proxy_url or "",
        "ollama_url": config.ollama_url,
        "ollama_model": config.ollama_model,
        "lanhu_cookie": config.lanhu_cookie or "",
        "bug_link_template": config.bug_link_template or "",
    })


@router.put("")
async def update_config(
    body: ConfigUpdateRequest,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """更新当前用户的系统配置"""
    user_id = user.user_id if user else None
    config = _get_or_create_config(session, user_id)

    # 记录旧的蓝湖 Cookie 值，用于比较
    old_cookie = config.lanhu_cookie or ""

    # 更新字段
    if body.model_type is not None:
        config.model_type = body.model_type
    if body.api_key is not None:
        config.api_key = body.api_key
    if body.api_base_url is not None:
        config.api_base_url = body.api_base_url
    if body.api_model is not None:
        config.api_model = body.api_model
    if body.api_proxy_url is not None:
        config.api_proxy_url = body.api_proxy_url
    if body.ollama_url is not None:
        config.ollama_url = body.ollama_url
    if body.ollama_model is not None:
        config.ollama_model = body.ollama_model
    if body.lanhu_cookie is not None:
        config.lanhu_cookie = body.lanhu_cookie
    if body.bug_link_template is not None:
        config.bug_link_template = body.bug_link_template

    session.add(config)
    session.commit()

    # 只有当蓝湖 Cookie 真正发生变化时，才异步转发到 MCP 服务
    new_cookie = body.lanhu_cookie if body.lanhu_cookie is not None else old_cookie
    if body.lanhu_cookie is not None and body.lanhu_cookie != old_cookie:
        # 异步执行，不阻塞响应
        asyncio.create_task(_forward_cookie_to_lanhu_mcp(body.lanhu_cookie))

    return Response(message="配置已保存")


def _get_lanhu_mcp_base_url() -> str:
    """从配置获取 lanhu-mcp 服务的基础地址"""
    return "http://lanhu-mcp:8002"


async def _forward_cookie_to_lanhu_mcp(cookie: str) -> bool:
    """将 cookie 转发到 lanhu-mcp 服务的运行时配置端点（后台任务）"""
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
            async with httpx.AsyncClient(timeout=3) as client:
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


@router.get("/models")
async def list_api_models(
    api_key: str = Query(..., description="API Key"),
    api_base_url: str = Query("https://api.deepseek.com", description="OpenAI 兼容 API 地址"),
):
    """从 OpenAI 兼容接口获取可用模型列表"""
    api_key = api_key.strip()
    api_base_url = api_base_url.strip().rstrip("/")
    if not api_base_url:
        api_base_url = "https://api.deepseek.com"
    if not api_key:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="api_key 不能为空")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{api_base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                result = [
                    {"id": m.get("id", ""), "owned_by": m.get("owned_by", "")}
                    for m in models
                ]
                # 按 ID 排序，chat 类模型优先
                result.sort(key=lambda m: (not m["id"].startswith(("gpt", "claude", "deepseek", "chat")), m["id"]))
                return Response(data=result)
            else:
                logger.warning(f"获取模型列表失败: status={resp.status_code} body={resp.text[:300]}")
                return Response(
                    code=status.HTTP_502_BAD_GATEWAY,
                    message=f"获取模型列表失败 ({resp.status_code})，请检查 API Key 和 Base URL 是否正确",
                )
    except httpx.ConnectError:
        return Response(
            code=status.HTTP_502_BAD_GATEWAY,
            message=f"无法连接到 {api_base_url}，请检查 Base URL 和网络连接",
        )
    except Exception as e:
        logger.error(f"获取模型列表异常: {e}")
        return Response(
            code=status.HTTP_502_BAD_GATEWAY,
            message=f"获取模型列表时出错: {str(e)}",
        )
