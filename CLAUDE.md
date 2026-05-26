# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指导。

## 项目概述

AI 驱动的测试用例生成与管理工具。使用 LangChain 代理配合 DeepSeek/Ollama 模型，从自然语言需求描述中生成软件测试用例。同时包含 IoT 数据推送平台、Mock API 服务器和定时任务管理平台。

## 开发命令

### 后端 (Python 3.11, FastAPI)
```bash
cd backend
python -m venv .venv && .venv/Scripts/activate        # Windows 创建/激活虚拟环境
pip install -r requirements.txt
$env:PYTHONPATH = "F:\VSCode\ai-generate-testcase\backend"  # 确保 import 正确
uvicorn main:app --reload --host 0.0.0.0 --port 8000   # 从 backend/ 目录运行
```
也可从项目根目录启动：`uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000`（无需额外设置 PYTHONPATH）。

### 前端 (React 18 + TypeScript, Vite)
```bash
cd frontend
npm install
npm run dev                                           # 端口 5173, 代理 /api 到 8000
npm run lint                                          # ESLint 检查 (--max-warnings 0)
npm run build                                         # tsc + vite build
npm run preview                                       # 预览 production build
```
TypeScript 严格模式已启用（`noUnusedLocals`, `noUnusedParameters`）。

### Docker 全栈部署
```bash
docker compose up                                     # 启动全部服务
docker compose up backend --build -d                  # 单独重建后端
docker compose up frontend --build -d                 # 单独重建前端
```
后端 Dockerfile 基于 `python:3.11-slim-bookworm`，使用 `uv` 安装 Python 依赖，并内嵌 Node.js 20.19.1（供 STDIO MCP 子进程使用）。Docker Compose 配置了 `npm_cache` 和 `uv_cache` 两个命名卷，分别挂载到 `/root/.npm` 和 `/root/.cache/uv`，避免容器重建后重复下载 npx/uv 包（`npx feishu-mcp` 首次下载约 19s）。前端 Dockerfile 多阶段构建：Node 18 编译后用 nginx:alpine 提供静态文件。

### 数据库迁移
```bash
cd backend
alembic upgrade head                                  # 执行迁移
alembic revision --autogenerate -m "描述"              # 创建新迁移
```

### 测试
后端暂无自动化测试。前端 lint 通过 `npm run lint` 检查。

## 架构

### 后端 (FastAPI + SQLModel + SQLite)

```
backend/
├── main.py                # FastAPI 应用入口，挂载 api_router 到 /api
├── app/
│   ├── main.py            # 注册所有路由模块，注入 Keycloak 认证依赖
│   ├── auth.py            # Keycloak JWT 验证
│   ├── deps.py            # FastAPI 依赖注入 (SessionDep, CurrentUser)
│   ├── scheduler.py       # APScheduler 定时任务引擎
│   ├── routes/            # 每个领域一个路由文件 (13 个)
│   │   ├── testcase.py    # 核心：AI 生成 + CRUD
│   │   ├── mcp.py         # MCP 工具列表探测（stdio & HTTP 传输）
│   │   ├── skills.py      # Skills Hub API（SKILL.md 解析与列表）
│   │   ├── mock_server.py # Mock API 服务器（免认证）
│   │   ├── proxy.py       # HTTP 代理 + 变量替换
│   │   ├── session.py, module.py, saved_request.py, ...
│   │   └── mock_log.py, mock_config.py, scheduled_task.py, ...
│   └── services/          # 业务逻辑层
├── db/
│   ├── models.py          # 10 个 SQLModel (Session, TestCase, Module, SavedRequest, GlobalParameter, HistoryPrompt, ScheduledTask, MockConfig, McpServer, MockLog)
│   └── db.py              # SQLite 引擎 + 自动迁移
├── skills/                # Skills Hub 技能文件（5 个 SKILL.md，YAML frontmatter + markdown）
├── utils/
│   ├── model_utils.py     # LangChain agent (DeepSeek/Ollama) + 结构化输出
│   ├── lanhu_mcp_adapter.py # MCP 客户端 (HTTP Streamable & STDIO)
│   └── base_response.py   # Response[T] 统一响应包装
└── config.py              # JSON 文件配置 (LLM API key, Ollama, MCP URL)
```

