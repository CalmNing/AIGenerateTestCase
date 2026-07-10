# 需求点 / 测试点管理功能 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让用户手动维护需求点 / 测试点，并通过显式按钮从需求点级联重生成测试点（一步到位再到用例）、从测试点重生成用例；重生成采用"就地更新"策略（按位置匹配，更新已有、补建缺失、删除多余），杜绝重复用例，并保证删除时级联清理无孤儿数据。完整支持功能测试与接口（API）测试两类用例。

**Architecture:** 复用现有 4 阶段 pipeline 的 `PipelineRunner` 与 `TestPointGenerator`/`TestCaseDesigner`/`build_api_context`。在 `PipelineRunner` 中新增"就地更新"核心方法，把 pipeline 里已有的用例写库 + `ApiScenario` 创建逻辑抽取复用。路由层补齐手动 CRUD + 级联删除 + 把 `regenerate` 改为异步任务（与 `generate-v2` 一致的 `task_id` + 轮询）。前端新增 `RequirementTestPointManager` 三层联动面板，复用既有 `generateV2`/`getPipelineStatus` 轮询模式。数据模型给 `RequirementPoint` / `TestPoint` 各加 2 个 `Optional` 字段（`api_project_id`、`api_endpoint_ids`）以支持手动 API 用例重生成，数据库自动迁移机制会在启动时 `ALTER TABLE` 补齐，无需手写 alembic。

**Tech Stack:** FastAPI + SQLModel + SQLite（自动迁移）、LangChain 异步 agent、React 18 + TypeScript + Ant Design + axios。

**确认的设计决策（来自 brainstorming）：**
1. 触发方式：显式按钮（不自动级联）。
2. 去重策略：就地更新（按位置顺序匹配，更新/补建/删除）。
3. 需求点级重生成：一步到位（重生成测试点后自动为每个测试点生成用例）。
4. 删除：级联删除（删需求点→删其下所有测试点及用例；删测试点→删其下所有用例）。
5. 手动重生成默认支持 API 用例：测试点带 `api_project_id`/`api_endpoint_ids` 时重建接口上下文，走 `is_api_test=True` 路径并复用 `ApiScenario` 创建。

---

## Task 1：数据模型加 API 上下文字段

**Files:**
- Modify: `backend/db/models.py`（`RequirementPoint` 约 101-112 行，`TestPoint` 约 115-127 行）

**Step 1：给 `RequirementPoint` 增加两个 Optional 字段**

在 `user_id` 字段后、`# 关系` 前插入：

```python
    api_project_id: Optional[int] = Field(default=None, description="关联的 API 项目ID（接口测试用，从需求点级联到测试点/用例）")
    api_endpoint_ids: Optional[str] = Field(default=None, description="关联的 API Endpoint ID 列表（逗号分隔，接口测试用）")
```

**Step 2：给 `TestPoint` 增加同样两个 Optional 字段**

在 `user_id` 字段后、`# 关系` 前插入：

```python
    api_project_id: Optional[int] = Field(default=None, description="关联的 API 项目ID（接口测试用例重生成时重建上下文用）")
    api_endpoint_ids: Optional[str] = Field(default=None, description="关联的 API Endpoint ID 列表（逗号分隔，接口测试用）")
```

**Step 3：验证自动迁移**

启动后端（或任意 import `db.db` 的脚本）：

```bash
cd backend
python -c "import db.db; from sqlalchemy import inspect, create_engine; e=db.db.engine; print([c['name'] for c in inspect(e).get_columns('requirementpoint')]); print([c['name'] for c in inspect(e).get_columns('testpoint')])"
```

Expected: 两个表的列中都包含 `api_project_id`、`api_endpoint_ids`。

**Step 4：Commit**

```bash
git add backend/db/models.py
git commit -m "feat(models): add api_project_id/api_endpoint_ids to RequirementPoint and TestPoint"
```

---

## Task 2：抽取可复用的用例生成 + ApiScenario 同步逻辑

**Files:**
- Modify: `backend/app/pipeline/pipeline_runner.py`

**目标：** 把 `_run_pipeline` 里"阶段3 写库 + 阶段4 优化 + ApiScenario 创建"中"为一组测试点生成用例并落库"的逻辑，抽成可复用方法，供完整 pipeline 与重生成共用。

**Step 1：新增纯函数——按位置合并（就地更新核心，可单测）**

在 `pipeline_runner.py` 顶部工具函数区新增：

```python
def merge_entities_by_position(existing: list, generated: list, *, max_keep: int | None = None) -> tuple[list, list, list]:
    """
    按位置顺序匹配，计算就地更新动作。

    Args:
        existing: 已存在的实体列表（按稳定顺序，如按 id 升序）。
        generated: 新生成的实体数据列表（可能为含空 dict 的占位）。
        max_keep: 最多保留的生成数量（None 表示全部保留）。

    Returns:
        (to_update, to_create, to_delete)
        - to_update: [(existing[i], generated[i])] 需要更新的 (实体, 新数据) 对
        - to_create: [generated[i]] 需要新建的数据（i >= len(existing)）
        - to_delete: [existing[i]] 多余的旧实体（i >= 生成数量）
    保留 generated 中位置 < len(existing) 但内容为空的占位项（跳过，不更新也不删）。
    """
    if max_keep is not None:
        generated = generated[:max_keep]

    to_update: list = []
    to_create: list = []
    to_delete: list = []

    for i, gen in enumerate(generated):
        is_empty = (gen is None) or (isinstance(gen, dict) and not gen)
        if i < len(existing):
            if is_empty:
                # 占位空项：既不更新也不删除已有实体（保留现状）
                continue
            to_update.append((existing[i], gen))
        else:
            if is_empty:
                continue
            to_create.append(gen)

    # 多余旧实体删除
    for i in range(len(generated), len(existing)):
        to_delete.append(existing[i])

    return to_update, to_create, to_delete
```

