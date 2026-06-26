# Variable Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将IoT数据推送平台的内置函数从Tooltip升级为全局悬浮按钮，支持查看变量、测试效果和快速插入。

**Architecture:** 创建独立的VariableAssistant组件，使用FloatButton + Popover展示变量列表，新增后端API支持变量测试。

**Tech Stack:** React 18, TypeScript, Ant Design 5, FastAPI, Python 3.11

## Global Constraints

- 使用项目现有的CSS变量（Design Tokens）保持样式一致
- 遵循4px/8px间距系统
- 动画时长150-300ms范围
- 支持`prefers-reduced-motion`设置
- API响应格式：`{code: number, message: string, data: T}`

---

### Task 1: 后端 - 新增测试变量API

**Files:**
- Modify: `backend/app/routes/proxy.py:287-310`

**Interfaces:**
- Produces: `POST /api/proxy/test-variable` 端点

- [ ] **Step 1: 添加测试变量请求模型**

在proxy.py中添加TestVariableRequest模型：

```python
class TestVariableRequest(BaseModel):
    """测试变量请求模型"""
    expression: str
    environment_id: Optional[int] = None
```

- [ ] **Step 2: 添加测试变量API端点**

在proxy.py的`forward_request`端点之后添加：

```python
@router.post("/test-variable")
async def test_variable(
    request: TestVariableRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    """测试变量表达式，返回替换后的值"""
    # 构建参数映射表
    environment_id = request.environment_id
    if environment_id and Permission.GLOBAL_PARAMETER_MANAGE not in get_user_permissions(user):
        environment_id = None
    param_map = build_param_map(db, environment_id, [])

    # 收集未解析的变量
    unresolved: set[str] = set()

    # 执行变量替换
    result = substitute_variables(request.expression, param_map, unresolved)

    return {
        "expression": request.expression,
        "result": result,
        "unresolved": list(unresolved) if unresolved else [],
    }
```

- [ ] **Step 3: 测试API端点**

启动后端服务，使用curl测试：

```bash
curl -X POST http://localhost:8000/api/proxy/test-variable \
  -H "Content-Type: application/json" \
  -d '{"expression": "{{$timestamp}}"}'
```

Expected: 返回类似 `{"expression": "{{$timestamp}}", "result": "1719235200000", "unresolved": []}`

- [ ] **Step 4: Commit**

```bash
git add backend/app/routes/proxy.py
git commit -m "feat(proxy): add test-variable API endpoint"
```

---

### Task 2: 前端 - 添加测试变量API函数

**Files:**
- Modify: `frontend/src/services/api.ts:302-305`

**Interfaces:**
- Consumes: `POST /api/proxy/test-variable` 端点
- Produces: `proxyApi.testVariable()` 函数

- [ ] **Step 1: 添加TestVariableRequest类型**

在api.ts中添加类型定义：

```typescript
interface TestVariableRequest {
  expression: string;
  environment_id?: number;
}

interface TestVariableResponse {
  expression: string;
  result: string;
  unresolved: string[];
}
```

- [ ] **Step 2: 添加testVariable函数**

在proxyApi对象中添加testVariable方法：

```typescript
export const proxyApi = {
  // 转发请求
  forwardRequest: (request: ProxyRequest): Promise<ProxyResponse<ApiResponse<any>>> => api.post('/proxy/forward', request),
  // 测试变量
  testVariable: (request: TestVariableRequest): Promise<ApiResponse<TestVariableResponse>> => api.post('/proxy/test-variable', request)
};
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/api.ts
git commit -m "feat(api): add testVariable function to proxyApi"
```

---

### Task 3: 前端 - 创建VariableAssistant组件

**Files:**
- Create: `frontend/src/components/VariableAssistant.tsx`
- Create: `frontend/src/components/VariableAssistant.css`

**Interfaces:**
- Consumes: `proxyApi.testVariable()` 函数
- Produces: `VariableAssistant` React组件

- [ ] **Step 1: 创建VariableAssistant.css**

创建样式文件：

