# Phase 1: Backend Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the duplicated backend infrastructure — 7+ `ChatOpenAI` constructions, two copies of API-context building, two MCP-config loaders, and two document-prefetch implementations — by extracting shared modules and replacing call sites, without changing behavior.

**Architecture:** Four new shared modules — `utils/llm_factory.py` (model construction + priority normalization), `app/services/api_context.py` (endpoint-context builder + id parsing), `app/services/mcp_loader.py` (user MCP-config loading), `utils/document_prefetch.py` (Feishu/Lanhu prefetch + URL regexes). Each existing duplicate is replaced with a call to the shared function. Behavior is held constant by the Phase 0 test suite plus import-smoke checks.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, LangChain (`langchain_openai.ChatOpenAI`), Pydantic v2, httpx.

## Global Constraints

- **Prerequisite:** Phase 0 (`backend/tests/`) must be merged and green. After each task, run `cd backend && pytest` and it must stay green.
- **Behavior-preserving refactor:** extract/move code without changing logic. The one intentional behavior change is unifying the `extra_body` writing convention (see Task 1) — call it out in the commit message.
- Every new module must import cleanly: after creating it, run `cd backend; .\.venv\Scripts\python.exe -c "import <module>"`.
- After replacing a call site, run the import-smoke for the touched route/pipeline module (commands given per task).
- Keep the `max_tokens` semantics each call site currently uses: pipeline agents use `max_tokens=None`; `model_utils` sites use `max_tokens=16384`. The factory must accept `max_tokens` as a parameter so both are preserved exactly (per CLAUDE.md: omitting `max_tokens` lets DeepSeek default to 8192 and truncate — do not change the 16384 sites to None or vice versa).
- Do not change public function names that other modules import. Where a function moves modules, re-export or update all importers in the same task.
- Python imports in this repo are rooted at `backend/` (run commands from `backend/`).

---

### Task 1: LLM model factory (`utils/llm_factory.py`)

**Files:**
- Create: `backend/utils/llm_factory.py`
- Test: `backend/tests/test_llm_factory.py`

**Interfaces:**
- Produces:
  - `build_chat_model(api_key: str, api_base_url: str | None = None, api_model: str | None = None, api_proxy_url: str | None = None, *, max_tokens: int | None = None, temperature: float = 0, max_retries: int = 2, timeout=None, **overrides) -> ChatOpenAI`
  - `normalize_priority(priority: str | None) -> str` (the shared `_normalize_priority` logic: maps P0/HIGH/H→high, P1/MEDIUM/M→medium, P2/LOW/L→low, default medium)
- Consumes: `langchain_openai.ChatOpenAI`, `pydantic.SecretStr`, `httpx`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_llm_factory.py`:

```python
"""Tests for the unified ChatOpenAI factory and priority normalization."""
from utils.llm_factory import build_chat_model, normalize_priority


def test_build_chat_model_basic_attributes():
    model = build_chat_model("sk-test", api_model="deepseek-v4-flash")
    assert model.model_name == "deepseek-v4-flash"
    assert model.temperature == 0
    # base_url falls back to deepseek default when not provided
    assert "deepseek.com" in str(model.openai_api_base)


def test_build_chat_model_passes_max_tokens():
    model = build_chat_model("sk-test", api_model="m", max_tokens=16384)
    assert model.max_tokens == 16384


def test_build_chat_model_thinking_disabled_in_extra_body():
    model = build_chat_model("sk-test", api_model="m")
    # extra_body carries the thinking-disabled flag uniformly
    extra = getattr(model, "extra_body", None) or {}
    assert extra.get("thinking") == {"type": "disabled"}


def test_normalize_priority_mapping():
    assert normalize_priority("P0") == "high"
    assert normalize_priority("HIGH") == "high"
    assert normalize_priority("p1") == "medium"
    assert normalize_priority("P2") == "low"
    assert normalize_priority("") == "medium"
    assert normalize_priority(None) == "medium"
    assert normalize_priority("unknown") == "medium"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_llm_factory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.llm_factory'`.

- [ ] **Step 3: Write the factory implementation**

Create `backend/utils/llm_factory.py`:

```python
"""Unified construction of ChatOpenAI models and shared priority normalization.

Replaces 7+ duplicated ChatOpenAI() blocks across the pipeline agents and
model_utils. The `thinking: disabled` flag is written uniformly via
top-level `extra_body` (langchain_openai forwards it to the request body),
replacing the inconsistent mix of `extra_body=...` and
`model_kwargs={"extra_body": ...}` that previously existed.
"""
from typing import Optional

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_THINKING_DISABLED = {"thinking": {"type": "disabled"}}

_PRIORITY_MAP = {
    "P0": "high", "HIGH": "high", "H": "high",
    "P1": "medium", "MEDIUM": "medium", "M": "medium",
    "P2": "low", "LOW": "low", "L": "low",
}


def build_chat_model(
    api_key: str,
    api_base_url: Optional[str] = None,
    api_model: Optional[str] = None,
    api_proxy_url: Optional[str] = None,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0,
    max_retries: int = 2,
    timeout=None,
    **overrides,
) -> ChatOpenAI:
    """Construct a ChatOpenAI for an OpenAI-compatible API (DeepSeek default).

    `max_tokens=None` lets the server apply its default; pass an explicit
    value (e.g. 16384) when long output must not be truncated.
    Extra keyword args are forwarded to ChatOpenAI unchanged.
    """
    extra_kwargs = dict(overrides)
    if api_proxy_url:
        import httpx
        extra_kwargs.setdefault(
            "http_async_client",
            httpx.AsyncClient(
                proxy=api_proxy_url,
                timeout=httpx.Timeout(None, connect=30.0, read=None, write=None, pool=None),
            ),
        )

    return ChatOpenAI(
        model=(api_model or "deepseek-v4-flash"),
        temperature=temperature,
        api_key=SecretStr(api_key),
        base_url=(api_base_url or _DEFAULT_BASE_URL),
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        extra_body=dict(_THINKING_DISABLED),
        **extra_kwargs,
    )


def normalize_priority(priority: Optional[str]) -> str:
    """Normalize a priority token to one of high/medium/low (default medium)."""
    if not priority:
        return "medium"
    return _PRIORITY_MAP.get(priority.upper(), "medium")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_llm_factory.py -v`
