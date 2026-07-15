# 实现计划：generate-v2 阶段1/2 显式注入接口 Schema

> 遵循 design.md 选定方案。每个任务 2-5 分钟，含验证步骤。

## 任务清单

### T1. 新增 API 提示词（prompts.py）
文件：`backend/app/pipeline/prompts.py`
- 新增 `API_REQUIREMENT_SPLITTER_SYSTEM`：定位为「接口测试需求分析专家」，强调**基于 API Schema 拆分需求点**、每个需求点对应一个可独立测试的接口能力、必须用 `[接口编号]` 引用接口、严格遵循「请求骨架-硬约束」、不得编造 schema 外字段/路径。
- 新增 `API_REQUIREMENT_ANALYSIS_USER`：仅含**用户文本**占位（用 `{aux}`），API schema 由调用方通过字符串拼接拼在前面，**禁止对此常量使用 `.format()`**（api_context 含 JSON 大括号会炸）。
- 新增 `API_TEST_POINT_GENERATOR_SYSTEM`：定位为「接口测试点生成专家」，强调围绕接口的字段/方法/路径展开测试点、复用阶段3 的测试类别与设计方法、接口测试不生成性能用例。
- 新增 `API_TEST_POINT_USER`：仅含**需求点内容**占位（用 `{content}`），API schema 同样由调用方拼接。

### T2. 改造 RequirementSplitter.analyze（显式参数）
文件：`backend/app/pipeline/requirement_splitter.py`
- 签名改为 `async def analyze(self, requirement_text, *, is_api_test=False, api_context="")`。
- API 模式（`is_api_test and api_context`）：
  - `system` 用 `API_REQUIREMENT_SPLITTER_SYSTEM`；
  - `user` 用**字符串拼接**：`"===== API 接口信息（需求拆分主要依据）=====\n\n" + api_context + "\n\n## 用户补充说明（次要，仅用于理解业务语境，不得脱离上述接口信息编造字段/路径）\n\n" + (requirement_text or "(无用户补充说明)")`。
- 非 API 模式：保持原 `REQUIREMENT_ANALYSIS_USER.format(content=requirement_text)` 不变。
- 其余（结构化输出、优先级标准化）不变。

### T3. 改造 TestPointGenerator.generate（显式参数）
文件：`backend/app/pipeline/test_point_generator.py`
- 签名改为 `async def generate(self, requirement_content, *, is_api_test=False, api_context="")`。
- API 模式：`system` 用 `API_TEST_POINT_GENERATOR_SYSTEM`；`user` 用拼接：`api_context`（主体）+ 需求点内容（辅助）。
- 非 API 模式：保持原 `TEST_POINT_USER.format(content=requirement_content)`。

### T4. PipelineRunner 调用点透传
文件：`backend/app/pipeline/pipeline_runner.py`
- 阶段1（~411）：`self.splitter.analyze(requirement_text, is_api_test=is_api_test, api_context=api_context)`。
- 阶段2（~447）：`self.tp_generator.generate(rp.content, is_api_test=is_api_test, api_context=api_context)`。
- 就地重生成（_inplace_update_test_points ~1097）：`self.tp_generator.generate(requirement_point.content, is_api_test=is_api_test, api_context=api_context)`。
- 阶段3（`design_batch`）已接收 `api_context`/`is_api_test`，无需改动。

### T5. TDD 测试（Red→Green 产物）
文件：`backend/tests/test_pipeline_api_context.py`
- mock `utils.model_utils._structured_output_robust`，捕获 `messages`。
- 断言 API 模式下 analyze/generate 构造的 messages 同时包含 `api_context` 主体信息与用户文本辅助信息。
- 注：本沙箱系统 Python 导入 langchain 会 segfault，无法本地运行；保留为 CI 产物，主验证用运行中的 `--reload` 后端 + curl。

### T6. 端到端验证（权威校验）
- 用运行中的后端（已 `--reload`）：`POST /api/testcases/generate-v2`，`requirement_text=1`、`api_endpoint_id=35,...`、`api_project_id=2`。
- 轮询 `/api/testcases/pipeline/{task_id}/status`，确认阶段1 不再 `未生成任何需求点`，最终 `status=completed` 且数据落地（需求点内容体现接口 schema）。
- 对比：修复前同样输入会在阶段1 failed。

### T7. 代码评审与收尾
- 对照本计划逐条评审；更新项目记忆；present 改动文件。

## 验证红线
- 功能测试（无 api_endpoint_id）行为零回归。
- 接口测试 + 退化文本：从「阶段1 failed」变为「completed 且 schema 驱动」。
