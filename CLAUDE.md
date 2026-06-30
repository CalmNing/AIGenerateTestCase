# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-driven test case generation and management tool. Uses LangChain agents with DeepSeek/Ollama models to generate software test cases from natural language requirements. Also includes an IoT data push platform, Mock API server, and scheduled task management platform.

## Development Commands

### Backend (Python 3.11, FastAPI)
```bash
cd backend
python -m venv .venv && .venv/Scripts/activate        # Windows create/activate venv
pip install -r requirements.txt
$env:PYTHONPATH = "F:\VSCode\ai-generate-testcase\backend"  # Ensure imports work
uvicorn main:app --reload --host 0.0.0.0 --port 8000   # Run from backend/ directory
```
Can also start from project root: `uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000` (no PYTHONPATH needed).

### Frontend (React 18 + TypeScript, Vite)
```bash
cd frontend
npm install
npm run dev                                           # Port 5173, proxy /api to 8000
npm run lint                                          # ESLint check (--max-warnings 0)
npm run build                                         # tsc + vite build
npm run preview                                       # Preview production build
```
TypeScript strict mode enabled (`noUnusedLocals`, `noUnusedParameters`).

### Docker Full Stack
```bash
docker compose up                                     # Start all services
docker compose up backend --build -d                  # Rebuild backend only
docker compose up frontend --build -d                 # Rebuild frontend only
```
Backend Dockerfile: `python:3.11-slim-bookworm` with `uv` for Python deps, Node.js 20.19.1 embedded (for STDIO MCP subprocesses). Docker Compose uses `npm_cache` and `uv_cache` named volumes. Frontend Dockerfile: multi-stage build (Node 18 compile → nginx:alpine static).

### Database Migrations
```bash
cd backend
alembic upgrade head                                  # Run migrations
alembic revision --autogenerate -m "description"      # Create new migration
```

### Testing
Backend: `cd backend && pytest` (or `.venv\Scripts\python.exe -m pytest`). Tests live in `backend/tests/`, use in-memory SQLite, and never call a real LLM or network. Frontend lint: `npm run lint`.

## Architecture

### Backend (FastAPI + SQLModel + SQLite)

```
backend/
├── main.py                # FastAPI app entry, mounts api_router to /api
├── app/
│   ├── main.py            # Registers all route modules, injects Keycloak auth dependency
│   ├── auth.py            # Keycloak JWT verification
│   ├── deps.py            # FastAPI dependency injection (SessionDep, CurrentUser)
│   ├── scheduler.py       # APScheduler scheduled task engine
│   ├── routes/            # One route file per domain (13 total)
│   │   ├── testcase.py    # Core: AI generation + CRUD
│   │   ├── mcp.py         # MCP tool list detection (stdio & HTTP transport)
│   │   ├── skills.py      # Skills Hub API (SKILL.md parsing & listing)
│   │   ├── mock_server.py # Mock API server (no auth required)
│   │   ├── proxy.py       # HTTP proxy + variable substitution
│   │   ├── session.py, module.py, saved_request.py, ...
│   │   └── mock_log.py, mock_config.py, scheduled_task.py, ...
│   ├── pipeline/          # 4-phase AI test case generation pipeline
│   │   ├── pipeline_runner.py    # Orchestrator: runs 4 phases sequentially
│   │   ├── requirement_splitter.py  # Phase 1: Extract requirement points
│   │   ├── test_point_generator.py  # Phase 2: Generate test points
│   │   ├── test_case_designer.py    # Phase 3: Design test cases
│   │   ├── test_case_optimizer.py   # Phase 4: Optimize test cases
│   │   ├── schemas.py     # Pydantic models for each phase's structured output
│   │   └── prompts.py     # System/user prompts for all 4 agents
│   └── services/          # Business logic layer
├── db/
│   ├── models.py          # 10+ SQLModel (Session, TestCase, Module, SavedRequest, GlobalParameter, HistoryPrompt, ScheduledTask, MockConfig, McpServer, MockLog, ApiScenario, ApiEndpoint, RequirementPoint, TestPoint)
│   └── db.py              # SQLite engine + auto migration
├── skills/                # Skills Hub skill files (5 SKILL.md, YAML frontmatter + markdown)
├── utils/
│   ├── model_utils.py     # LangChain agent (DeepSeek/Ollama) + structured output
│   ├── lanhu_mcp_adapter.py # MCP client (HTTP Streamable & STDIO)
│   ├── helpers.py         # JSON repair, level normalization, token diagnostics
│   ├── models.py          # Core data models (TestCase, ApiCallStep, ResponseFormat)
│   ├── prompts.py         # SYSTEM_PROMPT for single-pass generation
│   ├── base_response.py   # Response[T] unified response wrapper
│   ├── js_expression.py   # JavaScript template expression evaluator
│   └── history_prompt_cleaner.py # Cleans history prompts before saving
└── config.py              # JSON file config (LLM API key, Ollama, MCP URL)
```

### Two Test Case Generation Paths

