# Phase 2: Backend Performance + Big-File Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two measurable performance points (list-endpoint count queries, serial pipeline LLM calls) and split two oversized files (`generate_testcases` ~600-line function and `api_test_tool.py` ~2006 lines) into focused units, without changing behavior.

**Architecture:** (1) Collapse 5 count round-trips in `get_testcases` into 2. (2) Parallelize pipeline phases 2 and 3 with `asyncio.gather` + a bounded `Semaphore`, keeping all DB writes after the gather (Session is not coroutine-safe). (3) Extract four private helpers from `generate_testcases`. (4) Split `api_test_tool.py` into `swagger_parser.py`, `assertion_engine.py`, `step_runner.py`, `scenario_runner.py` (+ a shared `schema_utils.py`), keeping the 10-name public API importable.

**Tech Stack:** Python 3.11, FastAPI, SQLModel/SQLAlchemy, asyncio, httpx, jsonpath-ng.

## Global Constraints

- **Prerequisite:** Phase 0 test suite merged and green; **strongly recommended** Phase 1 merged first (this plan assumes `utils.llm_factory` exists for the concurrency task — if Phase 1 is not done, the concurrency task still works against the current agent constructors). After every task: `cd backend && pytest` must stay green.
- **Behavior-preserving:** the only intentional behavior change is execution *order/parallelism* in the pipeline (Task 2) and query *count* in `get_testcases` (Task 1) — both must produce identical results/aggregates.
- **SQLModel `Session` is NOT coroutine-safe.** In Task 2, only LLM calls run concurrently; every `session.add`/`commit`/`refresh` runs sequentially after results are collected.
- The public API of `app/services/api_test_tool.py` that other modules import MUST remain importable from `app.services.api_test_tool` after the split. Those names are exactly: `run_endpoint_steps`, `run_scenario`, `run_endpoint`, `create_unit_test_scenario`, `detect_base_url`, `endpoints_from_spec`, `generate_body_from_schema`, `infer_step_dependencies`, `parse_spec_text`, `sync_project_from_spec`. Achieve this by turning `api_test_tool.py` into a façade that re-exports from the new modules.
- After each extraction, run an import smoke for every importer: `cd backend; .\.venv\Scripts\python.exe -c "import app.routes.testcase, app.routes.api_test_tool; print('ok')"`.
- Run commands from `backend/`.

---

### Task 1: Collapse get_testcases count queries (5 → 2)

**Files:**
- Modify: `backend/app/routes/testcase.py:423-456` (the five `func.count()` blocks + `TestCasePage` construction)
- Test: `backend/tests/test_get_testcases_counts.py`

**Interfaces:**
- Consumes: `db.models.TestCase`, `db.models.StatusValue`, `sqlmodel.func`, `sqlmodel.select`.
- Produces: unchanged `TestCasePage` aggregate fields (`totalNumber`, `passed`, `failed`, `not_run`, `totalBugs`) — same values, fewer queries.

**Approach:** one grouped query `select(TestCase.status, func.count()).group_by(TestCase.status)` yields per-status counts (total = their sum); one separate `func.count()` for `totalBugs` (bug_id is not a status). 5 → 2 round-trips. The status filter (`status` query param) and `module_id` filter must be applied to the aggregate the same way the old code did — note the old count blocks applied `session_id` + optional `module_id` but did NOT apply the `case_name`/`status`/`bug_id`/`exist_bug` list filters (the counts were always whole-session/module aggregates). Preserve that exactly.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_get_testcases_counts.py`:

```python
"""Verify get_testcases aggregate counts are correct after query collapse.

Calls the route function directly with the in-memory db_session, bypassing
auth (CurrentUser is only used for nothing in the count path; pass a stub).
"""
from db.models import TestCase, StatusValue, Session as DbSession
from app.routes.testcase import get_testcases


class _StubUser:
    user_id = "u1"
    username = "u1"
    roles: list = []


def _seed(db_session):
    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    rows = [
        TestCase(case_name="a", session_id=sess.id, status=StatusValue.PASSED.value),
        TestCase(case_name="b", session_id=sess.id, status=StatusValue.PASSED.value),
        TestCase(case_name="c", session_id=sess.id, status=StatusValue.FAILED.value),
        TestCase(case_name="d", session_id=sess.id, status=StatusValue.NOT_RUN.value),
        TestCase(case_name="e", session_id=sess.id, status=StatusValue.NOT_RUN.value, bug_id=42),
    ]
    for r in rows:
        db_session.add(r)
    db_session.commit()
    return sess


def test_counts_aggregate_correctly(db_session):
    sess = _seed(db_session)
    resp = get_testcases(
        session_id=sess.id, session=db_session, user=_StubUser(),
        module_id=None, offset=0, limit=1000,
        case_name=None, status=None, bug_id=None, exist_bug=False,
    )
    page = resp.data
    assert page.totalNumber == 5
    assert page.passed == 2
    assert page.failed == 1
    assert page.not_run == 2
    assert page.totalBugs == 1
```

- [ ] **Step 2: Run to confirm it passes against current code (baseline)**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_get_testcases_counts.py -v`
Expected: PASS against the *current* 5-query implementation. (This is the behavior we must preserve. If it fails, the test harness/stub is wrong — fix the test until it passes against current code, then proceed.)

- [ ] **Step 3: Replace the five count blocks with two queries**

In `backend/app/routes/testcase.py`, replace lines 423-447 (from `# 构建基础查询条件` through the `totalBugs = session.scalar(...)` block) with:

```python
    # 统计各状态计数：一次分组查询 + 一次 bug 计数（替代原 5 次 count 往返）
    status_count_base = [TestCase.session_id == session_id]
    if module_id is not None:
        status_count_base.append(TestCase.module_id == module_id)

    status_rows = session.exec(
        select(TestCase.status, func.count())
        .where(*status_count_base)
        .group_by(TestCase.status)
    ).all()
    status_counts = {row[0]: row[1] for row in status_rows}

    passed = status_counts.get(StatusValue.PASSED.value, 0)
    failed = status_counts.get(StatusValue.FAILED.value, 0)
    not_run = status_counts.get(StatusValue.NOT_RUN.value, 0)
    totalNumber = sum(status_counts.values())

    total_bugs_query = [TestCase.session_id == session_id, TestCase.bug_id != None]
    if module_id is not None:
        total_bugs_query.append(TestCase.module_id == module_id)
    totalBugs = session.scalar(select(func.count()).where(*total_bugs_query))
```