**Step 2：新增 `_normalize_and_build_cases` —— 把 designer 输出转成 TestCase 写库参数**

把 `_run_pipeline` 阶段3 中"根据 test_points 构造 `tc_kwargs` + endpoint_ref 转换 + ApiScenario 创建"抽成方法：

```python
    def _build_testcase_kwargs(
        self,
        case_data: dict,
        *,
        test_point_id: int,
        session_id: int,
        requirement_content: str,
        is_api_test: bool,
        api_context: str,
        endpoint_index_to_id: Dict[int, int],
        api_project_id: Optional[int],
        api_endpoint_ids: Optional[List[int]],
        module_id: Optional[int],
        user_id: Optional[str],
        test_type: Optional[str] = None,
        design_method: Optional[str] = None,
        priority: Optional[str] = "medium",
    ) -> dict:
        """从 designer 输出的单条 case_data 构造 TestCase 构造参数（不含 id）。"""
        steps = case_data.get("test_steps", [])

        if is_api_test and isinstance(steps, list):
            converted = []
            for s in steps:
                if isinstance(s, dict):
                    s = dict(s)
                    if s.get("endpoint_id") or s.get("endpoint_ref"):
                        if not s.get("type"):
                            s["type"] = "api_call"
                    if s.get("endpoint_ref") and endpoint_index_to_id:
                        ref = s.pop("endpoint_ref", None)
                        eid = endpoint_index_to_id.get(int(ref)) if ref else None
                        if eid:
                            s["endpoint_id"] = eid
                converted.append(s)
            steps = converted
            steps = _normalize_pipeline_api_steps(steps, endpoint_index_to_id)

        tc_kwargs = dict(
            case_name=case_data.get("title", ""),
            steps=steps if isinstance(steps, list) else [],
            preset_conditions=case_data.get("preconditions", []) or [],
            case_level=case_data.get("case_level", 1),
            session_id=session_id,
            module_id=module_id,
            test_point_id=test_point_id,
            test_type=case_data.get("test_type") or test_type,
            design_method=case_data.get("design_method") or design_method,
            user_id=user_id,
        )

        if is_api_test:
            tc_kwargs["expected_results"] = [
                s.get("description", "") if isinstance(s, dict) else str(s)
                for s in steps
            ] if steps else []
            if api_endpoint_ids:
                tc_kwargs["api_endpoint_id"] = ",".join(str(eid) for eid in api_endpoint_ids)
            if api_project_id:
                tc_kwargs["api_project_id"] = api_project_id
            assertions = case_data.get("assertions")
            if assertions:
                tc_kwargs["assertions"] = assertions
        else:
            tc_kwargs["expected_results"] = [
                s.get("expected", "") for s in steps
            ] if steps else []

        return tc_kwargs
```

**Step 3：新增 `_sync_api_scenarios` —— 为用例集合创建/更新 ApiScenario（复用既有逻辑）**

把 `_run_pipeline` 里"为接口测试用例创建 ApiScenario 并关联"整段抽成方法，支持传入"已存在 scenario 映射"以复用既有 `scenario_id`（就地更新不产生孤儿场景）：

```python
    def _sync_api_scenarios(
        self,
        test_cases: list,
        *,
        api_project_id: int,
        endpoint_index_to_id: Dict[int, int],
        user_id: Optional[str],
    ) -> None:
        """为 test_cases 中带 API 调用步骤的用例创建/更新 ApiScenario，并回写 scenario_id。"""
        from db.models import ApiScenario, ApiEndpoint as ApiEndpointModel

        all_endpoint_ids = _collect_pipeline_endpoint_ids(test_cases, endpoint_index_to_id)
        endpoint_map: dict[int, ApiEndpointModel] = {}
        if all_endpoint_ids:
            rows = self.db.exec(select(ApiEndpointModel).where(ApiEndpointModel.id.in_(list(all_endpoint_ids)))).all()
            endpoint_map = {e.id: e for e in rows}

        for tc in test_cases:
            api_steps = []
            for pc in (tc.preset_conditions or []):
                if _is_pipeline_api_call_step(pc) and _resolve_pipeline_endpoint_id(pc, endpoint_index_to_id):
                    api_steps.append(("preset", pc))
            for step in (tc.steps or []):
                if _is_pipeline_api_call_step(step) and _resolve_pipeline_endpoint_id(step, endpoint_index_to_id):
                    api_steps.append(("step", step))
            if not api_steps:
                continue

            scenario_steps = []
            for step_type, s in api_steps:
                step_copy = _normalize_pipeline_api_step(s, endpoint_index_to_id)
                eid = _resolve_pipeline_endpoint_id(s, endpoint_index_to_id)
                if eid:
                    step_copy["endpoint_id"] = eid
                    step_copy.pop("endpoint_ref", None)
                ep = endpoint_map.get(eid) if eid else None
                if ep:
                    step_copy.setdefault("method", ep.method)
                    step_copy.setdefault("path", ep.path)
                    step_copy.setdefault("url", ep.url or ep.path)
                    step_copy.setdefault("endpoint_name", ep.name)
                    step_copy.setdefault("enabled", True)
                    step_copy.setdefault("continue_on_failure", True)
                if step_type == "preset":
                    step_copy["is_preset"] = True
                scenario_steps.append(step_copy)

            if tc.scenario_id:
                scenario = self.db.get(ApiScenario, tc.scenario_id)
                if scenario:
                    scenario.steps = scenario_steps
                    scenario.name = f"{tc.case_name}_场景"
                    self.db.add(scenario)
                    continue
            scenario = ApiScenario(
                project_id=api_project_id,
                name=f"{tc.case_name}_场景",
                description=f"测试用例 {tc.case_name} 的接口场景",
                steps=scenario_steps,
                user_id=user_id,
            )
            self.db.add(scenario)
            self.db.flush()
            tc.scenario_id = scenario.id
```