**Path A: Single-Pass Agent** (`utils/model_utils.py`)
- Entry: `POST /api/testcases/generate` → `generate_testcases()`
- One LLM call produces all test cases at once
- Creates LangChain agent with optional MCP tools (Lanhu/Feishu)
- Uses `ResponseFormat` schema with `SYSTEM_PROMPT` from `utils/prompts.py`
- Supports API models (DeepSeek/OpenAI-compatible) and local Ollama
- When Feishu/Lanhu document content is prefetched, skips agent ReAct loop and calls `model.with_structured_output()` directly

**Path B: 4-Phase Pipeline** (`app/pipeline/`)
- Entry: `PipelineRunner.run()` → background async task
- Four sequential LLM calls with specialized prompts:
  - Phase 1 (0-25%): Requirement splitting → `RequirementPoint` records
  - Phase 2 (25-50%): Test point generation → `TestPoint` records
  - Phase 3 (50-85%): Test case design → `TestCase` records
  - Phase 4 (85-100%): Test case optimization → update DB records
- Progress tracking with percentage updates via in-memory task state
- Each phase has its own Pydantic schema for structured output

Both paths share the same LLM infrastructure (`ChatOpenAI`), `_structured_output_robust()` function, and document pre-fetching logic.

### Structured Output Implementation

`_structured_output_robust()` uses LangChain's `model.with_structured_output(schema)` which automatically selects the best strategy based on model capabilities:
- OpenAI/DeepSeek (compatible API) → tool calling strategy
- Other models → json_mode or function_calling
- Built-in JSON parsing, Pydantic validation, and error retry

For agents, `create_agent()` uses `ToolStrategy(ResponseFormat)` which provides:
- Automatic strategy selection
- Built-in error handling (multiple outputs → retry, schema validation failure → retry)

**Important:** `max_tokens` must be explicitly set (e.g., `max_tokens=16384`) when using DeepSeek API. When omitted, the API applies a server-side default of 8192 tokens, which can truncate long responses.

### Key Architectural Details

**Authentication:** Keycloak PKCE (OIDC). `/api/mock/` is exempt; all other routes require Bearer JWT. Backend uses JWKS local verification (`app/auth.py`), 24-hour key cache. JWT issuer uses external address (`KEYCLOAK_EXTERNAL_URL`), JWKS fetch uses internal address (`KEYCLOAK_SERVER_URL`).

**MCP Integration:** Two transport modes:
- HTTP (Streamable HTTP): MCPClient via POST + SSE
- STDIO: Subprocess via stdin/stdout JSON-RPC, auto-detects HTTP port announcement

**Per-tool enable/disable:** `McpServer` model and `McpServerConfig` class have `enabled_tools: Optional[list[str]]` field. `lanhu_mcp_adapter.build_tools_from_configs()` filters by this list.

**Scheduled Tasks:** APScheduler AsyncIOScheduler, supports cron and interval scheduling. Executes saved HTTP requests in sequence with variable substitution and post-extraction.

**API Testing Engine** (`services/api_test_tool.py`): Parses Swagger/OpenAPI specs, syncs endpoints, runs multi-step test scenarios with variable substitution, pre/post actions (JSONPath extraction), and assertions (status_code, jsonpath_exists, jsonpath_equals, response_time_lt, etc.).

### Frontend (React 18 + Ant Design)

```
frontend/src/
├── App.tsx                # Large root component (~2054 lines), holds session/module/testcase/environment state and platform switching
├── AppWithAuth.tsx        # Keycloak PKCE initialization + token refresh
├── types/index.ts         # TypeScript interfaces and enums
├── services/
│   ├── api.ts             # Axios instance + Bearer token interceptor + 401 handling
│   └── keycloak.ts        # Keycloak JS adapter initialization
├── components/
│   ├── modals/            # 10 CRUD modals (McpConfigModal, SettingsModal, etc.)
│   ├── TestCaseGenerator.tsx, TestCaseManager.tsx, TestCaseTable.tsx
│   ├── IoTDataPushPlatform.tsx, IoTMockPlatform.tsx
│   ├── ScheduledTaskManager.tsx
│   ├── ModuleSidebar.tsx, SessionSidebar.tsx, HistoryPromptSidebar.tsx
│   └── HeaderComponent.tsx, HomePage.tsx
```

**Platform switching:** `currentPlatform` state variable controls view: `home`, `ai-testcase`, `iot-mock`, `scheduled-task`, `mock-api`.

**Keycloak config:** `frontend/.env` sets `VITE_KEYCLOAK_URL`, `VITE_KEYCLOAK_REALM`, `VITE_KEYCLOAK_CLIENT_ID`.

**State management:** React local state + localStorage. `zustand` is listed in package.json but not currently used in `src/`.

### Infrastructure

```
docker-compose.yml
├── backend      (8000)  FastAPI + SQLite
├── frontend     (8001)  Vite + React
├── postgres     (5432)  Keycloak database
├── keycloak     (8090)  OIDC authentication
└── lanhu-mcp    (8002)  Lanhu MCP service
```

