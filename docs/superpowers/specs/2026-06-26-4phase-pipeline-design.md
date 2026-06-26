# 4 阶段 AI 测试用例生成 Pipeline 设计文档

**日期:** 2026-06-26
**状态:** 已批准
**来源:** 从 [testflow](https://github.com/Ggbond626/testflow) 项目选择性引入

---

## 1. 概述

### 1.1 目标

将 testflow 的 4 阶段 AI 生成 pipeline 融入当前系统，提升测试用例生成的质量和结构性。保留现有 MCP 工具和 Skills Hub 能力，同时引入：

- **需求拆分能力** — 将需求文档拆分为原子化需求点
- **测试点中间层** — 在需求点和测试用例之间增加测试点层
- **用例优化能力** — AI 自动优化生成的测试用例质量
- **提示词模板升级** — 采用 testflow 的 XML 结构化提示词

### 1.2 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 融合策略 | 选择性引入 | 保留现有 MCP/Skills 能力 |
| 代码组织 | 独立 pipeline 模块 | 不影响现有生成功能 |
| LLM 架构 | 统一 LangChain | 复用现有基础设施 |
| 中间产物 | 完整持久化 | 支持分阶段编辑和重新生成 |

---

## 2. 架构设计

### 2.1 生成流程

```
用户输入需求文本
    ↓
[阶段1] 需求拆分 Agent (LangChain + structured_output)
    → RequirementPoint[] 存入 DB
    ↓
[阶段2] 测试点生成 Agent (LangChain + structured_output)
    → TestPoint[] 存入 DB（每个含 test_type + design_method）
    ↓
[阶段3] 用例设计 Agent (LangChain + structured_output + MCP + Skills)
    → TestCase[] 存入 DB（5-8步，二八法则）
    ↓
[阶段4] 用例优化 Agent (LangChain + structured_output)
    → 更新 TestCase[]（质量提升）
```

### 2.2 与现有系统的关系

```
现有流程（保持不变）:
  用户需求 → LangChain Agent (ReAct/Direct) → TestCase[]
  端点: POST /api/testcases/generate

新增流程（独立模块）:
  需求文档 → 4-Phase Pipeline → RequirementPoint + TestPoint + TestCase
  端点: POST /api/testcases/generate-v2
```

两套流程完全独立，用户可在前端选择生成模式。

### 2.3 模块结构

```
backend/app/pipeline/
├── __init__.py
├── prompts.py              # 4 个 agent 的提示词模板
├── requirement_splitter.py # Agent 1: 需求拆分
├── test_point_generator.py # Agent 2: 测试点生成
├── test_case_designer.py   # Agent 3: 用例设计（集成 MCP + Skills）
├── test_case_optimizer.py  # Agent 4: 用例优化
├── pipeline_runner.py      # Pipeline 编排器
└── schemas.py              # Pipeline 专用 Pydantic schema
```

---

## 3. 数据模型

### 3.1 新增 RequirementPoint 模型

```python
class RequirementPoint(SQLModel, table=True):
    __tablename__ = "requirementpoint"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    content: str                    # 需求点描述
    module: str = ""                # 所属功能模块
    priority: str = "medium"        # high/medium/low
    order_index: int = 0            # 排序
    created_at: datetime = Field(default_factory=lambda: datetime.now(shanghai_tz))
```

### 3.2 新增 TestPoint 模型

```python
class TestPoint(SQLModel, table=True):
    __tablename__ = "testpoint"

    id: Optional[int] = Field(default=None, primary_key=True)
    requirement_point_id: int = Field(foreign_key="requirementpoint.id", index=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    content: str                    # 测试点描述
    test_type: str = "functional"   # functional/performance/security/compatibility
    design_method: str = ""         # equivalence_partitioning/boundary_value/decision_table/state_transition
    priority: str = "medium"        # high/medium/low
    created_at: datetime = Field(default_factory=lambda: datetime.now(shanghai_tz))
```

### 3.3 扩展 TestCase 模型

在现有 TestCase 模型上新增字段：

```python
test_point_id: Optional[int] = Field(default=None, foreign_key="testpoint.id", index=True)
test_type: Optional[str] = None       # 测试类别（继承自测试点）
design_method: Optional[str] = None   # 设计方法（继承自测试点）
```

### 3.4 关系链路

```
Session (1) ──→ (N) RequirementPoint (1) ──→ (N) TestPoint (1) ──→ (N) TestCase
```

---

## 4. 提示词设计

从 testflow 移植 4 个 agent 的提示词，适配当前系统格式：

### 4.1 Agent 1: 需求拆分专家

**系统提示词要点：**
- 角色：需求分析专家
- 4 阶段流程：需求理解 → 需求拆分 → 优先级评估 → 质量检查
- 输出：`{ "requirement_points": [{ "content", "module", "priority", "order_index" }] }`
- 约束：原子性、独立性、可测试性

### 4.2 Agent 2: 测试点生成专家

**系统提示词要点：**
- 角色：测试分析专家
- 每个测试点必须指定测试类别 + 设计方法
- 遵循二八法则：覆盖 80% 业务核心的 20% 测试点
- 支持补充生成模式（识别已有测试点）
- 输出：`{ "test_points": [{ "content", "test_type", "design_method", "priority" }] }`

### 4.3 Agent 3: 测试用例设计专家

**系统提示词要点：**
- 角色：测试用例设计专家
- 严格按测试点指定的设计方法设计用例
- 步骤限制：5-8 步，遵循二八法则
- 批次生成：返回数量 = 输入测试点数量
- 集成 MCP 工具和 Skills Hub（复用现有能力）
- 输出：`{ "test_cases": [{ "test_point_id", "title", "description", "preconditions", "test_steps" }] }`

### 4.4 Agent 4: 测试用例优化专家

**系统提示词要点：**
- 角色：测试用例优化专家
- 5 维度评估：步骤数量、步骤质量、完整性、冗余度、逻辑连贯性
- 分类策略：简陋→扩展、完善→保持、冗长→精简、质量差→提升
- 克制性优化：只优化有问题的用例
- 输出：`{ "optimized_cases": [{ "id", "title", "description", "preconditions", "test_steps" }] }`

---

## 5. API 设计

### 5.1 新增端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/testcases/generate-v2` | 启动 4 阶段 pipeline |
| GET | `/api/testcases/pipeline/{task_id}/status` | 查询 pipeline 进度 |
| GET | `/api/testcases/sessions/{session_id}/requirement-points` | 获取需求点列表 |
| GET | `/api/testcases/sessions/{session_id}/test-points` | 获取测试点列表 |
| PUT | `/api/testcases/requirement-points/{id}` | 编辑需求点 |
| PUT | `/api/testcases/test-points/{id}` | 编辑测试点 |
| POST | `/api/testcases/test-points/{id}/regenerate` | 重新生成单个测试点的用例 |

### 5.2 generate-v2 请求格式

```json
{
  "session_id": 123,
  "requirement_text": "需求描述文本...",
  "module_id": 1,
  "selected_skills": ["testcase-design", "boundary-testing"]
}
```

### 5.3 pipeline 进度响应

```json
{
  "task_id": "uuid",
  "status": "running",
  "current_phase": 2,
  "total_phases": 4,
  "phase_name": "测试点生成",
  "progress": 45,
  "message": "正在生成测试点... (3/5 需求点已完成)",
  "result": null
}
```

---

## 6. Pipeline Runner 编排

### 6.1 流程编排

```python
class PipelineRunner:
    async def run(self, session_id, requirement_text, module_id, selected_skills):
        # 阶段 1: 需求拆分 (0-25%)
        requirement_points = await self.splitter.analyze(requirement_text)
        self.save_requirement_points(session_id, requirement_points)
        self.update_progress(25, "需求拆分完成")

        # 阶段 2: 测试点生成 (25-50%)
        test_points = await self.tp_generator.generate(requirement_points)
        self.save_test_points(session_id, test_points)
        self.update_progress(50, "测试点生成完成")

        # 阶段 3: 用例设计 (50-85%)
        # 分批处理，每批 3 个测试点
        test_cases = await self.tc_designer.design_batch(
            test_points, selected_skills=selected_skills
        )
        self.save_test_cases(session_id, test_cases)
        self.update_progress(85, "用例设计完成")

        # 阶段 4: 用例优化 (85-100%)
        optimized = await self.optimizer.optimize(test_cases)
        self.update_test_cases(optimized)
        self.update_progress(100, "生成完成")
```

### 6.2 错误处理

- 每个阶段独立处理错误，失败时记录进度并返回已完成的部分结果
- 支持从失败阶段恢复重试
- 单个需求点/测试点生成失败不影响其他并行任务

### 6.3 并发控制

- 阶段 2 和 3 支持并发处理（使用 `asyncio.Semaphore`）
- 默认并发数从现有配置读取
- 批次大小：测试点生成每批 1 个需求点，用例设计每批 3 个测试点

---

## 7. 前端设计

### 7.1 生成模式切换

在 `TestCaseGenerator` 组件中增加模式选择：

- **快速生成**（现有模式）：单次调用，直接生成测试用例
- **深度生成**（新模式）：4 阶段 pipeline，生成需求点→测试点→测试用例→优化

### 7.2 进度 UI

4 阶段进度条，显示：
- 当前阶段名称和图标
- 阶段内进度百分比
- 已完成/总数统计

### 7.3 中间产物查看

生成完成后，展示完整链路视图：
- 需求点列表（可编辑）
- 测试点列表（可编辑，显示测试类别和设计方法）
- 测试用例列表（最终结果）

支持：
- 点击需求点查看关联的测试点
- 点击测试点查看关联的测试用例
- 重新生成单个测试点的用例

---

## 8. 实现顺序

1. **数据库模型** — 新增 RequirementPoint、TestPoint，扩展 TestCase
2. **Schemas** — Pipeline 专用的 Pydantic 模型
3. **Prompts** — 从 testflow 移植并适配 4 个 agent 提示词
4. **Agent 1-4** — 实现 4 个 LangChain agent
5. **Pipeline Runner** — 编排器
6. **API 路由** — 新增端点
7. **前端** — 模式切换、进度 UI、中间产物查看
8. **Alembic 迁移** — 数据库迁移脚本

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 4 次 LLM 调用延迟高 | 用户等待时间长 | 异步任务 + 进度回调，分批并发 |
| 中间产物占用存储 | 数据库膨胀 | 定期清理旧 session 的中间产物 |
| 提示词效果不如 testflow | 生成质量不达标 | A/B 测试，逐步调优提示词 |
| MCP/Skills 集成复杂 | 阶段 3 实现困难 | 先实现基础版本，后续迭代集成 |
