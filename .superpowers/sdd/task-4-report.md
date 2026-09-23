# Task 4: Runner 核心(状态机、prompt、登录缓存、闸门) - Implementation Report

## Status: DONE

## What I implemented

Created `backend/app/services/browser_agent.py` — the ONLY file touching browser-use APIs (delayed import), with `agent_factory` injection so tests never touch a real browser/LLM.

Deliverables:
- `_ui_tasks: dict[str, dict]` module-level task state + `get_ui_task(task_id)` + `_update_task()` + `_find_task_id_by_record()`
- `LoginContext` dataclass with `.hint()` (spec §7.2 dual-branch wording; returns `""` when no credentials or no cache)
- `resolve_login_context(db, param_map, target_url) -> tuple[LoginContext, dict]` — reads `test_username`/`test_password` from param_map, checks `browser_sessions/{origin}.json` cache file
- `build_ui_prompt(target_url, actions, expected, preset_conditions, login_ctx)` — numbered steps, numbered expected results, preset-condition hints, login hint
- `UIExecutionRunner` class — full state machine `queued → launching_browser → executing → judging → persisting → done`
- `launch_ui_execution(db, record_id, agent_factory=None) -> task_id` — creates in-memory task, spawns `asyncio.create_task(runner.run(record_id))` under a module-level `asyncio.Semaphore(1)` serialization gate
- `_build_llm()` — reuses `config_manager` + `utils.llm_factory.build_chat_model` (api path) with `max_tokens=16384`; adds an ollama branch via `langchain_ollama.ChatOllama` when `model_type == "ollama"`
- Added `playwright>=1.62.0` to `backend/requirements.txt`

### Correction notes applied (from the brief)
1. **`agent`/`meta` init**: initialized `agent = None` and `meta = {}` at the top of the `try` block in `_run`, used directly in `finally` — removed the `"agent" in dir()` defensive style.
2. **`_persist` double update merged**: `_persist` now emits a single `_update_task(task_id, phase="done", progress=100, result_status=status)`.
3. **`record.task_id` removed**: `UIExecutionRecord` has no such attribute. `run()` now finds the task_id via the `_ui_tasks` lookup (`_find_task_id_by_record(record_id)`, the same pattern `_run` used) and passes it through to `_run(db, record, task_id)`.
4. **`run_until_complete` removed**: `_export_storage_state` restructured as `async` and awaited in the `finally` block; uses `await` on the storage-state coroutine. For stub agents (no `browser_session`/`browser_context`/`browser`) it silently no-ops.

### Deviation from skeleton: DB session acquisition (important)
The skeleton's `run()` unconditionally did `from db.db import engine; with Session(engine) as db`. That CANNOT pass the brief's tests: the tests create records in the in-memory SQLite `db_session` fixture (conftest), and the real `db.db.engine` points at `backend/data/testcases.db` — the record would not be found there.

