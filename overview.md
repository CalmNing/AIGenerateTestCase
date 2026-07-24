# MCP JSON 配置导入/导出功能

## 概述
为项目新增了 MCP 配置的 JSON 格式导入/导出功能，兼容 Claude Desktop/Cursor 的 `mcpServers` 标准格式。

## 改动文件

### 后端
- **`backend/app/routes/mcp.py`** — 新增 3 个 API 路由：
  - `POST /api/mcp/servers/import` — JSON 数据导入（支持对象/数组格式）
  - `POST /api/mcp/servers/import/file` — 从 JSON 文件导入（multipart upload）
  - `GET /api/mcp/servers/export` — 导出为标准 mcpServers JSON 格式

### 前端
- **`frontend/src/services/api.ts`** — `mcpServerApi` 新增 `importJson`、`importJsonFile`、`exportJson` 三个方法
- **`frontend/src/components/modals/McpConfigModal.tsx`** — 添加导入/导出工具栏（"导出 JSON"、"从文件导入"、"从 JSON 粘贴导入"三个按钮 + JSON 粘贴导入子弹窗）

### 测试
- **`backend/tests/test_mcp_import_export.py`** — 30 个测试用例，覆盖：
  - 对象格式/数组格式解析
  - 自动 type 推断（http/stdio）
  - env 对象↔数组格式转换
  - 空数据处理、同名覆盖更新
  - 导出格式验证、用户隔离
  - 路由注册验证

## 测试结果
**30/30 全部通过**