Expected: all PASS. If `test_build_chat_model_thinking_disabled_in_extra_body` fails because `extra_body` is stored under a different attribute, inspect with `python -c "from utils.llm_factory import build_chat_model; m=build_chat_model('k',api_model='x'); print(m.model_dump().keys())"` and adjust the test to read the real attribute (the goal is to prove the flag is set, not its storage location).

- [ ] **Step 5: Commit**

```bash
git add backend/utils/llm_factory.py backend/tests/test_llm_factory.py
git commit -m "feat: add unified ChatOpenAI factory and priority normalization"
```

---

### Task 2: Adopt factory in the 4 pipeline agents

**Files:**
- Modify: `backend/app/pipeline/requirement_splitter.py:21-46` (the `__init__`) and `:86-95` (`_normalize_priority`)
- Modify: `backend/app/pipeline/test_point_generator.py:20-45` (`__init__`) and `:87-96` (`_normalize_priority`)
- Modify: `backend/app/pipeline/test_case_designer.py:24-49` (`__init__`)
- Modify: `backend/app/pipeline/test_case_optimizer.py:22-47` (`__init__`)

**Interfaces:**
- Consumes: `utils.llm_factory.build_chat_model`, `utils.llm_factory.normalize_priority`.
- Produces: unchanged public methods (`analyze`, `generate`, `design_batch`, `optimize`) and unchanged `self.model` attribute.

- [ ] **Step 1: Replace `RequirementSplitter.__init__` body**

In `backend/app/pipeline/requirement_splitter.py`, replace the imports and `__init__` (lines 8-46) so the constructor uses the factory. Replace this block:

```python
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.pipeline.prompts import REQUIREMENT_SPLITTER_SYSTEM, REQUIREMENT_ANALYSIS_USER
from app.pipeline.schemas import RequirementAnalysisResult
```

with:

```python
from app.pipeline.prompts import REQUIREMENT_SPLITTER_SYSTEM, REQUIREMENT_ANALYSIS_USER
from app.pipeline.schemas import RequirementAnalysisResult
from utils.llm_factory import build_chat_model, normalize_priority
```

Then replace the `__init__` method (currently lines 21-46) with:

```python
    def __init__(
        self,
        api_key: str,
        api_base_url: str = "https://api.deepseek.com",
        api_model: str = "deepseek-v4-flash",
        api_proxy_url: Optional[str] = None,
    ):
        self.model = build_chat_model(
            api_key, api_base_url, api_model, api_proxy_url, max_tokens=None
        )
```

- [ ] **Step 2: Replace `RequirementSplitter._normalize_priority` usage**

In the same file, the `analyze` method calls `self._normalize_priority(...)` at line 81. Change that line to use the shared function:

```python
            p["priority"] = normalize_priority(p.get("priority", "medium"))
```

Then delete the entire `_normalize_priority` static method (lines 86-95).

- [ ] **Step 3: Verify the splitter imports and instantiates**

Run: `cd backend; .\.venv\Scripts\python.exe -c "from app.pipeline.requirement_splitter import RequirementSplitter; s=RequirementSplitter('sk-test'); print(s.model.model_name)"`
Expected: prints `deepseek-v4-flash`, no error.

- [ ] **Step 4: Apply the same change to `TestPointGenerator`**

In `backend/app/pipeline/test_point_generator.py`: replace the `ChatOpenAI`/`SecretStr` imports with `from utils.llm_factory import build_chat_model, normalize_priority` (keep the existing prompt/schema imports). Replace `__init__` (lines 20-45) with:

```python
    def __init__(
        self,
        api_key: str,
        api_base_url: str = "https://api.deepseek.com",
        api_model: str = "deepseek-v4-flash",
        api_proxy_url: Optional[str] = None,
    ):
        self.model = build_chat_model(
            api_key, api_base_url, api_model, api_proxy_url, max_tokens=None
        )
```

Change the priority line (currently line 82) to:

```python
            p["priority"] = normalize_priority(p.get("priority", "medium"))
```

Delete the `_normalize_priority` static method (lines 87-96).

- [ ] **Step 5: Apply to `TestCaseDesigner` and `TestCaseOptimizer` (constructor only)**

In `backend/app/pipeline/test_case_designer.py`: remove the `from langchain_openai import ChatOpenAI` and `from pydantic import SecretStr` lines, add `from utils.llm_factory import build_chat_model`, and replace `__init__` (lines 24-49) with:

```python
    def __init__(
        self,
        api_key: str,
        api_base_url: str = "https://api.deepseek.com",
        api_model: str = "deepseek-v4-flash",
        api_proxy_url: Optional[str] = None,
    ):
        self.model = build_chat_model(
            api_key, api_base_url, api_model, api_proxy_url, max_tokens=None
        )
```

