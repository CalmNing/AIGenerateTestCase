# API 关联测试用例生成功能优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 API 关联测试用例生成功能的 Bug，优化 Prompt 结构、前端可视化和编辑能力

**Architecture:** 后端修复 `api_context` 缩进 Bug 并重排 Prompt 结构（API 信息前置）；前端增强 TestCaseTable 的 api_call 步骤卡片展示、EditTestcaseModal 保留 endpoint_id、TestCaseGenerator 参数预览确认

**Tech Stack:** Python 3.11 + FastAPI (backend), React 18 + TypeScript + Ant Design (frontend)

---

## 文件改动总览

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `backend/app/routes/testcase.py` | 修改 | 修复 P0 Bug、Prompt 结构重排、执行结果增强 |
| `backend/utils/model_utils.py` | 修改 | SYSTEM_PROMPT 优化 |
| `frontend/src/components/TestCaseGenerator.tsx` | 修改 | 新增参数预览确认弹窗 |
| `frontend/src/components/TestCaseTable.tsx` | 修改 | api_call 步骤可视化（卡片展示） |
| `frontend/src/components/modals/EditTestcaseModal.tsx` | 修改 | 保存时保留 endpoint_id |
| `frontend/src/components/modals/TestCaseExecutionLogModal.tsx` | 修改 | 小幅优化步骤展示 |
| `frontend/src/App.tsx` | 修改 | 透传新的 props / state |

### Task 1: 修复 P0 Bug + Prompt 结构重排（后端）

**Files:**
- Modify: `backend/app/routes/testcase.py:364-442`

- [ ] **Step 1: 修复 `api_context = ""` 缩进 Bug**

将 `api_context = ""` 从 `if selected_skills` 块内部（indent=8）移到函数体级别（indent=4）：

```python
    # 解析选中的技能名称
    selected_skill_names = []
    if selected_skills and selected_skills.strip():
        try:
            parsed_skills = json.loads(selected_skills)
            if isinstance(parsed_skills, list):
                selected_skill_names = parsed_skills
                logger.info(f"接收到 {len(selected_skill_names)} 个选中技能: {selected_skill_names}")
        except json.JSONDecodeError:
            logger.warning(f"Skills 配置解析失败: {selected_skills[:200]}")

    api_context = ""
    api_endpoint_ids = []
```

- [ ] **Step 2: Prompt 结构重排**

将 API 信息从 requirement 末尾移到前面。在构建 `effective_requirement` 处（line 440-442）改为：

```python
    if api_context:
        effective_requirement = api_context + "\n\n## 用户需求\n\n" + requirement
    else:
        effective_requirement = requirement
```

这样 LLM 先看到 API 接口定义，再看到需求文本。

- [ ] **Step 3: 增强执行结果中的步骤信息**

在 `testcase.py` 的 `execute_testcase` 函数的 `_build_testcase_execution_plan` 调用处（约 line 665），确保 plan 中的每一项都包含完整的 `testcase_step` 信息。当前代码已包含，无需额外修改。

但需要在 `api_test_tool.py` 的 `run_endpoint_steps` 返回结果中增强 endpoint 信息。检查当前 `_execute_endpoint_step` 返回结果（line 864-878），已有 `endpoint_id`、`endpoint_name`、`endpoint_method`、`endpoint_path`，满足需求。

- [ ] **Step 4: 编译验证**

Run: `python -m py_compile backend/app/routes/testcase.py`
Expected: 无错误输出

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/testcase.py
git commit -m "fix: 修复 api_context 缩进 Bug 并重排 Prompt 结构"
```

### Task 2: SYSTEM_PROMPT 优化（后端）

**Files:**
- Modify: `backend/utils/model_utils.py:924-992`

- [ ] **Step 1: 强化 `api_endpoint_ref` 使用说明**

在 SYSTEM_PROMPT 的"当提供了 API 接口信息时"段落中（约 line 924-992），增加关于 `api_endpoint_ref` 和 `api_call` 步骤的清晰示例。找到以下段落并替换：

当前内容（line 943-948）：
```
当生成 API 接口相关的测试步骤时，使用结构化的 api_call 步骤代替纯文本步骤：
- api_call 步骤包含 endpoint_ref、body、headers、parameters、variables 等字段
- 在 body 中使用完整的 JSON 字符串，需要变量替换的地方用 {{变量名}} 语法
```

替换为：
```
当生成 API 接口相关的测试步骤时，使用结构化的 api_call 步骤代替纯文本步骤：
- api_call 步骤包含 endpoint_ref（接口编号，如 1）、body、headers、parameters、variables 等字段
- 每个 api_call 步骤必须设置 endpoint_ref，值为对应接口在 "===== [N] 接口名 =====" 中的数字 N
- 在 body 中使用完整的 JSON 字符串，需要变量替换的地方用 {{变量名}} 语法
- 通过 variables 定义步骤变量，在 body/parameters/headers 中用 {{变量名}} 引用
- 前置条件中的 api_call 步骤使用 preset_conditions 字段，格式与 steps 中的 api_call 一致
- 前置条件中提取的变量对主测试步骤自动可用
```

- [ ] **Step 2: 编译验证**

Run: `python -m py_compile backend/utils/model_utils.py`
Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add backend/utils/model_utils.py
git commit -m "feat: 优化 SYSTEM_PROMPT 中 api_call 步骤生成说明"
```

