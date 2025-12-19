# Testcase Generator — repo notes

快速说明

- 后端：FastAPI（`backend/main.py`）。启动：

```bash
uvicorn backend.main:app --reload
```

- 前端：React + Vite（`frontend/`）。启动：

```bash
cd frontend
pnpm dev
```



关键约定

- API 返回格式：`{ code: number, message: string, data: any }`（参见 `backend/main.py` 与 `frontend/src/services/api.ts`）。
- 数据库：SQLite 文件位于 `backend/data/testcases.db`。当前实现为每个 session 创建独立表 `testcases_<session_id>`（见 `backend/db/db_manager.py`）。
- 模型：LangChain agent 在 `backend/utils/model_utils.py`，支持 `api`（DeepSeek）和 `ollama` 两种模式。



