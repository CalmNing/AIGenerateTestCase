import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Body  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from pydantic import BaseModel

from app.main import api_router
from app.services.keycloak_sync import sync_keycloak_roles
from db.db import create_db_and_tables

# 创建FastAPI应用
app = FastAPI(
    title="测试用例生成工具API",
    description="测试用例生成工具的后端API",
    version="1.0.0"
)

# 配置CORS - 从环境变量读取允许的 origins
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8001").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    sync_keycloak_roles()
    from app.scheduler import scheduler, load_all_jobs
    scheduler.start()
    load_all_jobs()

app.include_router(api_router, prefix="/api")