> 注：阶段4 优化的 ApiScenario 刷新可继续复用 `_build_pipeline_scenario_steps`，保持不变。本任务不改动 `_run_pipeline` 主流程（保持现有 generate-v2 行为），仅新增可复用方法。

**Step 4：验证导入无误**

```bash
cd backend
python -c "from app.pipeline.pipeline_runner import PipelineRunner, merge_entities_by_position; print('ok')"
```

Expected: 打印 `ok`。

**Step 5：Commit**

```bash
git add backend/app/pipeline/pipeline_runner.py
git commit -m "refactor(pipeline): extract reusable case-build + api-scenario sync helpers"
```

---

## Task 3：实现用例就地更新算法（核心去重）

**Files:**
- Modify: `backend/app/pipeline/pipeline_runner.py`

**Step 1：新增 `_inplace_update_cases`**

```python
    async def _inplace_update_cases(
        self,
        test_point,
        requirement_content: str,
        *,
        is_api_test: bool,
        api_context: str = "",
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_project_id: Optional[int] = None,
        api_endpoint_ids: Optional[List[int]] = None,
        module_id: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """
        用 LLM 为一个测试点重设计用例，并就地更新其下已有关联用例（去重、不重复）。

        Returns: 该测试点下最终用例数量。
        """
        from db.models import TestCase

        endpoint_index_to_id = endpoint_index_to_id or {}

        # 1) 调 LLM 生成（design_batch 会把数量补齐/截断到 1 条，缺失返回 {}）
        cases_data = await self.tc_designer.design_batch(
            [{
                "id": test_point.id,
                "content": test_point.content,
                "test_type": test_point.test_type,
                "design_method": test_point.design_method,
                "priority": test_point.priority,
            }],
            requirement_content,
            is_api_test=is_api_test,
            api_context=api_context,
        )
        # design_batch 已保证 len(cases_data)==1
        generated = cases_data[0] if cases_data else {}

        # 2) 读取该测试点下已有关联用例（按 id 稳定顺序）
        existing: list = self.db.exec(
            select(TestCase).where(TestCase.test_point_id == test_point.id).order_by(TestCase.id)
        ).all()

        # 3) 按位置匹配
        to_update, to_create, to_delete = merge_entities_by_position(existing, [generated])

        # 4) 删除多余旧用例（同时清理其孤儿场景）
        for old in to_delete:
            if old.scenario_id:
                from db.models import ApiScenario
                sc = self.db.get(ApiScenario, old.scenario_id)
                if sc:
                    self.db.delete(sc)
            self.db.delete(old)

        # 5) 更新已有（保留 id）
        for old, gen in to_update:
            kwargs = self._build_testcase_kwargs(
                gen,
                test_point_id=test_point.id,
                session_id=test_point.session_id,
                requirement_content=requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
                test_type=test_point.test_type,
                design_method=test_point.design_method,
                priority=test_point.priority,
            )
            for k, v in kwargs.items():
                setattr(old, k, v)
            self.db.add(old)

        # 6) 新建缺失
        created: list = []
        for gen in to_create:
            kwargs = self._build_testcase_kwargs(
                gen,
                test_point_id=test_point.id,
                session_id=test_point.session_id,
                requirement_content=requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
                test_type=test_point.test_type,
                design_method=test_point.design_method,
                priority=test_point.priority,
            )
            tc = TestCase(**kwargs)
            self.db.add(tc)
            created.append(tc)

        self.db.commit()
        for tc in existing + created:
            self.db.refresh(tc)

        # 7) 接口测试：为所有（更新+新建）用例同步 ApiScenario
        all_cases = [t for t in (existing + created) if not _is_empty_case(t)]
        if is_api_test and api_project_id and all_cases:
            self._sync_api_scenarios(
                all_cases,
                api_project_id=api_project_id,
                endpoint_index_to_id=endpoint_index_to_id,
                user_id=user_id,
            )
            self.db.commit()

        return len(all_cases)
```

辅助函数（文件顶部）：

```python
def _is_empty_case(tc: "TestCase") -> bool:
    return not (tc.case_name or tc.steps)
```

**Step 2：补充 merge 单测（无 DB）**

Test: `backend/tests/test_merge_entities.py`

```python
def test_merge_keeps_existing_when_fewer_generated():
    existing = [object(), object(), object()]
    generated = [{"a": 1}]  # 只生成 1 条
    to_update, to_create, to_delete = merge_entities_by_position(existing, generated)
    assert len(to_update) == 1
    assert to_create == []
    assert len(to_delete) == 2  # 多余 2 条删除


def test_merge_creates_when_more_generated():
    existing = [object()]
    generated = [{"a": 1}, {"b": 2}]
    to_update, to_create, to_delete = merge_entities_by_position(existing, generated)
    assert len(to_update) == 1
    assert len(to_create) == 1
    assert to_delete == []


def test_merge_skips_empty_placeholder():
    existing = [object(), object()]
    generated = [{}, {"b": 2}]  # 第 0 条为空占位
    to_update, to_create, to_delete = merge_entities_by_position(existing, generated)
    assert len(to_update) == 1  # 只更新第 1 条
    assert to_create == []
    assert to_delete == []      # 第 0 条已有实体不被删
```

**Step 3：运行测试**

