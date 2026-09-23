"""global-parameters 路由权限:GET 任何登录用户可读(展示解析 {{var}}),写仍需 manage。"""
import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.auth import UserInfo
from app.permissions import Permission, require_http_method_permissions


def _dep():
    return require_http_method_permissions(
        get=[],
        post=[Permission.GLOBAL_PARAMETER_MANAGE],
        put=[Permission.GLOBAL_PARAMETER_MANAGE],
        delete=[Permission.GLOBAL_PARAMETER_MANAGE],
    )


def _request(method: str) -> MagicMock:
    req = MagicMock()
    req.method = method
    return req


def _user(roles: list[str]) -> UserInfo:
    return UserInfo(user_id="u1", username="u1", roles=roles)


def test_get_allowed_for_user_without_manage():
    """user 角色(无 global_parameter:manage)GET 必须放行——前端展示 {{web_url}} 依赖它。"""
    result = asyncio.run(_dep()(request=_request("GET"), user=_user(["user"])))
    assert result.username == "u1"


def test_post_denied_for_user_without_manage():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_dep()(request=_request("POST"), user=_user(["user"])))
    assert exc.value.status_code == 403


def test_post_allowed_for_admin():
    result = asyncio.run(_dep()(request=_request("POST"), user=_user(["admin"])))
    assert result.username == "u1"
