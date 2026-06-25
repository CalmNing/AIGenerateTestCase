# 用例只关联场景、不存快照 设计

**日期:** 2026-06-25
**范围:** 生成流程、执行流程、前端展示

---

## 核心原则

用例生成接口测试场景的目的是**快速创建接口测试场景**。用例和场景的数据是同一份，用例不存储 api_call 步骤的快照。

## 数据模型

**TestCase 不变：**
- `scenario_id` — 关联场景
- `steps` — 只存文本步骤（非 api_call）
- `preset_conditions` — 只存文本步骤（非 api_call）

**ApiScenario 不变：**
- `steps` — api_call 步骤的唯一数据源

## 改动点

### 1. 生成流程 — `backend/utils/model_utils.py`

当前：生成时 api_call 步骤同时写入 `testcase.steps` 和 `scenario.steps`。
改后：api_call 步骤**只写入 scenario.steps**，`testcase.steps` 和 `testcase.preset_conditions` 中不包含 api_call 步骤。

具体逻辑（约 line 1000-1050）：
1. `converted_preset_conditions` 中过滤掉 `type=api_call` 的步骤，只保留文本步骤
2. `converted_steps` 中过滤掉 `type=api_call` 的步骤，只保留文本步骤
3. 场景创建逻辑不变（仍然用完整的 api_call 步骤创建 scenario）
4. `scenario_id` 赋值给 testcase

### 2. 执行流程 — `backend/app/routes/testcase.py`

已改好：有 `scenario_id` 时走 `run_scenario`。无需再改。

### 3. 前端展示 — `TestCaseTable.tsx`

"接口"列当前从 `testcase.steps` 读取 api_call 步骤。
改后：从 `scenario.steps` 读取。需要在列表加载时批量获取关联的 scenario 数据。

方案：
- 后端 `GET /{session_id}/testcases` 返回的 testcase 列表中，内联 scenario 的 steps 摘要（method + path）
- 或者前端单独请求 scenario 列表（增加 API 调用）

推荐：后端在返回 testcase 列表时，如果有 scenario_id，附带 scenario 的 steps 摘要。

### 4. 前端展示 — `TestcaseDetailView.tsx`

"用例详情" tab 中：
- 文本步骤（非 api_call）正常显示
- 不再尝试从 testcase.steps 读取 api_call 步骤
- 接口相关信息通过"接口编排" tab 查看（已有）

### 5. 旧数据兼容

有 api_call 步骤但无 scenario_id 的旧用例：
- 执行时走原来的 `_build_testcase_execution_plan` 逻辑（已有）
- 展示时照常显示 api_call 步骤（已有逻辑）
- 不做自动迁移

## 不变的部分

- ApiScenario 模型
- 场景编辑（接口编排 tab）
- 场景执行逻辑
- 用例的文本步骤字段

## 验证

1. 新生成的用例：testcase.steps 不含 api_call，scenario.steps 包含完整 api_call
2. 执行用例：读取 scenario 最新步骤
3. 列表页"接口"列：显示 scenario 中的接口路径
4. 旧用例：保持原逻辑可执行