```bash
cd backend
python -m pytest tests/test_merge_entities.py -v
```

Expected: 3 个测试 PASS。

**Step 4：Commit**

```bash
git add backend/app/pipeline/pipeline_runner.py backend/tests/test_merge_entities.py
git commit -m "feat(pipeline): implement inplace case update with dedup"
```

---

## Task 4：实现测试点就地更新（级联到用例）

**Files:**
- Modify: `backend/app/pipeline/pipeline_runner.py`

**Step 1：新增 `_inplace_update_test_points`**

```python
    async def _inplace_update_test_points(
        self,
        requirement_point,
        requirement_content: str,
        *,
        is_api_test: bool,
        api_context: str = "",
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_project_id: Optional[int] = None,
        api_endpoint_ids: Optional[List[int]] = None,
        module_id: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> list:
        """
        用 LLM 为需求点重生成测试点，并就地更新其下已有关联测试点；
        每个（更新/新建的）测试点再就地重生成用例（一步到位）。
        返回最终测试点列表。
        """
        from db.models import TestPoint

        endpoint_index_to_id = endpoint_index_to_id or {}

        tp_data_list = await self.tp_generator.generate(requirement_point.content)

        existing: list = self.db.exec(
            select(TestPoint)
            .where(TestPoint.requirement_point_id == requirement_point.id)
            .order_by(TestPoint.id)
        ).all()

        to_update, to_create, to_delete = merge_entities_by_position(existing, tp_data_list)

        # 删除多余旧测试点（级联删其用例 + 孤儿场景）
        for old in to_delete:
            self._cascade_delete_test_point(old)

        # 更新已有测试点（保留 id）
        for old, gen in to_update:
            old.content = gen.get("content", old.content)
            old.test_type = gen.get("test_type", old.test_type)
            old.design_method = gen.get("design_method", old.design_method)
            old.priority = gen.get("priority", old.priority)
            old.api_project_id = api_project_id
            old.api_endpoint_ids = ",".join(str(e) for e in api_endpoint_ids) if api_endpoint_ids else None
            self.db.add(old)

        # 新建缺失测试点
        created: list = []
        for gen in to_create:
            tp = TestPoint(
                requirement_point_id=requirement_point.id,
                session_id=requirement_point.session_id,
                content=gen.get("content", ""),
                test_type=gen.get("test_type", "functional"),
                design_method=gen.get("design_method", ""),
                priority=gen.get("priority", "medium"),
                user_id=user_id,
                api_project_id=api_project_id,
                api_endpoint_ids=",".join(str(e) for e in api_endpoint_ids) if api_endpoint_ids else None,
            )
            self.db.add(tp)
            created.append(tp)

        self.db.commit()
        for tp in existing + created:
            self.db.refresh(tp)

        # 一步到位：为每个测试点就地重生成用例
        final_tps = [t for t in (existing + created) if not _is_empty_tp(t)]
        for tp in final_tps:
            await self._inplace_update_cases(
                tp,
                requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
            )
        return final_tps
```

辅助函数：

```python
def _is_empty_tp(tp: "TestPoint") -> bool:
    return not tp.content


def _cascade_delete_test_point(session, tp):
    """级联删除测试点及其下所有用例 + 孤儿场景（提交由调用方统一处理）。"""
    from db.models import TestCase, ApiScenario
    cases = session.exec(select(TestCase).where(TestCase.test_point_id == tp.id)).all()
    for c in cases:
        if c.scenario_id:
            sc = session.get(ApiScenario, c.scenario_id)
            if sc:
                session.delete(sc)
        session.delete(c)
    session.delete(tp)
```

> 注意：`_cascade_delete_test_point` 内部不 commit（调用方在事务中统一 commit）。把它定义在模块级，签名为 `(session, tp)`。

**Step 2：验证导入**

```bash
cd backend
python -c "from app.pipeline.pipeline_runner import PipelineRunner; print('ok')"
```

**Step 3：Commit**

```bash
git add backend/app/pipeline/pipeline_runner.py
git commit -m "feat(pipeline): inplace test-point update cascading to cases"
```

---

## Task 5：暴露重生成入口方法（异步任务包装）

**Files:**
- Modify: `backend/app/pipeline/pipeline_runner.py`

**Step 1：新增 `regenerate_test_point` / `regenerate_requirement_point`**

这两个方法由路由层在 `asyncio.create_task` 中调用（与 `run` 一致）。

```python
    async def regenerate_test_point(
        self,
        task_id: str,
        test_point_id: int,
        user_id: Optional[str] = None,
        *,
        module_id: Optional[int] = None,
    ) -> None:
        """重生成单个测试点的用例（就地更新）。"""
        from db.models import TestPoint
        try:
            tp = self.db.get(TestPoint, test_point_id)
            if not tp:
                raise ValueError("测试点不存在")

            is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids = \
                self._resolve_api_context(tp.api_project_id, tp.api_endpoint_ids)

            requirement_content = tp.content  # 测试点级用测试点内容作为上下文
            count = await self._inplace_update_cases(
                tp,
                requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
            )
            self._complete_task(task_id, {
                "test_point_id": test_point_id,
                "cases_count": count,
                "is_api_test": is_api_test,
            })
        except Exception as e:
            logger.error(f"重生成测试点用例失败: {e}", exc_info=True)
            self._fail_task(task_id, str(e))

    async def regenerate_requirement_point(
        self,
        task_id: str,
        requirement_point_id: int,
        user_id: Optional[str] = None,
        *,
        module_id: Optional[int] = None,
    ) -> None:
        """重生成需求点下的测试点（一步到位级联到用例）。"""
        from db.models import RequirementPoint
        try:
            rp = self.db.get(RequirementPoint, requirement_point_id)
            if not rp:
                raise ValueError("需求点不存在")

            is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids = \
                self._resolve_api_context(rp.api_project_id, rp.api_endpoint_ids)

            final_tps = await self._inplace_update_test_points(
                rp,
                rp.content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
            )
            self._complete_task(task_id, {
                "requirement_point_id": requirement_point_id,
                "test_points_count": len(final_tps),
                "is_api_test": is_api_test,
            })
        except Exception as e:
            logger.error(f"重生成需求点测试点失败: {e}", exc_info=True)
            self._fail_task(task_id, str(e))
```

