from fastapi import APIRouter, Depends

from app.permissions import Permission, require_http_method_permissions, require_permissions
from app.routes.config import router as config_router
from app.routes.global_parameter import router as global_parameter_router
from app.routes.api_test_tool import router as api_test_tool_router
from app.routes.mcp import router as mcp_router
from app.routes.mock_config import router as mock_config_router
from app.routes.mock_log import router as mock_log_router
from app.routes.mock_proxy import router as mock_proxy_router
from app.routes.mock_proxy import public_router as mock_proxy_public_router
from app.routes.mock_server import router as mock_server_router
from app.routes.module import router as module_router
from app.routes.mqtt import router as mqtt_router
from app.routes.nav_link import router as nav_link_router
from app.routes.proxy import router as proxy_router
from app.routes.push_log import router as push_log_router
from app.routes.saved_request import router as saved_request_router
from app.routes.saved_request_folder import router as saved_request_folder_router
from app.routes.scheduled_task import router as scheduled_task_router
from app.routes.session import router as session_router
from app.routes.skills import router as skills_router
from app.routes.rag import router as rag_router
from app.routes.testcase import router as testcase_router
from app.routes.pipeline import router as pipeline_router
from app.routes.document import router as document_router
from app.routes.requirement_doc import router as requirement_doc_router
from app.routes.chat import router as chat_router
from app.routes.chat_history import router as chat_history_router
from app.routes.ui_execution import router as ui_execution_router
from app.routes.ui_automation import router as ui_automation_router
from app.routes.websocket import router as ws_router

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
# WebSocket 订阅：连接/测试连接属于 iot:execute，读日志/删除沿用 iot:read / iot:delete。
api_router.include_router(
    ws_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.IOT_READ],
                post=[Permission.IOT_EXECUTE],
                delete=[Permission.IOT_DELETE],
            )
        )
    ],
)
# MQTT 发布：发布/测试连接属于 iot:execute，读日志/删除沿用 iot:read / iot:delete。
api_router.include_router(
    mqtt_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.IOT_READ],
                post=[Permission.IOT_EXECUTE],
                delete=[Permission.IOT_DELETE],
            )
        )
    ],
)
api_router.include_router(
    push_log_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                get=[Permission.IOT_READ],
                delete=[Permission.IOT_DELETE],
            )
        )
    ],
)
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

# 需求管理：飞书文档接入、结构化解析、变更历史与测试用例联动。
# 读 = testcase:read；写 = testcase:create/generate/update；删 = testcase:delete。
api_router.include_router(
    requirement_doc_router,
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

# Admin-only management routes.
api_router.include_router(config_router, dependencies=[Depends(require_permissions(Permission.CONFIG_MANAGE))])
api_router.include_router(mcp_router, dependencies=[Depends(require_permissions(Permission.MCP_MANAGE))])
api_router.include_router(skills_router, dependencies=[Depends(require_permissions(Permission.SKILLS_MANAGE))])
# RAG 检索观测/管理（索引统计、召回试查、同义词、重建）
api_router.include_router(rag_router, dependencies=[Depends(require_permissions(Permission.SKILLS_MANAGE))])
api_router.include_router(
    global_parameter_router,
    dependencies=[
        Depends(
            require_http_method_permissions(
                # 读环境参数:任何登录用户(展示时解析 {{web_url}} 等占位符需要)
                get=[],
                post=[Permission.GLOBAL_PARAMETER_MANAGE],
                put=[Permission.GLOBAL_PARAMETER_MANAGE],
                delete=[Permission.GLOBAL_PARAMETER_MANAGE],
            )
        )
    ],
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

# UI 自动化执行:启动/状态/记录,需 ui_execute:manage 权限。
api_router.include_router(
    ui_execution_router,
    dependencies=[Depends(require_permissions(Permission.UI_EXECUTE))],
)

# UI 自动化工具:页面元素抽取 / PageObject 管理 / 定位器验证 / 自愈执行。
api_router.include_router(
    ui_automation_router,
    dependencies=[Depends(require_permissions(Permission.UI_AUTOMATION))],
)

# Chat route: general-purpose AI conversation
api_router.include_router(chat_router)

# Chat 会话持久化：存储/续聊 AI 对话
api_router.include_router(chat_history_router)