Leave the `TestCasePage(...)` construction (lines 449-456) unchanged.

**Caveat to verify:** `session.exec(select(col, func.count())...)` returns rows as tuples — confirm `row[0]`/`row[1]` indexing works with this SQLModel version. If `session.exec` returns scalars only, use `session.execute(...)` instead (SQLAlchemy core) which always returns `Row` tuples. The test in Step 4 will catch this.

- [ ] **Step 4: Run the test against the new implementation**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_get_testcases_counts.py -v`
Expected: PASS with identical aggregates. If you get a "too many values to unpack" or scalar error, switch `session.exec(select(...))` to `session.execute(select(...))` for the grouped query and re-run.

- [ ] **Step 5: Add a module-filtered counts test**

Append to `backend/tests/test_get_testcases_counts.py`:

```python
def test_counts_respect_module_filter(db_session):
    sess = _seed(db_session)
    # add two more rows in module 7
    db_session.add(TestCase(case_name="m1", session_id=sess.id, module_id=7,
                            status=StatusValue.PASSED.value))
    db_session.add(TestCase(case_name="m2", session_id=sess.id, module_id=7,
                            status=StatusValue.FAILED.value))
    db_session.commit()

    resp = get_testcases(
        session_id=sess.id, session=db_session, user=_StubUser(),
        module_id=7, offset=0, limit=1000,
        case_name=None, status=None, bug_id=None, exist_bug=False,
    )
    page = resp.data
    assert page.totalNumber == 2
    assert page.passed == 1
    assert page.failed == 1
    assert page.not_run == 0
```

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_get_testcases_counts.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/testcase.py backend/tests/test_get_testcases_counts.py
git commit -m "perf: collapse get_testcases status counts from 5 queries to 2"
```

---

### Task 2: Pipeline concurrency (phases 2 & 3)

**Files:**
- Modify: `backend/app/pipeline/pipeline_runner.py` — phase-2 loop (lines 343-382) and phase-3 loop (lines 384-477)

**Interfaces:**
- Consumes: `asyncio.gather`, `asyncio.Semaphore`; the existing `self.tp_generator.generate(...)` and `self.tc_designer.design_batch(...)` coroutines.
- Produces: same `all_test_points` / `all_test_cases` DB rows, same progress endpoints — only the LLM calls overlap.

**Critical constraint:** `self.db` (SQLModel Session) is single-threaded and not coroutine-safe. Only the `await ...generate(...)` / `await ...design_batch(...)` LLM calls go inside `gather`; all `self.db.add/commit/refresh` stay on the main coroutine after the gather completes. The current code already collects-then-commits, so this is safe.

- [ ] **Step 1: Add a concurrency constant**

In `backend/app/pipeline/pipeline_runner.py`, near the top (after the URL regex / `_pipeline_tasks` definitions, ~line 34), add:

```python
# 并发度：同时进行的 LLM 调用上限，避免 API 限流
_PIPELINE_CONCURRENCY = 4
```

- [ ] **Step 2: Parallelize phase 2 (test-point generation)**

Replace the phase-2 loop (lines 343-382, from `# ========== 阶段2` through the `self._update_progress(... "测试点生成完成" ...)`) with a gather-based version. The LLM calls run concurrently; DB writes happen after:

```python
            # ========== 阶段2: 测试点生成 (25-50%) ==========
            all_test_points = []
            sem = asyncio.Semaphore(_PIPELINE_CONCURRENCY)
            completed = 0
            total_rp = len(req_points)

            async def _gen_for_point(rp):
                async with sem:
                    try:
                        return rp, await self.tp_generator.generate(rp.content)
                    except Exception as e:
                        logger.error(f"需求点 {rp.id} 测试点生成失败: {e}")
                        return rp, []

            self._update_progress(
                task_id, 2, "测试点生成", 25,
                f"正在为 {total_rp} 个需求点并发生成测试点..."
            )
            tp_results = await asyncio.gather(*[_gen_for_point(rp) for rp in req_points])

            # 收集后统一写库（Session 非协程安全，写操作不并发）
            for rp, tp_data_list in tp_results:
                for tp_data in tp_data_list:
                    tp = TestPoint(
                        requirement_point_id=rp.id,
                        session_id=session_id,
                        content=tp_data.get("content", ""),
                        test_type=tp_data.get("test_type", "functional"),
                        design_method=tp_data.get("design_method", ""),
                        priority=tp_data.get("priority", "medium"),
                        user_id=user_id,
                    )
                    self.db.add(tp)
                    all_test_points.append(tp)
                completed += 1

            self.db.commit()
            for tp in all_test_points:
                self.db.refresh(tp)

            self._update_progress(
                task_id, 2, "测试点生成", 50,
                f"测试点生成完成，共 {len(all_test_points)} 个测试点"
            )
```

- [ ] **Step 3: Parallelize phase 3 (case design batches)**

Replace the phase-3 loop (lines 384-477) with a gather-based version. Batches are designed concurrently; the per-batch DB writes happen sequentially after the gather. The `tc_kwargs` construction logic is unchanged — only the iteration structure changes:

```python
            # ========== 阶段3: 用例设计 (50-85%) ==========
            all_test_cases = []
            BATCH_SIZE = 3
            batches = [
                all_test_points[i:i + BATCH_SIZE]
                for i in range(0, len(all_test_points), BATCH_SIZE)
            ]
            requirement_content = requirement_text
            design_sem = asyncio.Semaphore(_PIPELINE_CONCURRENCY)

            async def _design_batch(batch):
                async with design_sem:
                    test_points_for_batch = [
                        {
                            "id": tp.id, "content": tp.content,
                            "test_type": tp.test_type,
                            "design_method": tp.design_method,
                            "priority": tp.priority,
                        }
                        for tp in batch
                    ]
                    try:
                        return await self.tc_designer.design_batch(
                            test_points_for_batch, requirement_content,
                            is_api_test=is_api_test, api_context=api_context,
                        )
                    except Exception as e:
                        logger.error(f"批次用例设计失败: {e}")
                        return []

            self._update_progress(
                task_id, 3, "用例设计", 50,
                f"正在并发设计{'接口' if is_api_test else '功能'}测试用例... 共 {len(batches)} 批次"
            )
            batch_results = await asyncio.gather(*[_design_batch(b) for b in batches])

            # 收集后统一写库
            for cases_data in batch_results:
                for case_data in cases_data:
                    steps = case_data.get("test_steps", [])
                    tc_kwargs = dict(
                        case_name=case_data.get("title", ""),
                        steps=steps if isinstance(steps, list) else [],
                        preset_conditions=[case_data.get("preconditions", "")] if case_data.get("preconditions") else [],
                        case_level=4,
                        session_id=session_id,
                        module_id=None,
                        test_point_id=case_data.get("test_point_id"),
                        test_type=case_data.get("test_type"),
                        design_method=case_data.get("design_method"),
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

                    tc = TestCase(**tc_kwargs)
                    self.db.add(tc)
                    all_test_cases.append(tc)

            self.db.commit()
            for tc in all_test_cases:
                self.db.refresh(tc)

            self._update_progress(
                task_id, 3, "用例设计", 85,
                f"用例设计完成，共 {len(all_test_cases)} 个用例"
            )
```

Note: I removed the previous `steps_str = json.dumps(...)` local (lines 423-425) because it was computed but never used in `tc_kwargs` (the original assigns `steps=steps`, not `steps_str`). Confirm by re-reading the original — if `steps_str` is genuinely unused, dropping it is safe; if it IS used somewhere, keep it.

- [ ] **Step 4: Verify pipeline_runner imports**