**Step 2：新增 `_resolve_api_context` 辅助**

```python
    def _resolve_api_context(self, api_project_id, api_endpoint_ids_str):
        """根据测试点/需求点上的 api 字段重建接口上下文。"""
        from app.services.api_context import build_api_context, parse_endpoint_ids
        from app.routes.testcase import infer_endpoint_dependencies

        api_endpoint_ids = parse_endpoint_ids(api_endpoint_ids_str) if api_endpoint_ids_str else []
        is_api_test = bool(api_endpoint_ids and api_project_id)
        endpoint_index_to_id: Dict[int, int] = {}
        api_context = ""
        if is_api_test:
            extra = infer_endpoint_dependencies(self.db, api_endpoint_ids, api_project_id)
            for eid in extra:
                if eid not in api_endpoint_ids:
                    api_endpoint_ids.append(eid)
            api_context, endpoint_index_to_id = build_api_context(
                self.db, api_endpoint_ids, api_project_id, {},
            )
        return is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids
```

**Step 3：验证导入**

```bash
cd backend
python -c "from app.pipeline.pipeline_runner import PipelineRunner; print('ok')"
```

**Step 4：Commit**

```bash
git add backend/app/pipeline/pipeline_runner.py
git commit -m "feat(pipeline): add regenerate entry methods + api context resolver"
```

---

## Task 6：路由层补齐 CRUD + 级联删除 + 改写 regenerate 为异步任务

**Files:**
- Modify: `backend/app/routes/pipeline.py`

**目标：**
- 手动新增需求点 / 测试点（POST）
- 级联删除需求点 / 测试点（DELETE）
- 把 `POST /test-points/{id}/regenerate` 改为异步任务（复用 `PipelineRunner`，返回 `task_id`，删除"每次新建"逻辑）
- 新增 `POST /requirement-points/{id}/regenerate`（异步任务，一步到位）
- 新增请求模型 `CreateRequirementPointRequest` / `CreateTestPointRequest`
- 写操作加 `user_id`/`session_id` 归属校验

**Step 1：新增 Pydantic 请求模型（在现有模型后）**

```python
class CreateRequirementPointRequest(BaseModel):
    session_id: int
    content: str
    module: Optional[str] = ""
    priority: Optional[str] = "medium"
    api_project_id: Optional[int] = None
    api_endpoint_ids: Optional[str] = None  # 逗号分隔


class CreateTestPointRequest(BaseModel):
    session_id: int
    requirement_point_id: int
    content: str
    test_type: Optional[str] = "functional"
    design_method: Optional[str] = ""
    priority: Optional[str] = "medium"
    api_project_id: Optional[int] = None
    api_endpoint_ids: Optional[str] = None
```

**Step 2：新增 `_require_owned` 归属校验辅助**

```python
def _check_session_owner(session, session_id: int, user: "UserInfo"):
    """校验 session 归属当前用户（user_id 匹配或管理员）。"""
    from db.models import Session as SessionModel
    s = session.get(SessionModel, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 仅当用户登录且 session 绑定了 user_id 时校验；管理员/未绑定不强制
    if user and s.user_id and user.user_id and s.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="无权操作该会话的数据")
```

**Step 3：新增手动新增需求点**

```python
@router.post("/requirement-points")
async def create_requirement_point(
    body: CreateRequirementPointRequest,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    _check_session_owner(session, body.session_id, user)
    rp = RequirementPoint(
        session_id=body.session_id,
        content=body.content,
        module=body.module or "",
        priority=body.priority or "medium",
        order_index=0,
        user_id=user.user_id if user else None,
        api_project_id=body.api_project_id,
        api_endpoint_ids=body.api_endpoint_ids,
    )
    session.add(rp)
    session.commit()
    session.refresh(rp)
    return Response(message="需求点已创建", data={"id": rp.id})
```

**Step 4：新增手动新增测试点（校验父需求点存在且同 session）**

```python
@router.post("/test-points")
async def create_test_point(
    body: CreateTestPointRequest,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    _check_session_owner(session, body.session_id, user)
    parent = session.get(RequirementPoint, body.requirement_point_id)
    if not parent or parent.session_id != body.session_id:
        raise HTTPException(status_code=400, detail="父需求点不存在或不属于该会话")
    tp = TestPoint(
        requirement_point_id=body.requirement_point_id,
        session_id=body.session_id,
        content=body.content,
        test_type=body.test_type or "functional",
        design_method=body.design_method or "",
        priority=body.priority or "medium",
        user_id=user.user_id if user else None,
        api_project_id=body.api_project_id,
        api_endpoint_ids=body.api_endpoint_ids,
    )
    session.add(tp)
    session.commit()
    session.refresh(tp)
    return Response(message="测试点已创建", data={"id": tp.id})
```

**Step 5：级联删除需求点**

