# 实现计划：拆分静态/动态前缀以提升生成用例缓存命中率

> 依据 `docs/cache-hit-root-cause.md` 的根因分析。
> 目标：把「大段可复用内容（接口规格 api_context、技能正文 skill）」从可变 human 消息移到静态 system 前缀，使同一接口项目/技能组合反复生成时，前缀 KV 被缓存命中。

## 根因回顾（一句话）
当前 `api_context` 在路由层被拼进可变 `requirement`（human 消息）顶部，`skill_context` 在 `generate_testcases` 里被 prepend 进 `requirement`。结果：缓存前缀只有 ~4000 token 的 `selected_prompt`，而输入主体（规格/技能）全在可变区 → 命中率极低。

## 设计要点
- **新增 helper** `_build_generation_messages(*, selected_prompt, core_requirement, selected_skill_names, api_context="", is_api_test=False) -> (system_text, human_text)`
  - `system_text` = selected_prompt +（可选）技能方法论段落 +（可选）接口上下文段落 —— **稳定、可缓存**
  - `human_text` = 用户真实需求（接口测试时加「## 补充背景（次要）」标签）—— **可变**
- 三条生成路径（直连 function_calling / agent / ollama）统一使用 `full_system` 作为 system、 `human_text` 作为 human。
- 路由层：停止把 `api_context` 并入 `effective_requirement`，改为单独传参 `api_context=`；`requirement` 仅传用户原文。

## 任务分解（TDD：先写失败测试，再实现）

### T1. 抽取并测试 `_build_generation_messages`（Red → Green）
- 文件：`backend/tests/test_cache_prefix.py`（新建）
- 失败测试：
  1. `api_context` 进入 system、不进入 human（接口测试）
  2. `selected_skill_names` 的技能正文进入 system
  3. 相同 `api_context`+技能+`selected_prompt` 两次调用 → `system_text` 逐字节一致（验证缓存前缀稳定）
  4. 功能性测试（`is_api_test=False`）→ human == core_requirement（无补充背景标签）
  5. 无 `api_context` → system == selected_prompt（无接口段落）

### T2. 改 `generate_testcases` 签名与消息组装（Green）
- `backend/utils/model_utils.py`
  - `generate_testcases` 增加 `api_context: str = ""` 参数（默认空，向后兼容）。
  - 重构 `requirement` 构造：保留 history + parsed/raw 逻辑，**但不再把 skill / api_context 塞进 `requirement`**。
  - 用 `_build_generation_messages` 生成 `full_system` / `human_text`。
  - 直连路径 `messages=[("system", full_system), ("human", human_text)]`
  - agent 路径：`create_testcase_agent(system_prompt=full_system, ...)`，`agent.ainvoke` 的 user content = `human_text`
  - ollama 路径：`messages=[("system", full_system), ("system", schema_note), ("human", human_text)]`
  - `_persist_testcases(requirement=human_text, ...)`（历史保存用户真实需求，去除重复上下文）

### T3. 路由层传参（Green）
- `backend/app/routes/testcase.py`
  - 删除 `effective_requirement = api_context + "## 补充背景..." + requirement` 合并逻辑。
  - `generate_testcases(..., requirement=requirement, api_context=api_context, is_api_test=bool(api_context))`

### T4. 验证（Verify）
- `pytest tests/test_cache_prefix.py -q` 全绿
- `pytest tests/ -q` 全量无回归（重点 `test_generate_testcases.py`、`test_llm_factory.py`）
- 导入冒烟：`import utils.model_utils`

## 风险与边界
- **不影响前端**：`api_context` 由服务端 `build_api_context` 从 `api_endpoint_ids` 生成，前端无需改动。
- **向后兼容**：`api_context` 默认 `""`，现有测试（positional/keyword 调用）不受影响。
- **历史提示词**：保存的 `requirement` 改为用户真实需求（不含 skill/api 冗余），HistoryPrompt 去重更干净。
- **不改动缓存开关/埋点**（上轮已完成）：`enable_prompt_cache` + `[缓存命中]` 日志保持不变。

## 预期收益
同一接口项目连续生成时，大段 `api_context`（常数千~上万 token）进入稳定前缀被命中；同一技能组合下技能正文也被命中。命中率从「仅 ~4000 token 系统前缀」提升至「系统 + 技能 + 接口规格」整体前缀，接口测试场景收益最大。功能性测试（纯自由文本需求）因需求本身可变，收益有限——这是输入结构决定的理论上限，符合预期。
