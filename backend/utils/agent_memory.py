"""Agent 记忆接线：LangGraph checkpointer（短期记忆）+ Store（长期记忆）。

背景（调研结论 P1）：
- `utils/model_utils.py` 里早已实例化 `checkpointer = InMemorySaver()`，但从未传进
  `create_agent`，等于死代码；
- `thread_id` 每次 `uuid4()` 新建，即使挂上 checkpointer 也读不到历史。

本模块统一提供：
1. `build_thread_id(...)`：把「用户 / 会话 / 模块」拼成**稳定**的 thread_id，
   同一模块的多次生成共享一条轨迹（可续跑、可追溯）。
2. `agent_memory_context()`：异步上下文管理器，产出 `(checkpointer, store)`；
   生产用 SQLite 落盘（进程重启不丢），测试/异常时降级为内存实现，
   **任何失败都不得影响生成主流程**。

设计约束（沿用仓库既有约定）：
- 记忆只是"增强"，失败一律降级放行（logger.warning，不抛异常）；
- 不触碰任何业务落库逻辑（TestCase/TestPoint 等仍是唯一事实来源）。

存储位置：默认 `backend/data/agent_memory.db`（与 testcases.db 同目录），
可用配置项 `agent_memory_enabled=false` 完全关闭，或用
`agent_memory_db_path` 指定路径。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional, Tuple

logger = logging.getLogger(__name__)

# 记忆库默认路径（与 SQLite 业务库同目录，避免容器内额外挂载）
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "agent_memory.db"
)

# 内存降级实现（进程内有效，重启即丢；用于测试或落盘不可用时）
_memory_checkpointer: Any = None
_memory_store: Any = None


def sanitize_thread_part(value: Any) -> str:
    """把任意标识规范成 thread_id 的安全片段（避免 '/'、空格等破坏键结构）。"""
    text = "" if value is None else str(value)
    text = text.strip()
    if not text:
        return "none"
    return "".join(ch if (ch.isalnum() or ch in "-_:.") else "_" for ch in text)


def build_thread_id(
    *,
    scope: str,
    user_id: Any = None,
    session_id: Any = None,
    module_id: Any = None,
) -> str:
    """构造**稳定**的 thread_id。

    与旧实现（`f"{session_id}-{uuid4()}"`）的差异：
    - 旧：每次调用都是新 thread → checkpointer 永远读不到历史（等于没用）；
    - 新：同一 (scope, user, session, module) 组合恒等 → 同模块多次生成共享轨迹。

    参数：
        scope: 业务域标签，例如 "testcase" / "chat" / "pipeline"。
        user_id: Keycloak sub；匿名/无权限时为 None。
        session_id: 会话 ID。
        module_id: 模块 ID；未选模块时传 None（会被规范成 "none"）。
    """
    return ":".join(
        [
            sanitize_thread_part(scope),
            sanitize_thread_part(user_id),
            sanitize_thread_part(session_id),
            sanitize_thread_part(module_id),
        ]
    )


def _db_path() -> Optional[str]:
    """解析记忆库路径；返回 None 表示"不启用落盘"（降级为内存）。"""
    try:
        from config import config_manager

        enabled = config_manager.get_agent_memory_enabled()
    except Exception:  # 配置不可用（如测试环境未初始化）时按默认启用
        enabled = True
    if not enabled:
        return None

    try:
        configured = config_manager.get_agent_memory_db_path()
    except Exception:
        configured = None
    path = configured or _DEFAULT_DB_PATH
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    except Exception as exc:  # 目录不可写 → 降级内存
        logger.warning("创建 Agent 记忆目录失败，降级为内存记忆: %s", exc)
        return None
    return path


class NullCheckpointer:
    """最终降级：不存任何检查点（`create_agent` 仍可正常运行）。"""

    def get_tuple(self, config):
        return None

    async def aget_tuple(self, config):
        return None

    def list(self, config, *, limit=None, before=None):  # noqa: A003
        return iter(())

    async def alist(self, config, *, limit=None, before=None):
        return
        yield  # pragma: no cover - 使其成为异步生成器

    def put(self, config, checkpoint, metadata, new_versions):
        return config

    async def aput(self, config, checkpoint, metadata, new_versions):
        return config

    def put_writes(self, config, writes, task_id, task_path=""):
        return None

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return None

    def delete_thread(self, thread_id):
        return None

    async def adelete_thread(self, thread_id):
        return None


def _memory_fallbacks() -> Tuple[Any, Any]:
    """惰性构造内存降级实现（导入失败也不抛）。"""
    global _memory_checkpointer, _memory_store
    if _memory_checkpointer is None:
        try:
            from langgraph.checkpoint.memory import InMemorySaver

            _memory_checkpointer = InMemorySaver()
        except Exception as exc:  # pragma: no cover - 极端降级路径
            logger.warning("InMemorySaver 不可用: %s", exc)
            _memory_checkpointer = NullCheckpointer()
    if _memory_store is None:
        try:
            from langgraph.store.memory import InMemoryStore

            _memory_store = InMemoryStore()
        except Exception as exc:  # pragma: no cover - 极端降级路径
            logger.warning("InMemoryStore 不可用: %s", exc)
            _memory_store = None
    return _memory_checkpointer, _memory_store


@asynccontextmanager
async def agent_memory_context() -> AsyncIterator[Tuple[Any, Any]]:
    """异步上下文管理器，产出 `(checkpointer, store)`。

    优先落盘（SQLite），任何异常都降级到内存实现，保证调用方拿到可用对象：

        async with agent_memory_context() as (checkpointer, store):
            agent = create_agent(..., checkpointer=checkpointer, store=store)
            await agent.ainvoke(input, config={"configurable": {"thread_id": tid}})

    注意：AsyncSqliteSaver / AsyncSqliteStore 的生命周期与上下文绑定，
    因此必须在 `async with` 内部完成 agent 调用（生成链路本身就是 async，无额外成本）。
    """
    path = _db_path()
    if path:
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.store.sqlite.aio import AsyncSqliteStore

            async with AsyncSqliteSaver.from_conn_string(path) as checkpointer:
                async with AsyncSqliteStore.from_conn_string(path) as store:
                    logger.debug("Agent 记忆已启用（SQLite: %s）", path)
                    yield checkpointer, store
                    return
        except Exception as exc:
            logger.warning("SQLite 记忆初始化失败，降级为内存记忆: %s", exc)

    checkpointer, store = _memory_fallbacks()
    logger.debug("Agent 记忆使用内存降级实现")
    yield checkpointer, store


def reset_memory_singletons() -> None:
    """仅测试用：清空内存降级单例。"""
    global _memory_checkpointer, _memory_store
    _memory_checkpointer = None
    _memory_store = None