### Task 3: TestCaseTable api_call 步骤可视化（前端）

**Files:**
- Modify: `frontend/src/components/TestCaseTable.tsx`

- [ ] **Step 1: 增强步骤展示 render**

在 TestCaseTable 中，找到 `testcaseColumns` 数组。在"用例名称"列之后新增一列"接口"（可选），或在当前列中展示 api_call 标记。

更好的方案：在"操作"列前插入一列"接口"，显示该用例关联的 API 步骤摘要。

找到 columns 定义（约 line 42-125），在"级别"列之后、"状态"列之前插入新列：

```typescript
{
  title: '接口',
  key: 'api_steps',
  width: 180,
  render: (_: any, record: TestCase) => {
    // 收集所有 api_call 步骤
    const allSteps = [
      ...(record.preset_conditions || []),
      ...(record.steps || []),
    ].filter(s => typeof s === 'object' && s.type === 'api_call');
    if (allSteps.length === 0) {
      // 没有 api_call 步骤，显示 api_endpoint_id
      if (!record.api_endpoint_id) return <span style={{ color: 'var(--color-text-disabled)', fontSize: 12 }}>-</span>;
      const ids = String(record.api_endpoint_id).split(',').map(s => s.trim()).filter(Boolean);
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {ids.map((id, i) => (
            <Tag key={i} color="default" style={{ fontSize: 11, margin: 0 }}>
              <ApiOutlined style={{ marginRight: 4 }} />接口 #{id}
            </Tag>
          ))}
        </div>
      );
    }
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {allSteps.map((step: any, i: number) => (
          <Tooltip key={i} title={step.description || step.name || `API 步骤 ${i + 1}`}>
            <Tag color="blue" style={{ fontSize: 11, margin: 0, cursor: 'pointer' }}>
              <ApiOutlined style={{ marginRight: 4 }} />
              {step.description || step.name || `步骤 ${i + 1}`}
            </Tag>
          </Tooltip>
        ))}
      </div>
    );
  },
},
```

需要在文件顶部补全 `ApiOutlined` 导入（已在现有导入中）。

- [ ] **Step 2: TypeScript 编译检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TestCaseTable.tsx
git commit -m "feat: TestCaseTable api_call 步骤卡片可视化"
```

### Task 4: EditTestcaseModal 保留 endpoint_id（前端）

**Files:**
- Modify: `frontend/src/components/modals/EditTestcaseModal.tsx`

- [ ] **Step 1: 在保存 api_call 步骤时包含 endpoint_id**

找到 `handleFinish` 函数中创建 api_call 步骤的部分（约 line 161-166），当前代码：

```typescript
if (apiCallEnabled && selectedTestcase?.api_endpoint_id) {
  const validHeaders = headers.filter(h => h.key.trim() !== '');
  steps.push({
    type: 'api_call',
    headers: validHeaders,
    body: body || undefined,
    environment_id: environmentId
  });
}
```

替换为：

```typescript
if (apiCallEnabled && selectedTestcase?.api_endpoint_id) {
  const validHeaders = headers.filter(h => h.key.trim() !== '');
  // 从现有 api_call 步骤中提取 endpoint_id，或从 api_endpoint_id 取第一个
  const existingApiCall = Array.isArray(selectedTestcase.steps)
    ? selectedTestcase.steps.find(s => typeof s === 'object' && s.type === 'api_call')
    : undefined;
  const existingEndpointId = existingApiCall
    ? (existingApiCall as any).endpoint_id
    : firstEndpointId(selectedTestcase.api_endpoint_id);
  steps.push({
    type: 'api_call',
    endpoint_id: existingEndpointId,
    headers: validHeaders,
    body: body || undefined,
    environment_id: environmentId
  });
}
```

- [ ] **Step 2: TypeScript 编译检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/modals/EditTestcaseModal.tsx
git commit -m "fix: EditTestcaseModal 保存 api_call 步骤时保留 endpoint_id"
```

