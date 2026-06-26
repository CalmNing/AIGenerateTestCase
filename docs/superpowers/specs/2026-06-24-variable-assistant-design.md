# Variable Assistant - 内置变量悬浮助手设计文档

## 1. 概述

### 1.1 背景

IoT数据推送平台当前将内置函数列表显示在请求体标题旁边的Tooltip中（QuestionCircleOutlined图标）。这种方式存在以下问题：

- 用户需要悬停才能查看变量列表
- 无法直接测试变量效果
- 无法快速插入变量到输入框

### 1.2 目标

将内置函数升级为全局悬浮按钮，提供以下功能：

1. **查看变量** - 点击按钮展示所有可用变量列表
2. **测试效果** - 每个变量支持实时测试，显示当前值
3. **快速插入** - 点击变量语法可插入到当前聚焦的输入框

## 2. 设计方案

### 2.1 组件架构

创建独立的 `VariableAssistant` 组件，通过props与IoTDataPushPlatform交互。

```
IoTDataPushPlatform
    └── VariableAssistant
        ├── FloatButton (悬浮按钮)
        └── Popover (变量面板)
            ├── Tabs (变量分组)
            │   ├── 内置函数
            │   ├── JS表达式
            │   └── 环境变量
            └── 变量列表
                ├── 语法文本 (可点击插入)
                ├── 说明文字
                └── 测试按钮
```

### 2.2 UI形式

使用Ant Design的FloatButton.Group组件，点击展开Popover弹出面板。

**悬浮按钮位置**：页面右下角固定定位

**触发方式**：点击按钮展开/收起面板

### 2.3 变量面板布局

使用Tabs分组显示变量：

#### Tab 1: 内置函数

| 语法 | 说明 | 示例值 |
|------|------|--------|
| `{{$timestamp}}` | 毫秒时间戳 | `1719235200000` |
| `{{$now}}` | 秒级时间戳 | `1719235200` |
| `{{$date}}` | 当前日期 (YYYY-MM-DD) | `2026-06-24` |
| `{{$date('YYYY-MM-DD HH:mm:ss')}}` | 自定义格式日期 | `2026-06-24 14:30:00` |
| `{{$randomInt}}` | 0~100 随机整数 | `42` |
| `{{$randomInt(1,1000)}}` | 指定范围随机整数 | `567` |
| `{{$uuid}}` | UUID v4 | `550e8400-e29b-41d4-a716-446655440000` |

#### Tab 2: JS表达式

| 语法 | 说明 | 示例值 |
|------|------|--------|
| `{{@Date.now()}}` | JS时间戳 | `1719235200000` |
| `{{@Math.random().toFixed(4)}}` | 随机小数 | `0.7234` |
| `{{@new Date().toISOString()}}` | ISO日期 | `2026-06-24T06:30:00.000Z` |
| `{{@'test_' + Math.floor(Math.random()*1000)}}` | 拼接表达式 | `test_456` |

#### Tab 3: 环境变量

从当前环境参数中获取用户定义的变量列表。

### 2.4 测试功能

每个变量旁边有"测试"按钮：

- 点击后调用后端API执行变量替换
- 显示变量的当前值
- 时间戳、随机数等每次测试重新计算

### 2.5 插入方式

**交互规则**：

- 点击变量语法文本（蓝色链接样式）→ 插入到当前聚焦的输入框
- 点击变量行其他位置 → 复制到剪贴板

**插入逻辑**：

1. 获取当前聚焦的输入框（URL/请求头/请求体）
2. 在光标位置插入变量语法
3. 触发输入框的onChange事件
4. 显示成功提示

## 3. 后端API

### 3.1 测试变量API

**端点**: `POST /api/proxy/test-variable`

**请求体**:
```json
{
  "expression": "{{$timestamp}}",
  "environment_id": 1  // 可选，用于测试环境变量
}
```

**响应**:
```json
{
  "code": 200,
  "data": {
    "expression": "{{$timestamp}}",
    "result": "1719235200000"
  }
}
```

### 3.2 实现逻辑

复用现有 `_substitute_builtins()` 和 `substitute_variables()` 函数，执行变量替换并返回结果。

## 4. 文件结构

```
frontend/src/components/
├── VariableAssistant.tsx      # 悬浮助手组件
├── VariableAssistant.css      # 样式文件
└── IoTDataPushPlatform.tsx    # 修改：集成VariableAssistant

backend/app/routes/
└── proxy.py                   # 修改：新增测试变量API
```

## 5. 组件接口

### 5.1 VariableAssistant Props

```typescript
interface VariableAssistantProps {
  /** 当前环境ID，用于获取环境变量 */
  environmentId?: number;
  /** 环境变量列表 */
  environmentVariables?: Array<{ key: string; value: string }>;
  /** 插入变量后的回调 */
  onInsert?: (syntax: string) => void;
}
```

### 5.2 使用示例

```tsx
<VariableAssistant
  environmentId={currentEnvironmentId}
  environmentVariables={currentEnvVars}
  onInsert={(syntax) => {
    // 可选的插入后回调
    console.log('Inserted:', syntax);
  }}
/>
```

## 6. 样式设计

### 6.1 悬浮按钮

- 位置：右下角 (right: 24px, bottom: 80px)
- 图标：FunctionOutlined
- 阴影：中等阴影
- hover效果：轻微放大

### 6.2 变量面板

- 宽度：380px
- 最大高度：500px
- 内边距：12px
- 变量行：hover时显示背景色

### 6.3 变量语法样式

- 颜色：主色调蓝色
- 字体：等宽字体
- 光标：pointer
- hover：下划线

## 7. 动画效果

- 悬浮按钮：hover时轻微放大 (scale: 1.05)
- 面板展开：淡入 + 轻微上移
- 测试结果：数字跳动动画

## 8. 错误处理

- 测试失败：显示错误提示，保留变量原值
- 插入失败：显示降级为复制到剪贴板
- API超时：显示超时提示，建议重试

## 9. 可访问性

- 悬浮按钮：aria-label="内置变量与函数"
- 变量语法：role="button"，aria-label包含变量说明
- 测试按钮：aria-label="测试变量 {语法}"

## 10. 实施步骤

1. 创建VariableAssistant组件和样式
2. 实现变量列表展示
3. 实现测试功能（调用后端API）
4. 实现插入功能
5. 集成到IoTDataPushPlatform
6. 新增后端测试变量API
7. 测试和优化

## 11. 验收标准

- [ ] 悬浮按钮正确显示在右下角
- [ ] 点击按钮展开变量面板
- [ ] 变量按Tabs分组显示
- [ ] 每个变量可测试并显示结果
- [ ] 点击语法文本可插入到输入框
- [ ] 点击其他位置可复制到剪贴板
- [ ] 环境变量正确显示
- [ ] 动画效果流畅
- [ ] 错误处理完善
