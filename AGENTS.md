# Repository Guidelines

## 项目结构与模块组织

本仓库是一个全栈测试用例生成应用。后端代码位于 `backend/`，其中 FastAPI 路由在 `backend/app/routes/`，服务逻辑在 `backend/app/services/`，数据模型在 `backend/db/`，工具函数在 `backend/utils/`，Alembic 迁移在 `backend/alembic/`，提示词技能在 `backend/skills/`。前端代码位于 `frontend/`，React 组件在 `frontend/src/components/`，API 客户端在 `frontend/src/services/`，共享类型在 `frontend/src/types/`。静态截图在 `img/`；Keycloak realm 配置在 `keycloak/`。`lanhu-mcp/` 是独立的 Python MCP 服务包。

## 构建、测试与开发命令

- `docker compose up --build`: 构建并运行后端、前端、Keycloak、Postgres 和 Lanhu MCP 服务。
- `cd backend && pip install -r requirements.txt`: 安装后端依赖。
- `cd backend && uvicorn main:app --reload`: 在本地运行后端 API。
- `cd backend && alembic upgrade head`: 应用数据库迁移。
- `cd frontend && npm install`: 安装前端依赖。
- `cd frontend && npm run dev`: 启动 Vite 开发服务器。
- `cd frontend && npm run build`: 对前端进行类型检查并构建产物。
- `cd frontend && npm run lint`: 对 TypeScript/React 文件运行 ESLint。
- `cd lanhu-mcp && pytest`: 运行 MCP 服务测试套件。

## 代码风格与命名规范

Python 使用 4 空格缩进，TypeScript/React 使用 2 空格缩进。后端模块和路由文件使用 `snake_case.py`；React 组件使用 `PascalCase.tsx`；hooks、辅助函数和服务导出尽量使用 `camelCase`。API 响应结构应与 `backend/utils/` 中的现有工具保持一致。`lanhu-mcp` 遵循 `lanhu-mcp/pyproject.toml` 中的 `black` 和 `isort` 配置，行宽为 120 字符。

## 测试指南

根应用目前自动化测试较少，因此修改后端路由后，应通过有针对性的手动 API 检查验证；条件允许时，在变更模块附近补充 pytest 覆盖。`lanhu-mcp` 的测试位于 `lanhu-mcp/tests/`，采用 `test_*.py` 命名。修改 MCP 提取、CSS 解析或服务行为前后，应运行 `pytest`。

## 提交与 Pull Request 指南

Git 历史使用 Conventional Commit 前缀，例如 `fix(build): ...`、`chore(config): ...` 和 `config(keycloak): ...`；请保持这种风格，并编写简洁、范围明确的提交信息。Pull Request 应说明变更内容，列出验证命令，标注迁移或配置更新；涉及可见前端变更时，应附上截图。

## 安全与配置提示

不要提交密钥、API Key、生成的数据库、`__pycache__/` 或本地 `.env` 文件。修改主机、端口、CORS origins 或认证行为时，应检查 `docker-compose.yml`、`backend/config.py` 和 Keycloak 配置。
