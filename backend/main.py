from fastapi import FastAPI, Body  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from pydantic import BaseModel

from backend.app.main import api_router
from backend.db.db import create_db_and_tables

# 创建FastAPI应用
app = FastAPI(
    title="测试用例生成工具API",
    description="测试用例生成工具的后端API",
    version="1.0.0"
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 在生产环境中应该配置具体的前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

app.include_router(api_router, prefix="/api")