```css
/* VariableAssistant.css */

.variable-assistant-popover {
  width: 400px;
  max-height: 500px;
  overflow: hidden;
}

.variable-assistant-popover .ant-popover-inner {
  padding: 0;
}

.variable-assistant-tabs {
  height: 100%;
}

.variable-assistant-tabs .ant-tabs-content {
  height: 100%;
  overflow: auto;
}

.variable-assistant-tabs .ant-tabs-tabpane {
  padding: 12px;
}

.variable-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.variable-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: all 150ms ease;
  cursor: pointer;
}

.variable-item:hover {
  background: var(--color-bg-text-hover);
  border-color: var(--color-primary-border);
}

.variable-syntax {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 13px;
  color: var(--color-primary);
  cursor: pointer;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-primary-bg);
  transition: all 150ms ease;
}

.variable-syntax:hover {
  text-decoration: underline;
  background: var(--color-primary-border);
}

.variable-description {
  flex: 1;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.variable-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.variable-test-result {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12px;
  color: var(--color-success);
  padding: 2px 8px;
  background: var(--color-success-bg);
  border-radius: var(--radius-sm);
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.variable-test-error {
  font-size: 12px;
  color: var(--color-danger);
  padding: 2px 8px;
  background: var(--color-danger-bg);
  border-radius: var(--radius-sm);
}

.variable-env-section {
  margin-top: 16px;
}

.variable-env-section-title {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 8px;
  color: var(--color-text);
}

/* FloatButton custom styles */
.variable-assistant-float-btn {
  box-shadow: var(--shadow-md);
}

.variable-assistant-float-btn:hover {
  transform: scale(1.05);
  box-shadow: var(--shadow-lg);
}
```

- [ ] **Step 2: 创建VariableAssistant.tsx**

创建组件文件：