**Docker Compose environment variables (backend service):**
- `KEYCLOAK_SERVER_URL` — Keycloak internal address (container-to-container, e.g., `http://keycloak:8080`), for JWKS fetch
- `KEYCLOAK_EXTERNAL_URL` — Keycloak external address (browser access, e.g., `http://localhost:8090`), for JWT issuer verification
- `CORS_ORIGINS` — Allowed cross-origin sources, comma-separated

**Backend config management:** `backend/config.py` `ConfigManager` reads `data/config.json`, merges defaults, provides global `config` object. Config items include model type (`api`/`ollama`), API key, Ollama URL, MCP switches, etc.

### Key Conventions

- Backend models use Shanghai timezone (UTC+8) for `created_at`/`updated_at`
- JSON fields stored as strings in SQLite, parsed in routes
- Module hierarchy uses self-referencing `parent_id`
- TestCase status enum: NOT_RUN, PASSED, FAILED
- Test case levels: 1-4 (functional, boundary, exception, scenario)
- Frontend Vite proxy: `/api` → `http://127.0.0.1:8000`
- All API responses: `{code: number, message: string, data: T}` — error descriptions in `message` field, `data` does not contain human-readable error info

### MCP Configuration Storage & Execution

MCP config supports **server-side persistence** and **browser localStorage**:

- **Persistence model:** `db.models.McpServer` — stored in SQLite `mcpserver` table, isolated by `user_id`
- **API routes:** `backend/app/routes/mcp.py` `GET/POST/PUT/DELETE /mcp/servers` CRUD endpoints
- **Frontend load order:** McpConfigModal loads from server first; falls back to localStorage if server unavailable
- **Save logic:** Writes to both server and localStorage (localStorage as UI fallback only)
- **Generation flow:** Backend reads only current user's server-saved enabled MCP configs; client `mcp_servers` field ignored to prevent bypassing `/api/mcp` permission control
- **Execution:**
  - HTTP type: Backend short-connects to external URL, disconnects after use
  - STDIO type: Backend uses `asyncio.create_subprocess_exec` to temporarily start subprocess (e.g., `npx ...`), killed via `_safe_kill()` after generation

### Skills Hub

5 testing methodology skills stored as `SKILL.md` files in `backend/skills/` (YAML frontmatter + markdown body):

| Skill | Description |
|-------|-------------|
| `testcase-design` | Core 3-step workflow: requirement analysis → 4-level case design → quality review |
| `boundary-testing` | Boundary value analysis (numeric, string, set, date) |
| `exception-testing` | Exception testing (error guessing: input/environment/state anomalies) |
| `scenario-testing` | End-to-end scenario and workflow testing |
| `coverage-analysis` | Coverage analysis, identify gaps to avoid duplicate cases |

**Skills ≠ MCP Tools**: MCP Tools give agent function-calling ability; Skills give agent **methodology guidance** — selected SKILL.md body is injected into the prompt.

**API:** `GET /api/skills` returns all skills (name, description, body_preview), `GET /api/skills/{name}` returns full content. Parsed results cached in memory.

**Frontend:** Header "Skills" button → `SkillsHubModal.tsx` → fetch skills → Switch enable/disable → save to localStorage

**Generation flow:** Selected skill names sent via FormData `selected_skills` → `model_utils._load_skill_bodies()` loads SKILL.md body → prepended to user requirement text (`## 激活的测试方法论技能\n\n[skills]\n\n## 用户需求\n\n[requirement]`), works for both API and Ollama model types.

### Environment Variables & Deployment

Different startup methods read different config files:

| Startup Method | Config File | Notes |
|----------------|-------------|-------|
| Docker Compose | Root `.env` | Recommended for prod/test. Restart containers after changes. |
| Local backend | `backend/.env` | Used with `cd backend && uvicorn main:app --reload` |
| Local frontend | `frontend/.env`, `frontend/.env.development` | Used with `cd frontend && npm run dev` |

Production deployment:
```bash
copy .env.example .env          # Windows
# cp .env.example .env          # Linux/macOS
# Edit .env with actual values
docker compose --env-file .env up -d --build
```

Key variables:
```env
VITE_KEYCLOAK_URL=http://localhost:8090    # Browser access to Keycloak (NOT internal)
KEYCLOAK_EXTERNAL_URL=http://localhost:8090 # Must match JWT issuer
KEYCLOAK_SERVER_URL=http://keycloak:8080    # Internal container-to-container
CORS_ORIGINS=http://localhost:5173,http://localhost:8001
```

Frontend Docker image generates `/config.js` from container env vars at startup. Changing Keycloak URL/CORS/ports only requires container restart, not rebuild.

### Configuration Files Overview

| File | Purpose |
|------|---------|
| `backend/data/config.json` | LLM config (model type, API key, Ollama URL, MCP URL) |
| `backend/data/testcases.db` | SQLite database file |
| `backend/skills/*/SKILL.md` | Testing methodology skill files (5) |
| `backend/.dockerignore` | Backend Docker build ignore rules |
| `frontend/.env` | Keycloak address and realm (Vite compile-time injection) |
| `.mcp.json` | Local MCP server config (Claude Code IDE integration), currently points to Lanhu MCP |
| `keycloak/realm-config.json` | Keycloak realm initialization config (first import) |
