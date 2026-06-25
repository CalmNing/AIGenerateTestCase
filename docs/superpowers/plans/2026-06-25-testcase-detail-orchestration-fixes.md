# 用例详情 & 接口编排修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix UX issues in ViewTestcaseModal (button semantics, result persistence, editing, bug link) and backend bugs in api_test_tool.

**Architecture:** Five independent fixes across frontend components and backend services. Tasks 1-4 are frontend-only or frontend+backend, Task 5 is backend-only. All tasks are independent and can be executed in parallel.

**Tech Stack:** React 18, TypeScript, Ant Design, FastAPI, Python 3.11, SQLModel

## Global Constraints

- TypeScript strict mode enabled (`noUnusedLocals`, `noUnusedParameters`)
- All API responses: `{code: number, message: string, data: T}`
- Backend models use Shanghai timezone (UTC+8)
- Frontend Vite proxy: `/api` → `http://127.0.0.1:8000`

---

### Task 1: A1 — footer "执行" → "标记完成"

**Files:**
- Modify: `frontend/src/components/modals/ViewTestcaseModal.tsx:49-50`

**Interfaces:**
- Produces: `onComplete` callback signature unchanged — downstream consumers unaffected

- [ ] **Step 1: Update button text and icon**

In `ViewTestcaseModal.tsx`, change the footer execute button from:

```tsx
<button key="execute" className="vtm-footer-btn vtm-footer-btn--primary" onClick={() => onComplete(tc)}>
  <CheckOutlined style={{ fontSize: 12 }} /> 执行
</button>
```

To:

```tsx
<button key="execute" className="vtm-footer-btn vtm-footer-btn--primary" onClick={() => onComplete(tc)}>
  <CheckCircleOutlined style={{ fontSize: 12 }} /> 标记完成
</button>
```

- [ ] **Step 2: Update import**

Change the import line to include `CheckCircleOutlined` instead of (or in addition to) `CheckOutlined`:

```tsx
import { CheckCircleOutlined, UpOutlined, DownOutlined } from '@ant-design/icons';
```

Remove `CheckOutlined` from the import if it's no longer used elsewhere in the file.

- [ ] **Step 3: Verify**

Run: `cd frontend && npm run lint`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/modals/ViewTestcaseModal.tsx
git commit -m "fix(ui): rename footer execute button to '标记完成' to avoid confusion with scenario execution"
```

---

### Task 2: A2 — 场景执行结果持久化

**Files:**
- Modify: `frontend/src/components/TestcaseScenarioView.tsx`

**Interfaces:**
- Consumes: `apiTestApi.getScenarioResults(scenarioId, limit)` — returns `ApiResponse<ApiScenarioResult[]>`
- Produces: `result` state holds latest `ApiScenarioResult` (from history or current execution)

- [ ] **Step 1: Add history result loading on mount**

After the existing `loadScenario` useEffect, add a new useEffect to load the latest historical result:

```tsx
// 加载最近一次执行结果
const loadLatestResult = useCallback(async () => {
  if (!scenarioId) return;
  try {
    const res = await apiTestApi.getScenarioResults(scenarioId, 1);
    if (res.code === 200 && res.data && res.data.length > 0) {
      setLatestResult(res.data[0]);
    }
  } catch (error) {
    console.error('加载历史结果失败:', error);
  }
}, [scenarioId]);

useEffect(() => {
  loadLatestResult();
}, [loadLatestResult]);
```

- [ ] **Step 2: Add `latestResult` and `resultSource` state**

Add new state variables alongside the existing `result` state:

```tsx
const [latestResult, setLatestResult] = useState<any>(null);
const [resultSource, setResultSource] = useState<'current' | 'history' | null>(null);
```

- [ ] **Step 3: Update `handleExecute` to set resultSource**

In the `handleExecute` function, after setting `result`, also update `resultSource`:

```tsx
const handleExecute = async () => {
  if (!scenarioId) return;
  setExecuting(true);
  try {
    const res = await apiTestApi.runScenario(scenarioId);
    if (res.code === 200 && res.data) {
      setResult(res.data);
      setResultSource('current');
      setLatestResult(res.data); // update cached latest
      message.success(res.data.passed ? '场景执行通过' : '场景执行失败');
    } else {
      message.error(res.message || '执行失败');
    }
  } catch (error: any) {
    message.error(error.message || '执行失败');
  } finally {
    setExecuting(false);
  }
};
```

- [ ] **Step 4: Update `renderResult` to show source tag and use effective result**

Replace the `renderResult` function to show the result source and use the effective result (current result takes priority over history):

```tsx
const effectiveResult = result || latestResult;
const effectiveSource = result ? 'current' : (latestResult ? 'history' : null);

