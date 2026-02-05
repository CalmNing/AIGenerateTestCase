from fastapi import APIRouter
from app.routes.session import router as session_router
from app.routes.testcase import router as testcase_router
from app.routes.module import router as module_router
from app.routes.saved_request import router as saved_request_router
from app.routes.proxy import router as proxy_router

api_router = APIRouter()
api_router.include_router(session_router)
api_router.include_router(testcase_router)
api_router.include_router(module_router)
api_router.include_router(saved_request_router)
api_router.include_router(proxy_router)
