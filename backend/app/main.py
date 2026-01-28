from fastapi import APIRouter
from app.routes import session, testcase, module

api_router = APIRouter()
api_router.include_router(session.router)
api_router.include_router(testcase.router)
api_router.include_router(module.router)
