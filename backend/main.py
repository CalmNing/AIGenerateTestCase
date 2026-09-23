import os
import logging
from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.main import api_router
from app.services.keycloak_sync import sync_keycloak_roles
from config import config_manager
from db.db import create_db_and_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建表/迁移、同步 Keycloak 角色、启动定时任务、恢复 Mock 代理；关闭时逐一停止。"""
    create_db_and_tables()
    # 服务重启会丢失内存任务:把中断的 UI 自动化运行标记为 ERROR,避免前端永久轮询
    try:
        from app.services.page_object_runner import fail_orphan_runs
        failed = fail_orphan_runs()
        if failed:
            logger.info("已将 %s 条中断的 UI 自动化运行标记为 ERROR", failed)
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响启动
        logger.warning("清理中断的 UI 自动化运行失败(不影响主服务): %s", exc)
    sync_keycloak_roles()
    from app.scheduler import scheduler, load_all_jobs
    scheduler.start()
    load_all_jobs()

    # 恢复 Mock 正向代理（上次运行时开启则自动重启；端口占用等失败仅告警）
    try:
        if config_manager.is_mock_proxy_enabled():
            from app.services import mock_proxy
            await mock_proxy.start_proxy(config_manager.get_mock_proxy_port())
            logger.info("Mock 代理已按上次状态恢复运行，端口 %s", config_manager.get_mock_proxy_port())
    except Exception as exc:
        logger.warning("Mock 代理自动恢复失败（不影响主服务）: %s", exc)

    yield

    try:
        from app.services import mock_proxy
        await mock_proxy.stop_proxy()
    except Exception:
        pass
    scheduler.shutdown()


logger = logging.getLogger(__name__)


# 创建FastAPI应用
app = FastAPI(
    title="测试用例生成工具API",
    description="测试用例生成工具的后端API",
    version="1.0.0",
    lifespan=lifespan,
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

app.include_router(api_router, prefix="/api")
