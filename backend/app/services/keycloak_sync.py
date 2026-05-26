import logging
import os
import time
from typing import Any

import httpx

from app.permissions import Permission, ROLE_PERMISSIONS

logger = logging.getLogger(__name__)

KEYCLOAK_ADMIN_URL = os.getenv("KEYCLOAK_ADMIN_URL", os.getenv("KEYCLOAK_SERVER_URL", "http://localhost:8080"))
KEYCLOAK_ADMIN_REALM = os.getenv("KEYCLOAK_ADMIN_REALM", "master")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "ai-testcase")
KEYCLOAK_ADMIN_CLIENT_ID = os.getenv("KEYCLOAK_ADMIN_CLIENT_ID", "admin-cli")
KEYCLOAK_ADMIN_USERNAME = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
KEYCLOAK_SYNC_STRICT = os.getenv("KEYCLOAK_SYNC_STRICT", "false").lower() == "true"
KEYCLOAK_SYNC_RETRIES = int(os.getenv("KEYCLOAK_SYNC_RETRIES", "6"))
KEYCLOAK_SYNC_RETRY_DELAY = float(os.getenv("KEYCLOAK_SYNC_RETRY_DELAY", "5"))

GROUP_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "group:ai-testcase": {
        Permission.TESTCASE_READ,
        Permission.TESTCASE_CREATE,
        Permission.TESTCASE_UPDATE,
        Permission.TESTCASE_DELETE,
        Permission.TESTCASE_GENERATE,
        Permission.SESSION_READ,
        Permission.SESSION_CREATE,
        Permission.SESSION_UPDATE,
        Permission.SESSION_DELETE,
        Permission.MODULE_READ,
        Permission.MODULE_CREATE,
        Permission.MODULE_UPDATE,
        Permission.MODULE_DELETE,
    },
    "group:mock-platform": {
        Permission.MOCK_MANAGE,
        Permission.MOCK_LOG_READ,
        Permission.MOCK_LOG_DELETE,
    },
    "group:iot-platform": {
        Permission.IOT_READ,
        Permission.IOT_CREATE,
        Permission.IOT_UPDATE,
        Permission.IOT_DELETE,
        Permission.IOT_EXECUTE,
    },
    "group:system-admin": {
        Permission.CONFIG_MANAGE,
        Permission.MCP_MANAGE,
        Permission.SKILLS_MANAGE,
        Permission.GLOBAL_PARAMETER_MANAGE,
        Permission.SCHEDULED_TASK_MANAGE,
    },
}

GROUP_TO_ROLE: dict[str, str] = {
    "AI生成测试用例": "group:ai-testcase",
    "Mock平台": "group:mock-platform",
    "IoT平台": "group:iot-platform",
    "系统管理": "group:system-admin",
}


def _role_payload(role_name: str) -> dict[str, Any]:
    return {
        "name": role_name,
        "description": f"Auto-managed role: {role_name}",
    }


def _build_expected_roles() -> set[str]:
    role_names = set(ROLE_PERMISSIONS.keys())
    for permissions in ROLE_PERMISSIONS.values():
        role_names.update(permissions)
    role_names.update(GROUP_ROLE_PERMISSIONS.keys())
    return role_names