```tsx
import React, { useState, useCallback, useMemo } from 'react';
import { FloatButton, Popover, Tabs, Button, Space, message, Spin } from 'antd';
import {
  FunctionOutlined,
  CopyOutlined,
  ThunderboltOutlined,
  EnvironmentOutlined,
} from '@ant-design/icons';
import { proxyApi } from '../services/api';
import './VariableAssistant.css';

// 内置函数定义
const builtinFunctions = [
  {
    syntax: '{{$timestamp}}',
    label: '毫秒时间戳',
    description: '当前时间的毫秒级时间戳',
  },
  {
    syntax: '{{$now}}',
    label: '秒级时间戳',
    description: '当前时间的秒级时间戳',
  },
  {
    syntax: '{{$date}}',
    label: '当前日期',
    description: 'YYYY-MM-DD 格式',
  },
  {
    syntax: "{{$date('YYYY-MM-DD HH:mm:ss')}}",
    label: '自定义格式日期',
    description: '支持 YYYY/MM/DD/HH/mm/ss/SSS',
  },
  {
    syntax: '{{$randomInt}}',
    label: '随机整数',
    description: '0~100 随机整数',
  },
  {
    syntax: '{{$randomInt(1,1000)}}',
    label: '指定范围随机整数',
    description: '可自定义范围',
  },
  {
    syntax: '{{$uuid}}',
    label: 'UUID',
    description: 'UUID v4',
  },
];

// JS表达式定义
const jsExpressions = [
  {
    syntax: '{{@Date.now()}}',
    label: 'JS时间戳',
    description: 'JavaScript时间戳',
  },
  {
    syntax: '{{@Math.random().toFixed(4)}}',
    label: '随机小数',
    description: '0-1之间4位小数',
  },
  {
    syntax: '{{@new Date().toISOString()}}',
    label: 'ISO日期',
    description: 'ISO 8601格式',
  },
  {
    syntax: "{{@'test_' + Math.floor(Math.random()*1000)}}",
    label: '拼接表达式',
    description: '自定义JS表达式',
  },
];

interface VariableAssistantProps {
  /** 当前环境ID，用于获取环境变量 */
  environmentId?: number;
  /** 环境变量列表 */
  environmentVariables?: Array<{ key: string; value: string }>;
  /** 插入变量后的回调 */
  onInsert?: (syntax: string) => void;
}

const VariableAssistant: React.FC<VariableAssistantProps> = ({
  environmentId,
  environmentVariables = [],
  onInsert,
}) => {
  const [open, setOpen] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  // 测试变量
  const handleTest = useCallback(async (syntax: string) => {
    setTesting(prev => ({ ...prev, [syntax]: true }));
    setErrors(prev => ({ ...prev, [syntax]: '' }));

    try {
      const res = await proxyApi.testVariable({
        expression: syntax,
        environment_id: environmentId,
      });

      if (res.data?.code === 200 && res.data.data) {
        setTestResults(prev => ({
          ...prev,
          [syntax]: res.data.data.result,
        }));
      } else {
        setErrors(prev => ({
          ...prev,
          [syntax]: res.data?.message || '测试失败',
        }));
      }
    } catch (error: any) {
      setErrors(prev => ({
        ...prev,
        [syntax]: error.message || '测试失败',
      }));
    } finally {
      setTesting(prev => ({ ...prev, [syntax]: false }));
    }
  }, [environmentId]);

  // 插入变量到当前输入框
  const handleInsert = useCallback((syntax: string) => {
    const activeElement = document.activeElement as HTMLInputElement | HTMLTextAreaElement;

    if (activeElement && (activeElement.tagName === 'INPUT' || activeElement.tagName === 'TEXTAREA')) {
      const start = activeElement.selectionStart || 0;
      const end = activeElement.selectionEnd || 0;
      const value = activeElement.value;

      // 插入变量语法
      activeElement.value = value.slice(0, start) + syntax + value.slice(end);

      // 触发React的onChange事件
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value'
      )?.set;

      if (nativeInputValueSetter) {
        nativeInputValueSetter.call(activeElement, activeElement.value);
      }

      activeElement.dispatchEvent(new Event('input', { bubbles: true }));
      activeElement.dispatchEvent(new Event('change', { bubbles: true }));

      // 移动光标到插入内容之后
      activeElement.selectionStart = start + syntax.length;
      activeElement.selectionEnd = start + syntax.length;
      activeElement.focus();

      message.success(`已插入: ${syntax}`);
      onInsert?.(syntax);
    } else {
      // 如果没有聚焦的输入框，复制到剪贴板
      navigator.clipboard.writeText(syntax);
      message.success('已复制到剪贴板');
    }

    setOpen(false);
  }, [onInsert]);

  // 复制变量语法
  const handleCopy = useCallback((syntax: string) => {
    navigator.clipboard.writeText(syntax);
    message.success('已复制到剪贴板');
  }, []);

  // 渲染变量列表
  const renderVariableList = (variables: typeof builtinFunctions) => (
    <div className="variable-list">
      {variables.map((variable) => (
        <div
          key={variable.syntax}
          className="variable-item"
          onClick={() => handleCopy(variable.syntax)}
        >
          <span
            className="variable-syntax"
            onClick={(e) => {
              e.stopPropagation();
              handleInsert(variable.syntax);
            }}
            title="点击插入到输入框"
          >
            {variable.syntax}
          </span>
          <span className="variable-description">{variable.description}</span>
          <div className="variable-actions">
            <Button
              type="text"
              size="small"
              icon={<ThunderboltOutlined />}
              loading={testing[variable.syntax]}
              onClick={(e) => {
                e.stopPropagation();
                handleTest(variable.syntax);
              }}
              title="测试变量"
            />
            <Button
              type="text"
              size="small"
              icon={<CopyOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                handleCopy(variable.syntax);
              }}
              title="复制到剪贴板"
            />
          </div>
          {testResults[variable.syntax] && (
            <span className="variable-test-result" title={testResults[variable.syntax]}>
              {testResults[variable.syntax]}
            </span>
          )}
          {errors[variable.syntax] && (
            <span className="variable-test-error" title={errors[variable.syntax]}>
              {errors[variable.syntax]}
            </span>
          )}
        </div>
      ))}
    </div>
  );

  // 面板内容
  const content = (
    <div className="variable-assistant-popover">
      <Tabs
        className="variable-assistant-tabs"
        items={[
          {
            key: 'builtin',
            label: (
              <Space>
                <FunctionOutlined />
                内置函数
              </Space>
            ),
            children: renderVariableList(builtinFunctions),
          },
          {
            key: 'expression',
            label: (
              <Space>
                <ThunderboltOutlined />
                JS表达式
              </Space>
            ),
            children: renderVariableList(jsExpressions),
          },
          {
            key: 'env',
            label: (
              <Space>
                <EnvironmentOutlined />
                环境变量
              </Space>
            ),
            children: environmentVariables.length > 0 ? (
              <div className="variable-list">
                {environmentVariables.map((envVar) => (
                  <div
                    key={envVar.key}
                    className="variable-item"
                    onClick={() => handleCopy(`{{${envVar.key}}}`)}
                  >
                    <span
                      className="variable-syntax"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleInsert(`{{${envVar.key}}}`);
                      }}
                      title="点击插入到输入框"
                    >
                      {`{{${envVar.key}}}`}
                    </span>
                    <span className="variable-description">{envVar.value}</span>
                    <Button
                      type="text"
                      size="small"
                      icon={<CopyOutlined />}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleCopy(`{{${envVar.key}}}`);
                      }}
                      title="复制到剪贴板"
                    />
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ textAlign: 'center', color: 'var(--color-text-tertiary)', padding: '20px 0' }}>
                暂无环境变量
              </div>
            ),
          },
        ]}
      />
    </div>
  );

  return (
    <Popover
      content={content}
      title="内置变量与函数"
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="topRight"
      overlayClassName="variable-assistant-popover-overlay"
    >
      <FloatButton
        icon={<FunctionOutlined />}
        tooltip="内置变量与函数"
        className="variable-assistant-float-btn"
        style={{ right: 24, bottom: 80 }}
      />
    </Popover>
  );
};

export default VariableAssistant;
```

