# 接口测试用例生成准确性保证 设计文档

> **For Claude:** REQUIRED SUB-SKILL: 实现阶段使用 superpowers:executing-plans 按任务逐步落地。

**Goal:** 在「选中接口 → 生成接口测试用例」链路中，端到端保证四维度准确性——请求构造准确、断言准确、测试数据准确、接口依赖/顺序准确。通过四道关实现：生成时强约束（schema 确定性骨架）→ 生成后自动一致性检查（对照 Swagger）→ 置信度分级 + 人工复核 → 手动「验证」按钮（真实执行回写结果）。

**Date:** 2026-07-10
**Tech Stack:** FastAPI + SQLModel + SQLite（自动迁移）、LangChain 异步 agent、React 18 + TypeScript + Ant Design + axios。与现有 4 阶段 pipeline（`PipelineRunner` / `TestCaseDesigner` / `build_api_context` / `swagger_parser`）与 `scenario_runner` 复用。

**确认的设计决策（来自 brainstorming）：**
1. 准确性四维度全部关心：请求构造 / 断言 / 测试数据 / 依赖顺序。
2. 四种保证机制全部纳入本期：生成时强约束、生成后一致性检查、置信度分级、手动真实执行验证。
3. 「真实执行验证」= 手动触发（用例「验证」按钮），不进入生成 pipeline；需 API 可达 + 环境参数/鉴权就绪。
4. 置信度以规则打分为主（不做二次 LLM 自评），避免额外调用成本。
5. 一致性检查为纯规则校验，零外部依赖，可在生成后处理中同步执行。

---

## 一、总体架构与数据流

```
选中接口(endpoint_ids)
   │
   ▼
[生成期] PipelineRunner
   ├─ build_api_context()          现有：注入 method/path/schema/body/断言文本
   ├─ 新增 build_request_skeleton() 机制1：确定性 body 模板 + 必填/枚举/类型约束
   └─ TestCaseDesigner(LLM)        机制1：prompt 增加"严格遵循 skeleton"硬约束
   │
   ▼ 生成用例写库
[生成后] PipelineRunner 后处理
   ├─ api_consistency_check()      机制2：逐字段对照 endpoint schema
   └─ score_confidence()           机制3：规则打分 → confidence_score/level
   │  (写入 TestCase.consistency_report / confidence_*)
   ▼
[展示] 前端：一致性通过率徽章 + 置信度等级 + 低分高亮
   │
   ▼ 用户按需
[验证] POST /api/testcases/{id}/validate
   └─ scenario_runner.run_scenario()  机制4：复用现有执行引擎实跑
      回写 validation_status / validation_detail / validation_at
```

核心复用点：
- 请求骨架：`backend/app/services/swagger_parser.py::build_body_from_schema` + `_schema_example`（已存在）。
- API 上下文：`backend/app/services/api_context.py::build_api_context`（已存在，仅扩展 skeleton 段）。
- 执行引擎：`backend/app/services/scenario_runner.py::run_scenario`（已存在，机制4 直接复用）。
- 变量/环境：`backend/app/routes/proxy.py::build_param_map`（已存在）。

---

## 二、机制 1 · 生成时强约束（Schema 确定性骨架）

**目标**：把 LLM 的"自由发挥"限制在测试语义层，请求结构层完全由 schema 决定。

**新增函数 `build_request_skeleton(endpoint)`（放在 `api_context.py`）**：
- 用 `build_body_from_schema(endpoint.request_schema)` 生成确定性 body 模板（已含必填字段与示例值）。
- 从 `request_schema` 抽取：`required`（必填字段列表）、`properties` 的 `enum`/`type`/`format`/`pattern`/`minLength`/`maxLength`/`minimum`/`maximum` 约束。
- 拼接成结构化文本块：
  ```
  [请求骨架-硬约束，必须严格遵循]
  方法: POST
  路径: /api/v1/users
  必填字段: name(类型:string, 约束:minLength=1), age(类型:integer)
  枚举字段: status ∈ {active, disabled}
  请求体模板(JSON):
  { "name": "...", "age": 0, "status": "active" }
  规则: 不得新增 schema 中不存在的字段；字段类型必须与约束一致；method/path 不得修改。
  ```

**Prompt 改动**：在 `backend/app/pipeline/prompts.py` 的 `API_TEST_CASE_DESIGNER_SYSTEM` / `API_TEST_CASE_DESIGN_USER` 中追加"严格遵循请求骨架硬约束"规则，并在 user prompt 中由 `build_api_context` 注入上述 skeleton 段（与现有 api_context 合并）。

**不新增 DB 字段**，纯 prompt + 复用 schema 工具。

---

## 三、机制 2 · 生成后自动一致性检查（对照 Swagger）

**新增服务 `backend/app/services/api_consistency.py`**：

`check_testcase_consistency(case: dict, endpoint: ApiEndpoint) -> dict`
返回：
```json
{
  "passed": false,
  "pass_rate": 0.8,
  "checks": [
    {"check": "method_match", "passed": true, "detail": "POST == POST"},
    {"check": "no_extra_body_fields", "passed": false, "detail": "请求体含 schema 外字段: foo"},
    {"check": "required_fields_present", "passed": true, "detail": "必填 name/age 均存在"},
    {"check": "enum_valid", "passed": true, "detail": "status=active 合法"},
    {"check": "assertion_fields_exist", "passed": true, "detail": "断言字段均存在于 response_schema"}
  ]
}
```

