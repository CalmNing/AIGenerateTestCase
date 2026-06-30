"""Shared loader for a user's enabled, server-persisted MCP configs.

Centralizes the permission check + query + model_dump duplicated in
routes/testcase.py and routes/pipeline.py. Returns [] when the user lacks
mcp:manage permission.
"""
import logging

from sqlmodel import select

from app.permissions import Permission, get_user_permissions
from db.models import McpServer

logger = logging.getLogger(__name__)

_EXCLUDE_FIELDS = {"id", "created_at", "updated_at", "user_id"}


def load_user_mcp_configs(session, user) -> list[dict]:
    """Return the current user's enabled MCP server configs as dicts.

    Only server-persisted configs are read (client-supplied configs are
    ignored elsewhere to preserve /api/mcp permission control). Returns an
    empty list when the user lacks the MCP_MANAGE permission.
    """
    if Permission.MCP_MANAGE not in get_user_permissions(user):
        logger.info("用户无 mcp:manage 权限，不加载自定义 MCP 配置")
        return []

    servers = session.exec(
        select(McpServer).where(
            McpServer.user_id == user.user_id,
            McpServer.enabled == True,  # noqa: E712 (SQLModel needs ==)
        )
    ).all()
    configs = [s.model_dump(exclude=_EXCLUDE_FIELDS) for s in servers]
    logger.info(f"已加载 {len(configs)} 个服务端 MCP 服务器配置")
    return configs