Run: `cd backend; .\.venv\Scripts\python.exe -c "from app.pipeline.pipeline_runner import PipelineRunner; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Run the full suite + a focused concurrency sanity check**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q`
Expected: green (the pipeline isn't directly unit-tested, so this confirms no import/syntax regression).

Then a logic sanity check that gather preserves ordering of collected results:
```powershell
cd backend; .\.venv\Scripts\python.exe -c "import asyncio; print(asyncio.run(asyncio.gather(*[asyncio.sleep(0, r) for r in (1,2,3)])))"
```
Expected: `[1, 2, 3]` (confirms `gather` returns results in submission order, which the DB-write loop relies on).

- [ ] **Step 6: Manual pipeline smoke (human gate)**

With a configured API key, trigger one `/api/testcases/generate-v2` run (functional) and one API-test run through the frontend; confirm both complete and the requirement-point/test-point/case counts are sensible. Note wall-clock time improvement vs. the serial baseline in the PR description.

- [ ] **Step 7: Commit**

```bash
git add backend/app/pipeline/pipeline_runner.py
git commit -m "perf: run pipeline phase 2 & 3 LLM calls concurrently with bounded semaphore"
```

---

### Task 3: Extract `_parse_response` from generate_testcases (pure function)

**Files:**
- Modify: `backend/utils/model_utils.py` — extract the response-parsing block (lines 986-1102) into a module-level `_parse_response`.
- Test: `backend/tests/test_parse_response.py`

**Interfaces:**
- Produces: `_parse_response(response) -> list` — takes the raw model response (dict / `ResponseFormat` / `FunctionalResponseFormat` / object-with-`.response` / None) and returns `local_testcases` (a list) or raises `ValueError` with the existing messages.
- Consumes (already module-level): `_extract_json_with_response`, `_repair_json`, `_normalize_steps`, `TestCase`, `ResponseFormat`, `FunctionalResponseFormat`.

**Why this one first among the splits:** it is the highest-value extraction (the spec explicitly calls it out as "可单测纯函数"), it is the most error-prone block, and Phase 0 already covers the helpers it depends on. The other three helpers (`_prepare_requirement`, `_invoke_model`, `_persist_testcases`) are heavily I/O-bound and are extracted in Task 4 as organize-only moves.

- [ ] **Step 1: Write the failing test for `_parse_response`**

Create `backend/tests/test_parse_response.py`:

```python
"""Tests for the extracted _parse_response pure function."""
import pytest

from utils.model_utils import _parse_response, ResponseFormat
from utils.models import TestCase


def test_parse_response_dict_with_response_key():
    tc = TestCase(case_name="x", steps=["s1"], preset_conditions=[],
                  expected_results=["e1"], case_level=1)
    out = _parse_response({"response": [tc]})
    assert out == [tc]


def test_parse_response_structured_response_attr():
    rf = ResponseFormat(response=[
        TestCase(case_name="y", steps=[], preset_conditions=[],
                 expected_results=[], case_level=4)
    ])
    out = _parse_response({"structured_response": rf})
    assert out[0].case_name == "y"


def test_parse_response_response_format_object():
    rf = ResponseFormat(response=[
        TestCase(case_name="z", steps=[], preset_conditions=[],
                 expected_results=[], case_level=2)
    ])
    out = _parse_response(rf)
    assert out[0].case_name == "z"


def test_parse_response_none_raises():
    with pytest.raises(ValueError):
        _parse_response(None)


def test_parse_response_unsupported_type_raises():
    with pytest.raises(ValueError):
        _parse_response(12345)


def test_parse_response_messages_json_content():
    # Simulate an agent response dict whose last message content is JSON.
    class _Msg:
        def __init__(self, content):
            self.content = content
            self.tool_calls = []
    payload = '{"response": [{"case_name": "from_msg", "steps": [], "preset_conditions": [], "expected_results": [], "case_level": 1}]}'
    out = _parse_response({"messages": [_Msg(payload)]})
    assert out[0].case_name == "from_msg"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_parse_response.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_response'`.

- [ ] **Step 3: Extract the function**

In `backend/utils/model_utils.py`, add a new module-level function (place it just above `generate_testcases`, ~line 690). Move the body of the response-parsing block (current lines 986-1102) into it. The function consolidates the three local `import json as _json` into one at the top and returns `local_testcases` instead of falling through:

```python
def _parse_response(response) -> list:
    """Parse a raw model/agent response into a list of TestCase objects.

    Handles: dict with 'structured_response'/'response'/'messages',
    ResponseFormat/FunctionalResponseFormat objects, objects exposing
    `.response`, and tool_calls/invalid_tool_calls repair. Raises ValueError
    on None or unsupported shapes. Pure function (no I/O).
    """
    import json as _json

    local_testcases = None

    if isinstance(response, dict):
        if 'structured_response' in response:
            local_testcases = response['structured_response'].response
        elif 'response' in response:
            local_testcases = response['response']
        elif 'messages' in response:
            messages = response.get('messages', [])
            # ... MOVE lines 1001-1077 here verbatim (the messages-scan,
            #     tool_calls repair, and invalid_tool_calls fallback),
            #     replacing each local `import json as _json` with use of
            #     the single _json imported above.
            if local_testcases is None:
                raise ValueError("模型生成了无效的结构化输出（messages 中无可解析用例）")
    elif isinstance(response, (ResponseFormat, FunctionalResponseFormat)):
        local_testcases = response.response
    elif response is None:
        raise ValueError("模型调用未返回有效结果（response 为 None）")
    elif hasattr(response, 'response'):
        local_testcases = response.response
    else:
        raise ValueError(f"不支持的响应格式: {type(response).__name__}")

    if local_testcases is None:
        raise ValueError("模型生成了无效的结构化输出")
    return local_testcases
```

**Executor note:** the `# ... MOVE ...` comment marks where the existing lines 1001-1077 (messages scan + `tool_calls` repair + `invalid_tool_calls` fallback, which call `_extract_json_with_response`, `_repair_json`, `_normalize_steps`, and rebuild `TestCase(...)`) must be pasted verbatim. Do not rewrite that logic — move it. Preserve the exact `TestCase(case_name=..., steps=_normalize_steps(...), preset_conditions=_normalize_steps(...), expected_results=..., case_level=...)` construction. Keep the existing `raise ValueError` messages from lines 1078-1102.

- [ ] **Step 4: Replace the inline block in generate_testcases with a call**

In `generate_testcases`, the response-parsing block lived inside the big `try:` at ~line 987. Replace lines 986-1102 (the `local_testcases = None` init through the final `else: raise ValueError(...)`) with:

```python
            local_testcases = _parse_response(response)
```

Keep the surrounding `try:`/`except` (the outer handler at line 1299) and the history-extraction Section C (lines 964-984) exactly where they are — Section C runs before this call and mutates `_history_save_content`.

- [ ] **Step 5: Run the new test + full suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_parse_response.py -v`
Expected: all PASS.
Then: `cd backend; .\.venv\Scripts\python.exe -c "import utils.model_utils; print('ok')"` → `ok`.
Then: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 6: Commit**

```bash
git add backend/utils/model_utils.py backend/tests/test_parse_response.py
git commit -m "refactor: extract _parse_response pure function from generate_testcases"
```

---

### Task 4: Extract the remaining generate_testcases helpers (organize-only)

**Files:**
- Modify: `backend/utils/model_utils.py` — extract `_prepare_requirement`, `_invoke_model`, `_persist_testcases`.

**Interfaces (the contract between extracted helpers — exact names/returns):**
- `async def _prepare_requirement(requirement, *, is_api_test, module_id, mcp_configs, selected_skill_names, db_session, user_id) -> dict` — returns a context dict with keys: `requirement` (rebuilt), `selected_prompt`, `selected_schema`, `history_save_content`, `parsed_content`, `is_lanhu`, `is_feishu`, `has_feishu_url`, `has_lanhu_url`, `history_context`. (Async because it awaits prefetch.)
- `async def _invoke_model(prep, *, model_type, api_key, api_base_url, api_proxy_url, api_model, ollama_url, ollama_model, mcp_configs, session_id) -> tuple[object, "_TokenUsageCallback"]` — returns `(response, diagnostic_cb)`. Initializes `_diagnostic_cb = _TokenUsageCallback()` at the top (fixes the latent NameError in the parsed-content branch).
- `def _persist_testcases(local_testcases, *, session_id, module_id, requirement, history_save_content, endpoint_index_to_id, db_session, user_id, api_project_id) -> list` — returns `db_testcases`.

**Important — known latent bug to fix here:** in the current parsed-content branch, `_diagnostic_cb` is referenced (lines 844/846/848) before assignment (assignments are at 865/901 in other branches). Initialize `_diagnostic_cb = _TokenUsageCallback()` at the top of `_invoke_model` so it exists on every path. Note this fix in the commit message.

**This is organize-only:** these helpers are heavily I/O bound (MCP, LLM, DB) and are not unit-tested here; correctness is verified by import-smoke + the full suite + the manual generation gate. Do NOT change logic — only move blocks and wire parameters.

- [ ] **Step 1: Extract `_prepare_requirement`**

Move `generate_testcases` Section A (lines 721-803: requirement cleaning, Lanhu/Feishu prefetch + size check, history-context build, final requirement assembly, skill injection) into a module-level `async def _prepare_requirement(...)` with the signature above. Return the context dict. In `generate_testcases`, replace that block with:

```python
        prep = await _prepare_requirement(
            requirement,
            is_api_test=is_api_test, module_id=module_id,
            mcp_configs=mcp_configs, selected_skill_names=selected_skill_names,
            db_session=db_session, user_id=user_id,
        )
        requirement = prep["requirement"]
```

(Subsequent code reads `prep["selected_prompt"]`, `prep["parsed_content"]`, etc., or unpack the keys into locals immediately after for minimal downstream churn — unpacking into the same local variable names the old code used means the rest of the function needs no further edits.)

**Executor note:** to minimize downstream edits, immediately unpack: `selected_prompt = prep["selected_prompt"]; selected_schema = prep["selected_schema"]; parsed_content = prep["parsed_content"]; has_feishu_url = prep["has_feishu_url"]; has_lanhu_url = prep["has_lanhu_url"]; _history_save_content = prep["history_save_content"]` etc. — restoring the exact local names the rest of `generate_testcases` already uses.

- [ ] **Step 2: Verify import + suite after first extraction**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import utils.model_utils; print('ok')"` → `ok`.
Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 3: Extract `_invoke_model`**

Move Section B (lines 805-962: the `model_type == "api"` agent/direct branching with its try/except, plus the ollama `else` branch, plus duration logging) into `async def _invoke_model(...)`. Initialize `_diagnostic_cb = _TokenUsageCallback()` at the top so it is defined on every branch (the bug fix). Return `(response, _diagnostic_cb)`. In `generate_testcases`, replace the block with:

```python
        response, _diagnostic_cb = await _invoke_model(
            prep,
            model_type=model_type, api_key=api_key, api_base_url=api_base_url,
            api_proxy_url=api_proxy_url, api_model=api_model,
            ollama_url=ollama_url, ollama_model=ollama_model,
            mcp_configs=mcp_configs, session_id=session_id,
        )
```

(If Phase 1 is merged, `_invoke_model` builds models via `build_chat_model`; otherwise it keeps the inline `ChatOpenAI(...)` blocks. Either way, move logic verbatim.)

- [ ] **Step 4: Verify import + suite**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import utils.model_utils; print('ok')"` → `ok`.
Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 5: Extract `_persist_testcases`**

Move Section E (lines 1104-1296: validation, per-testcase conversion of steps/preset_conditions, ApiScenario creation, `_upsert_history_prompt`) into `def _persist_testcases(...)` returning `db_testcases`. In `generate_testcases`, the tail becomes:

```python
        db_testcases = _persist_testcases(
            local_testcases,
            session_id=session_id, module_id=module_id, requirement=requirement,
            history_save_content=_history_save_content,
            endpoint_index_to_id=kwargs.get("endpoint_index_to_id", {}),
            db_session=db_session, user_id=user_id,
            api_project_id=kwargs.get("api_project_id"),
        )
        return db_testcases, requirement
```

Keep Section C (tool-message history extraction, lines 964-984) inline in `generate_testcases` before the `_parse_response` call — it mutates `_history_save_content` which feeds `_persist_testcases`.

- [ ] **Step 6: Verify import + suite**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import utils.model_utils; print('ok')"` → `ok`.
Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 7: Manual generation gate (human)**

With a configured API key, run one functional and one API generation via `POST /api/testcases/generate`; confirm both still produce cases and persist scenarios. This is the real regression gate for the I/O-bound helpers.

- [ ] **Step 8: Commit**

```bash
git add backend/utils/model_utils.py
git commit -m "refactor: split generate_testcases into prepare/invoke/persist helpers; fix _diagnostic_cb NameError"
```

---

### Task 5: Split api_test_tool.py — shared schema utils + assertion engine

**Files:**
- Create: `backend/app/services/schema_utils.py`
- Create: `backend/app/services/assertion_engine.py`
- Modify: `backend/app/services/api_test_tool.py` (remove moved functions, import them back for now)

**Interfaces:**
- `schema_utils.py` exports the schema/example/merge helpers consumed by parser + step + scenario modules: `MAX_SCHEMA_EXAMPLE_DEPTH`, `_resolve_ref`, `_ref_key`, `_schema_type`, `_coerce_int`, `_coerce_float`, `_first_schema_value`, `_minimum_number`, `_maximum_number`, `_valid_number_for_schema`, `_valid_string_for_schema`, `_valid_array_for_schema`, `_schema_example`, `_resolve_schema_deep`, `_schema_required`, `_schema_properties`, `_json_fingerprint`, `_json_equal`, `_load_json_text`.
- `assertion_engine.py` exports: `_pairs_to_map`, `_apply_pre_actions`, `_extract_post_actions`, `_run_assertions`, `DEFAULT_SUCCESS_ASSERTIONS`, `_success_assertions`, `_client_error_assertions`, `_schema_invalid_assertions`.

**Strategy:** the split is bottom-up — extract the leaf-dependency modules first (`schema_utils`, `assertion_engine`), then the modules that depend on them (`step_runner`, then `scenario_runner`), finally convert `api_test_tool.py` into a façade. After each extraction, `api_test_tool.py` re-imports the moved names so its public surface and internal references keep working; this keeps every step green.

- [ ] **Step 1: Create schema_utils.py**

Create `backend/app/services/schema_utils.py`. Move these functions verbatim from `api_test_tool.py` (lines as mapped): the constant `MAX_SCHEMA_EXAMPLE_DEPTH` (56) and functions `_resolve_ref` (59), `_ref_key` (74), `_schema_type` (80), `_coerce_int` (112), `_coerce_float` (119), `_first_schema_value` (126), `_minimum_number` (145), `_maximum_number` (158), `_valid_number_for_schema` (171), `_valid_string_for_schema` (184), `_valid_array_for_schema` (212), `_schema_example` (223), `_resolve_schema_deep` (279), plus `_schema_required` (1513), `_schema_properties` (1533), `_json_fingerprint` (478), `_json_equal` (482), `_load_json_text` (486). Add the module imports it needs at top: `import json`, `from typing import Any`. Keep all function bodies byte-for-byte.

- [ ] **Step 2: Re-import into api_test_tool.py and verify**

At the top of `api_test_tool.py`, after removing those definitions, add:

```python
from app.services.schema_utils import (
    MAX_SCHEMA_EXAMPLE_DEPTH, _resolve_ref, _ref_key, _schema_type,
    _coerce_int, _coerce_float, _first_schema_value, _minimum_number,
    _maximum_number, _valid_number_for_schema, _valid_string_for_schema,
    _valid_array_for_schema, _schema_example, _resolve_schema_deep,
    _schema_required, _schema_properties, _json_fingerprint, _json_equal,
    _load_json_text,
)
```

Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.services.api_test_tool, app.services.schema_utils; print('ok')"`
Expected: `ok`. If a `NameError` appears for a helper used inside a moved function, that helper also needs moving or importing — resolve by adding it to schema_utils and the re-import list.

Run the suite: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 3: Create assertion_engine.py**

Create `backend/app/services/assertion_engine.py`. Move verbatim: `DEFAULT_SUCCESS_ASSERTIONS` (28), `_pairs_to_map` (660), `_apply_pre_actions` (671), `_extract_post_actions` (686), `_run_assertions` (708), `_success_assertions` (1483), `_client_error_assertions` (1487), `_schema_invalid_assertions` (1494). Top imports needed: `import copy`, `from jsonpath_ng import parse as parse_jsonpath`, and `from app.routes.proxy import substitute_variables`. (`_run_assertions`/`_apply_pre_actions` use `substitute_variables`; `_extract_post_actions`/scenario use `parse_jsonpath`.)

- [ ] **Step 4: Re-import into api_test_tool.py and verify**

Add to `api_test_tool.py` top:

```python
from app.services.assertion_engine import (
    DEFAULT_SUCCESS_ASSERTIONS, _pairs_to_map, _apply_pre_actions,
    _extract_post_actions, _run_assertions, _success_assertions,
    _client_error_assertions, _schema_invalid_assertions,
)
```

Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.services.api_test_tool, app.services.assertion_engine; print('ok')"` → `ok`.
Run the suite: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/schema_utils.py backend/app/services/assertion_engine.py backend/app/services/api_test_tool.py
git commit -m "refactor: extract schema_utils and assertion_engine from api_test_tool"
```

---

### Task 6: Split api_test_tool.py — swagger_parser

**Files:**
- Create: `backend/app/services/swagger_parser.py`
- Modify: `backend/app/services/api_test_tool.py`
- Modify: `backend/app/routes/api_test_tool.py` (update imports for moved public names)

**Interfaces:**
- `swagger_parser.py` exports public: `parse_spec_text`, `detect_base_url`, `endpoints_from_spec`, `sync_project_from_spec`, `generate_body_from_schema`, `build_body_from_schema`. Plus the parsing/merge privates: `_param_example`, `_parameter_schema`, `_operation_body`, `_operation_request_schema`, `_operation_response_schema`, `_endpoint_key`, `_body_equal`, `_merge_pair_list`, `_merge_unique_dicts`, `_merge_body`, `_field_was_edited`, `_merge_protected_field`, `_global_variable_map`, `_normalize_body_key`, `_global_variable_key_map`, `_replace_body_keys_with_global_tokens`, `_apply_global_tokens_to_body`, `_extract_json_response`, `_endpoint_body_example`, `_merge_body_with_schema_example`, `_body_to_text`, and constants `HTTP_METHODS`, `REMOVED_FROM_SPEC_TAG`.
- Consumes: `app.services.schema_utils` (the schema helpers), `db.models`, `app.routes.proxy` helpers.

- [ ] **Step 1: Create swagger_parser.py**

Create `backend/app/services/swagger_parser.py`. Move verbatim the functions/constants listed above (file-order line refs: `HTTP_METHODS` 24, `REMOVED_FROM_SPEC_TAG` 25, `parse_spec_text` 31, `detect_base_url` 40, `_param_example` 303, `_parameter_schema` 314, `_operation_body` 343, `_operation_request_schema` 368, `_operation_response_schema` 387, `endpoints_from_spec` 413, `_endpoint_key` 472, `_body_equal` 492, `_merge_pair_list` 499, `_merge_unique_dicts` 522, `_merge_body` 535, `_field_was_edited` 554, `_merge_protected_field` 564, `sync_project_from_spec` 578, `build_body_from_schema` 990, `_global_variable_map` 997, `_normalize_body_key` 1012, `_global_variable_key_map` 1016, `_replace_body_keys_with_global_tokens` 1025, `_apply_global_tokens_to_body` 1040, `_extract_json_response` 1051, `generate_body_from_schema` 1067, `_endpoint_body_example` 1141, `_merge_body_with_schema_example` 1153, `_body_to_text` 1175).

Top imports: `import json, copy`, `from urllib.parse import urljoin`, `import yaml`, `from sqlmodel import Session, select`, `from db.models import ApiEndpoint, ApiProject, GlobalParameter`, and from schema_utils: `from app.services.schema_utils import (MAX_SCHEMA_EXAMPLE_DEPTH, _resolve_ref, _ref_key, _schema_type, _schema_example, _resolve_schema_deep, _json_fingerprint, _json_equal, _load_json_text, _first_schema_value)` (include exactly the ones the moved bodies reference — resolve `NameError`s iteratively per Step 2). Also `DEFAULT_SUCCESS_ASSERTIONS` is used by `endpoints_from_spec`: `from app.services.assertion_engine import DEFAULT_SUCCESS_ASSERTIONS`.

- [ ] **Step 2: Re-import into api_test_tool.py and resolve missing names**

Add to `api_test_tool.py`:

```python
from app.services.swagger_parser import (
    HTTP_METHODS, REMOVED_FROM_SPEC_TAG, parse_spec_text, detect_base_url,
    endpoints_from_spec, sync_project_from_spec, generate_body_from_schema,
    build_body_from_schema, _endpoint_body_example, _merge_body_with_schema_example,
    _body_to_text,
)
```

Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.services.api_test_tool, app.services.swagger_parser; print('ok')"`
Expected: `ok`. Iteratively fix any `NameError` by adding the missing helper to swagger_parser's imports (from schema_utils/assertion_engine) — do NOT duplicate function bodies.

- [ ] **Step 3: Update routes/api_test_tool.py imports**

`backend/app/routes/api_test_tool.py` currently imports (line 9-19) from `app.services.api_test_tool`: `create_unit_test_scenario, detect_base_url, endpoints_from_spec, generate_body_from_schema, infer_step_dependencies, parse_spec_text, run_endpoint, run_scenario, sync_project_from_spec`. These all still resolve via the façade re-exports, so **no change is strictly required yet**. Verify:

Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.routes.api_test_tool; print('ok')"` → `ok`.

- [ ] **Step 4: Run the suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/swagger_parser.py backend/app/services/api_test_tool.py
git commit -m "refactor: extract swagger_parser from api_test_tool"
```

---

### Task 7: Split api_test_tool.py — step_runner + unit-test generator

**Files:**
- Create: `backend/app/services/step_runner.py`
- Modify: `backend/app/services/api_test_tool.py`

**Interfaces:**
- `step_runner.py` exports public: `run_endpoint`, `run_endpoint_steps`, `generate_unit_test_steps`, `create_unit_test_scenario`. Plus privates: `API_TEST_HTTP_LIMITS`, `MAX_GENERATED_UNIT_STEPS`, `_merge_endpoint_step`, `_apply_path_parameters`, `_execute_endpoint_step`, and the unit-test cluster (`_unit_step`, `_path_label`, `_get_path_value`, `_path_exists`, `_modify_body_path`, `_iter_schema_fields`, `_request_value_text`, `_invalid_values_for_property`, `_boundary_values_for_property`, `_replace_pair_value`, `_append_unit_step`).
- Consumes: `assertion_engine` (`_run_assertions`, `_apply_pre_actions`, `_extract_post_actions`, `_pairs_to_map`, `_success_assertions`, `_client_error_assertions`, `_schema_invalid_assertions`), `schema_utils` (schema helpers, `_json_fingerprint`), `swagger_parser` (`_endpoint_body_example`), `app.routes.proxy` (`build_param_map`, substitution helpers).

- [ ] **Step 1: Create step_runner.py**

Create `backend/app/services/step_runner.py`. Move verbatim: `API_TEST_HTTP_LIMITS` (27), `MAX_GENERATED_UNIT_STEPS` (26), `_merge_endpoint_step` (777), `_apply_path_parameters` (787), `_execute_endpoint_step` (798), `run_endpoint` (915), `run_endpoint_steps` (933), and the unit-test generation cluster: `_unit_step` (1498), `_path_label` (1554), `_get_path_value` (1566), `_path_exists` (1580), `_modify_body_path` (1588), `_iter_schema_fields` (1608), `_request_value_text` (1633), `_invalid_values_for_property` (1641), `_boundary_values_for_property` (1688), `_replace_pair_value` (1727), `_append_unit_step` (1741), `generate_unit_test_steps` (1757), `create_unit_test_scenario` (1928).

Top imports: `import json, time`, `import httpx`, `from sqlmodel import Session`, `from db.models import ApiEndpoint, ApiProject, ApiScenario`, the proxy helpers `from app.routes.proxy import build_param_map, substitute_in_data, substitute_in_headers, substitute_in_params, substitute_variables, is_valid_url`, plus the needed names from `assertion_engine`, `schema_utils`, and `swagger_parser` (`_endpoint_body_example`). Resolve specifics iteratively in Step 2.

- [ ] **Step 2: Re-import into api_test_tool.py and resolve**

Add to `api_test_tool.py`:

```python
from app.services.step_runner import (
    run_endpoint, run_endpoint_steps, generate_unit_test_steps,
    create_unit_test_scenario,
)
```

Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.services.api_test_tool, app.services.step_runner; print('ok')"`
Expected: `ok`. Fix `NameError`s by adding missing imports to step_runner (no body duplication). Watch for a circular import: `swagger_parser` must NOT import from `step_runner`. Dependencies flow `step_runner → {swagger_parser, assertion_engine, schema_utils}` only.

- [ ] **Step 3: Run the suite + importer smoke**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.routes.testcase, app.routes.api_test_tool; print('ok')"` → `ok` (these import `run_endpoint_steps`, `run_scenario`, `run_endpoint`, `create_unit_test_scenario` via the façade).
Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/step_runner.py backend/app/services/api_test_tool.py
git commit -m "refactor: extract step_runner (execution + unit-test generation) from api_test_tool"
```

---

### Task 8: Split api_test_tool.py — scenario_runner + convert façade

**Files:**
- Create: `backend/app/services/scenario_runner.py`
- Modify: `backend/app/services/api_test_tool.py` (becomes a thin façade)

**Interfaces:**
- `scenario_runner.py` exports public: `run_scenario`, `infer_step_dependencies`. Plus the dependency-inference cluster privates (`_dependency_name_key`, `_dependency_aliases`, `_dependency_variable_name`, `_is_dependency_placeholder`, `_is_dependency_fillable`, `_iter_response_schema_fields`, `_body_from_step_for_inference`, `_infer_body_dependencies`, `_infer_pair_dependencies`, `_has_post_action`) and constants `GENERIC_DEPENDENCY_FIELD_NAMES`, `PLACEHOLDER_RE`, `_DEFAULT_RESPONSE_FIELDS`.
- Consumes: `step_runner` (`_execute_endpoint_step`, `API_TEST_HTTP_LIMITS`), `assertion_engine` (`_extract_post_actions`, etc.), `schema_utils` (`_schema_example`, `_schema_properties`), `app.routes.proxy` (`build_param_map`).

- [ ] **Step 1: Create scenario_runner.py**

Create `backend/app/services/scenario_runner.py`. Move verbatim: `GENERIC_DEPENDENCY_FIELD_NAMES` (1183), `PLACEHOLDER_RE` (1200), `_DEFAULT_RESPONSE_FIELDS` (1243), the dependency-inference functions `_dependency_name_key` (1203), `_dependency_aliases` (1207), `_dependency_variable_name` (1217), `_is_dependency_placeholder` (1226), `_is_dependency_fillable` (1230), `_iter_response_schema_fields` (1259), `_body_from_step_for_inference` (1287), `_infer_body_dependencies` (1297), `_infer_pair_dependencies` (1320), `_has_post_action` (1340), `infer_step_dependencies` (1352), and `run_scenario` (1945).

Top imports: `import json, re, time`, `import httpx`, `from sqlmodel import Session`, `from db.models import ApiScenario, ApiProject, ApiEndpoint`, `from app.routes.proxy import build_param_map, substitute_variables`, `from app.services.step_runner import _execute_endpoint_step, API_TEST_HTTP_LIMITS`, plus needed schema_utils/assertion_engine names. Note `_DEFAULT_RESPONSE_FIELDS` is built at import time by calling `_dependency_aliases` — keep that call after the function is defined (preserve original ordering).

- [ ] **Step 2: Convert api_test_tool.py into a pure façade**

After moving `run_scenario`/`infer_step_dependencies`, `api_test_tool.py` should contain NO function definitions of its own — only re-export imports. Replace its entire body with a façade that preserves the public API and any internal re-exports other modules rely on:

```python
"""Façade for the API testing engine.

The implementation was split into focused modules; this module preserves the
historical public import surface `from app.services.api_test_tool import ...`.
"""
from app.services.schema_utils import *  # noqa: F401,F403
from app.services.assertion_engine import *  # noqa: F401,F403
from app.services.swagger_parser import (  # noqa: F401
    parse_spec_text, detect_base_url, endpoints_from_spec,
    sync_project_from_spec, generate_body_from_schema, build_body_from_schema,
)
from app.services.step_runner import (  # noqa: F401
    run_endpoint, run_endpoint_steps, generate_unit_test_steps,
    create_unit_test_scenario,
)
from app.services.scenario_runner import (  # noqa: F401
    run_scenario, infer_step_dependencies,
)
```

(Prefer explicit names over `*` for the public ones, as shown. The two `import *` lines for schema_utils/assertion_engine are a convenience for any private helper that external code might import; if you prefer zero wildcard imports, enumerate them — both are acceptable.)

- [ ] **Step 3: Verify the full importer graph**

Run:
```powershell
cd backend; .\.venv\Scripts\python.exe -c "from app.services.api_test_tool import run_endpoint_steps, run_scenario, run_endpoint, create_unit_test_scenario, detect_base_url, endpoints_from_spec, generate_body_from_schema, infer_step_dependencies, parse_spec_text, sync_project_from_spec; print('public API ok')"
```
Expected: `public API ok` (this is the exact 10-name contract from the Global Constraints).
Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.routes.testcase, app.routes.api_test_tool, app.main; print('routes ok')"` → `routes ok`.

- [ ] **Step 4: Run the full suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 5: Add an import-contract regression test**

Create `backend/tests/test_api_test_tool_facade.py`:

```python
"""Guards the public import surface of the api_test_tool façade after the split."""
import importlib


def test_public_api_importable():
    mod = importlib.import_module("app.services.api_test_tool")
    for name in [
        "run_endpoint_steps", "run_scenario", "run_endpoint",
        "create_unit_test_scenario", "detect_base_url", "endpoints_from_spec",
        "generate_body_from_schema", "infer_step_dependencies",
        "parse_spec_text", "sync_project_from_spec",
    ]:
        assert hasattr(mod, name), f"missing public name: {name}"
```

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_api_test_tool_facade.py -v` → PASS.

- [ ] **Step 6: Manual API-testing smoke (human gate)**

Start the server and, via the frontend API-testing UI: import a small Swagger spec (exercises `parse_spec_text`/`endpoints_from_spec`/`sync_project_from_spec`), run one endpoint (`run_endpoint`), and run one scenario (`run_scenario`). Confirm all succeed. This is the regression gate for the runtime executors that aren't unit-tested.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/scenario_runner.py backend/app/services/api_test_tool.py backend/tests/test_api_test_tool_facade.py
git commit -m "refactor: extract scenario_runner; convert api_test_tool to facade"
```

---

## Self-Review

**Spec coverage** (spec §阶段 2): 2.1 get_testcases 5→2 queries via group_by + separate bug count ✓ (Task 1). 2.2 pipeline phase 2 & 3 concurrency with `asyncio.gather + Semaphore(N)`, N configurable default 4, DB writes after gather (Session not coroutine-safe) ✓ (Task 2). 2.3 generate_testcases split into `_prepare_requirement`/`_invoke_model`/`_parse_response`/`_persist_testcases`, with `_parse_response` as a unit-tested pure function ✓ (Tasks 3-4). 2.4 api_test_tool split into swagger_parser/assertion_engine/scenario_runner/step_runner — implemented as 5 modules (added `schema_utils` for the shared schema/example group the agent map flagged as cross-cutting, preventing circular imports), done last, verifying after each extraction ✓ (Tasks 5-8).

**Placeholder scan:** count-query, concurrency, and `_parse_response` tasks have full code. The four big-file extractions list every function by name + original line number and give the exact re-import block + verification command per step. The two organize-only moves (`_prepare_requirement`/`_invoke_model`/`_persist_testcases` and the verbatim function relocations) intentionally say "move verbatim" rather than re-printing hundreds of lines — this is correct for a behavior-preserving relocation, and each is gated by an import-smoke + full-suite run. No TBD/TODO.

**Type consistency:** the 10-name public contract is stated identically in Global Constraints, Task 8 Step 3, and the façade test. The dependency DAG `scenario_runner → step_runner → {swagger_parser, assertion_engine, schema_utils}` is stated in the architecture and enforced by Task 7 Step 2's circular-import warning. `_parse_response(response) -> list` signature matches its test and its call site. The `_invoke_model` returns `(response, _diagnostic_cb)` consistent with its call site in Task 4 Step 3.

**Surfaced bug fix:** the `_diagnostic_cb` NameError in the parsed-content branch (referenced before assignment) is fixed in Task 4 Step 3 by initializing it at the top of `_invoke_model`, and noted in that commit message — a real latent bug found during analysis, fixed as part of the extraction rather than silently.

**Ordering caveat:** Task 2 (concurrency) reads cleanest if Phase 1's `llm_factory` is merged, but works either way (noted in Global Constraints and Task 4 Step 3). Task 3 depends on Phase 0's helper tests being present.
