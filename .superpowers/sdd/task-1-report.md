# Task 1 Report: infer_endpoint_dependencies() 推断函数

## What Was Implemented

Added 4 functions to `backend/app/routes/testcase.py`:

1. **`infer_endpoint_dependencies(session, selected_endpoint_ids, api_project_id) -> list[int]`** -- Main function that infers which "create" endpoint IDs are missing when the user selects edit/delete/detail endpoints. Returns a list of endpoint IDs to auto-add.

2. **`_parse_body_fields(body) -> list[dict]`** -- Helper that parses a JSON body string into top-level field dicts.

3. **`_get_response_top_fields(response_schema) -> list[str]`** -- Helper that extracts top-level field names from a response schema dict.

4. **`_best_matching_create_endpoint(dependency_path, candidates)`** -- Helper that matches a dependency path to the best candidate create endpoint by path prefix similarity.

Also added `import json` and `import re` at the top of the file (`json` was previously only imported locally inside the `generate` function).

## Insertion Point

After `router = APIRouter(prefix="/testcases", tags=["testcases"])` (line 17), before `class TestCasePage(BaseModel):` (now line 169).

## Verification

- **Syntax check:** `python -c "import ast; ast.parse(...)"` -- PASSED
- **Import check:** `python -c "from app.routes.testcase import infer_endpoint_dependencies; print('Import OK')"` -- PASSED

## Files Changed

- `backend/app/routes/testcase.py` -- Added 159 lines (2 top-level imports + 4 functions)

## Self-Review Findings

No issues found. The implementation exactly matches the task brief specification. All regex patterns, data flow, and edge cases (empty inputs, type coercion, missing data) are handled per the spec.

## Commit

```
55ca565 feat: add infer_endpoint_dependencies() function for auto-inferring create endpoint dependencies
```