def _get_admin_token(client: httpx.Client) -> str:
    token_url = f"{KEYCLOAK_ADMIN_URL}/realms/{KEYCLOAK_ADMIN_REALM}/protocol/openid-connect/token"
    response = client.post(
        token_url,
        data={
            "client_id": KEYCLOAK_ADMIN_CLIENT_ID,
            "grant_type": "password",
            "username": KEYCLOAK_ADMIN_USERNAME,
            "password": KEYCLOAK_ADMIN_PASSWORD,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _get_realm_roles(client: httpx.Client) -> dict[str, dict[str, Any]]:
    roles_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/roles"
    response = client.get(roles_url)
    response.raise_for_status()
    return {role["name"]: role for role in response.json()}


def _create_realm_role(client: httpx.Client, role_name: str) -> None:
    roles_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/roles"
    response = client.post(roles_url, json=_role_payload(role_name))
    response.raise_for_status()
    logger.info("Created realm role: %s", role_name)


def _ensure_roles(client: httpx.Client, expected_roles: set[str]) -> dict[str, dict[str, Any]]:
    existing_roles = _get_realm_roles(client)
    for role_name in sorted(expected_roles):
        if role_name not in existing_roles:
            _create_realm_role(client, role_name)
    return _get_realm_roles(client)


def _get_composites(client: httpx.Client, role_name: str) -> set[str]:
    composites_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/roles/{role_name}/composites"
    response = client.get(composites_url)
    response.raise_for_status()
    return {role["name"] for role in response.json()}


def _add_composites(client: httpx.Client, role_name: str, composite_roles: list[dict[str, Any]]) -> None:
    if not composite_roles:
        return
    composites_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/roles/{role_name}/composites"
    response = client.post(composites_url, json=composite_roles)
    response.raise_for_status()
    logger.info(
        "Added %d composite roles to '%s': %s",
        len(composite_roles),
        role_name,
        ", ".join(sorted([item["name"] for item in composite_roles])),
    )


def _ensure_role_composites(client: httpx.Client, role_index: dict[str, dict[str, Any]]) -> None:
    for role_name, permissions in ROLE_PERMISSIONS.items():
        current_composites = _get_composites(client, role_name)
        missing = sorted(set(permissions) - current_composites)
        if missing:
            _add_composites(client, role_name, [role_index[name] for name in missing if name in role_index])

    for role_name, permissions in GROUP_ROLE_PERMISSIONS.items():
        current_composites = _get_composites(client, role_name)
        missing = sorted(set(permissions) - current_composites)
        if missing:
            _add_composites(client, role_name, [role_index[name] for name in missing if name in role_index])


def _get_groups(client: httpx.Client) -> dict[str, dict[str, Any]]:
    groups_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/groups"
    response = client.get(groups_url, params={"max": 500})
    response.raise_for_status()
    return {group["name"]: group for group in response.json()}


def _create_group(client: httpx.Client, group_name: str) -> None:
    groups_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/groups"
    response = client.post(groups_url, json={"name": group_name})
    response.raise_for_status()
    logger.info("Created group: %s", group_name)


def _get_group_realm_roles(client: httpx.Client, group_id: str) -> set[str]:
    roles_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/groups/{group_id}/role-mappings/realm"
    response = client.get(roles_url)
    response.raise_for_status()
    return {role["name"] for role in response.json()}


def _add_group_realm_roles(client: httpx.Client, group_id: str, roles: list[dict[str, Any]]) -> None:
    if not roles:
        return
    roles_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/groups/{group_id}/role-mappings/realm"
    response = client.post(roles_url, json=roles)
    response.raise_for_status()


def _ensure_groups(client: httpx.Client, role_index: dict[str, dict[str, Any]]) -> None:
    groups = _get_groups(client)
    for group_name in GROUP_TO_ROLE:
        if group_name not in groups:
            _create_group(client, group_name)
    groups = _get_groups(client)

    for group_name, role_name in GROUP_TO_ROLE.items():
        group = groups.get(group_name)
        if not group:
            continue
        existing_group_roles = _get_group_realm_roles(client, group["id"])
        if role_name not in existing_group_roles and role_name in role_index:
            _add_group_realm_roles(client, group["id"], [role_index[role_name]])
            logger.info("Bound role '%s' to group '%s'", role_name, group_name)


def sync_keycloak_roles() -> None:
    expected_roles = _build_expected_roles()
    last_error: Exception | None = None
    for attempt in range(1, KEYCLOAK_SYNC_RETRIES + 1):
        try:
            with httpx.Client(timeout=15) as client:
                token = _get_admin_token(client)
                client.headers.update({"Authorization": f"Bearer {token}"})
                role_index = _ensure_roles(client, expected_roles)
                _ensure_role_composites(client, role_index)
                _ensure_groups(client, role_index)
                logger.info("Keycloak role sync completed for realm '%s'", KEYCLOAK_REALM)
                return
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Keycloak role sync attempt %d/%d failed: %s",
                attempt,
                KEYCLOAK_SYNC_RETRIES,
                exc,
            )
            if attempt < KEYCLOAK_SYNC_RETRIES:
                time.sleep(KEYCLOAK_SYNC_RETRY_DELAY)

    logger.error("Keycloak role sync failed after %d attempts: %s", KEYCLOAK_SYNC_RETRIES, last_error)
    if KEYCLOAK_SYNC_STRICT and last_error is not None:
        raise last_error