In `backend/app/pipeline/test_case_optimizer.py`: same import swap, replace `__init__` (lines 22-47) with the identical body as above. (These two classes do not define `_normalize_priority`, so no priority change here.)

- [ ] **Step 6: Verify all four agents import**

Run: `cd backend; .\.venv\Scripts\python.exe -c "from app.pipeline.pipeline_runner import PipelineRunner; r=PipelineRunner.__init__; print('ok')"`
Then a deeper check:
```powershell
cd backend; .\.venv\Scripts\python.exe -c "from app.pipeline.requirement_splitter import RequirementSplitter; from app.pipeline.test_point_generator import TestPointGenerator; from app.pipeline.test_case_designer import TestCaseDesigner; from app.pipeline.test_case_optimizer import TestCaseOptimizer; [C('sk').model for C in (RequirementSplitter,TestPointGenerator,TestCaseDesigner,TestCaseOptimizer)]; print('all four ok')"
```
Expected: `all four ok`.

- [ ] **Step 7: Run the Phase 0 suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q`
Expected: same green set as the Phase 0 baseline.

- [ ] **Step 8: Commit**

```bash
git add backend/app/pipeline/requirement_splitter.py backend/app/pipeline/test_point_generator.py backend/app/pipeline/test_case_designer.py backend/app/pipeline/test_case_optimizer.py
git commit -m "refactor: use llm_factory in 4 pipeline agents, unify extra_body convention"
```

---

### Task 3: Adopt factory in model_utils (3 sites)

**Files:**
- Modify: `backend/utils/model_utils.py` — `create_testcase_agent` block (~608-627), and the two direct-call blocks (~822-839 and ~885-900).

**Interfaces:**
- Consumes: `utils.llm_factory.build_chat_model`.
- Produces: unchanged behavior — these three sites use `max_tokens=16384` and (for the proxy case) the same `http_async_client`. The factory's `api_proxy_url` handling reproduces the existing `extra_kwargs`/`direct_extra_kwargs` proxy construction.

- [ ] **Step 1: Add the factory import**

In `backend/utils/model_utils.py`, near the existing imports (the file imports `ChatOpenAI` and `SecretStr` already), add:

```python
from utils.llm_factory import build_chat_model
```

(Leave the existing `ChatOpenAI`/`SecretStr` imports in place for now — other code in the file may still reference them; removing them is out of scope and risks breakage.)

- [ ] **Step 2: Replace the `create_testcase_agent` ChatOpenAI block**

In `create_testcase_agent`, replace lines 608-627 (the `extra_kwargs = {}` proxy block plus the `model = ChatOpenAI(...)` call) with:

```python
        model = build_chat_model(
            api_key, api_base_url, api_model, api_proxy_url, max_tokens=16384
        )
```

(The factory builds the proxy `http_async_client` internally from `api_proxy_url`, replacing the deleted `extra_kwargs` block.)

- [ ] **Step 3: Replace the parsed-content direct-call block**

Replace lines 822-839 (the `direct_extra_kwargs = {}` proxy block plus `model = ChatOpenAI(...)`) with:

```python
                model = build_chat_model(
                    api_key, api_base_url, api_model, api_proxy_url, max_tokens=16384
                )
```

- [ ] **Step 4: Replace the no-parsed-content direct-call block**

Replace lines 885-900 (the second `direct_extra_kwargs` proxy block plus `model = ChatOpenAI(...)`) with:

```python
                    model = build_chat_model(
                        api_key, api_base_url, api_model, api_proxy_url, max_tokens=16384
                    )
```

Note indentation: this block is nested one level deeper (inside the `else` branch). Match the surrounding indentation exactly.

- [ ] **Step 5: Verify model_utils imports**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import utils.model_utils; print('ok')"`
Expected: `ok`. If a `direct_extra_kwargs` / `extra_kwargs` reference remains dangling (NameError on import is unlikely, but a later reference would break at runtime), grep for them: `grep -n "extra_kwargs" backend/utils/model_utils.py` and confirm none of the deleted variables are still referenced. Any remaining reference must be removed.

- [ ] **Step 6: Run the Phase 0 suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add backend/utils/model_utils.py
git commit -m "refactor: use llm_factory at 3 model_utils ChatOpenAI sites"
```

---

### Task 4: API context service (`app/services/api_context.py`)

**Files:**
- Create: `backend/app/services/api_context.py`
- Modify: `backend/app/routes/pipeline.py:48-123` (remove local `_parse_endpoint_ids` + `_build_api_context`, import from service)
- Modify: `backend/app/routes/testcase.py:559-641` (replace the inline api-context construction with a call to the service)
- Modify: `backend/tests/test_api_context.py` (update import path)

**Interfaces:**
- Produces:
  - `parse_endpoint_ids(value: str | int | None) -> list[int]`
  - `build_api_context(session, endpoint_ids: list[int], project_id: int | None, overrides: dict[int, dict], dependency_labels: dict[int, str] | None = None) -> tuple[str, dict[int, int]]`
- Consumes: `db.models.ApiEndpoint`, `db.models.ApiProject`.

**Design note:** the `testcase.py` copy adds a `[依赖补全]` label to the section header for dependency-completed endpoints. The unified `build_api_context` supports this via the optional `dependency_labels` param (maps endpoint_id → label string). The `pipeline.py` caller passes nothing (defaults to no labels), reproducing its current output exactly.

- [ ] **Step 1: Create the service module**

Create `backend/app/services/api_context.py` by moving the logic from `app/routes/pipeline.py:48-123`, generalized with `dependency_labels`:

```python
"""Shared API-context builder for test-case generation.

Builds the human-readable API-endpoint information block injected into the
generation prompt, plus the ordinal->endpoint-id map. Extracted from
duplicate copies in routes/pipeline.py and routes/testcase.py.
"""
import json
import logging

