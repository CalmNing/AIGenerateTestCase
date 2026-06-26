# 测试用例接口编排详情 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在测试用例详情弹窗中添加接口编排Tab，支持查看和编辑关联的API场景

**Architecture:** TestCase表添加scenario_id字段关联ApiScenario，前端修改ViewTestcaseModal添加Tabs，创建TestcaseScenarioView组件复用场景编辑逻辑

**Tech Stack:** React 18, TypeScript, Ant Design 5, FastAPI, SQLModel, SQLite

## Global Constraints

- 使用项目现有的CSS变量（Design Tokens）保持样式一致
- 遵循4px/8px间距系统
- API响应格式：`{code: number, message: string, data: T}`
- TestCase表使用SQLite，通过Alembic管理迁移

---

### Task 1: 数据模型 - TestCase表添加scenario_id字段

**Files:**
- Modify: `backend/db/models.py:72-90`
- Create: `backend/alembic/versions/xxxx_add_scenario_id_to_testcase.py`

**Interfaces:**
- Produces: `TestCase.scenario_id: Optional[int]` 字段

- [ ] **Step 1: 修改TestCase模型**

在`backend/db/models.py`的TestCase类中添加scenario_id字段：

```python
class TestCase(BaseModel, table=True):
    """测试用例数据模型"""
    case_name: str = Field(default="")
    case_level: Optional[int] = Field(default=4, ge=1, le=4)
    preset_conditions: List = Field(default_factory=list, sa_type=JSON)
    steps: List = Field(default_factory=list, sa_type=JSON)
    expected_results: List = Field(default_factory=list, sa_type=JSON)
    session_id: Optional[int] = Field(default=None, foreign_key="session.id")
    status: str = Field(default="NOT_RUN")
    bug_id: Optional[int] = Field(default=None)
    module_id: Optional[int] = Field(default=None)
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")
    api_endpoint_id: Optional[str] = Field(default=None, description="关联的 API Endpoint ID（逗号分隔）")
    api_project_id: Optional[int] = Field(default=None, description="关联的 API Project ID")
    assertions: Optional[List[dict]] = Field(default=None, sa_type=JSON, description="用例级断言规则")
    scenario_id: Optional[int] = Field(default=None, foreign_key="apiscenario.id", description="关联的接口场景ID")

    # 定义与会话的多对一关系
    session: Optional[Session] = Relationship(back_populates="test_cases")
```

- [ ] **Step 2: 创建数据库迁移**

```bash
cd backend
alembic revision --autogenerate -m "add scenario_id to testcase"
```

- [ ] **Step 3: 执行迁移**

```bash
cd backend
alembic upgrade head
```

- [ ] **Step 4: Commit**

```bash
git add backend/db/models.py backend/alembic/versions/
git commit -m "feat(db): add scenario_id field to TestCase model"
```

---

### Task 2: 前端类型 - 更新TestCase类型定义

**Files:**
- Modify: `frontend/src/types/index.ts:17-34`

**Interfaces:**
- Produces: `TestCase.scenario_id?: number` 字段

- [ ] **Step 1: 更新TestCase接口**

在`frontend/src/types/index.ts`中添加scenario_id字段：

```typescript
export interface TestCase {
  id: number;
  case_name: string;
  case_level: number;
  preset_conditions: (string | Record<string, any>)[];
  created_at: string;
  status?: TestCaseStatus;
  bug_id?: number;
  session_id: number;
  module_id?: number | null;
  user_id?: string;
  api_endpoint_id?: number | string | null;
  api_project_id?: number | null;
  assertions?: Record<string, any>[] | null;
  scenario_id?: number | null;
  steps: (string | Record<string, any>)[];
  expected_results: (string | Record<string, any>)[];
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat(types): add scenario_id to TestCase interface"
```

---

### Task 3: 后端API - 获取场景详情接口

**Files:**
- Modify: `backend/app/routes/api_test_tool.py`

