# 需求点 / 测试点 手动管理与级联更新 设计文档

**日期:** 2026-07-09
**状态:** 已与用户确认（brainstorming）
**关联:** `docs/superpowers/specs/2026-06-26-4phase-pipeline-design.md`

---

## 1. 背景与目标

现有 4 阶段 pipeline（`generate-v2`）已能自动生成 `需求点(RequirementPoint) → 测试点(TestPoint) → 测试用例(TestCase)` 三层链路，数据模型外键（`requirement_point_id`、`test_point_id`）已建好。但**缺少手动维护与级联更新能力**：

- 仅支持 `PUT` 编辑，**无新增 / 删除**接口；无"从需求点重生成测试点"接口。
- 现有 `POST /test-points/{id}/regenerate` 每次都 `TestCase(...)` 新建，**不删除旧用例 → 产生重复**（用户最在意的"不重复、更新正确"问题）。

本设计补齐：**手动 CRUD + 显式按钮触发的级联重生成 + 就地更新去重算法**，并支持手动重生成 **API 接口测试用例**。

### 已确认的设计决策

| 维度 | 决策 |
|------|------|
| 级联触发 | **显式按钮**：需求点/测试点旁提供「重新生成测试点」「重新生成用例」按钮，点击才调 LLM |
| 更新策略 | **就地更新**：按位置顺序匹配已有数据，更新字段、补建缺失、删除多余（id 稳定，零重复） |
| 需求点级深度 | **一步到位**：重生成测试点后自动为每个新测试点生成用例（2 阶段级联一次性完成） |
| 删除行为 | **级联删除**：删需求点→其下测试点+用例一并删；删测试点→其下用例一并删 |
| API 用例 | **支持**：手动重生成也能产出 API 接口测试用例（需重建 API 上下文 + ApiScenario 联动） |

---

## 2. 数据模型变更

在 `backend/db/models.py` 的 `RequirementPoint` 与 `TestPoint` 上**新增两个 Optional 字段**（利用现有 `_migrate_missing_columns` 自动 ALTER，无需手写迁移）：

```python
# RequirementPoint / TestPoint 均新增
api_project_id: Optional[int] = Field(default=None, description="关联的 API 项目ID（接口测试模式）")
api_endpoint_ids: Optional[str] = Field(default=None, description="关联的 API 接口ID（逗号分隔，接口测试模式）")
```

- pipeline 生成时由 `PipelineRunner` 写入（每轮 run 已知 `api_project_id` / `api_endpoint_ids`）。
- 手动新增测试点时可作为可选入参传入（前端让用户选 API 项目 + 接口）。
- 重生成时据此判断是否为 API 测试并重建上下文。

---

## 3. 后端接口（backend/app/routes/pipeline.py，前缀 `/api/testcases`）

### 需求点
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/requirement-points` | 手动新增（body: `session_id, content, module, priority, order_index?`） |
| PUT | `/requirement-points/{id}` | 编辑（已有，保留） |
| DELETE | `/requirement-points/{id}` | **级联删除**其下测试点 + 这些测试点的用例 |
| POST | `/requirement-points/{id}/regenerate` | 用该需求点 `content` 调 `TestPointGenerator` 生成测试点 → 就地更新 → 自动为每个测试点生成用例（一步到位） |

### 测试点
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/test-points` | 手动新增（body: `session_id, requirement_point_id, content, test_type, design_method, priority, api_project_id?, api_endpoint_ids?`） |
| PUT | `/test-points/{id}` | 编辑（已有，保留） |
| DELETE | `/test-points/{id}` | **级联删除**其下用例 |
| POST | `/test-points/{id}/regenerate` | **重写**：用父需求点 `content`（含 API 上下文）调 `TestCaseDesigner` 生成用例并就地更新（修复重复 bug） |

所有写操作按 `user_id` / `session_id` 做归属校验，与现有路由权限模型一致。

---

## 4. 就地更新 / 去重算法（核心）

按**位置顺序匹配**的确定性策略，保证"不重复、更新正确"：

### 4.1 测试点 → 用例（`_inplace_update_cases`）
1. `existing = TestCase.where(test_point_id == tp.id).order_by(id)`
2. 遍历 LLM 返回的用例 `generated`（**跳过空 `{}`**，来自 `design_batch` 的补齐项）：
   - `i < len(existing)` → **更新** `existing[i]` 字段（`case_name`/`steps`/`preset_conditions`/`expected_results`/`case_level`/`test_type`/`design_method`），保留其 `id`
   - `i >= len(existing)` → **新建** `TestCase`（`test_point_id=tp.id`）
