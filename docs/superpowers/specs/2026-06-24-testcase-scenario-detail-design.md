# 测试用例接口编排详情设计文档

## 1. 概述

### 1.1 背景

当前测试用例详情弹窗只显示文字描述（前置条件、测试步骤、预期结果），用户希望能够在详情中直接查看和编辑接口编排，实现测试用例与接口场景的关联。

### 1.2 目标

1. 生成测试用例时自动创建关联的API场景
2. 详情弹窗显示两个Tab：文字描述 + 接口编排
3. 接口编排支持只读/编辑模式切换
4. 支持在详情弹窗中执行场景

## 2. 数据模型

### 2.1 TestCase 表扩展

在 TestCase 表中添加 `scenario_id` 字段：

```python
class TestCase(SQLModel, table=True):
    # ... 现有字段 ...
    scenario_id: Optional[int] = Field(default=None, foreign_key="apiscenario.id")
```

### 2.2 ApiScenario 表

复用现有的 ApiScenario 表结构：

```python
class ApiScenario(SQLModel, table=True):
    id: int
    project_id: int
    name: str
    description: str
    environment_id: Optional[int]
    variables: list  # [{key, value}]
    steps: list  # [{endpoint_id, name, method, url, body, headers, parameters, assertions, post_actions, ...}]
    user_id: str
    created_at: datetime
    updated_at: datetime
```

### 2.3 数据关系

```
TestCase 1:1 ApiScenario
  └── scenario_id → ApiScenario.id
```

## 3. 前端组件设计

### 3.1 ViewTestcaseModal 修改

修改现有的 ViewTestcaseModal 组件，添加 Tabs：

```tsx
const ViewTestcaseModal: React.FC<ViewTestcaseModalProps> = ({ ... }) => {
  return (
    <Modal title="查看测试用例" width={800}>
      <Tabs items={[
        {
          key: 'detail',
          label: '用例详情',
          children: <TestcaseDetailView testcase={tc} />
        },
        {
          key: 'scenario',
          label: '接口编排',
          children: <TestcaseScenarioView 
            scenarioId={tc.scenario_id}
            readOnly={readOnly}
            onExecute={handleExecute}
          />
        }
      ]} />
    </Modal>
  );
};
```

### 3.2 TestcaseDetailView 组件

提取现有的文字描述部分为独立组件：

```tsx
const TestcaseDetailView: React.FC<{ testcase: TestCase }> = ({ testcase }) => {
  return (
    <div>
      {/* Header */}
      <div className="vtm-header">...</div>
      
      {/* Preset Conditions */}
      {testcase.preset_conditions.length > 0 && (
        <div className="vtm-section">
          <div className="vtm-section-label">前置条件</div>
          {renderStepList(testcase.preset_conditions, 'step')}
        </div>
      )}
      
      {/* Steps */}
      {testcase.steps.length > 0 && (
        <div className="vtm-section">
          <div className="vtm-section-label">测试步骤</div>
          {renderStepList(testcase.steps, 'step')}
        </div>
      )}
      
      {/* Expected Results */}
      {testcase.expected_results.length > 0 && (
        <div className="vtm-section">
          <div className="vtm-section-label">预期结果</div>
          {renderStepList(testcase.expected_results, 'expected')}
        </div>
      )}
    </div>
  );
};
```

### 3.3 TestcaseScenarioView 组件

创建新的组件，复用 ApiScenarioTestTool 的场景编辑逻辑：