```python
@router.delete("/requirement-points/{point_id}")
async def delete_requirement_point(
    point_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    from db.models import TestPoint as TPModel, TestCase as TCModel, ApiScenario
    rp = session.get(RequirementPoint, point_id)
    if not rp:
        raise HTTPException(status_code=404, detail="需求点不存在")
    if user and rp.user_id and user.user_id and rp.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="无权删除该需求点")

    tps = session.exec(select(TPModel).where(TPModel.requirement_point_id == point_id)).all()
    for tp in tps:
        cases = session.exec(select(TCModel).where(TCModel.test_point_id == tp.id)).all()
        for c in cases:
            if c.scenario_id:
                sc = session.get(ApiScenario, c.scenario_id)
                if sc:
                    session.delete(sc)
            session.delete(c)
        session.delete(tp)
    session.delete(rp)
    session.commit()
    return Response(message="需求点及其下游已级联删除")
```

**Step 6：级联删除测试点**

```python
@router.delete("/test-points/{point_id}")
async def delete_test_point(
    point_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    from db.models import TestCase as TCModel, ApiScenario
    tp = session.get(TestPoint, point_id)
    if not tp:
        raise HTTPException(status_code=404, detail="测试点不存在")
    if user and tp.user_id and user.user_id and tp.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="无权删除该测试点")

    cases = session.exec(select(TCModel).where(TCModel.test_point_id == point_id)).all()
    for c in cases:
        if c.scenario_id:
            sc = session.get(ApiScenario, c.scenario_id)
            if sc:
                session.delete(sc)
        session.delete(c)
    session.delete(tp)
    session.commit()
    return Response(message="测试点及其用例已级联删除")
```

**Step 7：改写 `POST /test-points/{id}/regenerate` 为异步任务**

删除原同步实现（317-389 行整段），替换为：

```python
@router.post("/test-points/{point_id}/regenerate")
async def regenerate_test_point_cases(
    point_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """重新生成单个测试点的测试用例（就地更新，异步任务）。"""
    tp = session.get(TestPoint, point_id)
    if not tp:
        raise HTTPException(status_code=404, detail="测试点不存在")

    user_config = _get_user_config(session, user.user_id if user else None)
    model_type = user_config["model_type"]
    api_key = user_config["api_key"]
    if model_type == "api" and not api_key:
        raise HTTPException(status_code=400, detail="API Key未配置，请先在配置页面设置")

    runner = PipelineRunner(
        db_session=session,
        api_key=api_key,
        api_base_url=user_config["api_base_url"],
        api_model=user_config["api_model"],
        api_proxy_url=user_config["api_proxy_url"] or None,
        mcp_configs=[],
    )
    task_id = runner._create_task()
    asyncio.create_task(
        runner.regenerate_test_point(
            task_id, point_id,
            user_id=user.user_id if user else None,
            module_id=None,
        )
    )
    return Response(data={"task_id": task_id}, message="测试点用例重生成已启动")
```

**Step 8：新增 `POST /requirement-points/{id}/regenerate`**

```python
@router.post("/requirement-points/{id}/regenerate")
async def regenerate_requirement_point_points(
    point_id: int,
    session: SessionDep = None,
    user: CurrentUser = None,
):
    """重新生成需求点下的测试点（一步到位级联到用例，异步任务）。"""
    rp = session.get(RequirementPoint, point_id)
    if not rp:
        raise HTTPException(status_code=404, detail="需求点不存在")

    user_config = _get_user_config(session, user.user_id if user else None)
    model_type = user_config["model_type"]
    api_key = user_config["api_key"]
    if model_type == "api" and not api_key:
        raise HTTPException(status_code=400, detail="API Key未配置，请先在配置页面设置")

    runner = PipelineRunner(
        db_session=session,
        api_key=api_key,
        api_base_url=user_config["api_base_url"],
        api_model=user_config["api_model"],
        api_proxy_url=user_config["api_proxy_url"] or None,
        mcp_configs=[],
    )
    task_id = runner._create_task()
    asyncio.create_task(
        runner.regenerate_requirement_point(
            task_id, point_id,
            user_id=user.user_id if user else None,
            module_id=None,
        )
    )
    return Response(data={"task_id": task_id}, message="需求点测试点重生成已启动")
```

**Step 9：验证路由导入与服务启动**

```bash
cd backend
python -c "from app.routes.pipeline import router; print([r.path for r in router.routes])"
```

Expected: 输出包含新增与改造的路径（`/requirement-points` POST、`/test-points` POST、`/requirement-points/{id}` DELETE、`/test-points/{id}` DELETE、`/test-points/{id}/regenerate`、`/requirement-points/{id}/regenerate`）。

**Step 10：Commit**

```bash
git add backend/app/routes/pipeline.py
git commit -m "feat(routes): add requirement/test-point CRUD, cascade delete, async regenerate"
```

---

## Task 7：后端集成验证（去重 + 级联）

**Files:**
- Test: `backend/tests/test_inplace_regenerate.py`（用 SQLite 内存库 + 真实模型，mock LLM 输出）

**Step 1：写集成测试，验证"重生成不重复 + 删除无孤儿"**

用 monkeypatch 替换 `TestCaseDesigner.design_batch` 与 `TestPointGenerator.generate` 返回固定数据，避免真实 LLM 调用。