**Interfaces:**
- Produces: `GET /api/test/scenarios/{scenario_id}` 端点

- [ ] **Step 1: 添加获取场景详情接口**

在`backend/app/routes/api_test_tool.py`中添加：

```python
@router.get("/scenarios/{scenario_id}", response_model=Response[ApiScenario])
def get_scenario(scenario_id: int, session: SessionDep, user: CurrentUser):
    """获取场景详情"""
    scenario = session.get(ApiScenario, scenario_id)
    if not scenario or scenario.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="场景不存在")
    return Response(data=scenario)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/routes/api_test_tool.py
git commit -m "feat(api): add get scenario detail endpoint"
```

---

### Task 4: 前端API - 添加场景相关API函数

**Files:**
- Modify: `frontend/src/services/api.ts`

**Interfaces:**
- Produces: `apiTestApi.getScenario()`, `apiTestApi.updateScenario()`, `apiTestApi.runScenario()` 函数

- [ ] **Step 1: 添加场景API函数**

在`frontend/src/services/api.ts`的apiTestApi对象中添加：

```typescript
export const apiTestApi = {
  // ... 现有函数 ...
  
  // 获取场景详情
  getScenario: (scenarioId: number): Promise<ApiResponse<any>> =>
    api.get(`/test/scenarios/${scenarioId}`),
  
  // 更新场景
  updateScenario: (scenarioId: number, data: any): Promise<ApiResponse<any>> =>
    api.put(`/test/scenarios/${scenarioId}`, data),
  
  // 执行场景
  runScenario: (scenarioId: number): Promise<ApiResponse<any>> =>
    api.post(`/test/scenarios/${scenarioId}/run`),
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/services/api.ts
git commit -m "feat(api): add scenario API functions"
```

---

### Task 5: 前端组件 - 创建TestcaseDetailView组件

**Files:**
- Create: `frontend/src/components/TestcaseDetailView.tsx`

**Interfaces:**
- Consumes: `TestCase` 类型
- Produces: `TestcaseDetailView` React组件

- [ ] **Step 1: 创建TestcaseDetailView组件**

```tsx
import React from 'react';
import { BugOutlined } from '@ant-design/icons';
import { TestCase, TestCaseStatus } from '../types';
import { formatStep } from '../utils/stepUtils';

interface TestcaseDetailViewProps {
  testcase: TestCase;
}

const statusMap: Record<string, { label: string; cls: string }> = {
  [TestCaseStatus.PASSED]: { label: '已通过', cls: 'vtm-status--passed' },
  [TestCaseStatus.FAILED]: { label: '未通过', cls: 'vtm-status--failed' },
  [TestCaseStatus.NOT_RUN]: { label: '未执行', cls: 'vtm-status--not_run' },
};

const TestcaseDetailView: React.FC<TestcaseDetailViewProps> = ({ testcase }) => {
  const tc = testcase;
  const status = tc ? (statusMap[tc.status || TestCaseStatus.NOT_RUN] || statusMap[TestCaseStatus.NOT_RUN]) : null;

  const renderStepList = (items: (string | object)[], type: 'step' | 'expected') => (
    <ul className="vtm-step-list">
      {items.map((item, index) => {
        const isObject = typeof item !== 'string';
        const text = isObject ? formatStep(item as Record<string, unknown>) : item;
        return (
          <li key={index} className="vtm-step-item">
            <span className="vtm-step-num">{index + 1}</span>
            {isObject && text.includes('\n') ? (
              <pre className="vtm-step-code">{text}</pre>
            ) : (
              <span className="vtm-step-text">{text}</span>
            )}
          </li>
        );
      })}
    </ul>
  );

  return (
    <div>
      {/* Header */}
      <div className="vtm-header">
        <div className="vtm-header-info">
          <h2 className="vtm-title">{tc.case_name}</h2>
          <div className="vtm-meta">
            <span className={`vtm-level vtm-level--${tc.case_level || 4}`}>
              P{tc.case_level || 4}
            </span>
            {status && (
              <span className={`vtm-status ${status.cls}`}>
                <span className="vtm-status-dot" />
                {status.label}
              </span>
            )}
            {tc.bug_id ? (
              <a
                href={`http://zt.luban.fit/index.php?m=bug&f=view&bugID=${tc.bug_id}`}
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
          </div>
        </div>
      </div>

      {/* Preset Conditions */}
      {tc.preset_conditions.length > 0 && (
        <div className="vtm-section">
          <div className="vtm-section-label">前置条件</div>
          {renderStepList(
            tc.preset_conditions.filter((s: any) => typeof s === 'string' ? s.trim() : true) as (string | object)[],
            'step'
          )}
        </div>
      )}

      {/* Steps */}
      {tc.steps.length > 0 && (
        <div className="vtm-section">
          <div className="vtm-section-label">测试步骤</div>
          {renderStepList(tc.steps, 'step')}
        </div>
      )}

      {/* Expected Results */}
      {tc.expected_results.length > 0 && (
        <div className="vtm-section">
          <div className="vtm-section-label">预期结果</div>
          {renderStepList(tc.expected_results, 'expected')}
        </div>
      )}
    </div>
  );
};

