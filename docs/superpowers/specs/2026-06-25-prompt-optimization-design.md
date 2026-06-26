# 系统提示词优化设计

## 背景

当前 `prompts.py` 中的系统提示词存在以下问题：
1. 用例覆盖不全：只有笼统的"1 成功 + 2 异常"，缺少具体场景指导
2. 步骤描述模糊：AI 生成的步骤过于简单，缺少具体操作细节
3. 断言简单：只展示基本断言，缺少边界值、字段类型等验证
4. 参数值不合理：AI 生成的测试数据不符合业务逻辑

## 目标

优化提示词结构和内容，提高 AI 生成测试用例的质量：
- 覆盖更全面（成功、异常、边界、端到端）
- 步骤描述更具体（有明确的输入数据和操作）
- 断言更精准（覆盖关键字段和边界条件）
- 参数值更合理（符合业务逻辑和数据格式）

## 优化策略

### 1. 分层结构

将提示词按以下层次组织：

```
## 角色定义
你是软件测试专家...

## 核心原则
- 测试用例必须可执行
- 步骤必须具体明确
- 断言必须验证关键点

## 用例结构规范
- 用例名称、级别、前置条件、步骤、预期结果

## 测试设计方法
- 等价类、边界值、场景法、错误推测法

## API 测试专项规则
- api_call 步骤格式
- post_actions 变量提取
- 断言规则

## 场景化指导

### CRUD 接口测试
- 成功场景：正常创建、查询、更新、删除
- 异常场景：必填字段缺失、字段类型错误、字段超长、唯一性冲突
- 边界场景：空字符串、极值、特殊字符
- 关联场景：创建后查询、更新后验证、删除后确认

### 复杂业务流程测试
- 正常流程：完整业务路径
- 分支流程：条件判断的各个分支
- 异常中断：流程中某一步失败
- 并发场景：同时操作同一资源

## 参数值生成规则
- 用户名：test_user_001 格式
- 邮箱：符合 RFC 5322 格式
- 密码：包含大小写、数字、特殊字符
- 金额：使用有意义的数值
- ID：使用 1、100 等典型值

## 断言密度要求
- 每个步骤至少 2 条断言
- 成功场景：状态码 + 关键字段存在 + 字段值正确
- 异常场景：状态码 + 错误码 + 错误消息

## 完整示例
- CRUD 示例：用户管理增删改查
- 复杂流程示例：订单审批流程
```

### 2. 关键改进点

| 改进项 | 当前状态 | 优化后 |
|--------|----------|--------|
| 覆盖要求 | "1 成功 + 2 异常" | 按场景类型给出具体测试点清单 |
| 步骤描述 | 简单的"修改用户名称" | "将用户名从 test_user 修改为 updated_user，验证修改生效" |
| 断言密度 | 未明确要求 | 每步骤至少 2 条断言 |
| 参数值 | 通用值 test_user | 按类型生成合理值（邮箱、密码等） |
| 场景指导 | 无 | CRUD 和复杂流程分别给出测试点 |

### 3. 新增示例

#### 示例 1：CRUD 接口（用户管理）

```
用例名称: "创建用户成功 - 完整参数"
用例级别: 1
前置条件: []
步骤:
  - type: api_call
    endpoint_ref: 1
    description: "使用完整有效参数创建新用户"
    body: '{"username": "test_user_001", "email": "test_user_001@example.com", "password": "P@ssw0rd123", "phone": "13800138000"}'
    assertions:
      - type: status_code
        value: 200
      - type: jsonpath_equals
        jsonpath: "$.code"
        value: 0
      - type: jsonpath_exists
        jsonpath: "$.data.id"
      - type: jsonpath_exists
        jsonpath: "$.data.username"
预期结果: ["创建成功，返回用户ID和基本信息"]
```

#### 示例 2：异常场景（必填字段缺失）

```
用例名称: "创建用户失败 - 缺少必填字段 username"
用例级别: 3
前置条件: []
步骤:
  - type: api_call
    endpoint_ref: 1
    description: "不传 username 字段，验证必填校验"
    body: '{"email": "test@example.com", "password": "P@ssw0rd123"}'
    assertions:
      - type: status_code
        value: 400
      - type: jsonpath_exists
        jsonpath: "$.message"
预期结果: ["返回参数校验错误，提示 username 为必填字段"]
```

#### 示例 3：复杂流程（订单审批）

```
用例名称: "订单审批成功流程"
用例级别: 1
前置条件:
  - api_call:
      endpoint_ref: 1
      description: "创建测试订单"
      body: '{"product_id": 1001, "quantity": 2, "amount": 199.00}'
      post_actions:
        - type: extract_jsonpath
          key: order_id
          jsonpath: "$.data.id"
步骤:
  - type: api_call
    endpoint_ref: 2
    description: "提交订单进入审批流程"
    parameters:
      - key: id
        value: "{{order_id}}"
        in: path
    assertions:
      - type: status_code
        value: 200
      - type: jsonpath_equals
        jsonpath: "$.data.status"
        value: "PENDING"
  - type: api_call
    endpoint_ref: 3
    description: "审批通过订单"
    body: '{"order_id": "{{order_id}}", "approved": true, "comment": "审批通过"}'
    assertions:
      - type: status_code
        value: 200
      - type: jsonpath_equals
        jsonpath: "$.data.status"
        value: "APPROVED"
预期结果: ["订单状态从 PENDING 变为 APPROVED"]
```

## 不涉及

- 不修改前端
- 不修改后端逻辑
- 不修改数据模型
