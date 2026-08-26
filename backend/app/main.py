from fastapi import APIRouter, Depends

from app.permissions import Permission, require_http_method_permissions, require_permissions
from app.routes.config import router as config_router
from app.routes.global_parameter import router as global_parameter_router
from app.routes.history_prompt import router as history_prompt_router
from app.routes.api_test_tool import router as api_test_tool_router
from app.routes.mcp import router as mcp_router
from app.routes.mock_config import router as mock_config_router
from app.routes.mock_log import router as mock_log_router
from app.routes.mock_proxy import router as mock_proxy_router
from app.routes.mock_proxy import public_router as mock_proxy_public_router
from app.routes.mock_server import router as mock_server_router
from app.routes.module import router as module_router
from app.routes.nav_link import router as nav_link_router
from app.routes.proxy import router as proxy_router
from app.routes.saved_request import router as saved_request_router
from app.routes.saved_request_folder import router as saved_request_folder_router
from app.routes.scheduled_task import router as scheduled_task_router
from app.routes.session import router as session_router
from app.routes.skills import router as skills_router
from app.routes.testcase import router as testcase_router
from app.routes.pipeline import router as pipeline_router
from app.routes.document import router as document_router
from app.routes.chat import router as chat_router

api_router = APIRouter()

# Authenticated business routes.
api_router.include_router(
    session_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.SESSION_READ],
                post=[Permission.SESSION_CREATE],
                put=[Permission.SESSION_UPDATE],
                delete=[Permission.SESSION_DELETE],
            )
        )
    ],
)
api_router.include_router(
    module_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.MODULE_READ],
                post=[Permission.MODULE_CREATE],
                put=[Permission.MODULE_UPDATE],
                delete=[Permission.MODULE_DELETE],
            )
        )
    ],
)
api_router.include_router(
    saved_request_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.IOT_READ],
                post=[Permission.IOT_CREATE],
                put=[Permission.IOT_UPDATE],
                delete=[Permission.IOT_DELETE],
            )
        )
    ],
)
api_router.include_router(
    saved_request_folder_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.IOT_READ],
                post=[Permission.IOT_CREATE],
                put=[Permission.IOT_UPDATE],
                delete=[Permission.IOT_DELETE],
            )
        )
    ],
)
api_router.include_router(proxy_router, dependencies=[Depends(require_permissions(Permission.IOT_EXECUTE))])
api_router.include_router(
    api_test_tool_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.IOT_READ],
                post=[Permission.IOT_CREATE, Permission.IOT_EXECUTE],
                put=[Permission.IOT_UPDATE],
                delete=[Permission.IOT_DELETE],
            )
        )
    ],
)
api_router.include_router(
    testcase_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.TESTCASE_READ],
                post=[Permission.TESTCASE_CREATE, Permission.TESTCASE_GENERATE],
                put=[Permission.TESTCASE_UPDATE],
                delete=[Permission.TESTCASE_DELETE],
            )
        )
    ],
)
api_router.include_router(
    history_prompt_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.TESTCASE_READ],
                post=[Permission.TESTCASE_CREATE, Permission.TESTCASE_GENERATE],
                delete=[Permission.TESTCASE_DELETE],
            )
        )
    ],
)
api_router.include_router(
    pipeline_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.TESTCASE_READ],
                post=[Permission.TESTCASE_CREATE, Permission.TESTCASE_GENERATE],
                put=[Permission.TESTCASE_UPDATE],
            )
        )
    ],
)
api_router.include_router(
    document_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                post=[Permission.TESTCASE_READ, Permission.TESTCASE_GENERATE],
            )
        )
    ],
)

# Admin-only management routes.
api_router.include_router(config_router, dependencies=[Depends(require_permissions(Permission.CONFIG_MANAGE))])
api_router.include_router(mcp_router, dependencies=[Depends(require_permissions(Permission.MCP_MANAGE))])
api_router.include_router(skills_router, dependencies=[Depends(require_permissions(Permission.SKILLS_MANAGE))])
api_router.include_router(
    global_parameter_router,
    dependencies=[Depends(require_permissions(Permission.GLOBAL_PARAMETER_MANAGE))],
)
api_router.include_router(
    scheduled_task_router,
    dependencies=[Depends(require_permissions(Permission.SCHEDULED_TASK_MANAGE))],
)
api_router.include_router(mock_config_router, dependencies=[Depends(require_permissions(Permission.MOCK_MANAGE))])
api_router.include_router(mock_proxy_router, dependencies=[Depends(require_permissions(Permission.MOCK_MANAGE))])
api_router.include_router(
    mock_log_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.MOCK_LOG_READ],
                delete=[Permission.MOCK_LOG_DELETE],
            )
        )
    ],
)

# Public runtime route: mock callbacks must be callable by external systems.
api_router.include_router(mock_server_router)

# Mock 代理 CA 证书下载：免认证（导入信任区前无法携带业务凭证）。
api_router.include_router(mock_proxy_public_router)

# 资源导航（接口文档 / 快捷工具 等共享书签）：读开放，写需 CONFIG_MANAGE。
api_router.include_router(
    nav_link_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[],
                post=[Permission.CONFIG_MANAGE],
                put=[Permission.CONFIG_MANAGE],
                delete=[Permission.CONFIG_MANAGE],
            )
        )
    ],
)

# Chat route: general-purpose AI conversation
api_router.include_router(chat_router)