const renderResult = () => {
  if (!effectiveResult) return null;

  return (
    <div className="scenario-result">
      <div style={{ marginBottom: 8 }}>
        {effectiveSource && (
          <Tag color={effectiveSource === 'current' ? 'blue' : 'default'} style={{ marginRight: 8 }}>
            {effectiveSource === 'current' ? '当前执行' : '上次结果'}
          </Tag>
        )}
        <Tag
          color={effectiveResult.passed ? 'success' : 'error'}
          style={{ fontSize: 14 }}
        >
          {effectiveResult.passed ? '执行通过' : '执行失败'}
        </Tag>
      </div>

      {effectiveResult.steps && effectiveResult.steps.length > 0 && (
        <div>
          <strong>步骤详情:</strong>
          {effectiveResult.steps.map((step: any, index: number) => (
            <div
              key={index}
              style={{
                padding: 8,
                marginTop: 4,
                background:
                  step.status === 'passed'
                    ? 'var(--color-success-bg)'
                    : 'var(--color-danger-bg)',
                borderRadius: 'var(--radius-sm)',
              }}
            >
              <Space>
                <span>{index + 1}.</span>
                <span>{step.name || `步骤 ${index + 1}`}</span>
                <Tag
                  color={step.status === 'passed' ? 'success' : 'error'}
                >
                  {step.status === 'passed' ? '通过' : '失败'}
                </Tag>
                {step.response?.elapsed_ms && (
                  <span style={{ color: 'var(--color-text-tertiary)' }}>
                    {step.response.elapsed_ms}ms
                  </span>
                )}
              </Space>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
```

- [ ] **Step 5: Update JSX to use `effectiveResult`**

In the return JSX, change `{renderResult()}` — it already works since `renderResult` now uses `effectiveResult` internally. No JSX change needed.

- [ ] **Step 6: Verify**

Run: `cd frontend && npm run lint`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/TestcaseScenarioView.tsx
git commit -m "feat(ui): load scenario execution history on mount and show result source"
```

---

### Task 3: A3 — 场景完整编辑

**Files:**
- Modify: `frontend/src/components/TestcaseScenarioView.tsx`
- Modify: `frontend/src/components/TestcaseScenarioView.css`

**Interfaces:**
- Consumes: `apiTestApi.updateScenario(scenarioId, data)` — `PUT /api-test/scenarios/{id}`
- Consumes: `apiTestApi.getEndpoints(projectId)` — `GET /api-test/projects/{id}/endpoints`
- Produces: `editing` state controls view/edit mode toggle; `scenario` state is mutable in edit mode

- [ ] **Step 1: Add editing state variables**

Add these state variables for tracking edits:

```tsx
const [editScenario, setEditScenario] = useState<ApiScenario | null>(null);
const [saving, setSaving] = useState(false);
const [endpoints, setEndpoints] = useState<any[]>([]);
```

- [ ] **Step 2: Load endpoints when entering edit mode**

Add a function to load endpoints for the current project:

```tsx
const loadEndpoints = useCallback(async () => {
  if (!scenario?.project_id) return;
  try {
    const res = await apiTestApi.getEndpoints(scenario.project_id);
    if (res.code === 200 && res.data) {
      setEndpoints(res.data);
    }
  } catch (error) {
    console.error('加载接口列表失败:', error);
  }
}, [scenario?.project_id]);
```

When `editing` becomes true, copy scenario to editScenario and load endpoints:

```tsx
useEffect(() => {
  if (editing && scenario) {
    setEditScenario(JSON.parse(JSON.stringify(scenario))); // deep clone
    loadEndpoints();
  }
}, [editing, scenario, loadEndpoints]);
```

- [ ] **Step 3: Implement save handler**

```tsx
const handleSave = async () => {
  if (!editScenario || !scenarioId) return;
  setSaving(true);
  try {
    const res = await apiTestApi.updateScenario(scenarioId, editScenario);
    if (res.code === 200 && res.data) {
      setScenario(res.data);
      setEditScenario(null);
      setEditing(false);
      message.success('场景保存成功');
    } else {
      message.error(res.message || '保存失败');
    }
  } catch (error: any) {
    message.error(error.message || '保存失败');
  } finally {
    setSaving(false);
  }
};
```

- [ ] **Step 4: Implement step editing helpers**

Add helper functions for step manipulation:

```tsx
const updateStep = (index: number, field: string, value: any) => {
  if (!editScenario) return;
  const newSteps = [...editScenario.steps];
  newSteps[index] = { ...newSteps[index], [field]: value };
  setEditScenario({ ...editScenario, steps: newSteps });
};

const addStep = (endpointId?: number) => {
  if (!editScenario) return;
  const ep = endpoints.find(e => e.id === endpointId);
  const newStep: any = {
    endpoint_id: endpointId || null,
    name: ep?.name || '新步骤',
    enabled: true,
    continue_on_failure: false,
    method: ep?.method || 'GET',
    path: ep?.path || '',
    url: ep?.url || '',
    headers: ep?.headers || [],
    parameters: ep?.parameters || [],
    body: ep?.body || '',
    pre_actions: ep?.pre_actions || [],
    post_actions: ep?.post_actions || [],
    assertions: ep?.assertions || [],
  };
  setEditScenario({
    ...editScenario,
    steps: [...editScenario.steps, newStep],
  });
};

const removeStep = (index: number) => {
  if (!editScenario) return;
  const newSteps = editScenario.steps.filter((_: any, i: number) => i !== index);
  setEditScenario({ ...editScenario, steps: newSteps });
};

const moveStep = (index: number, direction: 'up' | 'down') => {
  if (!editScenario) return;
  const newSteps = [...editScenario.steps];
  const targetIndex = direction === 'up' ? index - 1 : index + 1;
  if (targetIndex < 0 || targetIndex >= newSteps.length) return;
  [newSteps[index], newSteps[targetIndex]] = [newSteps[targetIndex], newSteps[index]];
  setEditScenario({ ...editScenario, steps: newSteps });
};
```

- [ ] **Step 5: Implement inline editor components**

Add these small editor components inside `TestcaseScenarioView.tsx` (before the main component):

```tsx
// Key-Value 行列表编辑器
const KeyValueEditor: React.FC<{
  value: Record<string, string> | Array<{key: string; value: string}>;
  onChange: (val: any) => void;
  keyLabel?: string;
  valueLabel?: string;
}> = ({ value, onChange, keyLabel = 'Key', valueLabel = 'Value' }) => {
  const entries = Array.isArray(value)
    ? value
    : Object.entries(value || {}).map(([k, v]) => ({ key: k, value: String(v) }));

  const updateEntry = (index: number, field: string, val: string) => {
    const newEntries = [...entries];
    newEntries[index] = { ...newEntries[index], [field]: val };
    onChange(newEntries);
  };

  const addEntry = () => {
    onChange([...entries, { key: '', value: '' }]);
  };

  const removeEntry = (index: number) => {
    onChange(entries.filter((_: any, i: number) => i !== index));
  };

  return (
    <div className="kv-editor">
      {entries.map((entry: any, i: number) => (
        <Space key={i} style={{ marginBottom: 4 }}>
          <Input
            size="small"
            placeholder={keyLabel}
            value={entry.key || ''}
            onChange={e => updateEntry(i, 'key', e.target.value)}
            style={{ width: 120 }}
          />
          <Input
            size="small"
            placeholder={valueLabel}
            value={entry.value || ''}
            onChange={e => updateEntry(i, 'value', e.target.value)}
            style={{ width: 200 }}
          />
          <Button size="small" danger type="text" icon={<DeleteOutlined />} onClick={() => removeEntry(i)} />
        </Space>
      ))}
      <Button size="small" type="dashed" onClick={addEntry} style={{ marginTop: 4 }}>
        + 添加
      </Button>
    </div>
  );
};

// PostActions 编辑器
const PostActionsEditor: React.FC<{
  value: any[];
  onChange: (val: any[]) => void;
}> = ({ value, onChange }) => {
  const update = (index: number, field: string, val: string) => {
    const newActions = [...value];
    newActions[index] = { ...newActions[index], [field]: val };
    onChange(newActions);
  };

  return (
    <div className="post-actions-editor">
      {(value || []).map((action: any, i: number) => (
        <Space key={i} style={{ marginBottom: 4 }}>
          <Input
            size="small"
            placeholder="变量名"
            value={action.key || ''}
            onChange={e => update(i, 'key', e.target.value)}
            style={{ width: 120 }}
          />
          <Input
            size="small"
            placeholder="$.jsonpath"
            value={action.jsonpath || ''}
            onChange={e => update(i, 'jsonpath', e.target.value)}
            style={{ width: 200 }}
          />
          <Button size="small" danger type="text" icon={<DeleteOutlined />} onClick={() => onChange(value.filter((_: any, idx: number) => idx !== i))} />
        </Space>
      ))}
      <Button size="small" type="dashed" onClick={() => onChange([...(value || []), { type: 'extract_jsonpath', key: '', jsonpath: '' }])} style={{ marginTop: 4 }}>
        + 添加提取
      </Button>
    </div>
  );
};

// Assertions 编辑器
const AssertionsEditor: React.FC<{
  value: any[];
  onChange: (val: any[]) => void;
}> = ({ value, onChange }) => {
  const update = (index: number, field: string, val: string) => {
    const newAssertions = [...value];
    newAssertions[index] = { ...newAssertions[index], [field]: val };
    onChange(newAssertions);
  };

  const assertionTypes = [
    { label: '状态码', value: 'status_code' },
    { label: '状态码范围', value: 'status_code_range' },
    { label: '响应时间<', value: 'response_time_lt' },
    { label: 'JSONPath 存在', value: 'jsonpath_exists' },
    { label: 'JSONPath 等于', value: 'jsonpath_equals' },
  ];

  return (
    <div className="assertions-editor">
      {(value || []).map((assertion: any, i: number) => (
        <Space key={i} style={{ marginBottom: 4 }}>
          <Select
            size="small"
            value={assertion.type || 'status_code'}
            onChange={val => update(i, 'type', val)}
            options={assertionTypes}
            style={{ width: 130 }}
          />
          <Input
            size="small"
            placeholder="jsonpath"
            value={assertion.jsonpath || ''}
            onChange={e => update(i, 'jsonpath', e.target.value)}
            style={{ width: 150 }}
          />
          <Input
            size="small"
            placeholder="期望值"
            value={assertion.value !== undefined ? String(assertion.value) : ''}
            onChange={e => update(i, 'value', e.target.value)}
            style={{ width: 100 }}
          />
          <Button size="small" danger type="text" icon={<DeleteOutlined />} onClick={() => onChange(value.filter((_: any, idx: number) => idx !== i))} />
        </Space>
      ))}
      <Button size="small" type="dashed" onClick={() => onChange([...(value || []), { type: 'status_code', jsonpath: '', value: '' }])} style={{ marginTop: 4 }}>
        + 添加断言
      </Button>
    </div>
  );
};
```

- [ ] **Step 6: Implement editable step rendering**

Add a new `renderEditStep` function that renders the editable form for each step:

```tsx
const renderEditStep = (step: any, index: number) => {
  const method = step.method || 'GET';
  const stepName = step.name || `步骤 ${index + 1}`;
  const totalSteps = editScenario?.steps.length || 0;

  return (
    <Collapse
      key={index}
      className="scenario-step-item"
      items={[
        {
          key: String(index),
          label: (
            <Space>
              <span className="step-number">{index + 1}</span>
              <Tag color={methodColors[method] || 'default'}>{method}</Tag>
              <span>{stepName}</span>
              {step.enabled === false && <Tag color="red">已禁用</Tag>}
            </Space>
          ),
          children: (
            <div className="step-edit-form">
              {/* 基本信息 */}
              <div className="step-edit-row">
                <label>步骤名称:</label>
                <Input
                  size="small"
                  value={step.name || ''}
                  onChange={e => updateStep(index, 'name', e.target.value)}
                  style={{ width: 300 }}
                />
              </div>

              <div className="step-edit-row">
                <Space>
                  <span>启用:</span>
                  <Switch
                    size="small"
                    checked={step.enabled !== false}
                    onChange={checked => updateStep(index, 'enabled', checked)}
                  />
                  <span style={{ marginLeft: 16 }}>失败继续:</span>
                  <Switch
                    size="small"
                    checked={step.continue_on_failure === true}
                    onChange={checked => updateStep(index, 'continue_on_failure', checked)}
                  />
                </Space>
              </div>

              {/* URL */}
              <div className="step-edit-row">
                <label>URL:</label>
                <Input
                  size="small"
                  value={step.url || ''}
                  onChange={e => updateStep(index, 'url', e.target.value)}
                  style={{ flex: 1 }}
                />
              </div>

              {/* 请求头 */}
              <div className="step-edit-row">
                <label>请求头:</label>
                <KeyValueEditor
                  value={step.headers || []}
                  onChange={val => updateStep(index, 'headers', val)}
                />
              </div>

              {/* 请求参数 */}
              <div className="step-edit-row">
                <label>请求参数:</label>
                <KeyValueEditor
                  value={step.parameters || []}
                  onChange={val => updateStep(index, 'parameters', val)}
                />
              </div>

              {/* 请求体 */}
              <div className="step-edit-row">
                <label>请求体:</label>
                <Input.TextArea
                  value={step.body || ''}
                  onChange={e => updateStep(index, 'body', e.target.value)}
                  rows={4}
                  style={{ fontFamily: 'monospace', fontSize: 12 }}
                />
              </div>

              {/* 变量提取 */}
              <div className="step-edit-row">
                <label>变量提取:</label>
                <PostActionsEditor
                  value={step.post_actions || []}
                  onChange={val => updateStep(index, 'post_actions', val)}
                />
              </div>

              {/* 断言 */}
              <div className="step-edit-row">
                <label>断言:</label>
                <AssertionsEditor
                  value={step.assertions || []}
                  onChange={val => updateStep(index, 'assertions', val)}
                />
              </div>

              {/* 排序和删除 */}
              <div className="step-edit-actions">
                <Space>
                  <Button
                    size="small"
                    disabled={index === 0}
                    onClick={() => moveStep(index, 'up')}
                  >
                    ↑ 上移
                  </Button>
                  <Button
                    size="small"
                    disabled={index === totalSteps - 1}
                    onClick={() => moveStep(index, 'down')}
                  >
                    ↓ 下移
                  </Button>
                  <Button
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => removeStep(index)}
                  >
                    删除
                  </Button>
                </Space>
              </div>
            </div>
          ),
        },
      ]}
    />
  );
};
```

- [ ] **Step 7: Update toolbar with save button and add-step dropdown**

Replace the existing toolbar section with:

```tsx
{/* 工具栏 */}
<div className="scenario-toolbar">
  <Space>
    <Button
      icon={editing ? <EyeOutlined /> : <EditOutlined />}
      onClick={() => setEditing(!editing)}
    >
      {editing ? '查看' : '编辑'}
    </Button>
    {editing && (
      <>
        <Button
          type="primary"
          loading={saving}
          onClick={handleSave}
        >
          保存
        </Button>
        <Select
          placeholder="添加步骤"
          style={{ width: 200 }}
          size="small"
          showSearch
          optionFilterProp="label"
          value={undefined}
          onChange={(endpointId) => {
            addStep(endpointId);
          }}
          options={endpoints.map(ep => ({
            label: `${ep.method} ${ep.name || ep.path}`,
            value: ep.id,
          }))}
        />
      </>
    )}
  </Space>
  <Button
    type="primary"
    icon={<PlayCircleOutlined />}
    loading={executing}
    onClick={handleExecute}
  >
    执行
  </Button>
</div>
```

- [ ] **Step 8: Update scenario info and steps list to support editing**

Replace the scenario info section:

```tsx
{/* 场景信息 */}
<div style={{ padding: '8px 0' }}>
  {editing ? (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Input
        value={editScenario?.name || ''}
        onChange={e => editScenario && setEditScenario({ ...editScenario, name: e.target.value })}
        placeholder="场景名称"
        style={{ fontWeight: 'bold' }}
      />
      <Input.TextArea
        value={editScenario?.description || ''}
        onChange={e => editScenario && setEditScenario({ ...editScenario, description: e.target.value })}
        placeholder="场景描述"
        rows={2}
      />
    </Space>
  ) : (
    <>
      <strong>场景名称:</strong> {scenario.name}
      {scenario.description && (
        <div style={{ color: 'var(--color-text-tertiary)', marginTop: 4 }}>
          {scenario.description}
        </div>
      )}
    </>
  )}
</div>
```

Replace the steps list section:

```tsx
{/* 步骤列表 */}
<div className="scenario-steps">
  {(editing ? editScenario?.steps : scenario.steps)?.length > 0 ? (
    (editing ? editScenario?.steps : scenario.steps).map((step: any, index: number) =>
      editing ? renderEditStep(step, index) : renderStep(step, index)
    )
  ) : (
    <Empty description="暂无步骤" />
  )}
</div>
```

- [ ] **Step 9: Add edit mode styles**

Add to `TestcaseScenarioView.css`:

```css
.step-edit-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.step-edit-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.step-edit-row > label {
  min-width: 80px;
  font-weight: 500;
  padding-top: 4px;
  color: var(--color-text-secondary);
}

.step-edit-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
  border-top: 1px solid var(--color-border);
}

.kv-editor,
.post-actions-editor,
.assertions-editor {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
```

- [ ] **Step 10: Add missing imports**

Add `DeleteOutlined` and `Select` to the imports at the top of `TestcaseScenarioView.tsx`:

```tsx
import { Button, Space, Collapse, Tag, message, Spin, Empty, Input, Switch, Select } from 'antd';
import {
  PlayCircleOutlined,
  EditOutlined,
  EyeOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
```

- [ ] **Step 11: Verify**

Run: `cd frontend && npm run lint`
Expected: No errors

- [ ] **Step 12: Commit**

```bash
git add frontend/src/components/TestcaseScenarioView.tsx frontend/src/components/TestcaseScenarioView.css
git commit -m "feat(ui): implement full scenario step editing in TestcaseScenarioView"
```

---

### Task 4: A4 — Bug 链接可配置

**Files:**
- Modify: `backend/config.py:13-25`
- Modify: `backend/app/routes/config.py`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/components/TestcaseDetailView.tsx`
- Modify: `frontend/src/components/modals/ViewTestcaseModal.tsx`

**Interfaces:**
- Produces: `GET /api/config/bug-link-template` → `Response<{template: string}>`
- Consumes: `configApi.getBugLinkTemplate()` in frontend
- Prop: `TestcaseDetailView` receives `bugLinkTemplate?: string`

- [ ] **Step 1: Add `bug_link_template` to backend default_config**

In `backend/config.py`, add to `default_config`:

```python
default_config = {
    # ... existing keys ...
    "bug_link_template": "",
}
```

- [ ] **Step 2: Add GET endpoint in backend routes**

In `backend/app/routes/config.py`, add:

```python
@router.get("/bug-link-template")
async def get_bug_link_template():
    """获取 Bug 链接模板配置"""
    template = config_manager.get("bug_link_template", "")
    return Response(data={"template": template})
```

- [ ] **Step 3: Add frontend API method**

In `frontend/src/services/api.ts`, add to `configApi`:

```typescript
getBugLinkTemplate: (): Promise<ApiResponse<{ template: string }>> => api.get('/config/bug-link-template'),
```

- [ ] **Step 4: Update TestcaseDetailView to accept `bugLinkTemplate` prop**

In `TestcaseDetailView.tsx`:

Update the interface:

```tsx
interface TestcaseDetailViewProps {
  testcase: TestCase;
  bugLinkTemplate?: string;
}
```

Update the destructuring:

```tsx
const TestcaseDetailView: React.FC<TestcaseDetailViewProps> = ({ testcase, bugLinkTemplate }) => {
```

Update the bug link rendering (lines 55-66):

```tsx
{tc.bug_id ? (
  <a
    href={bugLinkTemplate
      ? bugLinkTemplate.replace('{bug_id}', String(tc.bug_id))
      : `http://zt.luban.fit/index.php?m=bug&f=view&bugID=${tc.bug_id}`
    }
    target="_blank"
    rel="noopener noreferrer"
    className="vtm-bug-link"
  >
    <BugOutlined style={{ fontSize: 11 }} />
    Bug #{tc.bug_id}
  </a>
) : (
  <span className="vtm-bug-none">无 Bug</span>
)}
```

- [ ] **Step 5: Load template in ViewTestcaseModal and pass to TestcaseDetailView**

In `ViewTestcaseModal.tsx`:

Add state and useEffect:

```tsx
const [bugLinkTemplate, setBugLinkTemplate] = useState('');

useEffect(() => {
  if (visible) {
    configApi.getBugLinkTemplate().then(res => {
      if (res.code === 200 && res.data) {
        setBugLinkTemplate(res.data.template || '');
      }
    }).catch(() => {}); // silent fail
  }
}, [visible]);
```

Add import:

```tsx
import { configApi } from '../../services/api';
```

Pass to TestcaseDetailView:

```tsx
children: <TestcaseDetailView testcase={tc} bugLinkTemplate={bugLinkTemplate} />,
```

- [ ] **Step 6: Verify**

Run: `cd frontend && npm run lint`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add backend/config.py backend/app/routes/config.py frontend/src/services/api.ts frontend/src/components/TestcaseDetailView.tsx frontend/src/components/modals/ViewTestcaseModal.tsx
git commit -m "feat: make bug link template configurable via backend config"
```

---

### Task 5: B1+B2+B3 — 后端 Bug 修复

**Files:**
- Modify: `backend/app/routes/api_test_tool.py:121`
- Modify: `backend/app/services/api_test_tool.py:1110,1216-1223`

**Interfaces:**
- No interface changes — pure bug fixes

- [ ] **Step 1: Fix B1 — `_calculate_match_score` field reference**

In `backend/app/routes/api_test_tool.py`, line 121, change:

```python
summary = endpoint.summary or endpoint.description or ""
```

To:

```python
summary = endpoint.name or ""
```

- [ ] **Step 2: Fix B3 — Remove unreachable return**

In `backend/app/services/api_test_tool.py`, line 1110, delete the duplicate return statement:

```python
# DELETE this line:
return {"body": json.dumps(body_obj, ensure_ascii=False, indent=2), "used_ai": True, "message": "AI 生成成功"}
```

The line above it (1109) should remain:

```python
return {"body": body, "used_ai": True, "message": "AI generated successfully"}
```

- [ ] **Step 3: Fix B2 — Remove duplicate `_schema_properties`**

In `backend/app/services/api_test_tool.py`, delete lines 1216-1223 (the first, simpler `_schema_properties` definition):

```python
# DELETE these lines:
def _schema_properties(schema: Any) -> dict:
    if not isinstance(schema, dict):
        return {}
    if isinstance(schema.get("properties"), dict):
        return schema["properties"]
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        return _schema_properties(schema["items"])
    return {}
```

The remaining definition at line 1418 (which handles `allOf`/`oneOf`/`anyOf`) will be the only one.

- [ ] **Step 4: Verify callers of the first `_schema_properties`**

Check that `_iter_response_schema_fields` (which calls `_schema_properties` at line 1231) works correctly with the second definition. The second definition accepts `dict | None` and handles `allOf`/`oneOf`/`anyOf`, which is a superset of the first definition's behavior. The only difference is that the second definition doesn't recurse into `items` for arrays — but `_iter_response_schema_fields` already handles array recursion itself (line 1229-1230), so this is safe.

No code changes needed — the second `_schema_properties` is a drop-in replacement.

- [ ] **Step 5: Verify backend syntax**

Run: `cd backend && python -c "import ast; ast.parse(open('app/routes/api_test_tool.py', encoding='utf-8').read()); print('routes OK')"`
Run: `cd backend && python -c "import ast; ast.parse(open('app/services/api_test_tool.py', encoding='utf-8').read()); print('services OK')"`
Expected: Both print OK

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/api_test_tool.py backend/app/services/api_test_tool.py
git commit -m "fix(backend): fix _calculate_match_score AttributeError, remove duplicate _schema_properties, remove unreachable return"
```
