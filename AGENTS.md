# AGENTS.md

AI 测试用例生成与管理平台。后端 FastAPI + SQLModel + SQLite，前端 React 18 + Ant Design + Vite。

## 开发者命令

### 后端 (Python 3.11, FastAPI)
```bash
cd backend
.venv/Scripts/activate
pip install -r requirements.txt
$env:PYTHONPATH = "F:\VSCode\ai-generate-testcase\backend"
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
也可从根目录: `uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000`

### 前端 (React 18 + TypeScript, Vite)
```bash
cd frontend
npm install
npm run dev          # 端口 5173, Vite 代理 /api → localhost:8000
npm run lint         # ESLint (--max-warnings 0)
npm run build        # tsc + vite build
```

### Docker 全栈
```bash
docker compose up                                    # 启动全部服务
docker compose up backend --build -d                 # 单独重建后端
docker compose up frontend --build -d                # 单独重建前端
```

## 测试

```bash
# 后端（从 backend/ 目录）
pytest
# 或: .venv\Scripts\python.exe -m pytest
```

**重要**: `pytest.ini` 限制 `testpaths = tests`。不要在 tests/ 目录外执行 `pytest`，否则会因为生产类名为 `TestCase`/`TestPoint` 而被 pytest 收集为测试类导致失败。所有测试使用 in-memory SQLite（conftest.py），不调真实 LLM 或网络。

### Alembic 数据库迁移
```bash
cd backend
alembic upgrade head
alembic revision --autogenerate -m "描述"
```

## 架构要点

### 两条测试用例生成路径

| 路径 | 入口 | 方式 |
|------|------|------|
| **单次 Agent** | `POST /api/testcases/generate` | 一次 LLM 调用产生全部用例（`utils/model_utils.py`） |
| **4 阶段 Pipeline** | `PipelineRunner.run()` | 异步后台任务，4 轮 LLM 调用：需求拆分→测试点→用例设计→优化（`app/pipeline/`） |

文档预取（飞书/蓝湖）时跳过 agent ReAct 循环，直接 `model.with_structured_output()`。

### 认证 (Keycloak PKCE)

- `/api/mock/` 免认证，其余路由均需 Bearer JWT
- 后端本地验证 JWKS（24h 缓存），不回调 Keycloak introspect
- 两个不同的 Keycloak URL：
  - `KEYCLOAK_SERVER_URL` — 内部地址（容器间通信，如 `http://keycloak:8080`），用于获取 JWKS
  - `KEYCLOAK_EXTERNAL_URL` — 外部地址（浏览器访问，如 `http://localhost:8090`），用于验证 JWT issuer
- 权限按 HTTP 方法细分（`require_http_method_permissions`），如 testcase:read / testcase:create

### MCP 配置

- 后端读取**服务端已保存且启用**的全部 MCP 配置（数据全局共享，不按用户隔离）
- 客户端传入的 `mcp_servers` 字段**会被忽略**（避免绕过 `/api/mcp` 权限控制）
- 支持 HTTP（短连接到外部 URL）和 STDIO（临时子进程，用后 `_safe_kill()`）

### 数据库

- SQLite 文件: `backend/data/testcases.db`
- **启动时自动迁移**（`db/db.py._migrate_missing_columns()`）：SQLModel 有但表缺少的列会被自动 `ALTER TABLE ADD COLUMN`
- JSON 字段存为 SQLite 文本字符串，在路由层解析
- 所有模型使用 UTC+8 的 `created_at`/`updated_at`

### API 规范

- 前缀: `/api`
- 响应格式: `{code: number, message: string, data: T}` — 错误描述在 `message`，不在 `data`

## 关键约定

- 测试用例状态: `NOT_RUN` | `PASSED` | `FAILED`
- 用例等级: 1=功能, 2=边界, 3=异常, 4=场景
- 模块层级: 自引用 `parent_id`
- DeepSeek API 调用必须显式设置 `max_tokens`（如 `max_tokens=16384`），服务端默认 8192 可能导致截断
- LangGraph 的 `recursion_limit` 必须放在 config **顶层**，不能放 `configurable` 内
- Prompt caching 自动开启（DeepSeek 检测），通过 `prompt_cache_enabled` 配置强制开关
- 生成时不加注释到代码中