I made `run()` session-aware via `_acquire_session()`:
- Prefer the constructor-passed session when it is still active (`self._request_db.is_active`) → tests reuse the in-memory session, so writes are visible to the fixture.
- Otherwise build an independent session from `db.db.engine` → production background tasks that outlive the HTTP request still work (the brief's stated production concern is preserved).
- `_release_session()` only closes self-built sessions, never the reused constructor session.

This keeps the brief's production rationale while making the authoritative test file pass. Noted as a deviation for review.

## browser-use 0.13.8 real API findings and adaptations

Verified by reading the installed package (`backend/.venv/Lib/site-packages/browser_use/`):

| Skeleton assumption | 0.13.8 reality | Adaptation |
|---|---|---|
| `Agent(task=..., llm=...)` ctor | Same — `task`, `llm` are first two params | No change to `_default_agent_factory` |
| `Agent.run(max_steps=...) -> AgentResult` with `.success` | `run()` returns `AgentHistoryList`, which has **NO `.success`** attribute; has `is_successful() -> bool\|None` and `final_result() -> str\|None` | `_invoke_agent()` calls `run()` then normalizes via `_normalize_agent_result()` into a `SimpleNamespace(success=bool, final_result=lambda)`; handles both FakeAgent (`SimpleNamespace.success`) and real (`is_successful()`). Judgment code in `_run` only reads `.success`/`.final_result()`. |
| `register_history_step_callback(handler)` | Method **does not exist** in 0.13.8. Callbacks are constructor params `register_new_step_callback` / `register_done_callback`. BUT these are stored as plain instance attributes and read at run time (`if self.register_new_step_callback ...`), so they CAN be assigned post-construction. Callback signature is `(BrowserStateSummary, AgentOutput, int)` (sync or async). | `_attach_history_callback()`: (1) if `register_history_step_callback` exists (legacy) → use it; (2) elif `register_new_step_callback` attr exists (0.13.x) → assign a wrapper `_wrap_new_step_callback()` that renders the action list to text and forwards to the string-based `on_history_step`; (3) else FakeAgent contract (`agent.history_callback = callback`). |
| `agent.browser_context.storage_state()` | Agent has **no `browser_context`**; it has `agent.browser_session` (a `BrowserSession` wrapper) with an **async `export_storage_state(output_path=None) -> dict`** method (cookies in Playwright storage_state format) | `_export_storage_state()` (async): if `agent.browser_session` has `export_storage_state` → `await bs.export_storage_state()`; else falls back to legacy `browser_context`/`browser.context` `storage_state()`. Writes JSON to `_cache_path(origin)`, unconditionally on every run (self-heal), stubbed agents skip. |
| `AgentHistoryList.final_result()` | Same method exists | Used directly. |

## TDD Evidence

RED (before implementation):
```
cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_execution_runner.py -v
ERROR collecting tests/test_ui_execution_runner.py
E   ModuleNotFoundError: No module named 'app.services.browser_agent'
```

GREEN (after implementation):
```
tests/test_ui_execution_runner.py::TestBuildUiPrompt::test_prompt_contains_steps_expected_conditions PASSED
tests/test_ui_execution_runner.py::TestBuildUiPrompt::test_prompt_no_cache_branch PASSED
tests/test_ui_execution_runner.py::TestBuildUiPrompt::test_prompt_no_credentials_no_login_section PASSED
tests/test_ui_execution_runner.py::TestResolveLoginContext::test_reads_credentials_and_cache_flag PASSED
tests/test_ui_execution_runner.py::TestResolveLoginContext::test_no_cache_no_creds PASSED
tests/test_ui_execution_runner.py::TestRunnerRun::test_success_passes_case PASSED
tests/test_ui_execution_runner.py::TestRunnerRun::test_agent_fail_fails_case PASSED
tests/test_ui_execution_runner.py::TestRunnerRun::test_crash_marks_error_keeps_case_status PASSED
tests/test_ui_execution_runner.py::TestRunnerRun::test_timeout_marks_error PASSED
tests/test_ui_execution_runner.py::TestRunnerRun::test_prompt_passed_to_agent PASSED
10 passed, 53 warnings in 3.54s
```

Full suite:
```
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q
343 passed, 75 warnings in 9.62s
```

Smoke check of module internals (LoginContext.hint branches, resolve_login_context origin, _normalize_agent_result for both FakeAgent and AgentHistoryList-shaped results, launch_ui_execution, _EXECUTION_LOCK) all passed.

## Files changed

- `backend/app/services/browser_agent.py` (new)
- `backend/tests/test_ui_execution_runner.py` (new)
- `backend/requirements.txt` (added `playwright>=1.62.0`)

## Self-review findings

- `_build_llm()` runs even when a custom `agent_factory` is injected (test path); verified `build_chat_model` constructs `ChatOpenAI` without error when `api_key` is empty (tests never invoke the model).
- `resolve_login_context` keeps the `db` parameter for interface/tests even though credentials come from `param_map` (brief interface line said 2 args, but the test calls it with 3 — matched the test).
- `_update_task` guards `task_id` being `None` (tests call `run()` directly without a `_ui_tasks` entry).
- `launch_ui_execution`/`_EXECUTION_LOCK` are not exercised by the tests (they call `runner.run()` directly); the gate is used only in the production path.
- Timeout path: `asyncio.wait_for` cancels the inner agent coroutine; `TimeoutError` → `ERROR`, testcase untouched.
- Judgment mapping verified against spec §8: success→record+case PASSED, fail→record+case FAILED, crash/timeout→record ERROR, case status unchanged.

## Concerns

- **Session reuse deviation** (see above) is the main judgment call; flagging for reviewer. The alternative — literally following the skeleton's `Session(engine)` in `run()` — makes the brief's own test file fail, so the authoritative-test interpretation was chosen.
- `AgentHistoryList.is_successful()` returns `None` when the agent did not finish (`is_done != True`); normalized to `False` → FAILED. Reasonable default, but worth noting: an agent that exhausts max_steps without a verdict is marked FAILED, not ERROR.
- Real path (browser-use 0.13.8) was verified against installed package source but NOT executed end-to-end (no browser/LLM in this environment) — the callback wrapper and storage-state export are best-effort adaptations of the documented API.

## Fix Round 1

Review fixes applied to `backend/app/services/browser_agent.py`, `backend/tests/test_ui_execution_runner.py`, `backend/requirements.txt`. Findings 7-10 left for later triage (per instructions).

### Finding 1 (CRITICAL, session-reuse deviation) — fixed
`launch_ui_execution` now builds the runner with `UIExecutionRunner(None, agent_factory=agent_factory)` (`browser_agent.py:393`) so the production background run always self-builds a session from `db.db.engine` instead of committing on the request-scoped session that FastAPI closes after the HTTP response. The `_acquire_session` reuse branch (`browser_agent.py:167-181`) is kept for the direct `run()` test path (tests pass an in-memory `db_session`). `_release_session` (`browser_agent.py:183-190`) still only closes self-built sessions — verified it composes: test path `db is self._request_db` → no close; production path `db` is self-built → closed.

### Finding 2 (IMPORTANT, `str(None)` → "None" summary) — fixed
`_normalize_agent_result` final lambda now wraps `str(final_text or "")` (`browser_agent.py:374`) so real browser-use `final_result()` returning `None` yields empty summary, not literal `"None"`.

### Finding 3 (IMPORTANT, adaptation layer untested) — fixed
Appended 6 unit tests to `backend/tests/test_ui_execution_runner.py` (plain classes/doubles, no browser-use import):
- `TestNormalizeAgentResult::test_0138_shape_is_successful_and_final_result` — 0.13.x shape (`is_successful()`/`final_result()`), both success=True/False
- `TestNormalizeAgentResult::test_legacy_shape_uses_success_attr` — legacy `.success` attr
- `TestNormalizeAgentResult::test_final_result_none_maps_to_empty_summary` — `final_result()` None → `""`, asserts `!= "None"`
- `TestNormalizeAgentResult::test_unknown_shape_safe_default` — pins current behavior: no raise, `success=False`, empty summary
- `TestAttachHistoryCallback::test_new_style_register_new_step_callback_dispatch` — agent with `register_new_step_callback` attr → wrapper assigned, invoking it with `(None, output, 1)` triggers callback with rendered action text
- `TestAttachHistoryCallback::test_fallback_assigns_history_callback` — agent without it → callback assigned to `.history_callback`

### Finding 4 (IMPORTANT, stale version pin) — fixed
`backend/requirements.txt`: `browser-use>=0.9.0` → `browser-use>=0.13,<0.14` (code adapts to 0.13.x API).

### Finding 5 (MINOR, `run()` NameError hazard) — fixed
`run()` initializes `db = None` before the `try`, calls `_acquire_session()` inside it, and `_release_session` now guards `db is None` (`browser_agent.py:183-204`). If session acquisition raises, `finally` no longer hits an unbound `db`.

### Finding 6 (MINOR, `import asyncio as _asyncio` shadowing) — fixed
Removed the local shadowing import; `launch_ui_execution` uses module-level `asyncio.create_task` (`browser_agent.py:399`).

### Test commands & results
```
cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_execution_runner.py -v
16 passed, 53 warnings in ~4.5s   (10 existing + 6 new)

cd backend && .venv/Scripts/python.exe -m pytest tests/ -q
349 passed, 75 warnings in ~13s   (343 baseline + 6 new)
```

