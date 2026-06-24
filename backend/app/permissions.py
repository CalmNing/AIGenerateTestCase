from collections.abc import Iterable

from fastapi import Depends, HTTPException, Request, status

from app.auth import UserInfo, get_current_user


class Permission:
    TESTCASE_READ = "testcase:read"
    TESTCASE_CREATE = "testcase:create"
    TESTCASE_UPDATE = "testcase:update"
    TESTCASE_DELETE = "testcase:delete"
    TESTCASE_GENERATE = "testcase:generate"
    SESSION_READ = "session:read"
    SESSION_CREATE = "session:create"
    SESSION_UPDATE = "session:update"
    SESSION_DELETE = "session:delete"
    MODULE_READ = "module:read"
    MODULE_CREATE = "module:create"
    MODULE_UPDATE = "module:update"
    MODULE_DELETE = "module:delete"
    IOT_READ = "iot:read"
    IOT_CREATE = "iot:create"
    IOT_UPDATE = "iot:update"
    IOT_DELETE = "iot:delete"
    IOT_EXECUTE = "iot:execute"
    CONFIG_MANAGE = "config:manage"
    MCP_MANAGE = "mcp:manage"
    SKILLS_MANAGE = "skills:manage"
    GLOBAL_PARAMETER_MANAGE = "global_parameter:manage"
    SCHEDULED_TASK_MANAGE = "scheduled_task:manage"
    MOCK_MANAGE = "mock:manage"
    MOCK_LOG_READ = "mock_log:read"
    MOCK_LOG_DELETE = "mock_log:delete"


ADMIN_PERMISSIONS = {
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
    Permission.IOT_READ,
    Permission.IOT_CREATE,
    Permission.IOT_UPDATE,
    Permission.IOT_DELETE,
    Permission.IOT_EXECUTE,
    Permission.CONFIG_MANAGE,
    Permission.MCP_MANAGE,
    Permission.SKILLS_MANAGE,
    Permission.GLOBAL_PARAMETER_MANAGE,
    Permission.SCHEDULED_TASK_MANAGE,
    Permission.MOCK_MANAGE,
    Permission.MOCK_LOG_READ,
    Permission.MOCK_LOG_DELETE,
}

ALL_PERMISSIONS = {
    value
    for name, value in Permission.__dict__.items()
    if not name.startswith("__") and isinstance(value, str)
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": ADMIN_PERMISSIONS,
    "user": {
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
        Permission.IOT_READ,
        Permission.IOT_CREATE,
        Permission.IOT_UPDATE,
        Permission.IOT_DELETE,
        Permission.IOT_EXECUTE,
    },
}


def get_user_permissions(user: UserInfo) -> set[str]:
    permissions: set[str] = set()
    for role in user.roles:
        permissions.update(ROLE_PERMISSIONS.get(role, set()))
    return permissions


def require_permissions(*required: str, require_all: bool = True):
    required_permissions = set(required)

    async def dependency(user: UserInfo = Depends(get_current_user)) -> UserInfo:
        user_permissions = get_user_permissions(user)
        allowed = (
            required_permissions.issubset(user_permissions)
            if require_all
            else bool(required_permissions & user_permissions)
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied",
            )
        return user

    return dependency


def any_permission(permissions: Iterable[str]):
    return require_permissions(*permissions, require_all=False)


def require_method_permissions(read: Iterable[str], write: Iterable[str]):
    read_permissions = set(read)
    write_permissions = set(write)

    async def dependency(request: Request, user: UserInfo = Depends(get_current_user)) -> UserInfo:
        required_permissions = read_permissions if request.method in {"GET", "HEAD", "OPTIONS"} else write_permissions
        user_permissions = get_user_permissions(user)
        if required_permissions and not (required_permissions & user_permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied",
            )
        return user

    return dependency


def require_http_method_permissions(
    get: Iterable[str] = (),
    post: Iterable[str] = (),
    put: Iterable[str] = (),
    delete: Iterable[str] = (),
    patch: Iterable[str] = (),
):
    permissions_by_method = {
        "GET": set(get),
        "HEAD": set(get),
        "OPTIONS": set(get),
        "POST": set(post),
        "PUT": set(put),
        "DELETE": set(delete),
        "PATCH": set(patch),
    }

    async def dependency(request: Request, user: UserInfo = Depends(get_current_user)) -> UserInfo:
        required_permissions = permissions_by_method.get(request.method, set())
        user_permissions = get_user_permissions(user)
        if required_permissions and not (required_permissions & user_permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied",
            )
        return user

    return dependency
