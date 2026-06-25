# 用例只关联场景、不存快照 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test cases no longer store api_call step snapshots — they only reference scenarios via `scenario_id`.

**Architecture:** During generation, api_call steps are only written to the scenario. The test case stores text-only steps. The frontend reads scenario steps for display. Execution already uses `run_scenario` when `scenario_id` exists (done in previous fix).

**Tech Stack:** Python 3.11, FastAPI, SQLModel, React 18, TypeScript, Ant Design

## Global Constraints

- Backend models use Shanghai timezone (UTC+8)
- All API responses: `{code: number, message: string, data: T}`
- Frontend Vite proxy: `/api` → `http://127.0.0.1:8000`
- Old test cases (with api_call steps but no scenario_id) must continue to work

---

### Task 1: 生成时过滤 api_call 步骤

**Files:**
- Modify: `backend/utils/model_utils.py:1001-1054`

**Interfaces:**
- Produces: `testcase.steps` and `testcase.preset_conditions` contain only non-api_call steps
- Produces: `scenario.steps` contains the full api_call steps (unchanged)
- Produces: `testcase.scenario_id` links to the created scenario (unchanged)

- [ ] **Step 1: Read the current generation logic**

Read `backend/utils/model_utils.py` lines 995-1055 to understand the current flow:
- `converted_preset_conditions` — all preset conditions (text + api_call)
- `converted_steps` — all steps (text + api_call)
- `api_call_preset` — extracted api_call presets
- `api_call_steps` — extracted api_call steps
- `all_api_call_steps` — combined for scenario creation

- [ ] **Step 2: Filter api_call steps from testcase fields**

After the scenario is created (after line 1041), before the `DBTestCase` is created (line 1044), add filtering logic:

```python
            # 过滤 api_call 步骤：用例只存文本步骤，api_call 步骤在场景中
            if scenario_id:
                converted_preset_conditions = [
                    s for s in converted_preset_conditions
                    if not (isinstance(s, dict) and s.get("endpoint_id"))
                ]
                converted_steps = [
                    s for s in converted_steps
                    if not (isinstance(s, dict) and s.get("endpoint_id"))
                ]
```

Insert this block between line 1041 (`scenario_id = scenario.id`) and line 1043 (`# 创建DBTestCase对象`).

- [ ] **Step 3: Verify backend syntax**

Run: `cd backend && python -c "import ast; ast.parse(open('utils/model_utils.py', encoding='utf-8').read()); print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add backend/utils/model_utils.py
git commit -m "feat(backend): filter api_call steps from testcase, only store in scenario"
```

---

### Task 2: 列表接口返回 scenario steps 摘要

**Files:**
- Modify: `backend/app/routes/testcase.py:368-430`

**Interfaces:**
- Consumes: `TestCase.scenario_id` — links to ApiScenario
- Produces: Each testcase in the list response includes `scenario_steps` field (list of `{method, path, name}` dicts)

- [ ] **Step 1: Read the current list endpoint**

Read `backend/app/routes/testcase.py` lines 368-430 to understand the current flow.

- [ ] **Step 2: Add a response model for scenario step summary**

After the `TestCasePage` class (line 187), add:

```python
class ScenarioStepSummary(BaseModel):
    method: str = ""
    path: str = ""
    name: str = ""
```

- [ ] **Step 3: Batch-load scenario steps for the returned testcases**

After `testcases_db = session.exec(query.offset(offset).limit(limit)).all()` (line 396), add batch loading:

```python
    # 批量加载关联场景的步骤摘要
    scenario_ids = [tc.scenario_id for tc in testcases_db if tc.scenario_id]
    scenario_steps_map: dict[int, list[dict]] = {}
    if scenario_ids:
        scenarios = session.exec(
            select(ApiScenario).where(ApiScenario.id.in_(scenario_ids))
        ).all()
        for sc in scenarios:
            steps_summary = []
            for step in (sc.steps or []):
                if isinstance(step, dict):
                    steps_summary.append({
                        "method": step.get("method", ""),
                        "path": step.get("path") or step.get("url", ""),
                        "name": step.get("name") or step.get("endpoint_name", ""),
                    })
            scenario_steps_map[sc.id] = steps_summary
```

Make sure `ApiScenario` is imported at the top of the file (it was added in a previous fix).

- [ ] **Step 4: Attach scenario_steps to each testcase**

After building `scenario_steps_map`, iterate over testcases and attach:

```python
    # 附加场景步骤摘要到用例
    tc_items = []
    for tc in testcases_db:
        tc_dict = tc.model_dump()
        tc_dict["scenario_steps"] = scenario_steps_map.get(tc.scenario_id, []) if tc.scenario_id else []
        tc_items.append(tc_dict)
```

Then update the `TestCasePage` construction to use `tc_items` instead of `testcases_db`. The `TestCasePage.items` type needs to accept dicts — change it to `List[dict]` or use a custom response.

- [ ] **Step 5: Update TestCasePage model**

Change `TestCasePage.items` from `List[TestCase]` to `List[dict]` to support the extra `scenario_steps` field:

```python
class TestCasePage(BaseModel):
    items: List[dict]  # TestCase fields + scenario_steps
    totalNumber: int = Field(0, description="总条数")
    passed: int = Field(0, description="已通过的用例数")
    failed: int = Field(0, description="未通过的用例数")
    not_run: int = Field(0, description="未执行的用例数")
    totalBugs: int = Field(0, description="Bug数")
    model_config = {
        "arbitrary_types_allowed": True,
    }
```

- [ ] **Step 6: Verify backend syntax**

Run: `cd backend && python -c "import ast; ast.parse(open('app/routes/testcase.py', encoding='utf-8').read()); print('OK')"`
Expected: OK

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/testcase.py
git commit -m "feat(backend): include scenario steps summary in testcase list response"
```

---

### Task 3: 前端接口列读取 scenario_steps

**Files:**
- Modify: `frontend/src/components/test-case/TestCaseTable.tsx:73-112`
- Modify: `frontend/src/types/index.ts` (add `scenario_steps` to TestCase or as optional field)

**Interfaces:**
- Consumes: `testcase.scenario_steps` — array of `{method, path, name}` from the list API
- Consumes: `testcase.preset_conditions` / `testcase.steps` — for old test cases without scenario (backward compat)

- [ ] **Step 1: Add `scenario_steps` type**

In `frontend/src/types/index.ts`, add to the `TestCase` interface:

```typescript
  scenario_steps?: { method: string; path: string; name: string }[];
```

- [ ] **Step 2: Update the "接口" column rendering**

In `TestCaseTable.tsx`, replace the "接口" column render function (lines 77-112) with:

```tsx
      render: (_: any, record: TestCase) => {
        // 优先从 scenario_steps 读取（新逻辑）
        const scenarioSteps = record.scenario_steps || [];
        if (scenarioSteps.length > 0) {
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {scenarioSteps.map((step, i: number) => {
                const method = step.method ? step.method.toUpperCase() : '';
                const path = step.path || '';
                const label = method && path ? `${method} ${path}` : (step.name || `步骤 ${i + 1}`);
                return (
                  <Tooltip key={i} title={label}>
                    <Tag color="blue" style={{ fontSize: 11, margin: 0, cursor: 'pointer', maxWidth: 230, display: 'inline-flex', alignItems: 'center' }}>
                      <ApiOutlined style={{ marginRight: 4, flexShrink: 0 }} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{label}</span>
                    </Tag>
                  </Tooltip>
                );
              })}
            </div>
          );
        }

        // 回退：旧用例从 steps/preset_conditions 读取 api_call 步骤
        const allSteps = [
          ...((record.preset_conditions || []).filter((s: any) => typeof s === 'object' && s.type === 'api_call')),
          ...((record.steps || []).filter((s: any) => typeof s === 'object' && s.type === 'api_call')),
        ];
        if (allSteps.length === 0) {
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
            {allSteps.map((step: any, i: number) => {
              const method = step.method ? step.method.toUpperCase() : '';
              const path = step.path || step.url || '';
              const label = method && path ? `${method} ${path}` : (step.description || step.name || `步骤 ${i + 1}`);
              return (
                <Tooltip key={i} title={label}>
                  <Tag color="blue" style={{ fontSize: 11, margin: 0, cursor: 'pointer', maxWidth: 230, display: 'inline-flex', alignItems: 'center' }}>
                    <ApiOutlined style={{ marginRight: 4, flexShrink: 0 }} />
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{label}</span>
                  </Tag>
                </Tooltip>
              );
            })}
          </div>
        );
      },
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep "TestCaseTable" || echo "No errors"`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/components/test-case/TestCaseTable.tsx
git commit -m "feat(ui): read scenario_steps for endpoint column, fallback to old logic for legacy data"
```