```python
import pytest
from sqlmodel import SQLModel, Session as SQLSession, create_engine, select
from app.pipeline.pipeline_runner import PipelineRunner, merge_entities_by_position
from db.models import RequirementPoint, TestPoint, TestCase, Session as SessionModel, ApiScenario

@pytest.fixture
def engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e

@pytest.fixture
def db(engine):
    with SQLSession(engine) as s:
        yield s

def _seed(engine):
    with SQLSession(engine) as s:
        sess = SessionModel(name="t", user_id="u1")
        s.add(sess); s.commit(); s.refresh(sess)
        rp = RequirementPoint(session_id=sess.id, content="登录功能", user_id="u1")
        s.add(rp); s.commit(); s.refresh(rp)
        tp = TestPoint(requirement_point_id=rp.id, session_id=sess.id, content="校验用户名", user_id="u1")
        s.add(tp); s.commit(); s.refresh(tp)
        # 预置 3 条用例
        for i in range(3):
            s.add(TestCase(session_id=sess.id, test_point_id=tp.id, case_name=f"旧用例{i}", user_id="u1"))
        s.commit()
        return sess.id, rp.id, tp.id

def test_regenerate_dedup_keeps_ids(monkeypatch, engine, db):
    sess_id, rp_id, tp_id = _seed(engine)
    # 第一次生成返回 2 条
    async def fake_design(*a, **k):
        return [{"title": "新用例A", "test_steps": [], "preconditions": [], "case_level": 1}]
    monkeypatch.setattr("app.pipeline.test_case_designer.TestCaseDesigner.design_batch", staticmethod(fake_design))

    runner = PipelineRunner(db_session=db, api_key="x", api_base_url="x", api_model="x")
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        runner._inplace_update_cases(db.get(TestPoint, tp_id), "ctx", is_api_test=False)
    )
    cases = db.exec(select(TestCase).where(TestCase.test_point_id == tp_id).order_by(TestCase.id)).all()
    assert len(cases) == 2, f"期望 2 条，实际 {len(cases)}"  # 1 更新 + 1 新建
    assert cases[0].case_name == "新用例A"
    assert cases[0].id is not None

def test_cascade_delete_no_orphan(engine, db):
    sess_id, rp_id, tp_id = _seed(engine)
    from app.pipeline.pipeline_runner import _cascade_delete_test_point
    _cascade_delete_test_point(db, db.get(TestPoint, tp_id))
    db.commit()
    remain = db.exec(select(TestCase).where(TestCase.test_point_id == tp_id)).all()
    assert remain == []
    assert db.get(TestPoint, tp_id) is None
```

**Step 2：运行**

```bash
cd backend
python -m pytest tests/test_inplace_regenerate.py -v
```

Expected: 两个测试 PASS（用例数收敛到 2、无孤儿）。

**Step 3：Commit**

```bash
git add backend/tests/test_inplace_regenerate.py
git commit -m "test(pipeline): verify inplace dedup + cascade delete"
```

---

## Task 8：前端 API 封装

**Files:**
- Modify: `frontend/src/services/api.ts`（在 `testcaseApi` 内补充）

**Step 1：在 `testcaseApi` 对象内新增方法**（放在 `getTestPoints` 之后）

```typescript
  // ========== 需求点 / 测试点 手动管理 ==========

  /** 手动新增需求点 */
  createRequirementPoint: (data: {
    session_id: number;
    content: string;
    module?: string;
    priority?: string;
    api_project_id?: number | null;
    api_endpoint_ids?: string | null;
  }): Promise<ApiResponse<{ id: number }>> => api.post('/testcases/requirement-points', data),

  /** 编辑需求点 */
  updateRequirementPoint: (id: number, data: {
    content?: string; module?: string; priority?: string;
  }): Promise<ApiResponse<{ id: number }>> => api.put(`/testcases/requirement-points/${id}`, data),

  /** 删除需求点（级联） */
  deleteRequirementPoint: (id: number): Promise<ApiResponse> =>
    api.delete(`/testcases/requirement-points/${id}`),

  /** 需求点级联重生成测试点（异步，返回 task_id） */
  regenerateRequirementPoint: (id: number): Promise<ApiResponse<{ task_id: string }>> =>
    api.post(`/testcases/requirement-points/${id}/regenerate`),

  /** 手动新增测试点 */
  createTestPoint: (data: {
    session_id: number;
    requirement_point_id: number;
    content: string;
    test_type?: string;
    design_method?: string;
    priority?: string;
    api_project_id?: number | null;
    api_endpoint_ids?: string | null;
  }): Promise<ApiResponse<{ id: number }>> => api.post('/testcases/test-points', data),

  /** 编辑测试点 */
  updateTestPoint: (id: number, data: {
    content?: string; test_type?: string; design_method?: string; priority?: string;
  }): Promise<ApiResponse<{ id: number }>> => api.put(`/testcases/test-points/${id}`, data),

  /** 删除测试点（级联） */
  deleteTestPoint: (id: number): Promise<ApiResponse> =>
    api.delete(`/testcases/test-points/${id}`),

  /** 测试点重生成用例（异步，返回 task_id） */
  regenerateTestPoint: (id: number): Promise<ApiResponse<{ task_id: string }>> =>
    api.post(`/testcases/test-points/${id}/regenerate`),
```

**Step 2：前端类型补充**（`frontend/src/types/index.ts` 增加可选字段）

在 `RequirementPoint` / `TestPoint` 相关前端类型（若有）增加 `api_project_id?`、`api_endpoint_ids?`。若无独立类型，可复用 `any`，本任务不阻塞后续。

**Step 3：验证 lint**

```bash
cd frontend
npm run lint
```

Expected: 无 error（新增方法不影响既有）。

**Step 4：Commit**

```bash
git add frontend/src/services/api.ts frontend/src/types/index.ts
git commit -m "feat(frontend): add requirement/test-point management API methods"
```

---

## Task 9：前端 RequirementTestPointManager 组件

**Files:**
- Create: `frontend/src/components/test-case/RequirementTestPointManager.tsx`
- Modify: `frontend/src/components/test-case/TestCaseGenerator.tsx`（在生成结果区顶部挂载该组件，传入 `sessionId`）

**目标：** 三层联动树（需求点 → 测试点 → 用例计数），每行操作按钮；重生成走异步任务 + 轮询 `getPipelineStatus`。

