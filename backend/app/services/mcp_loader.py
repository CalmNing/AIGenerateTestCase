"""Shared loader for the shared, server-persisted MCP configs (all users).

Centralizes the permission check + query + model_dump duplicated in
routes/testcase.py and routes/pipeline.py. Returns [] when the user lacks
mcp:manage permission. Data is shared across users — no per-user filtering.
"""
import logging

from sqlmodel import select

from app.permissions import Permission, get_user_permissions
from db.models import McpServer

logger = logging.getLogger(__name__)

_EXCLUDE_FIELDS = {"id", "created_at", "updated_at", "user_id"}


def load_user_mcp_configs(session, user) -> list[dict]:
    """Return all enabled MCP server configs as dicts (shared across users).

    Only server-persisted configs are read (client-supplied configs are
    ignored elsewhere to preserve /api/mcp permission control). Returns an
    empty list when the user lacks the MCP_MANAGE permission.
    """
    if Permission.MCP_MANAGE not in get_user_permissions(user):
        logger.info("用户无 mcp:manage 权限，不加载自定义 MCP 配置")
        return []

    servers = session.exec(
        select(McpServer).where(
            McpServer.enabled == True,  # noqa: E712 (SQLModel needs ==)
        )
    ).all()
    configs = [s.model_dump(exclude=_EXCLUDE_FIELDS) for s in servers]
    logger.info(f"已加载 {len(configs)} 个服务端 MCP 服务器配置")
    return configs
