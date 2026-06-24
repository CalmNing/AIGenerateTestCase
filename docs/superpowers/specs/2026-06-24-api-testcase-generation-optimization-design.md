# API 关联测试用例生成功能优化设计

## 概述

优化"选择 API 接口后生成接口测试用例"的完整链路：前端选择接口 → Prompt 构建 → LLM 生成 → 用例保存 → 执行执行 → 结果展示。修复现有 Bug，提升各环节的可用性和用户体验。

## 问题清单

| # | 严重度 | 位置 | 问题 |
|---|--------|------|------|
| P0 | 🔴 崩溃 | `testcase.py:374` | `api_context = ""` 缩进错误，位于 `if selected_skills` 块内。当未选技能且接口加载失败时，第 441 行 `if api_context:` 引发 `NameError` |
| P1 | 🟡 Prompt | `testcase.py:438-442` | API Schema 信息拼接到需求**末尾**，但 SYSTEM_PROMPT 在 agent 调用侧，模型可能先处理需求再看到 API 信息，导致生成的 `api_call` 步骤质量不稳定 |
| P1 | 🟡 可视化 | `TestCaseTable.tsx` | 生成的 `api_call` 步骤在表格中以 JSON 文本显示，用户无法直观看到步骤关联的接口和执行状态 |
| P2 | 🟢 执行反馈 | `TestCaseExecutionLogModal.tsx` | 执行日志展示为原始 JSON，缺少结构化的步骤级展示 |
| P2 | 🟢 编辑丢失 | `EditTestcaseModal.tsx` | 编辑测试用例时，api_call 步骤不包含 `endpoint_id`，保存后关联丢失 |
| P2 | 🟢 参数预览 | `TestCaseGenerator.tsx` | 编辑的接口参数在生成前无最终确认步骤 |

## 架构决策

### Prompt 结构重排

**当前问题**：API 信息拼接在 requirement 末尾，LLM 可能优先处理前文需求而忽略末尾的接口 Schema。

**改造方案**：将 API 接口信息放在 requirement **前面**，格式为：

```
## 可用 API 接口

以下 API 接口可用于本测试用例生成。每个接口有唯一编号 [1], [2]...

[接口1 Schema]
[接口2 Schema]

## 用户需求

[原始需求文本]
```

**依据**：LLM 对上下文前部的信息关注度更高。将 API 定义前置可以让模型在分析需求时已经了解可用接口。

### 执行结果结构化展示

前端 `TestCaseExecutionLogModal` 改造为步骤级展示，每个步骤显示：
- 步骤序号
- 关联的接口名称 + 方法 + 路径
- 请求/响应摘要（可展开）
- 断言结果（通过/失败/未执行）
- 状态标签（通过/失败/错误）

### 编辑 TestCase 的 api_call 步骤保留 endpoint_id

`EditTestcaseModal` 保存 api_call 步骤时必须包含 `endpoint_id`，从 `selectedTestcase.api_endpoint_id` 或现有步骤中提取。

## 改动范围

### 后端（`backend/app/routes/testcase.py`）

1. **修复 P0 Bug**：将 `api_context = ""` 移至函数体级别缩进 (line 374, indent 4→4)
2. **Prompt 结构重排**：API 信息前置到 requirement 前面（`api_context + requirement`）
3. **增强错误信息**：生成失败时返回更详细的错误上下文（接口加载失败原因等）

### 后端（`backend/app/routes/testcase.py` - 执行部分）

4. **执行结果增强**：`execute_testcase` 端点返回的 result 中包含更详细的步骤信息（接口名、路径、方法等）

### 后端（`backend/utils/model_utils.py`）

5. **SYSTEM_PROMPT 优化**：强化关于 `api_endpoint_ref` 的使用说明，减少模型生成无效 ref 的概率

### 前端 - TestCaseGenerator

6. **参数预览确认**：生成前弹窗汇总显示已选接口和参数覆盖情况，用户确认后发送

### 前端 - TestCaseTable

7. **api_call 步骤可视化**：表格中 `api_call` 步骤显示为可展开的卡片（方法标签 + 路径 + 简要描述），而非原始 JSON

### 前端 - EditTestcaseModal

8. **保留 endpoint_id**：编辑保存时在 api_call 步骤中包含 `endpoint_id`

### 前端 - TestCaseExecutionLogModal

9. **结构化工执行日志**：步骤级展示请求/响应/断言结果

## 数据流

```
用户选择接口 + 输入需求
  → [前端] 参数预览确认
  → [后端] 构建 Prompt（API 信息前置）
  → [后端] 调用 LLM 生成（或代理方式）
  → [后端] 解析生成结果，映射 endpoint_index_to_id
  → [后端] 保存 TestCase（含 api_endpoint_id + 结构化步骤）
  → [前端] 刷新列表，显示 api_call 步骤卡片

执行测试用例
  → [后端] _build_testcase_execution_plan
  → [后端] 逐个执行步骤
  → [后端] 返回步骤级执行结果
  → [前端] TestCaseExecutionLogModal 结构化工展示
```

## 不受影响的范围

- 数据库模型（`db/models.py`）无需修改
- Axios API 层（`frontend/src/services/api.ts`）无需修改
- 认证和权限逻辑无需修改
- 非 API 相关的测试用例生成流程不受影响

## 工作分解

1. 修复 P0 Bug（`api_context` 缩进）
2. Prompt 结构重排（API 信息前置）
3. SYSTEM_PROMPT 优化（强化 api_endpoint_ref 说明）
4. TestCaseTable api_call 步骤可视化
5. TestCaseExecutionLogModal 结构化展示
6. EditTestcaseModal 保留 endpoint_id
7. TestCaseGenerator 参数预览确认
8. 验证和集成测试
