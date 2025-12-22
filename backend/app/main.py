from fastapi import APIRouter
from backend.app.routes import session, testcase

api_router = APIRouter()
api_router.include_router(session.router)
api_router.include_router(testcase.router)