- [ ] **Step 3: 验证组件编译**

运行TypeScript检查：

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep VariableAssistant
```

Expected: 无VariableAssistant相关错误

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/VariableAssistant.tsx frontend/src/components/VariableAssistant.css
git commit -m "feat(ui): add VariableAssistant component"
```

---

### Task 4: 前端 - 集成VariableAssistant到IoTDataPushPlatform

**Files:**
- Modify: `frontend/src/components/IoTDataPushPlatform.tsx:0-8` (imports)
- Modify: `frontend/src/components/IoTDataPushPlatform.tsx:1270-1305` (remove old Tooltip)

**Interfaces:**
- Consumes: `VariableAssistant` 组件
- Produces: 集成后的IoTDataPushPlatform

- [ ] **Step 1: 添加VariableAssistant导入**

在IoTDataPushPlatform.tsx的import部分添加：

```typescript
import VariableAssistant from './VariableAssistant';
```

- [ ] **Step 2: 获取环境变量**

在组件内部，找到获取环境变量的逻辑，确保可以传递给VariableAssistant。查看当前环境参数的获取方式：

```typescript
// 在组件内部添加环境变量获取
const currentEnvVars = useMemo(() => {
  const env = environments.find(e => e.id === currentEnvironmentId);
  return env?.parameters || [];
}, [environments, currentEnvironmentId]);
```

- [ ] **Step 3: 移除旧的Tooltip**

删除请求体标题旁边的QuestionCircleOutlined Tooltip（约1274-1304行）：