```tsx
interface TestcaseScenarioViewProps {
  scenarioId?: number;
  readOnly?: boolean;
  onExecute?: (scenarioId: number) => void;
}

const TestcaseScenarioView: React.FC<TestcaseScenarioViewProps> = ({
  scenarioId,
  readOnly = true,
  onExecute
}) => {
  const [scenario, setScenario] = useState<ApiScenario | null>(null);
  const [editing, setEditing] = useState(!readOnly);
  const [result, setResult] = useState<any>(null);
  
  // 加载场景数据
  useEffect(() => {
    if (scenarioId) {
      loadScenario(scenarioId);
    }
  }, [scenarioId]);
  
  // 执行场景
  const handleExecute = async () => {
    if (!scenarioId) return;
    const res = await apiTestApi.runScenario(scenarioId);
    setResult(res.data);
  };
  
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
          <Button 
            type="primary" 
            icon={<PlayCircleOutlined />}
            onClick={handleExecute}
            disabled={!scenarioId}
          >
            执行
          </Button>
        </Space>
      </div>
      
      {/* 场景步骤列表 */}
      <div className="scenario-steps">
        {scenario?.steps.map((step, index) => (
          <ScenarioStepItem
            key={index}
            step={step}
            index={index}
            readOnly={!editing}
            onChange={handleStepChange}
          />
        ))}
      </div>
      
      {/* 执行结果 */}
      {result && (
        <ScenarioResultView result={result} />
      )}
    </div>
  );
};
```

## 4. 后端API设计

### 4.1 生成测试用例时创建场景

修改 `generateTestcases` 函数，在生成测试用例时同时创建场景：

```python
async def generate_testcases(session_id, requirement, ...):
    # 1. AI生成测试用例
    testcases = await ai_generate_testcases(requirement, ...)
    
    for tc_data in testcases:
        # 2. 为每个测试用例创建场景
        scenario = ApiScenario(
            project_id=api_project_id,
            name=f"{tc_data['case_name']}_场景",
            description=tc_data.get('description', ''),
            steps=tc_data.get('scenario_steps', []),
            user_id=user_id
        )
        session.add(scenario)
        session.flush()
        
        # 3. 创建测试用例并关联场景
        testcase = TestCase(
            case_name=tc_data['case_name'],
            scenario_id=scenario.id,
            # ... 其他字段
        )
        session.add(testcase)
    
    session.commit()
```

### 4.2 AI生成场景步骤

在AI生成测试用例时，同时生成场景步骤：

```python
def build_scenario_generation_prompt(requirement, endpoints):
    return f"""
    根据以下需求生成测试用例的接口场景步骤：
    
    需求：{requirement}
    
    可用接口：
    {format_endpoints(endpoints)}
    
    为每个测试用例生成场景步骤，包含：
    1. 接口调用顺序
    2. 请求参数
    3. 变量提取（从响应中提取数据用于后续步骤）
    4. 断言规则
    
    输出格式：
    [
      {{
        "endpoint_id": 1,
        "name": "步骤1",
        "method": "POST",
        "url": "/api/login",
        "body": "{{\\"username\\": \\"test\\", \\"password\\": \\"123456\\"}}",
        "post_actions": [
          {{"type": "extract_jsonpath", "key": "token", "jsonpath": "$.data.token"}}
        ],
        "assertions": [
          {{"type": "status_code", "value": 200}},
          {{"type": "jsonpath_equals", "jsonpath": "$.code", "value": 0}}
        ]
      }}
    ]
    """
```

### 4.3 场景执行API

复用现有的场景执行API：

```python
@router.post("/scenarios/{scenario_id}/run")
async def run_scenario(scenario_id: int, ...):
    # 复用现有的 run_scenario 逻辑
    result = await run_scenario(db, scenario, project)
    return Response(data=result)
```

## 5. 场景步骤编辑器组件

### 5.1 提取独立组件

从 ApiScenarioTestTool 中提取场景步骤编辑器：

```tsx
// components/ScenarioStepEditor.tsx
interface ScenarioStepEditorProps {
  steps: ApiScenarioStep[];
  endpoints: ApiEndpoint[];
  readOnly?: boolean;
  onChange?: (steps: ApiScenarioStep[]) => void;
}

const ScenarioStepEditor: React.FC<ScenarioStepEditorProps> = ({
  steps,
  endpoints,
  readOnly = false,
  onChange
}) => {
  return (
    <div className="scenario-step-editor">
      {steps.map((step, index) => (
        <ScenarioStepItem
          key={index}
          step={step}
          index={index}
          endpoints={endpoints}
          readOnly={readOnly}
          onChange={(updated) => {
            const newSteps = [...steps];
            newSteps[index] = updated;
            onChange?.(newSteps);
          }}
          onDelete={() => {
            const newSteps = steps.filter((_, i) => i !== index);
            onChange?.(newSteps);
          }}
        />
      ))}
      
      {!readOnly && (
        <Button 
          type="dashed" 
          block 
          icon={<PlusOutlined />}
          onClick={() => onChange?.([...steps, defaultStep()])}
        >
          添加步骤
        </Button>
      )}
    </div>
  );
};
```