**Step 1：创建组件骨架**

组件接收 `props: { sessionId: number }`。内部状态：
- `reqPoints: RequirementPoint[]`、`testPoints: TestPoint[]`
- `loading`、`regeneratingTaskId`、`expandedReqId`、`expandedTpId`

关键逻辑：
- `loadData()`：并行调用 `getRequirementPoints(sessionId)` + `getTestPoints(sessionId)` 刷新。
- `handleRegenerateReq(rpId)`：调用 `regenerateRequirementPoint(rpId)` 得到 `task_id`，启用轮询 `getPipelineStatus`，完成/失败后 `loadData()` 并 `message.success/error`。
- `handleRegenerateTp(tpId)`：同上用 `regenerateTestPoint`。
- `handleAddReq()` / `handleAddTp(rpId)`：弹 `Modal` 表单（内容、模块、优先级、可选 api_project_id/api_endpoint_ids），调用 create 后刷新。
- `handleDeleteReq(rpId)` / `handleDeleteTp(tpId)`：`Modal.confirm` 二次确认后调用 delete 并刷新。
- 用例数统计：从 `testPoints` 聚合每个 `test_point_id` 下的用例（额外调用 `getTestcases` 或在树展开时拉取）。简化实现：展开测试点时调用 `testcaseApi.getTestcases(sessionId, { })` 并按 `test_point_id` 过滤展示前若干条。

**Step 2：UI 结构（Ant Design）**

- 顶部工具栏：`新增需求点` 按钮 + 刷新按钮。
- `List` 渲染 `reqPoints`：每项显示 `content` + `module` + `priority` + 操作（`新增测试点`、`重新生成测试点`、`编辑`、`删除`）。
  - 展开后渲染其子 `testPoints`（`List`）：每项显示 `content` + `test_type` + 操作（`重新生成用例`、`编辑`、`删除`）。
    - 展开后展示该测试点下用例（`case_name` 列表）。

**Step 3：挂载到 TestCaseGenerator**

在 `TestCaseGenerator` 的生成结果展示区上方插入：

```tsx
<RequirementTestPointManager sessionId={currentSessionId} />
```

**Step 4：验证 build**

```bash
cd frontend
npm run build
```

Expected: 构建通过（tsc 无类型错误）。

**Step 5：Commit**

```bash
git add frontend/src/components/test-case/RequirementTestPointManager.tsx frontend/src/components/test-case/TestCaseGenerator.tsx
git commit -m "feat(frontend): add RequirementTestPointManager 3-level panel"
```

---

## Task 10：端到端验证

**Step 1：启动后端 + 前端**

```bash
# 终端1：后端
cd backend && uvicorn main:app --reload --port 8000
# 终端2：前端
cd frontend && npm run dev
```

**Step 2：手动验证清单（浏览器）**

1. 打开某会话 → AI 测试页，确认 `RequirementTestPointManager` 显示已有需求点/测试点（由 `generate-v2` 生成）。
2. 手动新增一个需求点 → 列表出现；再在其下新增一个测试点 → 列表出现且 `requirement_point_id` 正确。
3. 点测试点「重新生成用例」→ 进度轮询完成 → 用例数正确、id 稳定（再次点不会翻倍）。
4. 点需求点「重新生成测试点」→ 完成后该需求点下测试点及其用例都被重建（无重复）。
5. 删除测试点 → 其用例消失；删除需求点 → 其测试点及用例全部消失（无孤儿）。
6. （可选）为需求点/测试点填 `api_project_id`+`api_endpoint_ids` 后重生成 → 生成接口测试用例并自动创建 `ApiScenario`。

**Step 3：回归**

- 运行 `cd backend && python -m pytest tests/ -v` 全部通过。
- 运行 `cd frontend && npm run lint` 无 error。

**Step 4：Commit（若有小修）**

```bash
git add -A
git commit -m "fix: end-to-end polish for requirement/test-point management"
```

---

## 风险与边界

- **SQLModel 关系**：新增字段均为 `Optional`，自动迁移会 `ALTER TABLE` 补齐；旧库无需重建。
- **异步任务与 Session**：`regenerate_*` 在 `asyncio.create_task` 中运行，使用传入的同一 `session` 对象。FastAPI 的 `SessionDep` 在请求返回后会被关闭，异步后台任务若晚于请求结束才访问 session 会报错。因此 regenerate 路由**不依赖请求级 session**：改为在任务内部通过 `db.db.engine` 新建独立 `Session`（见下方修正）。
- **ApiScenario 孤儿**：删除/就地更新时统一处理 `scenario_id` 清理，避免游离场景。
- **空结果**：LLM 返回空 `{}` 的用例不创建；测试点生成 0 条时清空其旧用例。

### 重要修正（Task 5/6 的会话生命周期）

`regenerate_test_point` / `regenerate_requirement_point` 必须在**任务内部新建独立 Session**，不能复用请求级 `SessionDep`（请求返回后即关闭）。实现方式：

在 `PipelineRunner.__init__` 中保存 `engine` 引用（或导入 `db.db.engine`），在 `regenerate_*` 方法开头：

```python
from db.db import engine as _engine
with SQLSession(_engine) as session:
    self.db = session
    # ... 原逻辑（用 session 替代 self.db）
```

即把 `regenerate_*` 内部所有 `self.db` 替换为任务内新建的 `session`，任务结束 `with` 自动关闭。路由层创建 `runner` 时可只传配置（不再传 `db_session`），或传 `engine`。

> 此修正需在 Task 5 实现时落地：`PipelineRunner` 增加一个接受 `engine` 的构造路径，`regenerate_*` 内部用 `with SQLSession(engine)` 包裹。
