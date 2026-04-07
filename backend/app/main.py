from fastapi import APIRouter
from app.routes.session import router as session_router
from app.routes.testcase import router as testcase_router
from app.routes.module import router as module_router
from app.routes.saved_request import router as saved_request_router
from app.routes.proxy import router as proxy_router
from app.routes.global_parameter import router as global_parameter_router
from app.routes.history_prompt import router as history_prompt_router
from app.routes.scheduled_task import router as scheduled_task_router
from app.routes.mock_config import router as mock_config_router
from app.routes.mock_server import router as mock_server_router

api_router = APIRouter()
api_router.include_router(session_router)
api_router.include_router(testcase_router)
api_router.include_router(module_router)
api_router.include_router(saved_request_router)
api_router.include_router(proxy_router)
api_router.include_router(global_parameter_router)
api_router.include_router(history_prompt_router)
api_router.include_router(scheduled_task_router)
api_router.include_router(mock_config_router)
api_router.include_router(mock_server_router)
