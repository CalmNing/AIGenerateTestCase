# AI 测试用例生成与 API 测试平台

一站式测试工具平台，集成 **AI 驱动测试用例生成**、**接口场景测试**、**HTTP 请求调试**、**Mock 接口服务**和**定时任务调度**五大能力。

## 目录

- [平台概览](#平台概览)
- [快速开始](#快速开始)
- [平台功能详解](#平台功能详解)
  - [AI 测试用例平台](#1-ai-测试用例平台)
  - [接口场景测试工具](#2-接口场景测试工具)
  - [IoT 数据推送平台](#3-iot-数据推送平台)
  - [Mock 接口平台](#4-mock-接口平台)
  - [定时任务平台](#5-定时任务平台)
- [技术架构](#技术架构)
- [环境变量与部署](#环境变量与部署)
- [配置文件提交规则](#配置文件提交规则)

---

## 平台概览

| 平台 | 说明 |
|---|---|
| **AI 测试用例平台** | 使用 LLM (DeepSeek/Ollama) 从需求描述自动生成测试用例，4 阶段深度管线 + 接口关联 |
| **接口场景测试工具** | OpenAPI/Swagger 导入、接口执行、场景编排、断言验证、AI 智能匹配 |
| **IoT 数据推送平台** | HTTP / MQTT / WebSocket 请求调试工具，多 Tab 编辑、变量替换、后置提取、全局参数管理 |
| **Mock 接口平台** | 可配置的 HTTP Mock 服务，支持参数化响应和分页模拟 |
| **定时任务平台** | 基于 APScheduler 的定时任务引擎，支持间隔/Cron 调度、多请求串行执行 |

---

## 快速开始

### 前置条件

- Python 3.11+
- Node.js 18+
- Docker & Docker Compose（可选，用于一键部署）

### 本地开发

#### 后端启动

```bash
cd backend
python -m venv .venv

# Linux / macOS
source .venv/bin/activate
# Windows
.venv/Scripts/activate

pip install -r requirements.txt

# 启动开发服务器（从 backend/ 目录）
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 或从项目根目录启动
cd ..
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

#### 前端启动

```bash
cd frontend
npm install
npm run dev    # 默认端口 5173，代理 /api 到 8000
```

前端进程会代理 `/api` 请求到后端，开发时无需单独配置跨域。

### Docker Compose 部署（推荐）

```bash
# 首次部署
copy .env.example .env        # Windows
# cp .env.example .env        # Linux/macOS

# 按需要修改 .env 中的配置

docker compose up -d --build
```

部署后访问：

- 前端：`http://localhost:8001`
- 后端 API：`http://localhost:8000/api`
- Keycloak 管理后台：`http://localhost:8090`（admin / admin）

---

## 平台功能详解

### 1. AI 测试用例平台

核心能力：从自然语言需求描述中自动生成结构化测试用例。

#### 4 阶段深度生成管线

| 阶段 | 产物 | 说明 |
|---|---|---|
| 阶段 1 — 需求分析 | 需求点 | 解析需求文本，提取结构化需求点 |
| 阶段 2 — 测试点生成 | 测试点 | 对每个需求点生成测试点（含测试类别、设计方法） |
| 阶段 3 — 用例设计 | 测试用例 | 生成详细测试用例（步骤、断言、预期结果） |
| 阶段 4 — 合并去重 | 优化用例 | 去重、合并、检查一致性 |

支持两种生成模式：

- **功能测试模式**：纯需求文本生成
- **接口测试模式**：关联 OpenAPI 端点，自动推断接口依赖，前置步骤注入

#### 核心特性

- **需求输入**：文本框输入 + 文件上传（`.docx` / `.pdf` / `.txt` / `.md`）
- **API 关联**：选择 API 项目 + 多选接口，支持智能匹配和参数覆盖
- **技能选择**：将测试方法论注入 AI 提示词（通过 Skills Hub）
- **用例结构管理**：三级结构（需求点 → 测试点 → 用例），支持分页、搜索、重生成
- **用例管理**：表格视图、筛选统计、导出 Excel、批量操作（删除/移动）
- **准确性标签**：一致性检查、置信度评分、验证状态
- **执行验证**：API 执行 + 断言引擎验证
- **依赖推断**：自动分析接口间调用依赖关系

#### 支持的操作

- 会话管理（CRUD、选择、切换）
- 模块管理（树形结构，无限层级）
- 需求点/测试点/用例的新增、编辑、删除、重生成
- 测试用例的执行、验证、批量移动
- 历史需求记录管理

---

### 2. 接口场景测试工具

完整的 API 测试生命周期管理工具。

#### 项目管理

- 创建/编辑/删除 API 项目
- **OpenAPI/Swagger 导入**：支持 URL 和文件上传，自动解析 YAML/JSON
- Swagger 同步更新

#### 接口管理

- 接口 CRUD，支持搜索和分组
- Headers / Parameters / Request Body 配置
- **前置动作**：在请求前执行的脚本或 API 调用
- **后置提取**：JSONPath 从响应中提取变量
- **断言配置**：支持 7 种断言类型
  - 状态码 / 状态码范围 / 响应时间
  - JSONPath 存在 / 等于 / 不等于
  - 变量替换断言

#### 场景编排

- 创建场景，从端点列表拖拽添加步骤
- 步骤排序、启用/禁用
- 设置"失败继续"选项
- **AI 生成场景测试**：自动生成多步骤场景

#### 执行能力

- 单接口执行，实时查看请求/响应详情
- 单场景运行 / 批量运行
- 执行结果详情展示
- AI 生成请求 Body

---

### 3. IoT 数据推送平台

HTTP / MQTT / WebSocket 三协议请求调试与推送平台。

#### 特性

- **多 Tab 编辑**：同时编辑多个请求，每个 Tab 独立选择协议（HTTP / MQTT / WebSocket）
- **三栏可拖拽布局**：请求配置 / 请求体(Payload)或消息流 / 已保存请求
- **变量语法**：`{{variable}}` 引用环境变量（HTTP 的 URL/头/参数/体、MQTT 的 Broker/Topic/Payload、WS 的 URL/请求头均支持）
- **参数类型**：文本 / 文件上传
- **请求头编辑** + 请求体 JSON 编辑器（CodeMirror）
- **响应展示**：状态码、耗时、JSON 高亮渲染；MQTT 展示发布结果（成功/失败、耗时、Broker/Topic/QoS/Retain）；WebSocket 展示消息流与连接结果
- **请求记录**：持久化每次推送的原始请求与变量替换后的解析结果，支持协议筛选、查看详情并一键复制为 cURL / mosquitto_pub / websocat 分享

#### MQTT 协议

- **Broker 地址**：支持 `mqtt://` `tcp://` `mqtts://` `ssl://` `ws://` `wss://` 与裸 `host` / `host:port`（默认端口 1883 / 8883 / 8083 / 8084）
- **发布参数**：Topic、Payload、QoS（0/1/2）、Retain、Client ID、Username / Password、TLS
- **测试连接**：发布前可先探测 Broker 连通性
- **接口**：`POST /api/mqtt/publish`、`POST /api/mqtt/test-connection`，权限 `iot:execute`
- **依赖**：`paho-mqtt>=2.0`

#### WebSocket 协议

- **地址**：`ws://` / `wss://`，允许带 query（如 `ws://host:9090/api/logs?id=xxx&token=xxx`）；也接受 `http(s)://` 自动映射与裸 `host:port`
- **连接参数**：最大消息数（1~500）、空闲超时秒数（1~60）、子协议、连接后首发消息、额外请求头
- **消息流**：按序号展示收到的文本/二进制消息（相对时间、大小、内容），二进制消息提供 base64 折叠
- **测试连接**：仅完成握手，不拉取消息
- **接口**：`POST /api/ws/connect`、`POST /api/ws/test-connection`，权限 `iot:execute`
- **依赖**：`websockets>=12`

#### 后置提取

从响应 JSON 中使用 **JSONPath 语法**提取值并保存到当前环境变量。

#### 变量助手

浮动工具面板，支持：

| 类型 | 示例 |
|---|---|
| 内置函数 | `{{$timestamp}}`、`{{$uuid}}`、`{{$randomInt}}`、`{{$randomFloat}}`、`{{$date}}` |
| JS 表达式 | `{{@Date.now()}}`、`{{@Math.random().toFixed(4)}}`、`{{@crypto.randomUUID()}}` |
| 时间偏移 | `{{@Date.now() + 3600000}}`（1 小时后）、`{{@new Date(Date.now() - 86400000).toISOString()}}`（1 天前） |
| 列表随机 | `{{@['苹果','香蕉','橘子'][Math.floor(Math.random()*3)]}}` |
| 环境变量 | `{{环境变量名}}` |

#### 全局参数管理

多环境支持，通过 `useEnvironments` Hook 统一管理环境变量的 CRUD 和切换。

---

### 4. Mock 接口平台

可配置的 HTTP Mock 服务器，用于接口模拟测试。

#### 特性

- Mock 配置 CRUD（名称、HTTP 方法、URL 路径）
- 自定义状态码、响应头、响应体
- 参数化响应
  - 内置函数：`{{$timestamp}}`、`{{$uuid}}`、`{{$randomInt}}`、`{{$randomFloat}}`
  - JS 表达式：`{{@表达式}}`
  - 时间偏移：`{{@Date.now() + 3600000}}`（1 小时后）、`{{@new Date(Date.now() - 86400000).toISOString()}}`（1 天前）
  - 列表随机：`{{@['苹果','香蕉','橘子'][Math.floor(Math.random()*3)]}}`
  - 路径参数提取
- **分页模拟**：`response_count > 1` 时自动生成多条数据
- 启用/禁用开关
- 在线测试按钮
- 请求/响应日志记录

---

### 5. 定时任务平台

基于 **APScheduler** 的定时任务引擎。

#### 特性

- **调度类型**：
  - 间隔执行（秒）
  - Cron 表达式（标准 5 段或 6 段）
- **多请求串行执行**：按顺序依次执行已保存的请求
- **变量替换**：执行时自动替换 `{{variable}}` 模板
- **Mock 匹配**：请求可被 Mock 接口拦截
- **后置提取**：从响应中提取变量并更新环境参数
- **任务参数**：支持文本和文件类型参数
- **执行日志**：请求/响应详情、变量提取、错误详情

#### 管理操作

- CRUD 定时任务
- 启停控制
- 手动触发执行
- 执行历史查看

---

## 技术架构

### 后端 (FastAPI + SQLModel + SQLite)

```
backend/
├── main.py                   # FastAPI 应用入口
├── app/
│   ├── main.py               # 路由聚合 & 权限绑定
│   ├── auth.py               # Keycloak JWT 验证
│   ├── deps.py               # 依赖注入
│   ├── scheduler.py          # APScheduler 定时任务引擎
│   ├── routes/               # 17 个路由模块
│   │   ├── testcase.py       # 测试用例 CRUD + 执行
│   │   ├── pipeline.py       # 4 阶段深度生成管线
│   │   ├── api_test_tool.py  # OpenAPI 导入 & 接口/场景执行
│   │   ├── proxy.py          # HTTP 请求代理 & 变量替换
│   │   ├── mock_server.py    # Mock 服务器（免认证）
│   │   ├── mock_config.py    # Mock 配置管理
│   │   ├── mock_log.py       # Mock 日志
│   │   ├── scheduled_task.py # 定时任务管理
│   │   ├── saved_request.py  # 请求配置 CRUD
│   │   ├── session.py        # 会话管理
│   │   ├── module.py         # 模块树形管理
│   │   ├── global_parameter.py # 全局参数管理
│   │   ├── config.py         # 系统配置
│   │   ├── mcp.py            # MCP 服务器管理
│   │   ├── skills.py         # Skills Hub 管理
│   │   └── history_prompt.py # 历史提示词管理
│   └── services/             # 业务逻辑层
│       ├── testcase_service.py  # 用例服务
│       ├── api_context.py       # API 上下文构建
│       ├── api_consistency.py   # 一致性检查引擎
│       ├── api_test_tool.py     # API 测试工具服务
│       ├── scenario_runner.py   # 场景执行引擎
│       ├── step_runner.py       # 单步执行器
│       ├── swagger_parser.py    # OpenAPI 解析器
│       ├── schema_utils.py      # Schema 工具
│       ├── assertion_engine.py  # 断言引擎
│       ├── mcp_loader.py        # MCP 配置加载
│       └── keycloak_sync.py     # Keycloak 角色同步
├── utils/
│   ├── model_utils.py        # LangChain agent + 结构化输出
│   ├── llm_factory.py        # ChatOpenAI 统一构建工厂
│   ├── prompts.py            # 提示词模板
│   ├── file_parser.py        # 文件解析（.docx/.pdf/.txt/.md）
│   ├── document_prefetch.py  # 飞书/蓝湖文档预取
│   ├── lanhu_mcp_adapter.py  # 蓝湖 MCP 适配器
│   ├── js_expression.py      # JS 表达式执行
│   └── base_response.py      # 统一响应模型
├── db/
│   ├── models.py             # 17 个 SQLModel 数据表
│   └── db.py                 # SQLite 引擎 + 自动迁移
├── config.py                 # JSON 文件配置管理
└── skills/                   # Skills Hub 技能文件
```

**数据表总览**：

| 表 | 用途 |
|---|---|
| Session | 测试会话（顶层容器） |
| Module | 功能模块（树形层级） |
| TestCase | 测试用例 |
| RequirementPoint | 需求点（Pipeline 阶段 1） |
| TestPoint | 测试点（Pipeline 阶段 2） |
| ApiProject | 导入的 API 项目 |
| ApiEndpoint | API 接口定义 |
| ApiScenario | 接口场景 |
| ApiScenarioResult | 场景执行结果 |
| SavedRequest | 保存的 HTTP 请求 |
| GlobalParameter | 环境/全局参数 |
| ScheduledTask | 定时任务 |
| MockConfig | Mock 配置 |
| McpServer | MCP 服务器配置 |
| SystemConfig | 系统配置 |
| HistoryPrompt | 历史提示词 |
| MockLog / TestCaseExecutionLog | 日志 |

### 前端 (React 18 + TypeScript + Ant Design 5)

```
frontend/src/
├── App.tsx                    # 主应用（6 个平台视图切换）
├── AppWithAuth.tsx            # Keycloak PKCE 认证
├── config.ts                  # 运行时配置
├── types/index.ts             # TypeScript 类型定义
├── services/
│   ├── api.ts                 # Axios 封装（token 注入、自动刷新）
│   ├── keycloak.ts            # Keycloak 初始化
│   └── permissions.ts         # 25+ 细粒度权限定义
├── components/
│   ├── test-case/             # AI 测试用例平台组件
│   │   ├── TestCaseGenerator.tsx    # 生成器（需求输入 + 文件上传 + Pipeline）
│   │   ├── TestCaseManager.tsx      # 用例管理（统计、筛选、导出）
│   │   ├── TestCaseTable.tsx        # 用例表格
│   │   └── ...
│   ├── iot/                   # IoT 平台组件
│   ├── api/                   # 接口测试组件
│   ├── modals/                # 12+ 模态框
│   └── ...                    # Header、Sidebar、HomePage 等
└── hooks/
    └── useEnvironments.ts     # 环境管理 Hook
```

### 基础设施

```
                  ┌──────────┐
                  │  Keycloak │  OIDC 认证（端口 8090）
                  └────┬─────┘
                       │
┌──────────┐     ┌─────▼──────┐     ┌──────────┐
│  Postgres │◄────│   Backend  │◄────│ Frontend │
│  (5432)   │     │  FastAPI   │     │   React  │
└──────────┘     │  (8000)    │     │  (8001)  │
                 └─────┬──────┘     └──────────┘
                       │
                 ┌─────▼──────┐
                 │   SQLite   │   data/testcases.db
                 └────────────┘
```

### 关键设计决策

- **认证**：Keycloak PKCE (OIDC)，本地 JWKS 验证 JWT（24h 缓存）
- **AI 模型**：LangChain agent 封装，支持 DeepSeek API 和 Ollama 本地模型
- **配置优先级**：运行时 `window.__APP_CONFIG__` > 环境变量 > 默认值
- **响应格式**：统一 `{code, message, data}`
- **时区**：上海时区 (UTC+8)
- **数据库**：SQLite（`backend/data/testcases.db`），SQLModel ORM
- **JSON 存储**：复杂字段以 JSON 字符串存储于 SQLite 中

---

## 环境变量与部署

### 文件结构

| 配置文件 | 用途 |
|---|---|
| `.env`（根目录） | Docker Compose 部署参数 |
| `backend/.env` | 后端本地开发配置 |
| `frontend/.env` | 前端本地开发配置 |

### 核心环境变量

```env
# --- Keycloak 配置 ---
KEYCLOAK_SERVER_URL=http://keycloak:8080          # 后端访问 Keycloak（Docker 内部）
KEYCLOAK_EXTERNAL_URL=http://localhost:8090        # 浏览器访问 Keycloak（JWT issuer）
KEYCLOAK_REALM=ai-testcase
KEYCLOAK_BACKEND_CLIENT_ID=backend
KEYCLOAK_ADMIN_USERNAME=admin
KEYCLOAK_ADMIN_PASSWORD=admin

# --- 前后端地址 ---
VITE_KEYCLOAK_URL=http://localhost:8090            # 浏览器访问 Keycloak
FRONTEND_PORT=8001
BACKEND_PORT=8000
KEYCLOAK_PORT=8090

# --- CORS ---
CORS_ORIGINS=http://localhost:5173,http://localhost:8001

# --- 前端 Keycloak 配置 ---
VITE_KEYCLOAK_REALM=ai-testcase
VITE_KEYCLOAK_CLIENT_ID=frontend
VITE_KEYCLOAK_BACKEND_CLIENT_ID=backend
VITE_KEYCLOAK_PKCE_METHOD=S256
```

### 部署命令

```bash
# 使用指定 .env 文件部署
docker compose --env-file .env up -d

# 代码/依赖有变动时重新构建
docker compose --env-file .env up -d --build
```

**注意**：前端 Docker 镜像会在容器启动时根据环境变量生成 `/config.js`，因此仅修改 Keycloak 地址、CORS 等部署参数时，只需要重启容器，无需重新构建前端镜像。

### 后端配置管理

后端启动时自动通过 `python-dotenv` 加载 `backend/.env`，关键配置：

```env
# 模型配置
MODEL_TYPE=api                                  # api (DeepSeek) 或 ollama
DEEPSEEK_API_KEY=sk-xxx
# DEEPSEEK_BASE_URL=https://api.deepseek.com
# DEEPSEEK_MODEL=deepseek-chat

# Ollama 配置（本地模型）
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_MODEL=qwen2.5:32b

# 飞书文档预取
# FEISHU_APP_ID=cli_xxx
# FEISHU_APP_SECRET=xxx
```

---

## 配置文件提交规则

以下文件不应提交到仓库：

- `.env`（根目录）
- `backend/.env`
- `frontend/.env`
- `frontend/.env.development`
- `backend/data/testcases.db`
- `backend/data/config.json`

修改配置模板请对应更新 `.env.example` 文件。
