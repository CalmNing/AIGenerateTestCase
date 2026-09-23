# Task 2 Report: steps 归一化与 URL 拼接(纯函数)

## What I implemented
Created two pure-function modules per the brief (verbatim):

- `backend/app/services/ui_steps.py` — three functions:
  - `normalize_ui_steps(steps: list) -> tuple[list[str], bool]` — normalizes 3 step formats (plain str pass-through; pipeline dict → `str(step["action"])`; api_call dict flagged & skipped) into UI-executable text actions + api_call flag.
  - `has_api_call_step(steps: list) -> bool` — any api_call dict → True.
  - `build_target_url(site_origin, page_path) -> str` — origin strip trailing slash; empty path → origin only; `#`/`/`-prefixed path → `origin + path`; else `origin + "/" + path`.
  - Private helper `_is_api_call_step` shares口径 with `pipeline_runner._is_pipeline_api_call_step`.
- `backend/tests/test_ui_steps.py` — 13 tests (6 normalize / 3 has_api_call / 4 build_target_url).

## TDD Evidence

### RED
Command: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_steps.py -v`

Output (abridged):
```
collected 0 items / 1 error
tests\test_ui_steps.py:2: in <module>
    from app.services.ui_steps import normalize_ui_steps, has_api_call_step, build_target_url
E   ModuleNotFoundError: No module named 'app.services.ui_steps'
==================== 51 warnings, 1 error in 0.58s ====================
```
Why expected: implementation file did not exist yet, so the import at module top fails during collection.

### GREEN
Command: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ui_steps.py -v`

Output:
```
collected 13 items
13 passed, 51 warnings in 0.07s
```
All 13 tests pass. (51 warnings are pre-existing pydantic `json_encoders` deprecation warnings, unrelated.)

## Files changed
- Created `backend/app/services/ui_steps.py` (58 lines)
- Created `backend/tests/test_ui_steps.py` (70 lines)

Commit: `c1e6417` "feat(ui-exec): steps 归一化与目标 URL 拼接纯函数" — verified via `git show --stat HEAD` that only these 2 files are in the commit (128 insertions).

## Self-review findings
- Signatures match the brief's Interfaces section exactly (`normalize_ui_steps`, `has_api_call_step`, `build_target_url`) — downstream runner/routes tasks can import them as specified.
- Behavior cross-checked against spec:
  - Strings pass through (implementation `.strip()`s, which the tests rely on for `"  "` → skipped); dict without `action` and non-str/non-dict garbage skipped; empty strings skipped; api_call dicts both flagged and excluded from actions.
  - `build_target_url` handles origin trailing slash, empty path, hash path, slash path, bare path — all 4 test cases pass.
- No lint concerns (pure Python, type-annotated). No frontend impact.
- Note: `_is_api_call_step` treats `endpoint_id: 0` as NOT api_call (uses `is not None`). This is verbatim from the plan's implementation and matches the stated口径, so left as-is.

## Concerns
- None. The LF→CRLF git warnings on commit are benign Windows line-ending normalization.