**认证:** Keycloak PKCE (OIDC)。`/api/mock/` 免认证，其余路由均需要 Bearer JWT。后端使用 JWKS 本地验证 JWT（`app/auth.py`），24 小时缓存公钥，不每次回调 Keycloak introspect 端点。JWT issuer 使用外部地址（`KEYCLOAK_EXTERNAL_URL`），JWKS 获取使用内部地址（`KEYCLOAK_SERVER_URL`），以适配 Docker 网络与浏览器访问的地址差异。

**AI 测试用例生成:** 前端发送需求文本 → `POST /api/testcases/generate` → LangChain agent 调用 LLM → 结构化输出 TestCase 列表。agent 可挂载 MCP 工具（蓝湖原型/飞书文档）获取上下文。

   **关键逻辑 - 蓝湖内容预取时跳过 agent：** 当需求文本中检测到蓝湖 URL 且成功预取页面内容时，不走 LangChain ReAct agent（避免 40+ 次无用迭代），而是直接调用 `model.with_structured_output()` 一次返回结构化结果。判断逻辑在 `model_utils._check_lanhu_document_size()` 中：先匹配页面名称（已排除 URL 自身文本避免误匹配），再检查文档大小是否超过阈值。

   **LangGraph config 注意事项：** `recursion_limit` 必须放在 config 顶层，不能放在 `configurable` 内。LangGraph 只从 `config["recursion_limit"]` 读取该值。

**MCP 集成:** 支持两种传输方式：
- HTTP (Streamable HTTP): MCPClient 通过 POST + SSE 通信
- STDIO: 启动子进程通过 stdin/stdout JSON-RPC 通信，自动检测 HTTP 端口宣告

**Per-tool 启用/禁用：** `McpServer` 模型和 `McpServerConfig` 类均有 `enabled_tools: Optional[list[str]]` 字段。前端 McpConfigModal 中可获取方法列表后通过 Switch 开关控制。`lanhu_mcp_adapter.build_tools_from_configs()` 中根据此列表过滤，禁用的工具不会注入到模型中。

**定时任务:** APScheduler AsyncIOScheduler，支持 cron 和间隔调度。任务执行时按顺序调用保存的 HTTP 请求，支持变量替换和后置提取。

### 前端 (React 18 + Ant Design)

```
frontend/src/
├── App.tsx                # 单体组件（62K+ 行），包含几乎所有 UI 逻辑
├── AppWithAuth.tsx        # Keycloak PKCE 初始化 + token 刷新
├── types/index.ts         # TypeScript 接口与枚举
├── services/
│   ├── api.ts             # Axios 实例 + Bearer token 拦截器 + 401 处理
│   └── keycloak.ts        # Keycloak JS 适配器初始化
├── components/
│   ├── modals/            # 10 个 CRUD 模态框 (McpConfigModal, SettingsModal 等)
│   ├── TestCaseGenerator.tsx, TestCaseManager.tsx, TestCaseTable.tsx
│   ├── IoTDataPushPlatform.tsx, IoTMockPlatform.tsx
│   ├── ScheduledTaskManager.tsx
│   ├── ModuleSidebar.tsx, SessionSidebar.tsx, HistoryPromptSidebar.tsx
│   └── HeaderComponent.tsx, HomePage.tsx
```

**平台切换:** `currentPlatform` 状态变量控制视图切换：`home`, `ai-testcase`, `iot-mock`, `scheduled-task`, `mock-api`。

**Keycloak 配置:** `frontend/.env` 中设置 `VITE_KEYCLOAK_URL`、`VITE_KEYCLOAK_REALM`、`VITE_KEYCLOAK_CLIENT_ID`。

**状态管理:** 主要依赖 React 本地 state，部分全局状态使用 zustand。

### 基础设施

```
docker-compose.yml
├── backend      (8000)  FastAPI + SQLite
├── frontend     (8001)  Vite + React
├── postgres     (5432)  Keycloak 数据库
├── keycloak     (8090)  OIDC 认证
└── lanhu-mcp    (8002)  蓝湖 MCP 服务
```

**Docker Compose 环境变量（backend 服务）：**
- `KEYCLOAK_SERVER_URL` — Keycloak 内部地址（容器间通信，如 `http://keycloak:8080`），用于 JWKS 获取
- `KEYCLOAK_EXTERNAL_URL` — Keycloak 外部地址（浏览器访问，如 `http://localhost:8090`），用于 JWT issuer 验证
- `CORS_ORIGINS` — 允许的跨域来源，逗号分隔

