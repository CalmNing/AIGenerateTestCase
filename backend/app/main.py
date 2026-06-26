from fastapi import APIRouter, Depends

from app.permissions import Permission, require_http_method_permissions, require_permissions
from app.routes.config import router as config_router
from app.routes.global_parameter import router as global_parameter_router
from app.routes.history_prompt import router as history_prompt_router
from app.routes.api_test_tool import router as api_test_tool_router
from app.routes.mcp import router as mcp_router
from app.routes.mock_config import router as mock_config_router
from app.routes.mock_log import router as mock_log_router
from app.routes.mock_server import router as mock_server_router
from app.routes.module import router as module_router
from app.routes.proxy import router as proxy_router
from app.routes.saved_request import router as saved_request_router
from app.routes.scheduled_task import router as scheduled_task_router
from app.routes.session import router as session_router
from app.routes.skills import router as skills_router
from app.routes.testcase import router as testcase_router

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


# Public config lookup: bug link template is needed by all users viewing test cases.
@api_router.get("/config/bug-link-template")
async def get_bug_link_template():
    """获取 Bug 链接模板配置"""
    from config import config_manager
    from utils.base_response import Response
    template = config_manager.get("bug_link_template", "")
    return Response(data={"template": template})