3. `delete existing[len(generated):]` → 删除**多余**旧用例

### 4.2 需求点 → 测试点（`_inplace_update_test_points`）
- 同理对测试点做就地更新（保留 `id`）；多出的测试点**先级联删其用例**再删自身
- 更新/新建的每个测试点，再调用 §4.1 为其生成用例（传入父需求点 `content`）→ 一步到位

### 4.3 API 用例联动
- 若 `test_point.api_project_id` 存在且 `api_endpoint_ids` 解析非空 → `is_api_test=True`，用 `build_api_context` 重建 `api_context` + `endpoint_index_to_id`
- `design_batch(is_api_test=True, api_context=...)` 返回的 step 用 `endpoint_ref`（接口编号），需转成 `endpoint_id`
- **复用** `PipelineRunner` 中已有的 ApiScenario 创建逻辑（抽取为 `_sync_api_scenarios(self, test_cases, endpoint_index_to_id)` 方法）：
  - 已有用例且带 `scenario_id` → **更新**既有 ApiScenario 的 steps（保持 scenario_id 稳定，不新建）
  - 新建用例 → 新建 ApiScenario 并回填 `scenario_id`

---

## 5. 实现方式（复用 PipelineRunner）

在 `PipelineRunner` 中新增两个方法，内部复用成熟的生成器、designer 与 ApiScenario 逻辑：

- `async def regenerate_test_point(self, test_point_id)` — 加载测试点 + 父需求点 `content` + API 上下文 → `design_batch` → §4.1 就地更新
- `async def regenerate_requirement_point(self, requirement_point_id)` — 加载需求点 `content` → `TestPointGenerator.generate` → §4.2 就地更新测试点 → 对每个测试点调用 §4.1

路由层把现有 `regenerate_test_point_cases` 改为调用 `PipelineRunner.regenerate_test_point`，并新增需求点级与 CRUD 端点。

---

## 6. 前端 UI（frontend/src/components/test-case/）

在测试用例页新增"需求点 / 测试点"管理面板（独立 `RequirementTestPointManager` 组件或并入 `TestCaseGenerator`）：

- **三层联动**：需求点列表 → 选中展开其测试点 → 选中展开其用例，展示各级关联计数
- 每行操作：
  - 需求点：`新增测试点`、`重新生成测试点`（级联用例）、`编辑`、`删除`（二次确认）
  - 测试点：`重新生成用例`、`编辑`、`删除`
  - 顶部：`新增需求点`
- 新增/编辑弹窗包含：内容、模块、优先级；API 测试点额外可选 API 项目 + 接口
- 重生成走异步 loading（简单 loading 即可，无需完整 4 阶段进度条），完成后刷新列表并提示结果

---

## 7. 边界与完整性

- **级联删除**：单事务内先删子后删父，避免外键悬挂；删除需求点先删其测试点下的用例 → 测试点 → 需求点
- **手动新增测试点**：强制要求选父需求点，保证关联链路完整
- **空结果**：LLM 返回空 `{}` 用例不创建；某测试点生成 0 条用例时保留测试点但清空其旧用例
- **API 场景稳定**：就地更新时复用既有 `scenario_id`，不新建孤儿场景
- **权限**：所有写操作校验 `user_id` / `session_id` 归属

---

## 8. 实现顺序

1. 数据模型：给 `RequirementPoint` / `TestPoint` 加 `api_project_id` / `api_endpoint_ids` 字段（自动迁移）
2. `PipelineRunner`：抽取 `_sync_api_scenarios`；新增 `regenerate_test_point` / `regenerate_requirement_point`
3. 就地更新辅助函数 `_inplace_update_cases` / `_inplace_update_test_points`
4. `backend/app/routes/pipeline.py`：新增 CRUD（create/delete）+ 两个 regenerate 端点；改写旧 `regenerate_test_point_cases`
5. 前端：三层联动管理面板 + 弹窗 + 异步重生成
6. 自测：新增→级联重生成→就地更新（验证 id 稳定、无重复）→级联删除验证无孤儿数据