**后端配置管理：** `backend/config.py` 中的 `ConfigManager` 类读取 `data/config.json`，合并默认值后提供全局 `config` 对象。配置项包括模型类型（`api`/`ollama`）、API key、Ollama 地址、MCP 开关等。

### 关键约定

- 后端模型使用上海时区 (UTC+8) 的 `created_at`/`updated_at`
- JSON 字段以字符串形式存储在 SQLite 中，在路由中解析
- 模块层级使用自引用的 `parent_id`
- TestCase 状态枚举：NOT_RUN, PASSED, FAILED
- 用例等级：1-4 (功能、边界、异常、场景)
- 前端 Vite 代理：`/api` → `http://127.0.0.1:8000`
- 所有 API 响应格式：`{code: number, message: string, data: T}` — 错误描述文字放在 `message` 字段，`data` 不放人类可读的错误信息

### MCP 配置存储与执行

MCP 配置同时支持**服务端持久化**和**浏览器 localStorage** 两层存储：

- **持久化模型:** `db.models.McpServer` — 存储在 SQLite `mcpserver` 表中，按 `user_id` 隔离
- **API 路由:** `backend/app/routes/mcp.py` 中 `GET/POST/PUT/DELETE /mcp/servers` CRUD 端点
- **前端加载顺序:** 打开 McpConfigModal 时，优先从服务端加载；服务端不可用或为空时回退到 localStorage
- **保存逻辑:** McpConfigModal 保存时同时写入服务端和 localStorage（localStorage 仅作为界面回退）
- **生成流程:** 后端只读取当前用户服务端已保存且启用的 MCP 配置；客户端传入的 `mcp_servers` 字段会被忽略，避免绕过 `/api/mcp` 权限控制
- **执行方式:**
  - HTTP 类型: 后端短连接到外部 URL，用完即断
  - STDIO 类型: 后端用 `asyncio.create_subprocess_exec` 在服务器上**临时启动**子进程（如 `npx ...`），测试用例生成完成后通过 `_safe_kill()` 杀死进程
- 后端 `config.py` 中另有静态配置（`mcp_server_url`/`mcp_server_url_fallback`），用于默认的蓝湖 MCP 连接，与前端 dynamic MCP 配置独立

### Skills Hub

5 个测试方法论技能以 `SKILL.md` 文件形式存储在 `backend/skills/` 目录下，采用 YAML frontmatter + markdown body 格式：

| 技能 | 说明 |
|------|------|
| `testcase-design` | 核心三步工作流：需求分析 → 四级用例设计 → 质量审查 |
| `boundary-testing` | 边界值分析（数值、字符串、集合、日期） |
| `exception-testing` | 异常测试（错误推测法：输入/环境/状态异常） |
| `scenario-testing` | 端到端场景和工作流测试 |
| `coverage-analysis` | 覆盖度分析，识别缺口避免重复用例 |

**Skills ≠ MCP Tools**：MCP Tools 给 agent 函数调用能力，Skills 给 agent **方法论指导**——选中的 SKILL.md body 在生成时注入到提示词中。

**API：** `GET /api/skills` 返回所有技能列表（名称、描述、body_preview），`GET /api/skills/{name}` 返回单个技能完整内容。解析结果在内存中缓存。

**前端：** Header 中 "Skills" 按钮 → `SkillsHubModal.tsx` → 从后端获取技能列表 → Switch 开关启用/禁用 → 选中状态存入 localStorage

**生成流程：** 生成时将选中的技能名称通过 FormData `selected_skills` 字段发送到后端 → `model_utils._load_skill_bodies()` 加载 SKILL.md body → 拼接到用户需求文本前（`## 激活的测试方法论技能\n\n[技能内容]\n\n## 用户需求\n\n[原始需求]`），适用于 API 和 Ollama 两种模型类型。

### 配置文件总览

| 文件 | 用途 |
|------|------|
| `backend/data/config.json` | LLM 配置（模型类型、API key、Ollama URL、MCP URL） |
| `backend/data/testcases.db` | SQLite 数据库文件 |
| `backend/skills/*/SKILL.md` | 测试方法论技能文件（5 个） |
| `backend/.dockerignore` | 后端 Docker 构建忽略规则 |
| `frontend/.env` | Keycloak 地址和 realm（Vite 编译时注入） |
| `.mcp.json` | 本地 MCP 服务器配置（Claude Code IDE 集成用），目前指向蓝湖 MCP |
| `keycloak/realm-config.json` | Keycloak realm 初始化配置（首次导入） |