### Task 5: TestCaseGenerator 参数预览确认（前端）

**Files:**
- Modify: `frontend/src/components/TestCaseGenerator.tsx`

- [ ] **Step 1: 新增生成前预览确认弹窗**

在 TestCaseGenerator 中新增状态和控制：

```typescript
const [previewVisible, setPreviewVisible] = useState(false);

// 在点击"AI 生成测试用例"时，如果有 API 接口，先弹出预览确认
const handleGenerateWithPreview = () => {
  if (selectedApiEndpointId && selectedApiEndpointId.length > 0) {
    setPreviewVisible(true);
  } else {
    onGenerate();
  }
};
```

- [ ] **Step 2: 添加预览确认 Modal**

在 return 中的 API Override Editor Modal 之后（约 line 448），添加预览确认 Modal：

```tsx
<Modal
  title="确认生成测试用例"
  open={previewVisible}
  onCancel={() => setPreviewVisible(false)}
  width={640}
  footer={[
    <button key="cancel" className="tcg-smart-btn" onClick={() => setPreviewVisible(false)}>
      取消
    </button>,
    <button
      key="confirm"
      className="tcg-smart-btn"
      style={{ background: 'var(--color-primary)', color: '#fff', border: 'none' }}
      onClick={() => {
        setPreviewVisible(false);
        onGenerate();
      }}
    >
      确认生成
    </button>,
  ]}
>
  <div style={{ marginBottom: 16 }}>
    <div style={{ fontWeight: 600, marginBottom: 8 }}>
      <ApiOutlined style={{ marginRight: 8 }} />已选接口（{selectedApiEndpointId?.length || 0} 个）
    </div>
    {selectedApiEndpointId?.map((eid) => {
      const ep = apiEndpoints.find(e => e.id === eid);
      if (!ep) return null;
      const color = methodColorMap[ep.method?.toUpperCase()] || 'default';
      const hasOverrides = apiEndpointOverrides?.[eid] && (
        apiEndpointOverrides[eid]?.body ||
        (apiEndpointOverrides[eid]?.headers?.length || 0) > 0 ||
        (apiEndpointOverrides[eid]?.parameters?.length || 0) > 0
      );
      return (
        <div key={eid} style={{ padding: '8px 0', borderBottom: '1px solid var(--color-border-secondary)' }}>
          <Tag color={color}>{ep.method?.toUpperCase()}</Tag>
          <span style={{ fontWeight: 500 }}>{ep.path}</span>
          <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-text-tertiary)' }}>{ep.name}</span>
          {hasOverrides && <Tag color="warning" style={{ marginLeft: 8 }}>已自定义参数</Tag>}
        </div>
      );
    })}
  </div>
  <div style={{ fontSize: 13, color: 'var(--color-text-tertiary)' }}>
    AI 将根据以上接口信息和您的需求描述生成包含可执行 API 调用步骤的测试用例。
  </div>
</Modal>
```

需要在文件顶部补全 `ApiOutlined` 导入（已在现有导入中）。

- [ ] **Step 3: 绑定生成按钮**

将现有"AI 生成测试用例"按钮的 `onClick` 从 `onGenerate` 改为 `handleGenerateWithPreview`。找到约 line 300-316 的按钮：

将:
```tsx
onClick={onGenerate}
```
改为:
```tsx
onClick={handleGenerateWithPreview}
```

- [ ] **Step 4: TypeScript 编译检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TestCaseGenerator.tsx
git commit -m "feat: TestCaseGenerator 生成前接口参数预览确认"
```

### Task 6: 全链路验证

- [ ] **Step 1: 验证所有后端文件编译**

Run: `python -m py_compile backend/app/routes/testcase.py && python -m py_compile backend/utils/model_utils.py`
Expected: 无错误输出

- [ ] **Step 2: 验证前端编译**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误输出

- [ ] **Step 3: 验证前端 lint**

Run: `cd frontend && npm run lint`
Expected: 无错误输出

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "feat: 优化 API 关联测试用例生成全链路体验"
```
