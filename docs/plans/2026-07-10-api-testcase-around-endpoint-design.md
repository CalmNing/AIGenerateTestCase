# 接口测试用例"围绕接口本身"生成 — 设计文档

- 日期：2026-07-10
- 分支：feat/requirement-testpoint-management
- 状态：已与用户确认（brainstorming 通过）

## 背景

当前"通过接口生成接口测试用例"的链路中，模型生成结果**围绕用户输入的提示词（用户需求）展开**，而非围绕接口本身。具体表现：

1. `BASE_PROMPT` 开头即声明"你的任务是**根据用户需求**生成测试用例"，把用户需求当主角。
2. 路由层把接口信息标为"以下 API 接口信息"，再单独追加 `## 用户需求` 章节；当用户勾选技能时还会再强调一次"## 用户需求"。
3. 尽管 `build_api_context` 已拼接了大量接口信息（方法/路径/标签/参数/字段 schema/请求骨架/响应 schema/前后置），但缺乏"接口业务描述"这一语义载体（`ApiEndpoint` 模型没有 `description` 字段）。
4. 依赖推断 `infer_endpoint_dependencies` 只做粗粒度的"前置新增接口补全"，未做字段级数据流分析。

结果：当用户提示词写得详细时，模型往往从提示词文本提取测试点，而不是从接口字段/约束/依赖去衍生用例。

## 目标

生成接口测试用例时，用例应**围绕接口本身**展开，覆盖三个维度：

- **接口的描述**（业务语义）
- **接口的字段**（类型/enum/约束/必填）
- **上下文依赖推断**（接口间字段级数据流）

## 约束（硬）

- **只改"接口测试用例生成"链路**，功能测试用例（`FUNCTIONAL_PROMPT`、功能测试路由、`BASE_PROMPT` 通用部分）完全不受影响。
- 所有提示词改动收敛在 `API_PROMPT` 与 API 上下文/依赖代码；若需触碰 `BASE_PROMPT`，只允许"仅当为接口测试时"的条件分支。

## 方案

### ① 数据模型与前端录入

- **后端** `backend/db/models.py` 的 `ApiEndpoint` 新增字段：
  ```python
  description: Optional[str] = Field(default=None, description="接口业务描述")
  ```
  已有库由 `backend/db/db.py` 的 `_migrate_missing_columns` 自动加列（默认 `''`），向后兼容、无需 alembic。
- **前端类型** `frontend/src/types/index.ts` 的 `ApiEndpoint` 增加 `description?: string`。
- **前端表单** `frontend/src/components/api/ApiScenarioTestTool.tsx` 的接口新增/编辑表单增加"接口描述"多行输入（可选），保存与回显透传。

### ② 接口上下文构建增强（`backend/app/services/api_context.py`）

- `build_api_context` 在每段接口信息中增加 `接口描述: <description>`（为空则省略该行）。
- **新增字段级依赖分析** `analyze_field_dependencies(session, endpoint_ids, project_id)`：
  - 提取每个接口的 `response_schema` 顶层字段名；
  - 对另一接口，检查其 `path`（`{field}`）、`parameters`、`body`（解析 JSON）是否引用这些字段名；
  - 产出结构化依赖，例如：`接口[2] 依赖 接口[1] 的字段 "id"（用于路径参数 {id}）`；
  - 以独立 `## 接口间依赖关系` 章节写入 context；在被依赖接口段打 `[提供字段: id]`、依赖方打 `[依赖: 接口1的字段id]` 标记。
- 保留现有 `infer_endpoint_dependencies`（前置新增接口补全），两者合并到 `api_endpoint_ids` / `dependency_labels`。

### ③ API 提示词编排强化（`backend/utils/prompts.py` 的 `API_PROMPT`）

- 开头加**核心原则**：提供了接口信息时，**接口信息是生成测试用例的主要依据**，用户需求仅作背景补充，不得脱离接口信息凭空生成用例。
- 加**逐接口覆盖**：每个接口 `[i]` 至少 1 个成功场景 + 针对其必填字段的异常场景 + 边界场景。
- 加**逐字段覆盖**：基于请求 Schema 的字段类型 / enum / min-max / length / pattern，为每个关键字段生成异常/边界用例。
- 加**依赖驱动端到端**：利用"接口间依赖关系"章节生成跨接口联动场景（前置创建 → 提取变量 → 后续使用），明确引用依赖字段。
- **降权用户需求**：
  - 路由层 `backend/app/routes/testcase.py` 把拼接的 `## 用户需求` 改为 `## 补充背景（次要，仅用于理解业务语境）`；
  - 接口测试时，`model_utils.py` 中 `selected_skill_names` 注入（约 1066 行）不再把"用户需求"当唯一主角（API 场景下降权）。

### ④ 隔离与验证

- 仅改动 `API_PROMPT` + 接口上下文/依赖 + 接口录入；`FUNCTIONAL_PROMPT`、功能测试路由、`BASE_PROMPT` 通用部分不动。
- **验证方式**：
  1. 选多个带 `description` 的接口生成，确认用例围绕接口字段/描述/依赖（而非提示词）。
  2. 对照纯功能测试生成，确认未被影响（用例仍为纯文本步骤、无 api_call）。

## 风险与回退

- 新增 `description` 列自动迁移，若老库异常可在 `_migrate_missing_columns` 日志中观察（不影响已有数据）。
- 依赖分析为只读推断，失败不影响主流程（try/except 兜底，缺依赖信息时退化为现有行为）。
- 提示词改动仅在 `is_api_test=True` 时生效，功能测试路径零影响。