```tsx
// 删除以下代码
<Tooltip
  title={
    <div style={{ maxWidth: 660, fontSize: 12, lineHeight: 1.8 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>1. 内置函数 {'{{$function}}'}</div>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <tbody>
          <tr><td style={{ whiteSpace: 'nowrap', paddingRight: 12 }}><code>{'{{$timestamp}}'}</code></td><td>毫秒时间戳</td></tr>
          <tr><td><code>{'{{$now}}'}</code></td><td>秒级时间戳</td></tr>
          <tr><td><code>{'{{$date}}'}</code></td><td>当前日期 (YYYY-MM-DD)</td></tr>
          <tr><td><code>{"{{$date('YYYY-MM-DD HH:mm:ss')}}"}</code></td><td>自定义格式日期</td></tr>
          <tr><td><code>{'{{$randomInt}}'}</code></td><td>0~100 随机整数</td></tr>
          <tr><td><code>{'{{$randomInt(1,1000)}}'}</code></td><td>指定范围随机整数</td></tr>
          <tr><td><code>{'{{$uuid}}'}</code></td><td>UUID v4</td></tr>
        </tbody>
      </table>
      <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>2. JS 表达式 {'{{@expression}}'}</div>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <tbody>
          <tr><td style={{ whiteSpace: 'nowrap', paddingRight: 12 }}><code>{'{{@Date.now()}}'}</code></td><td>JS 时间戳</td></tr>
          <tr><td><code>{'{{@Math.random().toFixed(4)}}'}</code></td><td>随机小数</td></tr>
          <tr><td><code>{'{{@new Date().toISOString()}}'}</code></td><td>ISO 日期</td></tr>
          <tr><td><code>{"{{@'test_' + Math.floor(Math.random()*1000)}}"}</code></td><td>拼接表达式</td></tr>
        </tbody>
      </table>
    </div>
  }
  overlayClassName="iot-tooltip"
  overlayInnerStyle={{ maxWidth: 680 }}
>
  <QuestionCircleOutlined style={{ color: 'var(--color-text-tertiary)', cursor: 'pointer', fontSize: 13 }} />
</Tooltip>
```

- [ ] **Step 4: 添加VariableAssistant组件**

在组件的return语句末尾（在`</div>`闭合标签之前）添加：

```tsx
<VariableAssistant
  environmentId={currentEnvironmentId ? Number(currentEnvironmentId) : undefined}
  environmentVariables={currentEnvVars}
/>
```

- [ ] **Step 5: 验证编译**

运行TypeScript检查：

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -E "IoTDataPushPlatform|VariableAssistant"
```

Expected: 无相关错误

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/IoTDataPushPlatform.tsx
git commit -m "feat(iot): integrate VariableAssistant into IoTDataPushPlatform"
```

---

### Task 5: 测试与验证

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

- [ ] **Step 3: 测试悬浮按钮**

1. 打开IoT数据推送平台页面
2. 确认右下角显示悬浮按钮（FunctionOutlined图标）
3. 点击按钮，确认Popover面板弹出

- [ ] **Step 4: 测试变量列表**

1. 确认Tabs分组显示：内置函数、JS表达式、环境变量
2. 确认每个变量显示语法和说明
3. 确认环境变量Tab正确显示当前环境的变量

- [ ] **Step 5: 测试变量测试功能**

1. 点击`{{$timestamp}}`旁边的测试按钮
2. 确认显示毫秒时间戳结果
3. 点击`{{$uuid}}`旁边的测试按钮
4. 确认显示UUID结果

- [ ] **Step 6: 测试插入功能**

1. 点击URL输入框使其获得焦点
2. 点击悬浮按钮打开面板
3. 点击`{{$timestamp}}`语法文本
4. 确认变量语法插入到URL输入框
5. 确认面板自动关闭

- [ ] **Step 7: 测试复制功能**

1. 点击变量行的其他位置（非语法文本）
2. 确认显示"已复制到剪贴板"提示
3. 粘贴验证复制内容正确

- [ ] **Step 8: 最终Commit**

```bash
git add -A
git commit -m "feat: complete variable assistant feature"
```

---

## 验收标准

- [ ] 悬浮按钮正确显示在右下角
- [ ] 点击按钮展开变量面板
- [ ] 变量按Tabs分组显示
- [ ] 每个变量可测试并显示结果
- [ ] 点击语法文本可插入到输入框
- [ ] 点击其他位置可复制到剪贴板
- [ ] 环境变量正确显示
- [ ] 动画效果流畅
- [ ] 错误处理完善
