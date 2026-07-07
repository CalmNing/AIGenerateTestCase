# Phase 0: Test Safety Net Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a minimal `pytest` suite covering the pure/near-pure functions that Phases 1–2 will refactor, so behavior can be compared before and after.

**Architecture:** New `backend/tests/` package with a `conftest.py` providing an in-memory SQLite session fixture, plus four test modules targeting refactor hotspots: JSON/level/testcase normalization helpers, pipeline output schemas, API-context building, and endpoint dependency inference. No real LLM calls, no real DB file — tests run against in-memory SQLite or pure functions only.

**Tech Stack:** Python 3.11, pytest, SQLModel + SQLite (`sqlite:///:memory:`), Pydantic v2.

## Global Constraints

- Tests MUST NOT call a real LLM, hit the network, or touch `backend/data/testcases.db`. Use in-memory SQLite and pure-function calls only.
- Tests are run with the working directory at `backend/` so that `import db...`, `import utils...`, `import app...` resolve (the project uses top-level package imports rooted at `backend/`, per CLAUDE.md `$env:PYTHONPATH = "...\backend"`).
- `pytest` is added to `backend/requirements.txt` (unpinned floor `pytest>=8.0` to match the repo's mixed pin/floor style).
- The run command `cd backend && pytest` is documented in CLAUDE.md under Testing.
- Do not modify any production code in this phase. Phase 0 is additive only. If a test reveals a bug, record it in the test as an `xfail` with a reason — do not fix production code here.
- Importing `app.routes.testcase` transitively imports `app.deps` → `db.db`, which calls `create_db_and_tables()` against the real `data/testcases.db` at import time. This is existing behavior and is acceptable for tests (it only ensures the real DB schema exists; tests still use their own in-memory engine). Do not try to suppress it.

---

### Task 1: Test package scaffold + conftest

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Modify: `backend/requirements.txt` (append `pytest>=8.0`)
- Modify: `backend/../CLAUDE.md` (i.e. `F:\VSCode\ai-generate-testcase\CLAUDE.md`) — Testing section

**Interfaces:**
- Consumes: `db.models` (SQLModel table models), `sqlmodel.create_engine`, `SQLModel.metadata`.
- Produces: a pytest fixture `db_session` yielding an in-memory `sqlmodel.Session` with all tables created; importable by every other test module via pytest's conftest discovery.

- [ ] **Step 1: Create the test package marker**

Create `backend/tests/__init__.py` as an empty file:

```python
```

(Empty file — it exists only so `tests` is a package and pytest import mode is unambiguous.)

- [ ] **Step 2: Write conftest with an in-memory DB fixture**

Create `backend/tests/conftest.py`:

```python
"""Shared pytest fixtures for the backend test suite.

All fixtures use in-memory SQLite — no real database file is touched.
"""
import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

# Importing db.models registers every table on SQLModel.metadata.
import db.models  # noqa: F401


@pytest.fixture()
def db_engine():
    """Fresh in-memory SQLite engine with all tables created, per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """In-memory SQLModel Session bound to the per-test engine."""
    with Session(db_engine) as session:
        yield session
```

Note: `StaticPool` + a single in-memory URL keeps one shared connection so the schema persists across `Session` usage within a test.

- [ ] **Step 3: Add a smoke test to prove the harness works**

Append to `backend/tests/conftest.py`? No — create a tiny standalone check instead. Add to a new file `backend/tests/test_smoke.py`:

```python
"""Smoke test: confirms the in-memory DB fixture and imports work."""
from db.models import ApiProject


def test_db_session_fixture_creates_tables(db_session):
    project = ApiProject(name="smoke-project")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    assert project.id is not None
    assert project.name == "smoke-project"
```

- [ ] **Step 4: Add pytest to requirements**

In `backend/requirements.txt`, append a new last line:

```
pytest>=8.0
```

- [ ] **Step 5: Install pytest into the venv**

Run (Windows PowerShell, from repo root):

```powershell
backend\.venv\Scripts\python.exe -m pip install "pytest>=8.0"
```

Expected: pip reports pytest (and pluggy/iniconfig) installed or already satisfied.

- [ ] **Step 6: Run the smoke test**

Run (from `backend/`):

```powershell
cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_smoke.py -v
```

Expected: `1 passed`. If you get import errors for `db.models`, confirm the command was run from `backend/` (CWD must be `backend/` so top-level packages resolve).

- [ ] **Step 7: Document the test command in CLAUDE.md**

In `F:\VSCode\ai-generate-testcase\CLAUDE.md`, find the `### Testing` section which currently reads:

```
### Testing
No automated backend tests. Frontend lint: `npm run lint`.
```

Replace it with:

```
### Testing
Backend: `cd backend && pytest` (or `.venv\Scripts\python.exe -m pytest`). Tests live in `backend/tests/`, use in-memory SQLite, and never call a real LLM or network. Frontend lint: `npm run lint`.
```

- [ ] **Step 8: Commit**

```bash
git add backend/tests/__init__.py backend/tests/conftest.py backend/tests/test_smoke.py backend/requirements.txt CLAUDE.md
git commit -m "test: add backend pytest harness with in-memory SQLite fixture"
```

---

### Task 2: test_helpers — JSON repair, level + testcase normalization

**Files:**
- Create: `backend/tests/test_helpers.py`

**Interfaces:**
- Consumes: `utils.helpers._normalize_level`, `utils.helpers._normalize_testcase_dict`, `utils.helpers._repair_json`, `utils.helpers._extract_json_with_response`, `utils.helpers._normalize_extracted_json`; `utils.models.ApiCallStep`.
- Produces: regression coverage for the normalization functions that `generate_testcases` (Phase 2) will extract into `_parse_response`.

- [ ] **Step 1: Write tests for `_normalize_level`**

Create `backend/tests/test_helpers.py`:

```python
"""Regression tests for utils.helpers normalization functions.

These cover the refactor hotspots in Phase 1/2: _parse_response extraction
and any movement of normalization logic. Pure functions, no DB/LLM.
"""
import pytest

from utils.helpers import (
    _normalize_level,
    _normalize_testcase_dict,
    _repair_json,
    _extract_json_with_response,
    _normalize_extracted_json,
)
from utils.models import ApiCallStep


@pytest.mark.parametrize("raw,expected", [
    (1, 1), (2, 2), (3, 3), (4, 4),
    (0, 4), (5, 4), (99, 4),          # out of range -> 4
    ("1", 1), ("4", 4), ("7", 4),     # numeric strings
    ("P0", 1), ("P1", 1), ("P2", 2), ("P3", 3), ("P4", 4),
    ("功能测试", 1), ("边界测试", 2), ("异常测试", 3), ("场景测试", 4),
    ("garbage", 4), ("", 4), (None, 4),
])
def test_normalize_level(raw, expected):
    assert _normalize_level(raw) == expected
```

- [ ] **Step 2: Run it to verify it passes**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_helpers.py -v`
Expected: all `test_normalize_level` cases PASS. (This is characterization of existing behavior; if a case fails, the assertion is wrong — re-read `_normalize_level` in `utils/helpers.py:321` and correct the expectation, do not change production code.)

- [ ] **Step 3: Add tests for `_repair_json`**

Append to `backend/tests/test_helpers.py`:

```python
def test_repair_json_passthrough_valid():
    valid = '{"a": 1, "b": "ok"}'
    assert _repair_json(valid) == valid


def test_repair_json_fixes_cjk_inner_quotes():
    # Unescaped inner quotes between CJK chars get replaced with full-width quotes,
    # making the whole string parseable.
    import json
    broken = '{"name": "点击"确定"按钮"}'
    repaired = _repair_json(broken)
    parsed = json.loads(repaired)
    assert parsed["name"].startswith("点击")


def test_repair_json_returns_original_when_unfixable():
    unfixable = '{this is not json at all'
    assert _repair_json(unfixable) == unfixable
```

- [ ] **Step 4: Add tests for `_extract_json_with_response`**

Append to `backend/tests/test_helpers.py`:

```python
def test_extract_json_object_with_response_key():
    text = 'noise before {"response": [{"name": "tc1"}]} noise after'
    data = _extract_json_with_response(text)
    assert data is not None
    assert "response" in data
    assert data["response"][0]["name"] == "tc1"


def test_extract_json_object_with_test_cases_key():
    text = 'prefix {"test_cases": [{"name": "tc1"}]} suffix'
    data = _extract_json_with_response(text)
    assert data is not None
    assert "test_cases" in data


def test_extract_top_level_array_wraps_as_test_cases():
    text = '[{"name": "tc1"}, {"name": "tc2"}]'
    data = _extract_json_with_response(text)
    assert data is not None
    assert data["test_cases"][1]["name"] == "tc2"


def test_extract_returns_none_when_no_match():
    assert _extract_json_with_response("just plain text, no json") is None
```

- [ ] **Step 5: Add tests for `_normalize_testcase_dict`**

Append to `backend/tests/test_helpers.py`:

```python
def test_normalize_testcase_dict_english_keys():
    raw = {
        "name": "登录成功",
        "level": "P0",
        "precondition": "用户已注册",
        "expected_result": "跳转首页",
        "steps": ["输入账号", "输入密码", "点击登录"],
    }
    out = _normalize_testcase_dict(raw)
    assert out["case_name"] == "登录成功"
    assert out["case_level"] == 1
    assert out["preset_conditions"] == ["用户已注册"]
    assert out["expected_results"] == ["跳转首页"]
    assert out["steps"] == ["输入账号", "输入密码", "点击登录"]


def test_normalize_testcase_dict_chinese_keys():
    raw = {
        "用例名称": "边界值校验",
        "用例级别": "边界测试",
        "前置条件": ["前提1"],
        "预期结果": ["结果1", "结果2"],
        "用例步骤": ["步骤1"],
    }
    out = _normalize_testcase_dict(raw)
    assert out["case_name"] == "边界值校验"
    assert out["case_level"] == 2
    assert out["preset_conditions"] == ["前提1"]
    assert out["expected_results"] == ["结果1", "结果2"]
    assert out["steps"] == ["步骤1"]


def test_normalize_testcase_dict_missing_fields_defaults():
    out = _normalize_testcase_dict({})
    assert out["case_name"] == ""
    assert out["case_level"] == 4
    assert out["preset_conditions"] == []
    assert out["expected_results"] == []
    assert out["steps"] == []


def test_normalize_testcase_dict_api_call_step_in_steps():
    raw = {
        "name": "调用接口",
        "steps": [
            {"api_call": {"endpoint_ref": "[2]", "description": "创建用户"}},
        ],
    }
    out = _normalize_testcase_dict(raw)
    assert len(out["steps"]) == 1
    step = out["steps"][0]
    assert isinstance(step, ApiCallStep)
    assert step.endpoint_ref == 2
    assert step.description == "创建用户"


def test_normalize_testcase_dict_api_endpoint_ref_string():
    out = _normalize_testcase_dict({"name": "x", "api_endpoint_ref": "[1,3]"})
    assert out["api_endpoint_ref"] == [1, 3]
```

- [ ] **Step 6: Add tests for `_normalize_extracted_json`**

Append to `backend/tests/test_helpers.py`:

```python
def test_normalize_extracted_json_maps_test_cases_to_response():
    data = {"test_cases": [{"name": "tc1", "level": 1}]}
    out = _normalize_extracted_json(data)
    assert "response" in out
    assert out["response"][0]["case_name"] == "tc1"


def test_normalize_extracted_json_normalizes_existing_response():
    data = {"response": [{"name": "tc1", "level": "P2"}]}
    out = _normalize_extracted_json(data)
    assert out["response"][0]["case_level"] == 2


def test_normalize_extracted_json_passthrough_when_neither_key():
    data = {"other": 1}
    assert _normalize_extracted_json(data) == {"other": 1}
```

- [ ] **Step 7: Run the full helpers test module**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_helpers.py -v`
Expected: all PASS. If any expectation fails, re-read the corresponding function in `utils/helpers.py` and fix the test expectation to match actual current behavior (this is characterization, not TDD of new behavior).

- [ ] **Step 8: Commit**

```bash
git add backend/tests/test_helpers.py
git commit -m "test: characterize helpers JSON repair and testcase normalization"
```

---

### Task 3: test_pipeline_schemas — schema parsing & validation

**Files:**
- Create: `backend/tests/test_pipeline_schemas.py`

**Interfaces:**
- Consumes: `app.pipeline.schemas` (`RequirementAnalysisResult`, `TestPointGenerationResult`, `TestCaseOut`, `TestCaseDesignResult`, `ApiTestCaseOut`, `ApiCallStepOut`, `ApiCallKeyValueOut`, `AssertionRuleOut`, `ApiTestCaseDesignResult`).
- Produces: regression coverage that the structured-output schemas parse model-shaped dicts correctly — protects Phase 1's pipeline-agent refactor and Phase 2's concurrency change.

- [ ] **Step 1: Write parsing tests for phase-1/2 schemas**

Create `backend/tests/test_pipeline_schemas.py`:

```python
"""Schema parse/validation tests for the 4-phase pipeline structured output.

Protects the structured_output contracts that Phase 1 (agent dedup) and
Phase 2 (pipeline concurrency) must preserve. Pure Pydantic, no DB/LLM.
"""
from app.pipeline.schemas import (
    RequirementAnalysisResult,
    TestPointGenerationResult,
    TestCaseOut,
    TestCaseDesignResult,
    ApiTestCaseOut,
    ApiCallStepOut,
    ApiCallKeyValueOut,
    AssertionRuleOut,
    ApiTestCaseDesignResult,
)


def test_requirement_analysis_result_parses_points():
    result = RequirementAnalysisResult.model_validate({
        "requirement_points": [
            {"content": "登录功能", "module": "用户", "priority": "high", "order_index": 1},
            {"content": "登出功能"},  # other fields use defaults
        ]
    })
    assert len(result.requirement_points) == 2
    assert result.requirement_points[0].priority == "high"
    assert result.requirement_points[1].module == ""
    assert result.requirement_points[1].priority == "medium"


def test_requirement_analysis_result_empty_default():
    result = RequirementAnalysisResult.model_validate({})
    assert result.requirement_points == []


def test_test_point_generation_result_defaults():
    result = TestPointGenerationResult.model_validate({
        "test_points": [{"content": "校验空输入"}]
    })
    tp = result.test_points[0]
    assert tp.test_type == "functional"
    assert tp.priority == "medium"
    assert tp.design_method == ""
```

- [ ] **Step 2: Run to verify pass**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_pipeline_schemas.py -v`
Expected: 3 PASS.

- [ ] **Step 3: Add functional test-case design schema tests**

Append to `backend/tests/test_pipeline_schemas.py`:

```python
def test_testcase_out_parses_steps():
    tc = TestCaseOut.model_validate({
        "title": "登录成功用例",
        "test_steps": [
            {"step": 1, "action": "打开登录页", "expected": "显示表单"},
            {"step": 2, "action": "提交凭证", "expected": "登录成功"},
        ],
    })
    assert tc.preconditions == "无"   # default
    assert tc.description == ""
    assert len(tc.test_steps) == 2
    assert tc.test_steps[1].action == "提交凭证"


def test_testcase_design_result_collects_cases():
    result = TestCaseDesignResult.model_validate({
        "test_cases": [
            {"title": "用例A", "test_steps": []},
            {"title": "用例B", "test_steps": []},
        ]
    })
    assert [c.title for c in result.test_cases] == ["用例A", "用例B"]
```

- [ ] **Step 4: Add API test-case schema tests (including the `in` alias)**

Append to `backend/tests/test_pipeline_schemas.py`:

```python
def test_api_call_key_value_in_alias():
    # The "in" field is a Python keyword; schema aliases it to in_field.
    kv = ApiCallKeyValueOut.model_validate({"key": "userId", "value": "1", "in": "query"})
    assert kv.in_field == "query"
    # populate_by_name also allows the python name
    kv2 = ApiCallKeyValueOut.model_validate({"key": "x", "in_field": "path"})
    assert kv2.in_field == "path"


def test_api_call_step_out_full():
    step = ApiCallStepOut.model_validate({
        "endpoint_ref": 2,
        "description": "创建订单",
        "parameters": [{"key": "id", "value": "{{orderId}}", "in": "path"}],
        "assertions": [{"type": "status_code", "value": 200}],
    })
    assert step.endpoint_ref == 2
    assert step.parameters[0].in_field == "path"
    assert step.assertions[0].type == "status_code"


def test_api_testcase_out_defaults_and_refs():
    tc = ApiTestCaseOut.model_validate({
        "title": "下单流程",
        "test_steps": [{"endpoint_ref": 1}],
        "api_endpoint_ref": [1, 2],
    })
    assert tc.preconditions == "无"
    assert tc.test_steps[0].endpoint_ref == 1
    assert tc.api_endpoint_ref == [1, 2]


def test_assertion_rule_out_status_code_range():
    rule = AssertionRuleOut.model_validate({
        "type": "status_code_range", "min": 200, "max": 299
    })
    assert rule.min == 200 and rule.max == 299
    assert rule.value is None


def test_api_testcase_design_result_collects():
    result = ApiTestCaseDesignResult.model_validate({
        "test_cases": [{"title": "T", "test_steps": [{"endpoint_ref": 1}]}]
    })
    assert result.test_cases[0].test_steps[0].endpoint_ref == 1
```

- [ ] **Step 5: Run full schema module**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_pipeline_schemas.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_pipeline_schemas.py
git commit -m "test: cover pipeline structured-output schema parsing"
```

---

### Task 4: test_api_context — build_api_context / parse_endpoint_ids

**Files:**
- Create: `backend/tests/test_api_context.py`

**Interfaces:**
- Consumes (CURRENT, pre-Phase-1 locations): `app.routes.pipeline._parse_endpoint_ids`, `app.routes.pipeline._build_api_context`. These take `(session, api_endpoint_ids, api_project_id, endpoint_overrides)` and return `(api_context_str, endpoint_index_to_id)`.
- Produces: characterization of the API-context builder that Phase 1.2 will extract into `app/services/api_context.py`. After Phase 1, the import path in this file changes — the assertions stay identical.

**Note for Phase 1 executor:** when you extract `build_api_context`/`parse_endpoint_ids` into `app/services/api_context.py`, update the imports at the top of THIS file to the new module and re-run; the behavioral assertions must continue to pass unchanged.

- [ ] **Step 1: Write `_parse_endpoint_ids` tests**

Create `backend/tests/test_api_context.py`:

```python
"""Characterization tests for the API-context builder.

Phase 1.2 extracts these into app/services/api_context.py; the assertions
here must hold before and after that move. Uses in-memory DB, no LLM.
"""
from app.routes.pipeline import _parse_endpoint_ids, _build_api_context
from db.models import ApiProject, ApiEndpoint


def test_parse_endpoint_ids_variants():
    assert _parse_endpoint_ids(None) == []
    assert _parse_endpoint_ids("") == []
    assert _parse_endpoint_ids(5) == [5]
    assert _parse_endpoint_ids("1,2,3") == [1, 2, 3]
    assert _parse_endpoint_ids(" 1 , 2 ,bad, 3 ") == [1, 2, 3]  # bad parts skipped
    assert _parse_endpoint_ids("nope") == []
```

- [ ] **Step 2: Run to verify pass**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_api_context.py -v`
Expected: 1 PASS.

- [ ] **Step 3: Add a fixture-builder helper and empty-input test**

Append to `backend/tests/test_api_context.py`:

```python
def _make_project_with_endpoints(db_session):
    project = ApiProject(name="商城")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    ep1 = ApiEndpoint(
        project_id=project.id, name="创建用户", method="POST", path="/users",
        parameters=[{"key": "name", "value": "", "in": "query"}],
        body='{"name": "demo"}',
    )
    ep2 = ApiEndpoint(
        project_id=project.id, name="查询用户", method="GET", path="/users/{id}",
    )
    db_session.add(ep1)
    db_session.add(ep2)
    db_session.commit()
    db_session.refresh(ep1)
    db_session.refresh(ep2)
    return project, ep1, ep2


def test_build_api_context_empty_ids_returns_empty(db_session):
    ctx, index_map = _build_api_context(db_session, [], None, {})
    assert ctx == ""
    assert index_map == {}
```

- [ ] **Step 4: Add the main context-building test**

Append to `backend/tests/test_api_context.py`:

```python
def test_build_api_context_builds_sections_and_index_map(db_session):
    project, ep1, ep2 = _make_project_with_endpoints(db_session)

    ctx, index_map = _build_api_context(
        db_session, [ep1.id, ep2.id], project.id, {}
    )

    # index map is 1-based ordinal -> endpoint id
    assert index_map == {1: ep1.id, 2: ep2.id}
    # context contains the header and both endpoint sections with ordinals
    assert "API 接口信息" in ctx
    assert "[1] 创建用户" in ctx
    assert "[2] 查询用户" in ctx
    assert "请求方法: POST" in ctx
    assert "请求路径: /users/{id}" in ctx
    assert "所属项目: 商城" in ctx


def test_build_api_context_skips_missing_endpoint(db_session):
    project, ep1, _ = _make_project_with_endpoints(db_session)
    # 999999 does not exist -> skipped, only ep1 remains, re-indexed
    ctx, index_map = _build_api_context(
        db_session, [ep1.id, 999999], project.id, {}
    )
    assert index_map == {1: ep1.id}
    assert "[1] 创建用户" in ctx


def test_build_api_context_applies_overrides(db_session):
    project, ep1, _ = _make_project_with_endpoints(db_session)
    overrides = {ep1.id: {"body": '{"name": "overridden"}'}}
    ctx, _ = _build_api_context(db_session, [ep1.id], project.id, overrides)
    assert "overridden" in ctx
    assert "demo" not in ctx  # original body replaced
```

- [ ] **Step 5: Run full module**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_api_context.py -v`
Expected: all PASS. If `test_build_api_context_skips_missing_endpoint` fails on re-indexing, re-read `_build_api_context` (`app/routes/pipeline.py:65`) — note `endpoint_index_to_id[idx]` uses the loop ordinal `idx`, so a skipped (missing) endpoint still consumes an ordinal only if it passes the `session.get` check; the existing code increments `idx` via `enumerate` BEFORE the existence check, so a missing endpoint leaves a gap. Adjust the assertion to match actual behavior: if the code produces `{2: ep1.id}` when the missing one is first, assert that instead. **Read the code and assert what it actually does.**

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_api_context.py
git commit -m "test: characterize API context builder and endpoint id parsing"
```

---

### Task 5: test_dependency_infer — infer_endpoint_dependencies

**Files:**
- Create: `backend/tests/test_dependency_infer.py`

**Interfaces:**
- Consumes: `app.routes.testcase.infer_endpoint_dependencies(session, selected_endpoint_ids, api_project_id) -> list[int]`; `db.models.ApiProject`, `db.models.ApiEndpoint`.
- Produces: characterization of the dependency-inference rules. This function is referenced by Phase 1/2 work and is the most logic-dense pure-ish function in the routes layer.

- [ ] **Step 1: Write the no-op / guard tests**

Create `backend/tests/test_dependency_infer.py`:

```python
"""Characterization tests for endpoint dependency inference.

infer_endpoint_dependencies auto-completes a missing prerequisite "create"
(POST) endpoint when a selected endpoint depends on an id it cannot produce.
Uses in-memory DB, no LLM.
"""
from app.routes.testcase import infer_endpoint_dependencies
from db.models import ApiProject, ApiEndpoint


def _project(db_session):
    p = ApiProject(name="proj")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _add_endpoint(db_session, project_id, **kwargs):
    ep = ApiEndpoint(project_id=project_id, **kwargs)
    db_session.add(ep)
    db_session.commit()
    db_session.refresh(ep)
    return ep


def test_returns_empty_without_project(db_session):
    assert infer_endpoint_dependencies(db_session, [1, 2], None) == []


def test_returns_empty_without_selected_ids(db_session):
    p = _project(db_session)
    assert infer_endpoint_dependencies(db_session, [], p.id) == []


def test_returns_empty_when_no_endpoints(db_session):
    p = _project(db_session)
    assert infer_endpoint_dependencies(db_session, [123], p.id) == []
```

- [ ] **Step 2: Run to verify pass**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_dependency_infer.py -v`
Expected: 3 PASS.

- [ ] **Step 3: Add the positive-inference test (path id dependency)**

Append to `backend/tests/test_dependency_infer.py`:

```python
def test_infers_missing_create_endpoint_for_path_id_dependency(db_session):
    p = _project(db_session)
    # A POST create endpoint (NOT selected) that the dependency could come from
    create_ep = _add_endpoint(
        db_session, p.id, name="创建订单", method="POST", path="/orders",
    )
    # A selected GET endpoint whose path needs {id} -> depends on a create
    get_ep = _add_endpoint(
        db_session, p.id, name="查询订单", method="GET", path="/orders/{id}",
    )

    result = infer_endpoint_dependencies(db_session, [get_ep.id], p.id)
    assert create_ep.id in result
```

- [ ] **Step 4: Add the "already has create" suppression test**

Append to `backend/tests/test_dependency_infer.py`:

```python
def test_no_inference_when_create_already_selected(db_session):
    p = _project(db_session)
    create_ep = _add_endpoint(
        db_session, p.id, name="创建订单", method="POST", path="/orders",
    )
    get_ep = _add_endpoint(
        db_session, p.id, name="查询订单", method="GET", path="/orders/{id}",
    )
    # Both selected -> create already present -> nothing to infer
    result = infer_endpoint_dependencies(
        db_session, [create_ep.id, get_ep.id], p.id
    )
    assert result == []


def test_no_inference_when_no_candidate_create_exists(db_session):
    p = _project(db_session)
    get_ep = _add_endpoint(
        db_session, p.id, name="查询订单", method="GET", path="/orders/{id}",
    )
    # No POST create endpoint at all
    result = infer_endpoint_dependencies(db_session, [get_ep.id], p.id)
    assert result == []
```

- [ ] **Step 5: Add the string-project-id coercion test**

Append to `backend/tests/test_dependency_infer.py`:

```python
def test_accepts_string_project_id(db_session):
    p = _project(db_session)
    create_ep = _add_endpoint(
        db_session, p.id, name="创建订单", method="POST", path="/orders",
    )
    get_ep = _add_endpoint(
        db_session, p.id, name="查询订单", method="GET", path="/orders/{id}",
    )
    # project id passed as a string should still resolve (int() coercion)
    result = infer_endpoint_dependencies(db_session, [get_ep.id], str(p.id))
    assert create_ep.id in result


def test_invalid_string_project_id_returns_empty(db_session):
    assert infer_endpoint_dependencies(db_session, [1], "not-a-number") == []
```

- [ ] **Step 6: Run full module**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_dependency_infer.py -v`
Expected: all PASS. If a positive case fails, re-read `infer_endpoint_dependencies` (`app/routes/testcase.py:21`) and the helpers `_best_matching_create_endpoint` / `_get_response_top_fields` to confirm the rule, then adjust the test data (e.g. ensure the create endpoint's response schema does not already expose `id`, which would suppress inference) — keep the test as accurate characterization.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/test_dependency_infer.py
git commit -m "test: characterize endpoint dependency inference rules"
```

---

### Task 6: Full-suite green gate

**Files:** none (verification only)

- [ ] **Step 1: Run the entire suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -v`
Expected: every test PASS (smoke + helpers + schemas + api_context + dependency_infer). Record the total count.

- [ ] **Step 2: Capture a baseline run for cross-phase comparison**

Run and save output:

```powershell
cd backend; .\.venv\Scripts\python.exe -m pytest -q | Tee-Object -FilePath ..\docs\superpowers\plans\phase0-baseline.txt
```

This baseline is the "behavior before" reference. Phases 1 and 2 must reproduce the same passing set (after updating import paths in `test_api_context.py` per Task 4's note).

- [ ] **Step 3: Commit the baseline**

```bash
git add docs/superpowers/plans/phase0-baseline.txt
git commit -m "test: record phase 0 green baseline"
```

---

## Self-Review

**Spec coverage** (spec §阶段 0): conftest in-memory SQLite ✓ (Task 1); `test_helpers.py` JSON repair + `_normalize_testcase_dict` + level normalization ✓ (Task 2); `test_pipeline_schemas.py` ApiTestCaseOut/TestCaseOut parse+validate ✓ (Task 3); `test_api_context.py` build_api_context/parse_endpoint_ids ✓ (Task 4); `test_dependency_infer.py` infer_endpoint_dependencies with in-memory DB ✓ (Task 5); pytest added to requirements ✓ (Task 1 Step 4); run command in CLAUDE.md ✓ (Task 1 Step 7); only pure/near-pure functions, no real LLM/DB ✓ (Global Constraints + in-memory fixture).

**Placeholder scan:** every code step contains runnable code; every run step has an exact command and expected outcome. No TBD/TODO.

**Type consistency:** `db_session` fixture name is consistent across Tasks 1–5. `_build_api_context` signature `(session, api_endpoint_ids, api_project_id, endpoint_overrides)` matches `app/routes/pipeline.py:65`. `infer_endpoint_dependencies(session, selected_endpoint_ids, api_project_id)` matches `app/routes/testcase.py:21`. `ApiEndpoint`/`ApiProject` field names (`project_id`, `method`, `path`, `parameters`, `body`, `response_schema`) match `db/models.py`.

**Known caveat surfaced to executor:** Task 4 Step 5 and Task 5 Step 6 explicitly instruct the executor to read the production function and assert its *actual* behavior if the drafted expectation disagrees — these are characterization tests, so the production code is the source of truth, not the draft assertion.