校验项：
1. `method_match`：用例请求方法 == endpoint.method。
2. `no_extra_body_fields`：解析用例请求体 JSON，字段集合 ⊆ schema.properties 键集合。
3. `required_fields_present`：schema.required 字段全部出现。
4. `enum_valid`：枚举字段取值 ∈ schema 枚举。
5. `assertion_fields_exist`：用例 assertions 中引用的响应字段存在于 `endpoint.response_schema`（按 jsonpath 前缀匹配；response_schema 为空时跳过该项并标记 `skipped`）。

**挂载点**：`PipelineRunner` 写库后处理（机制2/3 一起跑）；也为「验证」接口与手动重生成复用同一函数。

**写入**：`TestCase.consistency_report`（JSON 字符串）。

---

## 四、机制 3 · 置信度分级 + 人工复核

**新增函数 `score_confidence(consistency: dict, case: dict) -> (float, str)`**（放在 `api_consistency.py` 或独立 `confidence.py`）：

规则打分（0~1）：
- 一致性通过率占 60%：`pass_rate`（机制2 输出，跳过项按通过计）。
- 用例完整度占 30%：preconditions / steps / expected_results 是否齐全（每项缺失扣分）。
- 测试类别权重占 10%：边界值/异常类天然不确定性高，基准分 ×0.9；功能/场景类 ×1.0。

等级映射：`>=0.8 → high`，`0.5~0.8 → medium`，`<0.5 → low`。

**写入**：`TestCase.confidence_score`(float) + `TestCase.confidence_level`(str)。

**前端**：低置信度（low/medium）用例在列表/详情高亮，tooltip 展示扣分原因（来自 consistency_report + 完整度检查）。

---

## 五、机制 4 · 手动「验证」按钮（真实执行）

**新增路由 `POST /api/testcases/{id}/validate`**（`backend/app/routes/testcase.py` 或 `api_test_tool.py`）：
- 入参：可选 `environment_id`（默认用 `ApiProject.environment_id`）。
- 取 TestCase：`api_endpoint_id` / `api_project_id` / `assertions` / 关联 `ApiScenario`（若有 `scenario_id` 直接复用，否则按 endpoint + 用例 headers/body/params/assertions 临时构建 scenario steps）。
- 调用 `scenario_runner.run_scenario(db, scenario, project, session_headers)` 实跑。
- 结果回写：`validation_status`(passed/failed/error)、`validation_detail`(JSON：status_code、响应摘要、各断言通过情况、错误信息)、`validation_at`(UTC+8)。

**前端**：
- API 用例行显示「验证」按钮；点击后调接口，loading 态。
- 结果以徽章展示 passed/failed；failed 可展开看实际响应与失败断言，辅助修正。
- `validation_status` 持久展示（再次生成/重生成后重置为 pending）。

**前置要求**：API 可达 + 环境参数/鉴权就绪（project.base_url、environment_id）。不可达时 `validation_status=error`，detail 说明原因，不误标失败。

---

## 六、数据模型变更（TestCase 新增 6 字段）

`backend/db/models.py` 的 `TestCase`（约 77-99 行）末尾新增：
```python
consistency_report: Optional[str] = Field(default=None, description="一致性检查报告(JSON)")
confidence_score: Optional[float] = Field(default=None, description="置信度评分 0~1")
confidence_level: Optional[str] = Field(default=None, description="置信度等级: high/medium/low")
validation_status: Optional[str] = Field(default=None, description="验证状态: pending/passed/failed/error")
validation_detail: Optional[str] = Field(default=None, description="验证结果详情(JSON)")
validation_at: Optional[datetime] = Field(default=None, description="验证执行时间(UTC+8)")
```
自动迁移机制（`backend/db/db.py` 启动时 ALTER TABLE）会补齐，无需手写 alembic。

---

## 七、前端改动

`frontend/src/components/test-case/`：
- `TestCaseTable.tsx` / `TestcaseDetailView.tsx`：用例行增加三列/徽章——一致性通过率、置信度等级、验证状态；「验证」按钮（仅 `api_endpoint_id` 非空时显示）。
- `RequirementTestPointManager.tsx` / `TestCaseGenerator.tsx`：重生成后展示一致性/置信度。
- `types/index.ts`：TestCase 接口补 6 字段；`services/api.ts` 补 `validateTestcase(id, envId?)`。
- 低置信度/一致性失败用例高亮 + tooltip（扣分原因）。

---

## 八、错误处理与边界

- 一致性检查：endpoint 无 `request_schema` 时，`no_extra_body_fields`/`required_fields_present` 标记为 `skipped`，不拉低通过率；`assertion_fields_exist` 同理。
- 验证接口：API 不可达/超时 → `validation_status=error`，detail 含异常信息，不影响用例本身。
- 重生成/就地更新：新生成后 `validation_status` 重置为 `pending`，`consistency_report`/`confidence_*` 重新计算覆盖。

## 九、测试

- `backend/tests/test_api_consistency.py`：单测 `check_testcase_consistency`——构造含多余字段/缺必填/枚举非法/断言字段缺失的用例，断言各项 `passed` 与 `pass_rate`。
- `backend/tests/test_confidence.py`：单测 `score_confidence` 等级映射与权重。
- `backend/tests/test_validate_endpoint.py`：集成测试「验证」接口——用本地 mock http server（或 `respx` 拦截 `scenario_runner` 的 httpx），断言 `validation_status` 回写与 detail 结构。
- 前端：`npm run lint` 通过。

## 十、实施顺序（建议任务拆分）

1. 数据模型 + 自动迁移（TestCase 6 字段）。
2. 机制1：请求骨架 + prompt 硬约束。
3. 机制2：一致性检查服务 + 挂载到 pipeline 后处理。
4. 机制3：置信度打分 + 挂载。
5. 机制4：验证路由 + 复用 run_scenario + 前端按钮与回显。
6. 前端展示（徽章/高亮/tooltip）+ 测试。