export default TestcaseDetailView;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/TestcaseDetailView.tsx
git commit -m "feat(ui): add TestcaseDetailView component"
```

---

### Task 6: 前端组件 - 创建TestcaseScenarioView组件

**Files:**
- Create: `frontend/src/components/TestcaseScenarioView.tsx`
- Create: `frontend/src/components/TestcaseScenarioView.css`

**Interfaces:**
- Consumes: `apiTestApi.getScenario()`, `apiTestApi.runScenario()` 函数
- Produces: `TestcaseScenarioView` React组件

- [ ] **Step 1: 创建CSS文件**

```css
/* TestcaseScenarioView.css */
.testcase-scenario-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 400px;
}

.scenario-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--color-bg-container);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.scenario-steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  overflow-y: auto;
}

.scenario-step-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.scenario-step-item .ant-collapse-header {
  padding: 8px 12px !important;
}

.scenario-step-item .ant-collapse-content-box {
  padding: 12px !important;
}

.step-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--color-primary-bg);
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 600;
}

.scenario-result {
  margin-top: 12px;
  padding: 12px;
  background: var(--color-bg-container);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.scenario-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
  color: var(--color-text-tertiary);
}
```

- [ ] **Step 2: 创建TestcaseScenarioView组件**

```tsx
import React, { useState, useEffect, useCallback } from 'react';
import { Button, Space, Collapse, Tag, message, Spin, Empty } from 'antd';
import { 
  PlayCircleOutlined, 
  EditOutlined, 
  EyeOutlined,
  PlusOutlined,
  DeleteOutlined 
} from '@ant-design/icons';
import { apiTestApi } from '../services/api';
import './TestcaseScenarioView.css';

interface ScenarioStep {
  endpoint_id?: number;
  name?: string;
  method?: string;
  url?: string;
  body?: string;
  headers?: Array<{ key: string; value: string }>;
  parameters?: Array<{ key: string; value: string }>;
  post_actions?: Array<{ type: string; key: string; jsonpath: string }>;
  assertions?: Array<{ type: string; value?: any; jsonpath?: string }>;
  enabled?: boolean;
  continue_on_failure?: boolean;
}

interface ApiScenario {
  id: number;
  project_id: number;
  name: string;
  description: string;
  steps: ScenarioStep[];
  variables: Array<{ key: string; value: string }>;
}

interface TestcaseScenarioViewProps {
  scenarioId?: number;
  readOnly?: boolean;
}

const methodColors: Record<string, string> = {
  GET: 'green',
  POST: 'blue',
  PUT: 'orange',
  DELETE: 'red',
  PATCH: 'purple',
};