### 5.2 场景步骤项组件

```tsx
const ScenarioStepItem: React.FC<{
  step: ApiScenarioStep;
  index: number;
  endpoints: ApiEndpoint[];
  readOnly: boolean;
  onChange: (step: ApiScenarioStep) => void;
  onDelete: () => void;
}> = ({ step, index, endpoints, readOnly, onChange, onDelete }) => {
  return (
    <Collapse
      items={[{
        key: index,
        label: (
          <Space>
            <span className="step-number">{index + 1}</span>
            <Tag color={methodColor(step.method)}>{step.method}</Tag>
            <span>{step.name || step.url}</span>
          </Space>
        ),
        children: (
          <div>
            {/* 请求配置 */}
            <Form layout="vertical" disabled={readOnly}>
              <Form.Item label="接口">
                <Select options={endpointOptions} value={step.endpoint_id} />
              </Form.Item>
              <Form.Item label="请求体">
                <CodeMirror value={step.body} />
              </Form.Item>
            </Form>
            
            {/* 后置提取 */}
            <PostActionsEditor 
              actions={step.post_actions} 
              readOnly={readOnly} 
            />
            
            {/* 断言 */}
            <AssertionsEditor 
              assertions={step.assertions} 
              readOnly={readOnly} 
            />
          </div>
        )
      }]}
    />
  );
};
```

## 6. 执行结果展示

### 6.1 结果组件

```tsx
const ScenarioResultView: React.FC<{ result: any }> = ({ result }) => {
  return (
    <div className="scenario-result">
      <div className="result-header">
        <Tag color={result.passed ? 'success' : 'error'}>
          {result.passed ? '通过' : '失败'}
        </Tag>
      </div>
      
      <Table 
        dataSource={result.steps}
        columns={[
          { title: '#', dataIndex: 'index', width: 40 },
          { title: '步骤', dataIndex: 'name' },
          { 
            title: '结果', 
            render: (_, row) => (
              <Tag color={row.status === 'passed' ? 'success' : 'error'}>
                {row.status === 'passed' ? '通过' : '失败'}
              </Tag>
            )
          },
          { title: '耗时', render: (_, row) => `${row.response?.elapsed_ms}ms` }
        ]}
        expandable={{
          expandedRowRender: (row) => <StepDetailView step={row} />
        }}
      />
    </div>
  );
};
```

## 7. 文件结构

```
frontend/src/components/
├── modals/
│   └── ViewTestcaseModal.tsx          # 修改：添加Tabs
├── TestcaseDetailView.tsx             # 新增：文字描述详情
├── TestcaseScenarioView.tsx           # 新增：接口编排视图
├── ScenarioStepEditor.tsx             # 新增：场景步骤编辑器
└── ScenarioResultView.tsx             # 新增：执行结果展示

backend/app/
├── routes/
│   └── testcase.py                    # 修改：生成时创建场景
└── services/
    └── testcase_generator.py          # 修改：AI生成场景步骤
```

## 8. 实施步骤

1. **数据模型**：TestCase表添加scenario_id字段，创建数据库迁移
2. **后端API**：修改测试用例生成逻辑，同时创建场景
3. **前端组件**：创建TestcaseDetailView、TestcaseScenarioView等组件
4. **修改ViewTestcaseModal**：添加Tabs，集成新组件
5. **场景编辑器**：提取并复用ApiScenarioTestTool的编辑逻辑
6. **执行功能**：实现场景执行和结果展示
7. **测试验证**：测试完整流程

## 9. 验收标准

- [ ] 生成测试用例时自动创建关联场景
- [ ] 详情弹窗显示两个Tab
- [ ] 文字描述Tab正确显示
- [ ] 接口编排Tab显示场景步骤
- [ ] 支持只读/编辑模式切换
- [ ] 支持执行场景并显示结果
- [ ] 场景步骤编辑器功能完整
- [ ] 执行结果正确展示
