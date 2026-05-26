# API Permission Matrix

This document maps all backend APIs under `/api` to required permission codes.

## Rule Source

- Router-level permission dependencies are configured in `backend/app/main.py`.
- Permission codes are defined in `backend/app/permissions.py`.
- Keycloak realm roles are defined in `keycloak/realm-config.json`.

## Route Matrix

### Session (`/api/sessions`)

- `GET /api/sessions/` -> `session:read`
- `POST /api/sessions/` -> `session:create`
- `PUT /api/sessions/{session_id}` -> `session:update`
- `DELETE /api/sessions/{session_id}` -> `session:delete`

### Module (`/api/module`)

- `GET /api/module/{session_id}/modules` -> `module:read`
- `GET /api/module/{session_id}/modules/tree` -> `module:read`
- `GET /api/module/{module_id}` -> `module:read`
- `POST /api/module/` -> `module:create`
- `PUT /api/module/{module_id}` -> `module:update`
- `DELETE /api/module/{module_id}` -> `module:delete`

### Saved Request (`/api/saved-requests`)

- `GET /api/saved-requests` -> `iot:read`
- `POST /api/saved-requests` -> `iot:create`
- `PUT /api/saved-requests/{saved_request_id}` -> `iot:update`
- `DELETE /api/saved-requests/{saved_request_id}` -> `iot:delete`

### Proxy (`/api/proxy`)

- `POST /api/proxy/forward` -> `iot:execute`

### Testcase (`/api/testcases`)

- `GET /api/testcases/{session_id}/testcases` -> `testcase:read`
- `POST /api/testcases/{session_id}/testcases` -> `testcase:create` OR `testcase:generate`
- `PUT /api/testcases/{session_id}/testcases/{testcase_id}` -> `testcase:update`
- `DELETE /api/testcases/{session_id}/testcases` -> `testcase:delete`
- `POST /api/testcases/{testcase_id}/move` -> `testcase:create` OR `testcase:generate`
- `POST /api/testcases/move` -> `testcase:create` OR `testcase:generate`
- `POST /api/testcases/{session_id}/testcases/create` -> `testcase:create` OR `testcase:generate`

### History Prompt (`/api/history_prompt`)

- `GET /api/history_prompt/{module_id}` -> `testcase:read`
- `GET /api/history_prompt/session/{session_id}` -> `testcase:read`
- `POST /api/history_prompt` -> `testcase:create` OR `testcase:generate`
- `DELETE /api/history_prompt/{prompt_id}` -> `testcase:delete`

### Config (`/api/config`)

- `GET /api/config/lanhu-cookie` -> `config:manage`
- `POST /api/config/lanhu-cookie` -> `config:manage`

### MCP (`/api/mcp`)

- `POST /api/mcp/list-tools` -> `mcp:manage`
- `GET /api/mcp/servers` -> `mcp:manage`
- `POST /api/mcp/servers` -> `mcp:manage`
- `PUT /api/mcp/servers/{server_id}` -> `mcp:manage`
- `DELETE /api/mcp/servers/{server_id}` -> `mcp:manage`

### Skills (`/api/skills`)

- `GET /api/skills` -> `skills:manage`
- `GET /api/skills/{skill_name}` -> `skills:manage`
- `DELETE /api/skills/{skill_name}` -> `skills:manage`
- `POST /api/skills/install` -> `skills:manage`

### Global Parameters (`/api/global-parameters`)

- `GET /api/global-parameters` -> `global_parameter:manage`
- `POST /api/global-parameters` -> `global_parameter:manage`
- `PUT /api/global-parameters/{parameter_id}` -> `global_parameter:manage`
- `DELETE /api/global-parameters/{parameter_id}` -> `global_parameter:manage`
- `GET /api/global-parameters/default` -> `global_parameter:manage`
- `POST /api/global-parameters/extract-and-save` -> `global_parameter:manage`

### Scheduled Tasks (`/api/scheduled-tasks`)

- `GET /api/scheduled-tasks` -> `scheduled_task:manage`
- `POST /api/scheduled-tasks` -> `scheduled_task:manage`
- `PUT /api/scheduled-tasks/{task_id}` -> `scheduled_task:manage`
- `DELETE /api/scheduled-tasks/{task_id}` -> `scheduled_task:manage`
- `POST /api/scheduled-tasks/{task_id}/run` -> `scheduled_task:manage`

### Mock Configs (`/api/mock-configs`)

- `GET /api/mock-configs` -> `mock:manage`
- `POST /api/mock-configs` -> `mock:manage`
- `PUT /api/mock-configs/{config_id}` -> `mock:manage`
- `DELETE /api/mock-configs/{config_id}` -> `mock:manage`

### Mock Logs (`/api/mock-logs`)

- `GET /api/mock-logs` -> `mock_log:read`
- `GET /api/mock-logs/{log_id}` -> `mock_log:read`
- `DELETE /api/mock-logs/{log_id}` -> `mock_log:delete`

### Public Mock Callback (`/api/mock`)

- `/api/mock/**` -> public route, no permission required.

## Permission Codes Required in Keycloak

- `testcase:read`
- `testcase:create`
- `testcase:update`
- `testcase:delete`
- `testcase:generate`
- `session:read`
- `session:create`
- `session:update`
- `session:delete`
- `module:read`
- `module:create`
- `module:update`
- `module:delete`
- `iot:read`
- `iot:create`
- `iot:update`
- `iot:delete`
- `iot:execute`
- `config:manage`
- `mcp:manage`
- `skills:manage`
- `global_parameter:manage`
- `scheduled_task:manage`
- `mock:manage`
- `mock_log:read`
- `mock_log:delete`
