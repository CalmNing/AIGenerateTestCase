# Task 4: Bug 链接可配置 (Configurable Bug Link) - Implementation Report

## Status: DONE

## Summary

Implemented configurable bug link template via backend config system. The bug link URL is now retrieved from the backend configuration instead of being hardcoded in the frontend component.

## Changes Made

### 1. Backend Configuration (`backend/config.py`)
- Added `bug_link_template` key to `default_config` dictionary (line 25)
- Default value is empty string, which preserves the existing hardcoded URL as fallback

### 2. Backend API Endpoint (`backend/app/routes/config.py`)
- Added `GET /api/config/bug-link-template` endpoint (lines 15-19)
- Returns `Response(data={"template": template})` where template is read from config
- Follows existing patterns for config endpoints (uses `config_manager.get()`)

### 3. Frontend API Method (`frontend/src/services/api.ts`)
- Added `getBugLinkTemplate` method to `configApi` object (line 374)
- Returns `Promise<ApiResponse<{ template: string }>>` matching backend response format

### 4. TestcaseDetailView Component (`frontend/src/components/TestcaseDetailView.tsx`)
- Added optional `bugLinkTemplate` prop to `TestcaseDetailViewProps` interface (line 8)
- Updated component destructuring to accept `bugLinkTemplate` (line 17)
- Updated bug link rendering logic (lines 58-60):
  - If `bugLinkTemplate` is provided, uses `template.replace('{bug_id}', String(tc.bug_id))`
  - If empty/null, falls back to hardcoded URL: `http://zt.luban.fit/index.php?m=bug&f=view&bugID=${tc.bug_id}`

### 5. ViewTestcaseModal Component (`frontend/src/components/modals/ViewTestcaseModal.tsx`)
- Added import for `configApi` from `../../services/api` (line 5)
- Added `bugLinkTemplate` state with `useState('')` (line 33)
- Added `useEffect` to fetch template when modal becomes visible (lines 35-43)
  - Calls `configApi.getBugLinkTemplate()` on visibility change
  - Silently fails if endpoint is unavailable
- Passed `bugLinkTemplate` prop to `TestcaseDetailView` (line 77)

## Usage

**Backend Configuration:**
Set `bug_link_template` in `backend/data/config.json`:
```json
{
  "bug_link_template": "https://your-bug-tracker.com/bug/{bug_id}"
}
```

**Template Syntax:**
- Use `{bug_id}` as placeholder for the bug ID
- Example: `https://jira.example.com/browse/BUG-{bug_id}`
- If template is empty, uses default hardcoded URL

**API Endpoint:**
- `GET /api/config/bug-link-template`
- Response: `{ code: 200, message: "success", data: { template: "..." } }`

## Verification

- TypeScript compilation completed with pre-existing errors only (not related to this task)
- All modified files verified for correct implementation
- No new TypeScript errors introduced

## Commit

```
37ad420 feat: make bug link template configurable via backend config
```

## Files Modified

1. `backend/config.py` - Added `bug_link_template` to default_config
2. `backend/app/routes/config.py` - Added GET endpoint for bug-link-template
3. `frontend/src/services/api.ts` - Added `getBugLinkTemplate` API method
4. `frontend/src/components/TestcaseDetailView.tsx` - Added prop and updated rendering logic
5. `frontend/src/components/modals/ViewTestcaseModal.tsx` - Added state, effect, and prop passing

## Notes

- The implementation follows existing patterns in the codebase
- Template is fetched on modal open (cached by browser HTTP cache)
- Falls back gracefully to hardcoded URL if template is empty
- Error handling is silent (catch(() => {})) to avoid disrupting UX
