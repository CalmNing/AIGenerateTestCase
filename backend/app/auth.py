"""Keycloak JWT 认证模块

实现基于 Keycloak JWKS 公钥的本地 JWT 验证，无需每次请求回调 Keycloak introspect 端点。
"""
import os
import time
import logging
from typing import Optional

import httpx
from jose import JWTError, jwt, jwk
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Keycloak 配置（从环境变量读取）
# KEYCLOAK_SERVER_URL: 内部网络地址（用于获取 JWKS 公钥，Docker 内用 http://keycloak:8080）
# KEYCLOAK_EXTERNAL_URL: 外部访问地址（用于验证 JWT issuer，浏览器看到的地址）
KEYCLOAK_SERVER_URL = os.getenv("KEYCLOAK_SERVER_URL", "http://localhost:8090")
KEYCLOAK_EXTERNAL_URL = os.getenv("KEYCLOAK_EXTERNAL_URL", KEYCLOAK_SERVER_URL)
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "ai-testcase")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "backend")

# JWKS 缓存
_jwks_cache: Optional[dict] = None
_jwks_cache_time: float = 0
_JWKS_CACHE_TTL = 86400  # 24小时刷新一次

security = HTTPBearer(auto_error=False)


class UserInfo(BaseModel):
    """从 JWT Token 中提取的用户信息"""
    user_id: str  # Keycloak sub (UUID)
    username: str
    email: Optional[str] = None
    roles: list[str] = []


def _get_jwks_url() -> str:
    """获取 Keycloak JWKS 端点 URL"""
    return f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"


def _get_jwks() -> dict:
    """从 Keycloak 获取 JWKS 公钥，带内存缓存"""
    global _jwks_cache, _jwks_cache_time

    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < _JWKS_CACHE_TTL:
        return _jwks_cache

    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(_get_jwks_url())
            response.raise_for_status()
            _jwks_cache = response.json()
            _jwks_cache_time = now
            logger.info("JWKS public keys fetched and cached successfully")
            return _jwks_cache
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
        if _jwks_cache:
            logger.warning("Using cached JWKS as fallback")
            return _jwks_cache
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to fetch authentication keys"
        )


def _refresh_jwks() -> dict:
    """强制刷新 JWKS 缓存"""
    global _jwks_cache, _jwks_cache_time
    _jwks_cache = None
    _jwks_cache_time = 0
    return _get_jwks()


def verify_token(token: str) -> UserInfo:
    """验证 JWT Token 签名和有效期，返回用户信息

    Args:
        token: JWT Token 字符串

    Returns:
        UserInfo: 从 Token 中提取的用户信息

    Raises:
        HTTPException: Token 无效或过期
    """
    jwks = _get_jwks()

    try:
        # 获取 Token 的 kid (Key ID)
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        # 从 JWKS 中找到匹配的公钥
        rsa_key = {}
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                rsa_key = key
                break

        if not rsa_key:
            # 公钥未找到，尝试刷新 JWKS 缓存
            logger.info(f"Key ID '{kid}' not found in JWKS, refreshing cache")
            jwks = _refresh_jwks()
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    rsa_key = key
                    break

        if not rsa_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to find matching key in JWKS"
            )

        # 构造公钥用于验证
        public_key = jwk.construct(rsa_key)

        # 验证 Token
        # issuer 使用外部地址（浏览器视角），JWKS 使用内部地址
        issuer = f"{KEYCLOAK_EXTERNAL_URL}/realms/{KEYCLOAK_REALM}"
        payload = jwt.decode(
            token,
            public_key.to_pem().decode("utf-8"),
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False},  # 前端 token 的 aud 是 frontend client，后端不做 audience 校验
        )

        # 提取用户信息
        realm_access = payload.get("realm_access", {})
        roles = list(realm_access.get("roles", []))

        # 也检查 resource_access 中的客户端角色
        resource_access = payload.get("resource_access", {})
        client_roles = resource_access.get(KEYCLOAK_CLIENT_ID, {}).get("roles", [])
        roles = list(dict.fromkeys(roles + client_roles))

        return UserInfo(
            user_id=payload.get("sub", ""),
            username=payload.get("preferred_username", payload.get("sub", "")),
            email=payload.get("email"),
            roles=roles,
        )

    except JWTError as e:
        logger.warning(f"JWT verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UserInfo:
    """FastAPI 依赖：从请求头提取并验证 Bearer Token

    用法:
        @router.get("/protected")
        async def protected_route(user: CurrentUser):
            ...
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return verify_token(credentials.credentials)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Optional[UserInfo]:
    """可选认证依赖：有 Token 则验证，没有则返回 None"""
    if credentials is None:
        return None
    try:
        return verify_token(credentials.credentials)
    except HTTPException:
        return None
