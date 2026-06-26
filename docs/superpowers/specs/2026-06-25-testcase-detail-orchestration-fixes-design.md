# 用例详情 & 接口编排修复设计

**日期:** 2026-06-25
**范围:** ViewTestcaseModal、TestcaseDetailView、TestcaseScenarioView、后端 config、api_test_tool bug 修复

---

## 1. A1: footer "执行" → "标记完成"

**问题:** ViewTestcaseModal footer 的"执行"按钮调用 `onComplete`（标记用例状态），但"接口编排" tab 里也有自己的"执行"按钮（运行场景），用户容易混淆。

**方案:** 将 footer 按钮文案从"执行"改为 **"标记完成"**，图标改为 `CheckCircleOutlined`。`onComplete` 回调逻辑不变。

**改动文件:**
- `frontend/src/components/modals/ViewTestcaseModal.tsx` — 按钮文案和图标

---

## 2. A2: 场景执行结果持久化

**问题:** TestcaseScenarioView 的执行结果存在本地 state，切 tab 或关闭 modal 后丢失。

**方案:**
1. 组件 mount 时调用 `apiTestApi.getScenarioResults(scenarioId)` 加载最近一次执行结果
2. 执行场景后，result 存本地 state（立即显示），后端已自动持久化（ApiScenarioResult）
3. 在执行结果区域显示来源标签："当前执行" vs "上次结果"

**改动文件:**
- `frontend/src/components/TestcaseScenarioView.tsx` — 加载历史结果逻辑

**依赖:** 已有 `GET /api-test/scenarios/{id}/results` 接口和 `apiTestApi.getScenarioResults()` 方法。

---

## 3. A3: 场景完整编辑

**问题:** TestcaseScenarioView 有"查看/编辑"切换按钮，但 editing 状态从未被使用，没有任何编辑 UI。

**方案:** 在编辑模式下，将步骤列表从只读 Collapse 变为可编辑表单。

### 3.1 步骤编辑表单

每个步骤展开后显示以下可编辑字段：

| 字段 | 控件 | 说明 |
|------|------|------|
| 步骤名称 | `<Input>` | step.name |
| 启用/禁用 | `<Switch>` | step.enabled |
| 失败继续 | `<Switch>` | step.continue_on_failure |
| URL | `<Input>` | step.url，带方法 Tag 前缀 |
| 请求头 | KeyValueEditor | step.headers，key-value 行列表 |
| 请求参数 | KeyValueEditor | step.parameters，key-value 行列表 |
| 请求体 | `<Input.TextArea>` | step.body，JSON 格式 |
| 变量提取 | PostActionsEditor | step.post_actions，行列表：key + jsonpath |
| 断言 | AssertionsEditor | step.assertions，行列表：type(Select) + jsonpath + value |

### 3.2 工具栏操作

- **保存** — 调用 `apiTestApi.updateScenario(scenarioId, updatedScenario)`
- **添加步骤** — 新增空步骤到末尾
- **删除步骤** — 每行有删除按钮
- **排序** — 上/下箭头移动步骤顺序

### 3.3 场景基本信息编辑

编辑模式下，场景名称和描述也变为可编辑：
- 场景名称: `<Input>`
- 场景描述: `<Input.TextArea>`

### 3.4 步骤来源选择

添加步骤时，需要从当前项目的接口列表中选择。复用已有的 `apiTestApi.getEndpoints(projectId)` 接口，用 `<Select>` 下拉框选择接口，选中后自动填充 method、url 等默认值。

**改动文件:**
- `frontend/src/components/TestcaseScenarioView.tsx` — 编辑逻辑、表单组件
- `frontend/src/components/TestcaseScenarioView.css` — 编辑模式样式

**依赖:** 已有 `PUT /api-test/scenarios/{id}` 接口和 `apiTestApi.updateScenario()` 方法。

---

## 4. A4: Bug 链接可配置

**问题:** TestcaseDetailView 中 bug_id 链接硬编码为 `http://zt.luban.fit/index.php?m=bug&f=view&bugID=...`。

**方案:** 在后端 config.json 中增加 `bug_link_template` 配置项，前端从配置接口读取。

### 4.1 后端

- `backend/config.py` — `default_config` 增加 `"bug_link_template": ""`
- `backend/app/routes/config.py` — 新增 `GET /config/bug-link-template` 端点
- 模板格式示例: `http://zt.luban.fit/index.php?m=bug&f=view&bugID={bug_id}`
- `{bug_id}` 为占位符，后端返回原始模板字符串

### 4.2 前端

- `TestcaseDetailView` 增加可选 prop `bugLinkTemplate?: string`
- 如果有模板且 `tc.bug_id` 存在，用 `template.replace('{bug_id}', String(tc.bug_id))` 生成链接
- 如果模板为空，fallback 到当前硬编码链接（向后兼容）
- `ViewTestcaseModal` mount 时从 `/config/bug-link-template` 读取配置，传入 TestcaseDetailView

**改动文件:**
- `backend/config.py` — default_config 增加字段
- `backend/app/routes/config.py` — 新增端点
- `frontend/src/components/TestcaseDetailView.tsx` — 接收 prop，动态生成链接
- `frontend/src/components/modals/TestcaseScenarioView.tsx` — 传递 prop
- `frontend/src/services/api.ts` — 增加 configApi.getBugLinkTemplate()
- `frontend/src/components/modals/ViewTestcaseModal.tsx` — 加载配置并传递

---

## 5. B1+B2+B3: 后端 Bug 修复

### B1: _calculate_match_score 字段引用错误

**文件:** `backend/app/services/api_test_tool.py` (line 121)

**问题:** `endpoint.summary` 和 `endpoint.description` 在 `ApiEndpoint` 模型中不存在，运行时会报 `AttributeError`。

**修复:** 改为 `endpoint.name`（ApiEndpoint 有 `name` 字段），fallback 空字符串：
```python
summary = endpoint.name or ""
```

### B2: _schema_properties 重复定义

**文件:** `backend/app/services/api_test_tool.py` (lines 1216, 1418)

**问题:** 函数定义了两次，第二次覆盖第一次。第一个是简单版本（只提取 properties），第二个是完整版本（支持 allOf/oneOf/anyOf）。

**修复:**
1. 保留第二个更完整的版本（line 1418）
2. 删除第一个版本（line 1216-1223）
3. 检查第一个版本的调用处（在 `infer_step_dependencies` 附近），确保改为使用第二个版本

### B3: generate_body_from_schema 不可达代码

**文件:** `backend/app/services/api_test_tool.py` (line 1110)

**问题:** 两个 return 语句连续出现，第二个永远执行不到。

**修复:** 删除 line 1110 的重复 return。

---

## 不在本次范围内

以下问题已记录但不在本次修复范围：
- ApiScenarioTestTool.tsx 2729 行单体组件（重构为独立组件）
- 批量执行并发优化
- 执行结果分页
- 场景条件分支逻辑