from db.models import ApiEndpoint, ApiProject

logger = logging.getLogger(__name__)


def parse_endpoint_ids(value: "str | int | None") -> list[int]:
    """Parse a comma-separated endpoint-id string (or int) into a list of ints."""
    if not value:
        return []
    if isinstance(value, int):
        return [value]
    ids: list[int] = []
    for part in str(value).split(","):
        part = part.strip()
        if part:
            try:
                ids.append(int(part))
            except ValueError:
                pass
    return ids


def build_api_context(
    session,
    endpoint_ids: list[int],
    project_id: "int | None",
    overrides: dict,
    dependency_labels: "dict[int, str] | None" = None,
) -> "tuple[str, dict[int, int]]":
    """Build the API info text and the ordinal->endpoint-id map.

    `dependency_labels` optionally tags an endpoint's header with a label
    (e.g. {"依赖补全"}) for endpoints auto-added by dependency inference.
    """
    if not endpoint_ids:
        return "", {}

    dependency_labels = dependency_labels or {}
    endpoint_index_to_id: dict[int, int] = {}
    api_sections: list[str] = []

    for idx, eid in enumerate(endpoint_ids, 1):
        try:
            endpoint_db = session.get(ApiEndpoint, eid)
            if not endpoint_db:
                continue
            endpoint_index_to_id[idx] = eid
            project_db = session.get(ApiProject, project_id or endpoint_db.project_id)
            ep_lines = []
            dep_label = f" [{dependency_labels[eid]}]" if eid in dependency_labels else ""
            ep_lines.append(f'===== [{idx}] {endpoint_db.name}{dep_label} =====')
            ep_lines.append('所属项目: ' + (project_db.name if project_db else '未知'))
            ep_lines.append('请求方法: ' + endpoint_db.method)
            ep_lines.append('请求路径: ' + endpoint_db.path)
            if endpoint_db.tags:
                ep_lines.append('标签: ' + ', '.join(endpoint_db.tags))
            if endpoint_db.assertions:
                ep_lines.append('默认断言: ' + json.dumps(endpoint_db.assertions, ensure_ascii=False, indent=2))
            override = overrides.get(eid, {})
            ep_headers = override.get("headers") if override.get("headers") is not None else endpoint_db.headers
            ep_parameters = override.get("parameters") if override.get("parameters") is not None else endpoint_db.parameters
            ep_body = override.get("body") if override.get("body") is not None else endpoint_db.body
            if ep_headers:
                ep_lines.append('请求头: ' + json.dumps(ep_headers, ensure_ascii=False, indent=2))
            if ep_parameters:
                ep_lines.append('请求参数: ' + json.dumps(ep_parameters, ensure_ascii=False, indent=2))
            if endpoint_db.request_schema:
                ep_lines.append('请求 Schema:\n' + json.dumps(endpoint_db.request_schema, ensure_ascii=False, indent=2))
            if endpoint_db.response_schema:
                ep_lines.append('响应 Schema:\n' + json.dumps(endpoint_db.response_schema, ensure_ascii=False, indent=2))
            if ep_body:
                ep_lines.append('请求体示例:\n' + ep_body)
            if endpoint_db.pre_actions:
                ep_lines.append('前置操作: ' + json.dumps(endpoint_db.pre_actions, ensure_ascii=False, indent=2))
            if endpoint_db.post_actions:
                ep_lines.append('后置提取: ' + json.dumps(endpoint_db.post_actions, ensure_ascii=False, indent=2))
            api_sections.append('\n'.join(ep_lines))
            logger.info('已加载 API 端点: ' + endpoint_db.name + ' (' + endpoint_db.method + ' ' + endpoint_db.path + ')')
        except Exception as e:
            logger.warning('加载端点 ' + str(eid) + ' 失败: ' + str(e))

    if not api_sections:
        return "", {}

    all_api_info = '\n\n'.join(api_sections)
    api_context = '\n\n===== 以下 API 接口信息（每个接口前的[数字]为接口编号）=====\n' + all_api_info
    return api_context, endpoint_index_to_id
```

- [ ] **Step 2: Verify the service imports**

Run: `cd backend; .\.venv\Scripts\python.exe -c "from app.services.api_context import build_api_context, parse_endpoint_ids; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Update test_api_context.py to import from the service**

In `backend/tests/test_api_context.py`, change the import line from:

```python
from app.routes.pipeline import _parse_endpoint_ids, _build_api_context
```

to:

```python
from app.services.api_context import parse_endpoint_ids as _parse_endpoint_ids, build_api_context as _build_api_context
```