const TestcaseScenarioView: React.FC<TestcaseScenarioViewProps> = ({
  scenarioId,
  readOnly: initialReadOnly = true,
}) => {
  const [scenario, setScenario] = useState<ApiScenario | null>(null);
  const [loading, setLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [editing, setEditing] = useState(!initialReadOnly);

  // 加载场景数据
  const loadScenario = useCallback(async () => {
    if (!scenarioId) return;
    setLoading(true);
    try {
      const res = await apiTestApi.getScenario(scenarioId);
      if (res.code === 200 && res.data) {
        setScenario(res.data);
      }
    } catch (error) {
      console.error('加载场景失败:', error);
    } finally {
      setLoading(false);
    }
  }, [scenarioId]);

  useEffect(() => {
    loadScenario();
  }, [loadScenario]);

  // 执行场景
  const handleExecute = async () => {
    if (!scenarioId) return;
    setExecuting(true);
    try {
      const res = await apiTestApi.runScenario(scenarioId);
      if (res.code === 200 && res.data) {
        setResult(res.data);
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

  // 保存场景
  const handleSave = async () => {
    if (!scenarioId || !scenario) return;
    try {
      const res = await apiTestApi.updateScenario(scenarioId, scenario);
      if (res.code === 200) {
        message.success('保存成功');
        setEditing(false);
      } else {
        message.error(res.message || '保存失败');
      }
    } catch (error: any) {
      message.error(error.message || '保存失败');
    }
  };

  // 渲染步骤
  const renderStep = (step: ScenarioStep, index: number) => {
    const method = step.method || 'GET';
    const stepName = step.name || step.url || `步骤 ${index + 1}`;

    return (
      <Collapse
        key={index}
        className="scenario-step-item"
        items={[
          {
            key: index,
            label: (
              <Space>
                <span className="step-number">{index + 1}</span>
                <Tag color={methodColors[method] || 'default'}>{method}</Tag>
                <span>{stepName}</span>
                {step.enabled === false && <Tag color="red">已禁用</Tag>}
              </Space>
            ),
            children: (
              <div>
                {/* 基本信息 */}
                <div style={{ marginBottom: 8 }}>
                  <strong>URL:</strong> {step.url || '-'}
                </div>
                
                {/* 请求体 */}
                {step.body && (
                  <div style={{ marginBottom: 8 }}>
                    <strong>请求体:</strong>
                    <pre style={{ 
                      background: 'var(--color-bg)', 
                      padding: 8, 
                      borderRadius: 'var(--radius-sm)',
                      fontSize: 12,
                      maxHeight: 200,
                      overflow: 'auto'
                    }}>
                      {step.body}
                    </pre>
                  </div>
                )}

                {/* 后置提取 */}
                {step.post_actions && step.post_actions.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    <strong>变量提取:</strong>
                    <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                      {step.post_actions.map((action, i) => (
                        <li key={i}>
                          <code>{action.key}</code> = <code>{action.jsonpath}</code>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 断言 */}
                {step.assertions && step.assertions.length > 0 && (
                  <div>
                    <strong>断言:</strong>
                    <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                      {step.assertions.map((assertion, i) => (
                        <li key={i}>
                          {assertion.type}: {assertion.jsonpath || ''} {assertion.value !== undefined ? `= ${assertion.value}` : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ),
          },
        ]}
      />
    );
  };

  // 渲染执行结果
  const renderResult = () => {
    if (!result) return null;

    return (
      <div className="scenario-result">
        <div style={{ marginBottom: 8 }}>
          <Tag color={result.passed ? 'success' : 'error'} style={{ fontSize: 14 }}>
            {result.passed ? '执行通过' : '执行失败'}
          </Tag>
        </div>
        
        {result.steps && result.steps.length > 0 && (
          <div>
            <strong>步骤详情:</strong>
            {result.steps.map((step: any, index: number) => (
              <div key={index} style={{ 
                padding: 8, 
                marginTop: 4,
                background: step.status === 'passed' ? 'var(--color-success-bg)' : 'var(--color-danger-bg)',
                borderRadius: 'var(--radius-sm)'
              }}>
                <Space>
                  <span>{index + 1}.</span>
                  <span>{step.name || `步骤 ${index + 1}`}</span>
                  <Tag color={step.status === 'passed' ? 'success' : 'error'}>
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

  if (loading) {
    return (
      <div className="scenario-empty">
        <Spin />
      </div>
    );
  }

  if (!scenarioId) {
    return (
      <div className="scenario-empty">
        <Empty description="未关联接口场景" />
      </div>
    );
  }

  if (!scenario) {
    return (
      <div className="scenario-empty">
        <Empty description="场景不存在" />
      </div>
    );
  }

  return (
    <div className="testcase-scenario-view">
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
            <Button type="primary" onClick={handleSave}>
              保存
            </Button>
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

      {/* 场景信息 */}
      <div style={{ padding: '8px 0' }}>
        <strong>场景名称:</strong> {scenario.name}
        {scenario.description && (
          <div style={{ color: 'var(--color-text-tertiary)', marginTop: 4 }}>
            {scenario.description}
          </div>
        )}
      </div>

      {/* 步骤列表 */}
      <div className="scenario-steps">
        {scenario.steps && scenario.steps.length > 0 ? (
          scenario.steps.map((step, index) => renderStep(step, index))
        ) : (
          <Empty description="暂无步骤" />
        )}
      </div>

      {/* 执行结果 */}
      {renderResult()}
    </div>
  );
};

export default TestcaseScenarioView;
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TestcaseScenarioView.tsx frontend/src/components/TestcaseScenarioView.css
git commit -m "feat(ui): add TestcaseScenarioView component"
```

---

### Task 7: 前端组件 - 修改ViewTestcaseModal添加Tabs

**Files:**
- Modify: `frontend/src/components/modals/ViewTestcaseModal.tsx`

**Interfaces:**
- Consumes: `TestcaseDetailView`, `TestcaseScenarioView` 组件
- Produces: 修改后的ViewTestcaseModal组件

- [ ] **Step 1: 修改ViewTestcaseModal**

```tsx
import React, { useState } from 'react';
import { Modal, Tabs } from 'antd';
import { CheckOutlined, UpOutlined, DownOutlined, BugOutlined } from '@ant-design/icons';
import { TestCase, TestCaseStatus } from '../../types';
import { formatStep } from '../../utils/stepUtils';
import TestcaseDetailView from '../TestcaseDetailView';
import TestcaseScenarioView from '../TestcaseScenarioView';
import './ViewTestcaseModal.css';

interface ViewTestcaseModalProps {
  visible: boolean;
  nextButtonDisabled: boolean;
  prevButtonDisabled: boolean;
  selectedTestcase: TestCase | null;
  onCancel: () => void;
  onNext: () => void;
  onPrev: () => void;
  onComplete: (testcase: TestCase | null) => void;
}

const ViewTestcaseModal: React.FC<ViewTestcaseModalProps> = ({
  visible,
  selectedTestcase,
  onCancel,
  onNext,
  nextButtonDisabled,
  prevButtonDisabled,
  onPrev,
  onComplete
}) => {
  const tc = selectedTestcase;
  const [activeTab, setActiveTab] = useState('detail');

  return (
    <Modal
      title="查看测试用例"
      open={visible}
      onCancel={onCancel}
      width={800}
      styles={{
        body: { padding: '12px 24px' },
      }}
      footer={[
        <button key="prev" className="vtm-footer-btn" onClick={onPrev} disabled={prevButtonDisabled}>
          <UpOutlined style={{ fontSize: 11 }} /> 上一个
        </button>,
        <button key="next" className="vtm-footer-btn" onClick={onNext} disabled={nextButtonDisabled}>
          <DownOutlined style={{ fontSize: 11 }} /> 下一个
        </button>,
        <button key="execute" className="vtm-footer-btn vtm-footer-btn--primary" onClick={() => onComplete(tc)}>
          <CheckOutlined style={{ fontSize: 12 }} /> 执行
        </button>,
        <button key="close" className="vtm-footer-btn" onClick={onCancel}>
          关闭
        </button>,
      ]}
    >
      {tc && (
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'detail',
              label: '用例详情',
              children: <TestcaseDetailView testcase={tc} />,
            },
            {
              key: 'scenario',
              label: '接口编排',
              children: <TestcaseScenarioView scenarioId={tc.scenario_id} />,
            },
          ]}
        />
      )}
    </Modal>
  );
};

export default ViewTestcaseModal;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/modals/ViewTestcaseModal.tsx
git commit -m "feat(ui): add Tabs to ViewTestcaseModal with scenario view"
```

---

### Task 8: 后端逻辑 - 生成测试用例时创建场景

**Files:**
- Modify: `backend/utils/model_utils.py:1762-1774`
- Modify: `backend/app/routes/testcase.py:440-460`

**Interfaces:**
- Consumes: `ApiScenario` 模型
- Produces: 生成测试用例时自动创建关联场景

- [ ] **Step 1: 修改测试用例生成逻辑**

在`backend/utils/model_utils.py`中，修改创建DBTestCase的逻辑，同时创建ApiScenario：

```python
# 在 db_testcases = [] 之前添加
from db.models import ApiScenario

# 修改循环中的逻辑
db_testcases = []
endpoint_index_to_id = kwargs.get("endpoint_index_to_id", {})
db_session = kwargs.get("db_session")
user_id = kwargs.get("user_id")
api_project_id = kwargs.get("api_project_id")

for tc in local_testcases:
    # ... 现有的处理逻辑 ...
    
    # 创建关联的场景
    scenario = None
    if converted_steps and api_project_id:
        scenario = ApiScenario(
            project_id=api_project_id,
            name=f"{tc.case_name}_场景",
            description=f"测试用例 {tc.case_name} 的接口场景",
            steps=converted_steps,
            user_id=user_id,
        )
        db_session.add(scenario)
        db_session.flush()
    
    # 创建DBTestCase对象
    db_tc = DBTestCase(
        case_name=tc.case_name,
        case_level=tc.case_level,
        preset_conditions=converted_preset_conditions,
        steps=converted_steps,
        session_id=session_id,
        module_id=module_id,
        expected_results=tc.expected_results,
        api_endpoint_id=attached_ids,
        assertions=serialized_assertions,
        scenario_id=scenario.id if scenario else None,
    )
    db_testcases.append(db_tc)
```

- [ ] **Step 2: Commit**

```bash
git add backend/utils/model_utils.py
git commit -m "feat(backend): create scenario when generating testcases"
```

---

### Task 9: 测试验证

**Files:**
- None (manual testing)

**Interfaces:**
- None

- [ ] **Step 1: 启动后端服务**

```bash
cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: 启动前端服务**

```bash
cd frontend && npm run dev
```

- [ ] **Step 3: 测试生成测试用例**

1. 打开AI测试用例生成页面
2. 输入需求描述
3. 生成测试用例
4. 验证测试用例是否关联了场景

- [ ] **Step 4: 测试查看详情**

1. 点击测试用例的"查看"按钮
2. 验证弹窗显示两个Tab
3. 点击"用例详情"Tab，验证显示正确
4. 点击"接口编排"Tab，验证显示场景步骤

- [ ] **Step 5: 测试执行场景**

1. 在"接口编排"Tab中点击"执行"按钮
2. 验证执行结果显示

- [ ] **Step 6: Final Commit**

```bash
git add -A
git commit -m "feat: complete testcase scenario detail feature"
```

---

## 验收标准

- [ ] TestCase表添加scenario_id字段
- [ ] 生成测试用例时自动创建关联场景
- [ ] 详情弹窗显示两个Tab
- [ ] 文字描述Tab正确显示
- [ ] 接口编排Tab显示场景步骤
- [ ] 支持只读/编辑模式切换
- [ ] 支持执行场景并显示结果
