from fastapi import APIRouter, Depends
from app.routes.session import router as session_router
from app.routes.testcase import router as testcase_router
from app.routes.module import router as module_router
from app.routes.saved_request import router as saved_request_router
from app.routes.proxy import router as proxy_router
from app.routes.global_parameter import router as global_parameter_router
from app.routes.history_prompt import router as history_prompt_router
from app.routes.scheduled_task import router as scheduled_task_router
from app.routes.mock_config import router as mock_config_router
from app.routes.mock_log import router as mock_log_router
from app.routes.mock_server import router as mock_server_router
from app.routes.mcp import router as mcp_router
from app.routes.skills import router as skills_router
from app.routes.config import router as config_router
from app.auth import get_current_user

api_router = APIRouter()

# 需要鉴权的路由：注入 get_current_user 依赖
api_router.include_router(session_router, dependencies=[Depends(get_current_user)])
api_router.include_router(testcase_router, dependencies=[Depends(get_current_user)])
api_router.include_router(module_router, dependencies=[Depends(get_current_user)])
api_router.include_router(saved_request_router, dependencies=[Depends(get_current_user)])
api_router.include_router(proxy_router, dependencies=[Depends(get_current_user)])
api_router.include_router(global_parameter_router, dependencies=[Depends(get_current_user)])
api_router.include_router(history_prompt_router, dependencies=[Depends(get_current_user)])
api_router.include_router(scheduled_task_router, dependencies=[Depends(get_current_user)])
api_router.include_router(mock_config_router, dependencies=[Depends(get_current_user)])
api_router.include_router(mock_log_router, dependencies=[Depends(get_current_user)])
api_router.include_router(mcp_router, dependencies=[Depends(get_current_user)])

# Config — 需要认证
api_router.include_router(config_router, dependencies=[Depends(get_current_user)])

# Skills Hub — 需要认证
api_router.include_router(skills_router, dependencies=[Depends(get_current_user)])

# 免鉴权路由：Mock 运行服务需要外部可访问
api_router.include_router(mock_server_router)