(Aliasing keeps the existing test bodies unchanged. The `_build_api_context` calls in tests pass 4 positional args; the new signature's 5th `dependency_labels` defaults to None, so they remain valid.)

- [ ] **Step 4: Run the api-context tests against the service**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_api_context.py -v`
Expected: all PASS (same as Phase 0).

- [ ] **Step 5: Replace the copy in pipeline.py**

In `backend/app/routes/pipeline.py`:
- Delete the local `_parse_endpoint_ids` (lines 48-62) and `_build_api_context` (lines 65-123).
- Add to the imports near the top: `from app.services.api_context import build_api_context, parse_endpoint_ids`
- Update the two call sites: `_parse_endpoint_ids(api_endpoint_id)` (line 177) → `parse_endpoint_ids(api_endpoint_id)`; and `_build_api_context(...)` (line 201) → `build_api_context(...)` with the same arguments.
- Remove now-unused imports if `ApiEndpoint`/`ApiProject`/`json` are no longer referenced elsewhere in the file — check with grep before deleting (line 16 imports them). If still used, leave them.

- [ ] **Step 6: Verify pipeline route imports**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.routes.pipeline; print('ok')"`
Expected: `ok`.

- [ ] **Step 7: Replace the copy in testcase.py**

In `backend/app/routes/testcase.py`, the inline construction spans lines 559-641 (the `api_context = ""` init, the `api_endpoint_ids` parse loop at 560-569, then the `endpoint_index_to_id` building loop at 595-641). Replace as follows:
- Add import: `from app.services.api_context import build_api_context, parse_endpoint_ids`
- Replace the manual parse loop (lines 561-569) with: `api_endpoint_ids = parse_endpoint_ids(api_endpoint_id)`
- **Keep** the dependency-inference block (lines 571-581) and the `endpoint_overrides` parse block (lines 583-593) exactly as-is.
- Replace the entire `endpoint_index_to_id`-building block (lines 595-641) with:

```python
    api_context, endpoint_index_to_id = build_api_context(
        session, api_endpoint_ids, api_project_id_int_or_none(api_project_id),
        endpoint_overrides, dependency_labels=dependency_labels,
    )
```

Wait — `api_project_id` is a raw string here and `api_project_id_int` is computed later (line 650). To avoid a forward reference, pass the raw value and let `build_api_context` coerce: the service does `project_id or endpoint_db.project_id` and `session.get(ApiProject, project_id...)`. Passing a string project_id to `session.get` works for SQLite (it coerces), but to match the old `int(api_project_id)` behavior precisely, compute the int first. Insert before the call:

```python
    _proj_id_int = None
    if api_project_id:
        try:
            _proj_id_int = int(api_project_id)
        except (ValueError, TypeError):
            _proj_id_int = None
    api_context, endpoint_index_to_id = build_api_context(
        session, api_endpoint_ids, _proj_id_int, endpoint_overrides,
        dependency_labels=dependency_labels,
    )
```

Leave the subsequent `if api_context:` requirement-assembly block (lines 643-646) unchanged.

- [ ] **Step 8: Verify testcase route imports and the helpers are intact**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.routes.testcase; print('ok')"`
Then grep to confirm no orphaned references: `grep -n "api_sections\|ep_lines" backend/app/routes/testcase.py` — expected: no matches (all moved to the service).
Expected: `ok` and no orphan matches.

- [ ] **Step 9: Run the full suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q`
Expected: green.

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/api_context.py backend/app/routes/pipeline.py backend/app/routes/testcase.py backend/tests/test_api_context.py
git commit -m "refactor: extract shared api_context service, dedupe pipeline+testcase copies"
```

---

### Task 5: MCP config loader (`app/services/mcp_loader.py`)

**Files:**
- Create: `backend/app/services/mcp_loader.py`
- Modify: `backend/app/routes/pipeline.py:161-173` (the MCP-loading block)
- Modify: `backend/app/routes/testcase.py:527-546` (the MCP-loading block)

**Interfaces:**
- Produces: `load_user_mcp_configs(session, user) -> list[dict]` — encapsulates the permission check, the `McpServer` query filtered by `user_id` + `enabled`, and the `model_dump(exclude=...)`.
- Consumes: `db.models.McpServer`, `app.permissions.Permission`, `app.permissions.get_user_permissions`.

- [ ] **Step 1: Create the loader module**

Create `backend/app/services/mcp_loader.py`:

```python
"""Shared loader for a user's enabled, server-persisted MCP configs.

Centralizes the permission check + query + model_dump duplicated in
routes/testcase.py and routes/pipeline.py. Returns [] when the user lacks
mcp:manage permission.
"""
import logging

from sqlmodel import select

from app.permissions import Permission, get_user_permissions
from db.models import McpServer

logger = logging.getLogger(__name__)

_EXCLUDE_FIELDS = {"id", "created_at", "updated_at", "user_id"}


def load_user_mcp_configs(session, user) -> list[dict]:
    """Return the current user's enabled MCP server configs as dicts.

    Only server-persisted configs are read (client-supplied configs are
    ignored elsewhere to preserve /api/mcp permission control). Returns an
    empty list when the user lacks the MCP_MANAGE permission.
    """
    if Permission.MCP_MANAGE not in get_user_permissions(user):
        logger.info("用户无 mcp:manage 权限，不加载自定义 MCP 配置")
        return []

    servers = session.exec(
        select(McpServer).where(
            McpServer.user_id == user.user_id,
            McpServer.enabled == True,  # noqa: E712 (SQLModel needs ==)
        )
    ).all()
    configs = [s.model_dump(exclude=_EXCLUDE_FIELDS) for s in servers]
    logger.info(f"已加载 {len(configs)} 个服务端 MCP 服务器配置")
    return configs
```

- [ ] **Step 2: Verify it imports**

Run: `cd backend; .\.venv\Scripts\python.exe -c "from app.services.mcp_loader import load_user_mcp_configs; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Replace the loader in pipeline.py**

In `backend/app/routes/pipeline.py`, replace the block at lines 161-173:

```python
    # 加载 MCP 服务器配置（用于飞书/蓝湖文档预取）
    mcp_configs = []
    if Permission.MCP_MANAGE in get_user_permissions(user):
        mcp_servers_db = session.exec(
            select(McpServer).where(
                McpServer.user_id == user.user_id,
                McpServer.enabled == True,
            )
        ).all()
        mcp_configs = [
            server.model_dump(exclude={"id", "created_at", "updated_at", "user_id"})
            for server in mcp_servers_db
        ]
        logger.info(f"Pipeline: 已加载 {len(mcp_configs)} 个 MCP 服务器配置")
```

with:

```python
    # 加载 MCP 服务器配置（用于飞书/蓝湖文档预取）
    from app.services.mcp_loader import load_user_mcp_configs
    mcp_configs = load_user_mcp_configs(session, user)
```

(Use a local import to avoid touching the module's top import list; or add it to the top imports — either is fine. If `McpServer` / `Permission` / `get_user_permissions` / `select` become unused in this file afterward, leave them unless grep confirms zero other uses.)

- [ ] **Step 4: Replace the loader in testcase.py**

In `backend/app/routes/testcase.py`, replace the block at lines 527-546 (the comment through the `else: logger.info(...)`):

```python
    # MCP 服务器配置只允许从服务端持久化配置读取，避免生成接口绕过 /api/mcp 的权限控制。
    import json
    mcp_configs = []
    if mcp_servers and mcp_servers.strip():
        logger.info("忽略客户端传入的 MCP 服务器配置，改用服务端已保存配置")

    if Permission.MCP_MANAGE in get_user_permissions(user):
        mcp_servers_db = session.exec(
            select(McpServer).where(
                McpServer.user_id == user.user_id,
                McpServer.enabled == True,
            )
        ).all()
        mcp_configs = [
            server.model_dump(exclude={"id", "created_at", "updated_at", "user_id"})
            for server in mcp_servers_db
        ]
        logger.info(f"已加载 {len(mcp_configs)} 个服务端 MCP 服务器配置")
    else:
        logger.info("当前用户无 mcp:manage 权限，生成用例时不加载自定义 MCP 工具")
```

with:

```python
    # MCP 服务器配置只允许从服务端持久化配置读取，避免生成接口绕过 /api/mcp 的权限控制。
    import json
    from app.services.mcp_loader import load_user_mcp_configs
    if mcp_servers and mcp_servers.strip():
        logger.info("忽略客户端传入的 MCP 服务器配置，改用服务端已保存配置")
    mcp_configs = load_user_mcp_configs(session, user)
```

(Keep the `import json` line — it is used later in this function for parsing skills/overrides.)

- [ ] **Step 5: Verify both routes import**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import app.routes.pipeline, app.routes.testcase; print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Run the full suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/mcp_loader.py backend/app/routes/pipeline.py backend/app/routes/testcase.py
git commit -m "refactor: extract shared MCP config loader, dedupe two route copies"
```

---

### Task 6: Document prefetch (`utils/document_prefetch.py`)

**Files:**
- Create: `backend/utils/document_prefetch.py`
- Modify: `backend/utils/model_utils.py` — `_LANHU_URL_RE`/`_FEISHU_URL_RE` (lines 148-155) and `_fetch_feishu_document_content` (lines 302-359)
- Modify: `backend/app/pipeline/pipeline_runner.py` — `_LANHU_URL_RE`/`_FEISHU_URL_RE` (lines 24-31) and `_prefetch_document` (lines 187-284)

**Interfaces:**
- Produces:
  - `LANHU_URL_RE`, `FEISHU_URL_RE` (compiled regexes, single source of truth)
  - `async def fetch_feishu_document(text: str, mcp_configs: list | None) -> str | None` — Feishu wiki/docx prefetch (the logic currently in `model_utils._fetch_feishu_document_content`)
  - `async def fetch_lanhu_document(text: str, mcp_configs: list | None) -> str | None` — Lanhu prefetch (the Lanhu half of `pipeline_runner._prefetch_document`)
  - `async def prefetch_document(text: str, mcp_configs: list | None) -> str` — tries Feishu then Lanhu, returns replaced text or the original (the pipeline's combined behavior)
- Consumes: `utils.lanhu_mcp_adapter.connect_single_server`, `utils.helpers._load_json_object`.

**Design note:** `model_utils` and `pipeline_runner` use the prefetch slightly differently. `model_utils._fetch_feishu_document_content` returns the *content* (or None) and the caller decides how to splice it. `pipeline_runner._prefetch_document` returns the full *requirement text* with the link replaced (or the original). To preserve both behaviors: expose the granular `fetch_feishu_document`/`fetch_lanhu_document` (content-or-None) for model_utils, and the combined `prefetch_document` (text-replacing) for the pipeline. The combined one is built on top of the granular ones.

- [ ] **Step 1: Create the module with shared regexes and the Feishu fetcher**

Create `backend/utils/document_prefetch.py`:

```python
"""Shared Feishu/Lanhu document prefetch via MCP.

Single source of truth for the Lanhu/Feishu URL regexes and the prefetch
logic previously duplicated in utils/model_utils.py and
app/pipeline/pipeline_runner.py.
"""
import logging
import re

from utils.helpers import _load_json_object

logger = logging.getLogger(__name__)

LANHU_URL_RE = re.compile(
    r'https?://lanhuapp\.com/web/#/item/project/[a-z]+[^"\s]*',
    re.IGNORECASE,
)
FEISHU_URL_RE = re.compile(
    r'https?://[a-zA-Z0-9-]+\.feishu\.cn/(?:wiki|docx)/([A-Za-z0-9_-]+)',
    re.IGNORECASE,
)


async def fetch_feishu_document(text: str, mcp_configs: "list | None") -> "str | None":
    """Read a Feishu wiki/docx link's body via MCP. Returns content or None."""
    match = FEISHU_URL_RE.search(text or "")
    if not match or not mcp_configs:
        return None

    token = match.group(1)
    try:
        from utils.lanhu_mcp_adapter import connect_single_server
    except Exception:
        logger.warning("Feishu MCP prefetch unavailable: adapter import failed", exc_info=True)
        return None

    enabled_configs = [c for c in mcp_configs if c.get("enabled", True)]
    for config in enabled_configs:
        server_name, client, error = await connect_single_server(config)
        if error or client is None:
            logger.info("Feishu MCP prefetch skipped unavailable server %s: %s", server_name, error)
            continue
        try:
            tool_names = {t.get("name") for t in client.tools}
            document_id = token
            if "wiki_v2_space_getNode" in tool_names and "/wiki/" in match.group(0).lower():
                node_raw = await client.call_tool("wiki_v2_space_getNode", {"params": {"token": token}})
                node_data = _load_json_object(node_raw) or {}
                node = node_data.get("node")
                if not isinstance(node, dict):
                    inner = node_data.get("data")
                    node = inner.get("node") if isinstance(inner, dict) else None
                if isinstance(node, dict):
                    document_id = node.get("obj_token") or document_id
                else:
                    logger.warning("飞书 wiki getNode 响应无 node 字段: %s", str(node_data)[:300])

            if "docx_v1_document_rawContent" not in tool_names:
                continue
            raw = await client.call_tool("docx_v1_document_rawContent", {"path": {"document_id": document_id}})
            data = _load_json_object(raw) or {}
            content = data.get("content")
            if not isinstance(content, str) or not content.strip():
                inner = data.get("data")
                if isinstance(inner, dict):
                    content = inner.get("content")
            if isinstance(content, str) and content.strip():
                logger.info("已预取飞书文档正文: server=%s document_id=%s size=%s", server_name, document_id, len(content))
                return content
            logger.warning("飞书 rawContent 响应无有效 content: %s", str(data)[:300])
        except Exception as e:
            logger.warning("Feishu MCP prefetch failed on server %s: %s", server_name, e)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    return None
```

- [ ] **Step 2: Add the Lanhu fetcher and the combined prefetch**

Append to `backend/utils/document_prefetch.py`:

```python
async def fetch_lanhu_document(text: str, mcp_configs: "list | None") -> "str | None":
    """Read a Lanhu document link's content via MCP. Returns content or None."""
    match = LANHU_URL_RE.search(text or "")
    if not match or not mcp_configs:
        return None
    try:
        from utils.lanhu_mcp_adapter import connect_single_server
    except Exception:
        logger.warning("Lanhu MCP prefetch unavailable: adapter import failed", exc_info=True)
        return None

    enabled_configs = [c for c in mcp_configs if c.get("enabled", True)]
    for config in enabled_configs:
        server_name, client, error = await connect_single_server(config)
        if error or client is None:
            continue
        try:
            tool_names = {t.get("name") for t in client.tools}
            if "get_lanhu_content" not in tool_names:
                continue
            raw = await client.call_tool("get_lanhu_content", {"url": match.group(0)})
            data = _load_json_object(raw) or {}
            content = data.get("content") or data.get("text")
            if isinstance(content, str) and content.strip():
                logger.info("已预取蓝湖文档 server=%s size=%s", server_name, len(content))
                return content
        except Exception as e:
            logger.warning("蓝湖预取失败 server=%s: %s", server_name, e)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
    return None


async def prefetch_document(text: str, mcp_configs: "list | None") -> str:
    """Replace a Feishu/Lanhu link in `text` with its fetched content.

    Tries Feishu first, then Lanhu. Returns the fetched content if found,
    otherwise the original text unchanged (matching pipeline_runner's
    previous behavior of returning replacement-or-original).
    """
    feishu = await fetch_feishu_document(text, mcp_configs)
    if feishu:
        return feishu
    lanhu = await fetch_lanhu_document(text, mcp_configs)
    if lanhu:
        return lanhu
    return text
```

- [ ] **Step 3: Verify the module imports**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import utils.document_prefetch as d; print(bool(d.FEISHU_URL_RE), bool(d.LANHU_URL_RE))"`
Expected: `True True`.

- [ ] **Step 4: Add a regex characterization test**

Create `backend/tests/test_document_prefetch.py`:

```python
"""Tests for the shared document-prefetch URL regexes."""
from utils.document_prefetch import FEISHU_URL_RE, LANHU_URL_RE


def test_feishu_wiki_url_matches_and_captures_token():
    m = FEISHU_URL_RE.search("see https://abc.feishu.cn/wiki/Xy12Z_token here")
    assert m is not None
    assert m.group(1) == "Xy12Z_token"


def test_feishu_docx_url_matches():
    m = FEISHU_URL_RE.search("https://team.feishu.cn/docx/AbC123")
    assert m is not None
    assert m.group(1) == "AbC123"


def test_lanhu_url_matches():
    m = LANHU_URL_RE.search("https://lanhuapp.com/web/#/item/project/detail?pid=1")
    assert m is not None


def test_no_match_plain_text():
    assert FEISHU_URL_RE.search("no url here") is None
    assert LANHU_URL_RE.search("no url here") is None
```

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_document_prefetch.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Point model_utils at the shared module**

In `backend/utils/model_utils.py`:
- Replace the local regex definitions at lines 148-155 with an import (add near the other imports): `from utils.document_prefetch import FEISHU_URL_RE as _FEISHU_URL_RE, LANHU_URL_RE as _LANHU_URL_RE, fetch_feishu_document`
- Delete the local `_LANHU_URL_RE`/`_FEISHU_URL_RE` assignments (lines 148-155). Keep `_MAX_PREANALYZE_PAGES` (line 156).
- Replace the body of `_fetch_feishu_document_content` (lines 302-359) with a thin delegator (preserving the existing internal name and call sites):

```python
async def _fetch_feishu_document_content(requirement: str, mcp_configs: list | None) -> str | None:
    """Read Feishu wiki/docx links directly via MCP and skip agent tool loops."""
    return await fetch_feishu_document(requirement, mcp_configs)
```

(The aliased `_FEISHU_URL_RE`/`_LANHU_URL_RE` keep the ~6 other references in `model_utils` — e.g. lines 166, 304, 737, 742 — working unchanged.)

- [ ] **Step 6: Verify model_utils imports and regex refs resolve**

Run: `cd backend; .\.venv\Scripts\python.exe -c "import utils.model_utils as m; print(bool(m._FEISHU_URL_RE), bool(m._LANHU_URL_RE))"`
Expected: `True True`.

- [ ] **Step 7: Point pipeline_runner at the shared module**

In `backend/app/pipeline/pipeline_runner.py`:
- Replace the local regex definitions (lines 23-31) with: `from utils.document_prefetch import FEISHU_URL_RE as _FEISHU_URL_RE, LANHU_URL_RE as _LANHU_URL_RE, prefetch_document`
- Replace the entire `_prefetch_document` method (lines 187-284) with a delegator that uses the instance's `mcp_configs`:

```python
    async def _prefetch_document(self, requirement_text: str) -> str:
        """预取飞书/蓝湖文档内容，替换需求文本中的链接为实际内容"""
        return await prefetch_document(requirement_text, self.mcp_configs)
```

- The existing usage in `_run_pipeline` (lines 305-310) calls `self._prefetch_document(...)` and checks `if _FEISHU_URL_RE.search(...) or _LANHU_URL_RE.search(...)` — these still work via the aliased imports. Leave that block unchanged.

- [ ] **Step 8: Verify pipeline_runner imports**

Run: `cd backend; .\.venv\Scripts\python.exe -c "from app.pipeline.pipeline_runner import PipelineRunner; print('ok')"`
Expected: `ok`.

- [ ] **Step 9: Run the full suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q`
Expected: green.

- [ ] **Step 10: Commit**

```bash
git add backend/utils/document_prefetch.py backend/tests/test_document_prefetch.py backend/utils/model_utils.py backend/app/pipeline/pipeline_runner.py
git commit -m "refactor: extract shared document_prefetch, dedupe feishu/lanhu logic"
```

---

### Task 7: Phase-1 integration verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend import smoke**

Run:
```powershell
cd backend; .\.venv\Scripts\python.exe -c "import app.main; print('app.main ok')"
```
Expected: `app.main ok` (this imports all route modules and exercises every refactored import chain).

- [ ] **Step 2: Full test suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q`
Expected: green, with the new `test_llm_factory.py` and `test_document_prefetch.py` included.

- [ ] **Step 3: Manual generation smoke (functional + API path)**

Per the spec's verification strategy, manually run one generation through each path against a dev server to confirm behavior is unchanged:
```powershell
cd backend; .\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Then from the frontend (or via an authenticated API client): trigger one functional test-case generation and one API test-case generation. Confirm both complete and produce cases. Document the result in the commit/PR description. (This step requires a configured API key and is a human/manual gate.)

---

## Self-Review

**Spec coverage** (spec §阶段 1): 1.1 LLM factory ✓ (Task 1) replacing 4 pipeline `__init__` (Task 2) + 3 model_utils sites (Task 3), with `extra_body` convention unified (Task 1 Step 3 docstring + Task 2 commit msg) and `_normalize_priority` extracted to `llm_factory.normalize_priority` (Task 1, applied Task 2) ✓. 1.2 api_context service with optional `dependency_labels` ✓ (Task 4) replacing both copies. 1.3 mcp_loader ✓ (Task 5) replacing both copies. 1.4 document_prefetch with unified regexes ✓ (Task 6) replacing `_fetch_feishu_document_content` + `_prefetch_document`.

**Placeholder scan:** every code step shows full replacement code; every verification step has an exact command + expected output. No TBD/TODO. The one judgment point (Task 4 Step 7 project-id coercion) is resolved inline with explicit code.

**Type consistency:** `build_chat_model(api_key, api_base_url, api_model, api_proxy_url, *, max_tokens=...)` is called identically in Tasks 2 and 3. `build_api_context(session, endpoint_ids, project_id, overrides, dependency_labels=None)` signature in Task 4 Step 1 matches the call sites in Steps 5 and 7, and matches the spec's stated signature. `load_user_mcp_configs(session, user)` consistent across Task 5. `prefetch_document(text, mcp_configs)` / `fetch_feishu_document(text, mcp_configs)` consistent across Task 6. The `_FEISHU_URL_RE`/`_LANHU_URL_RE` aliases preserve every existing reference in `model_utils` and `pipeline_runner`.

**Cross-phase dependency:** this plan depends on Phase 0 being merged (Task 4 modifies `tests/test_api_context.py`). If executed before Phase 0, create that test file first or skip the test-update steps and rely on the import-smoke checks.
