# Phase 3: Frontend Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce prop drilling and monolith size in the React frontend by extracting domain hooks from `App.tsx`, introducing a zustand store for cross-domain shared state, splitting the 2731-line `ApiScenarioTestTool.tsx`, and completing targeted cleanup (mojibake comments, stray `console.*`, `any` types, a shared `useApiCall` hook), plus correcting stale CLAUDE.md claims.

**Architecture:** Incremental, behavior-preserving extractions. Domain hooks (`useSessions`, `useModules`, `useTestcases`, `useEnvironments`) pull related `useState`+handlers out of `App.tsx`. A zustand store holds the genuinely shared selections (`selectedSession`, `selectedModule`, testcase selection) so `TestCaseManager`/`TestCaseGenerator` subscribe directly instead of receiving 13–19 props. `ApiScenarioTestTool.tsx` is decomposed into sub-components + `useScenario`/`useScenarioRun` hooks. A `useApiCall` hook unifies the try/catch + notification pattern.

**Tech Stack:** React 18, TypeScript (strict: `noUnusedLocals`, `noUnusedParameters`), Vite, Ant Design, zustand ^4.4.7 (already in `package.json`, currently unused), axios.

## Global Constraints

- **No backend dependency.** This phase is frontend-only and can proceed in parallel with Phases 0–2.
- **Verification gate after every task:** `cd frontend && npx tsc --noEmit` must pass (zero errors), then `cd frontend && npm run lint` must pass (`--max-warnings 0`). TypeScript strict mode means an unused import or variable is a hard error — clean up as you go.
- **Behavior-preserving:** extractions must not change runtime behavior. Verify by building (`npm run build`) and a manual click-through of the affected screens at task end where noted.
- **No new heavy dependencies.** zustand is already present; do not add others.
- **localStorage keys are a contract** with persisted user state — preserve exact key strings: `'currentPlatform'`, `'appSettings'`, `'selectedSkills'`, `'currentEnvironmentId'`.
- **Incremental store adoption:** introduce the zustand store alongside existing props first; migrate consumers one at a time; only remove a prop once every consumer reads it from the store. Never do a big-bang prop removal.
- Run commands from `frontend/`.

---

### Task 1: Cleanup — mojibake comments, stray console.*, any-types

**Files:**
- Modify: `frontend/src/services/api.ts` (lines 191, 229 mojibake; 9 debug `console.*` in `generateTestcases`; `any` in `scheduledTaskApi` L406/409/412 and `mcpApi` L423)
- Modify: `frontend/src/App.tsx` (25 `console.*` — remove debug ones, keep intentional error logging only if it has no user-facing notification)

**Interfaces:**
- Produces: `ScheduledTask`, `ScheduledTaskInput`, `McpToolInfo` TypeScript interfaces (in `frontend/src/types/index.ts`) to replace `any` in the two API groups.
- Consumes: existing `ApiResponse<T>` wrapper in `api.ts`.

**Why first:** lowest risk, no structural change, immediately improves type safety and removes noise before the larger refactors touch the same files.

- [ ] **Step 1: Fix the two mojibake comments in api.ts**

In `frontend/src/services/api.ts`:
- Line 191 currently reads `    // ?????API??ID?????????ID?`. Replace with a correct Chinese comment describing the block that follows (the `api_endpoint_id` FormData append): `    // 追加 API 接口 ID 与项目 ID 到 FormData`.
- Line 229 currently reads `  /** ?????????? API ?? */`. Replace with: `  /** 执行测试用例对应的 API 调用 */`.

(If the surrounding code makes the precise original intent clear, match it; the goal is a readable comment, not a guess at lost bytes.)

- [ ] **Step 2: Remove debug console.* from generateTestcases**

In `frontend/src/services/api.ts`, inside `generateTestcases`, remove the 9 debug logging calls at lines 153, 164, 168, 171, 173, 176, 180, 183, 185 (the `准备上传图片`, `转换后的Blob`, `图片Blob已添加`, `FormData中是否包含file`, the image-conversion `console.error`, `没有图片数据`, `FormData内容`, and the two per-key dumps). Keep the actual logic (the FormData construction) — only delete the `console.*` statements. Leave the three interceptor `console.error` calls (lines 36/54/59) — those are operational error logging, not debug noise.

- [ ] **Step 3: Verify tsc + lint after api.ts console removal**

Run: `cd frontend; npx tsc --noEmit`
Expected: no errors. If removing a `console.log` leaves an unused variable (e.g. a `const blob = ...` only used in a log), either remove the now-dead variable or confirm it is still used by the FormData append. Strict mode will flag it.
Run: `cd frontend; npm run lint`
Expected: clean.

- [ ] **Step 4: Add typed interfaces for scheduled tasks and MCP tools**

In `frontend/src/types/index.ts`, add (match field names to the backend `ScheduledTask` model / `mcpApi.listTools` response — read `backend/db/models.py` `ScheduledTask` and `backend/app/routes/mcp.py` to confirm shapes):

```typescript
export interface ScheduledTask {
  id: number;
  name: string;
  cron?: string;
  interval_seconds?: number;
  enabled: boolean;
  request_ids: number[];
  parameters: Array<{ key: string; value: string }>;
  created_at?: string;
  updated_at?: string;
}

export type ScheduledTaskInput = Omit<ScheduledTask, 'id' | 'created_at' | 'updated_at'>;

export interface McpServerInput {
  name: string;
  transport: string;
  url?: string;
  command?: string;
  args?: string[];
  enabled: boolean;
  enabled_tools?: string[] | null;
}

export interface McpToolInfo {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}
```

**Executor note:** verify each field against the real backend model before committing — if a field name differs (e.g. `request_ids` vs `saved_request_ids`), use the backend's actual name. These types must match the JSON the API returns.

- [ ] **Step 5: Apply the types in api.ts**

In `frontend/src/services/api.ts`, replace the `any` signatures:
- `scheduledTaskApi.getTasks` (L406): `(): Promise<ApiResponse<ScheduledTask[]>>`
- `scheduledTaskApi.createTask` (L409): `(task: ScheduledTaskInput): Promise<ApiResponse<ScheduledTask>>`
- `scheduledTaskApi.updateTask` (L412): `(taskId: number, task: Partial<ScheduledTaskInput>): Promise<ApiResponse<ScheduledTask>>`
- `mcpApi.listTools` (L423): `(servers: McpServerInput[]): Promise<ApiResponse<McpToolInfo[]>>`

Add the imports at the top of `api.ts`: `import type { ScheduledTask, ScheduledTaskInput, McpServerInput, McpToolInfo } from '../types';`

- [ ] **Step 6: Verify tsc + lint**

Run: `cd frontend; npx tsc --noEmit`
Expected: no errors. If `ScheduledTaskManager.tsx` or `McpConfigModal.tsx` passed shapes that don't match the new types, tsc will flag the call sites — fix those call sites to construct correctly-typed objects (this is the type system catching real mismatches; resolve them, don't loosen the types back to `any`).
Run: `cd frontend; npm run lint` → clean.

- [ ] **Step 7: Remove debug console.* from App.tsx**

In `frontend/src/App.tsx`, review all 25 `console.*` calls. Remove debug/trace logs. Keep only `console.error` calls that log an exception with no other user-facing surface (and even then, prefer they already pair with a `notification.error`). Use judgment: a `console.log('selected', x)` is debug → remove; a `console.error('Failed to load X', err)` inside a catch that ALSO shows a notification → remove the console (redundant); a bare catch with only `console.error` and no notification → keep it (it's the only error signal) OR convert to a notification. Document which you kept and why in the commit.

- [ ] **Step 8: Verify and commit**

Run: `cd frontend; npx tsc --noEmit && npm run lint`
Expected: both clean.

```bash
git add frontend/src/services/api.ts frontend/src/types/index.ts frontend/src/App.tsx
git commit -m "chore(frontend): fix mojibake comments, strip debug console logs, type scheduledTask/mcp APIs"
```

---

### Task 2: Correct stale CLAUDE.md claims

**Files:**
- Modify: `F:\VSCode\ai-generate-testcase\CLAUDE.md` (frontend section)

**Interfaces:** none (documentation).

- [ ] **Step 1: Fix the App.tsx line-count claim**

In CLAUDE.md, find the line in the Frontend section:

```
├── App.tsx                # Monolith component (62K+ lines), contains most UI logic
```

Replace with:

```
├── App.tsx                # Large root component (~2054 lines), holds session/module/testcase/environment state and platform switching
```

- [ ] **Step 2: Fix the zustand claim**

In CLAUDE.md, find:

```
**State management:** Primarily React local state, some global state via zustand.
```

Replace with:

```
**State management:** React local state + localStorage. `zustand` is listed in package.json but, as of this writing, not yet used in `src/` (a store is being introduced in the frontend optimization work).
```

(If Task 4 of this plan has already landed the store by the time you edit this, update the wording to reflect that the store now exists and what it holds.)

- [ ] **Step 3: Note the real largest frontend file**

In CLAUDE.md's components list, ensure `ApiScenarioTestTool.tsx` is mentioned as the largest component. If it is not listed under `components/`, add a line under the component tree:

```
│   ├── api/ApiScenarioTestTool.tsx   # API scenario test tool (largest frontend file, being split into sub-components)
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: correct stale App.tsx line count and zustand usage claims"
```

---

### Task 3: Extract `useApiCall` hook and adopt in App.tsx

**Files:**
- Create: `frontend/src/hooks/useApiCall.ts`
- Modify: `frontend/src/App.tsx` (migrate a representative subset of the 19 try/catch+notification sites)

**Interfaces:**
- Produces:
  ```typescript
  function useApiCall(): {
    run: <T>(
      fn: () => Promise<ApiResponse<T>>,
      opts?: { successMessage?: string; errorMessage?: string; onSuccess?: (data: T) => void }
    ) => Promise<T | undefined>;
    loading: boolean;
  }
  ```
  `run` awaits `fn`, shows `notification.success` with `successMessage` on `code === 0` (or whatever the success convention is — confirm against `ApiResponse`), shows `notification.error` with `errorMessage` (or the response `message`) on failure/throw, manages a `loading` boolean, and returns the `data` (or `undefined` on failure).
- Consumes: antd `notification`, the `ApiResponse<T>` type.

**Scope note:** the codebase has ~129 try/catch and ~258 notification calls. Migrating all of them is out of scope for one task and high-risk. This task creates the hook and migrates a representative cluster in `App.tsx` (the session/module CRUD handlers) to prove the pattern; remaining migrations happen opportunistically during Tasks 4–5 and beyond. Do NOT attempt a global sweep.

- [ ] **Step 1: Confirm the success convention**

Read `frontend/src/services/api.ts` and `frontend/src/types/index.ts` for the `ApiResponse<T>` shape. Per CLAUDE.md, responses are `{code, message, data}`. Confirm the success code (likely `code === 0`) by reading an existing handler in `App.tsx` (e.g. how `loadSessions` checks success). Use that exact convention in the hook.

- [ ] **Step 2: Create the hook**

Create `frontend/src/hooks/useApiCall.ts`:

```typescript
import { useState, useCallback } from 'react';
import { notification } from 'antd';
import type { ApiResponse } from '../types';

interface RunOptions<T> {
  successMessage?: string;
  errorMessage?: string;
  onSuccess?: (data: T) => void;
}

/**
 * Unifies the try/catch + antd notification pattern around an API call.
 * Returns response.data on success (code === 0), undefined on failure.
 */
export function useApiCall() {
  const [loading, setLoading] = useState(false);

  const run = useCallback(
    async <T,>(
      fn: () => Promise<ApiResponse<T>>,
      opts: RunOptions<T> = {}
    ): Promise<T | undefined> => {
      setLoading(true);
      try {
        const res = await fn();
        if (res.code === 0) {
          if (opts.successMessage) {
            notification.success({ message: opts.successMessage });
          }
          opts.onSuccess?.(res.data);
          return res.data;
        }
        notification.error({
          message: opts.errorMessage ?? '操作失败',
          description: res.message,
        });
        return undefined;
      } catch (err) {
        notification.error({
          message: opts.errorMessage ?? '请求失败',
          description: err instanceof Error ? err.message : String(err),
        });
        return undefined;
      } finally {
        setLoading(false);
      }
    },
    []
  );

  return { run, loading };
}
```

**Executor note:** if `ApiResponse` is not exported from `../types`, find where it lives (likely `api.ts`) and import from there. Adjust the success-code check to match Step 1's finding.

- [ ] **Step 3: Verify the hook type-checks in isolation**

Run: `cd frontend; npx tsc --noEmit`
Expected: no errors (the hook is unused so far, but its own types must be valid). The `<T,>` syntax in a `.ts` file is fine; if you author it in a `.tsx` file the trailing comma is required to disambiguate from JSX.

- [ ] **Step 4: Migrate the session CRUD handlers in App.tsx**

In `App.tsx`, instantiate the hook (`const { run: runApi } = useApiCall();`) and migrate the create/edit/delete session handlers (around lines 180+) to use `runApi(() => sessionApi.create(...), { successMessage: '创建成功', errorMessage: '创建失败', onSuccess: () => loadSessions() })`. Keep behavior identical — same notifications, same reload-after-success. Migrate only the session handlers in this task.

- [ ] **Step 5: Verify and commit**

Run: `cd frontend; npx tsc --noEmit && npm run lint` → both clean.
Run: `cd frontend; npm run build` → builds successfully.
Manual: start `npm run dev`, create/rename/delete a session, confirm notifications and list refresh behave as before.

```bash
git add frontend/src/hooks/useApiCall.ts frontend/src/App.tsx
git commit -m "feat(frontend): add useApiCall hook, adopt in session CRUD handlers"
```

---

### Task 4: Introduce zustand store for shared selection state

**Files:**
- Create: `frontend/src/store/appStore.ts`
- Modify: `frontend/src/App.tsx` (back the shared selections with the store)
- Modify: `frontend/src/components/test-case/TestCaseGenerator.tsx` (read shared state from store, drop those props)

**Interfaces:**
- Produces:
  ```typescript
  interface AppState {
    selectedSession: Session | null;
    selectedModule: number | string;
    selectedTestcaseIds: number[];
    setSelectedSession: (s: Session | null) => void;
    setSelectedModule: (m: number | string) => void;
    setSelectedTestcaseIds: (ids: number[]) => void;
  }
  export const useAppStore: UseBoundStore<StoreApi<AppState>>;
  ```
- Consumes: `zustand`'s `create`, the `Session`/`Module` types from `../types`.

**Migration discipline (per Global Constraints):** add the store, mirror the three shared values into it, migrate ONE consumer (`TestCaseGenerator`) to read from the store, and only then remove the corresponding props from that one consumer. `TestCaseManager` migration is Task 5's concern. App.tsx keeps its existing local state in sync with the store during the transition.

- [ ] **Step 1: Create the store**

Create `frontend/src/store/appStore.ts`:

```typescript
import { create } from 'zustand';
import type { Session } from '../types';

interface AppState {
  selectedSession: Session | null;
  selectedModule: number | string;
  selectedTestcaseIds: number[];
  setSelectedSession: (s: Session | null) => void;
  setSelectedModule: (m: number | string) => void;
  setSelectedTestcaseIds: (ids: number[]) => void;
}

/** Cross-domain selection state shared by the test-case screens. */
export const useAppStore = create<AppState>((set) => ({
  selectedSession: null,
  selectedModule: 0,
  selectedTestcaseIds: [],
  setSelectedSession: (s) => set({ selectedSession: s }),
  setSelectedModule: (m) => set({ selectedModule: m }),
  setSelectedTestcaseIds: (ids) => set({ selectedTestcaseIds: ids }),
}));
```

**Executor note:** confirm the `Session` type's import path; if `selectedModule`'s initial value should be `0` vs `''`, match App.tsx's current init (`useState(0)` at line 63).

- [ ] **Step 2: Verify store type-checks**

Run: `cd frontend; npx tsc --noEmit` → no errors.

- [ ] **Step 3: Sync App.tsx local selections into the store**

In `App.tsx`, where `selectedSession`/`selectedModule`/`selectedTestcaseIds` are set (the `setSelectedSession`/`setSelectedModule`/`setSelectedTestcaseIds` calls), also push into the store — simplest approach: keep the existing `useState` as the source of truth for now and add `useEffect`s that mirror into the store:

```typescript
const setStoreSession = useAppStore((s) => s.setSelectedSession);
const setStoreModule = useAppStore((s) => s.setSelectedModule);
const setStoreTcIds = useAppStore((s) => s.setSelectedTestcaseIds);
useEffect(() => { setStoreSession(selectedSession); }, [selectedSession, setStoreSession]);
useEffect(() => { setStoreModule(selectedModule); }, [selectedModule, setStoreModule]);
useEffect(() => { setStoreTcIds(selectedTestcaseIds); }, [selectedTestcaseIds, setStoreTcIds]);
```

This keeps existing behavior intact while making the values available via the store.

- [ ] **Step 4: Migrate TestCaseGenerator to read shared state from the store**

In `frontend/src/components/test-case/TestCaseGenerator.tsx`, replace the `selectedSession`, `modules`, `selectedModule` props that come from shared state with store reads where they are genuinely shared. Specifically `selectedSession` and `selectedModule` become `useAppStore((s) => s.selectedSession)` / `useAppStore((s) => s.selectedModule)`. Remove those two props from the component's props interface and from the `<TestCaseGenerator ... />` usage in `App.tsx` (line 1598). Leave the generator-specific props (`requirement`, `loading`, `selectedApiEndpointId`, overrides, etc.) as props — they are not shared state.

**Caution:** `modules` is a list, not a selection — it is arguably domain state for `useModules` (Task — future), not the selection store. For this task, leave `modules` as a prop; only migrate the two selection scalars.

- [ ] **Step 5: Verify and commit**

Run: `cd frontend; npx tsc --noEmit` → no errors (tsc will confirm the removed props are no longer required at the call site and not referenced inside the component).
Run: `cd frontend; npm run lint` → clean.
Run: `cd frontend; npm run build` → success.
Manual: switch sessions/modules, confirm `TestCaseGenerator` still reflects the current selection.

```bash
git add frontend/src/store/appStore.ts frontend/src/App.tsx frontend/src/components/test-case/TestCaseGenerator.tsx
git commit -m "feat(frontend): add zustand selection store, migrate TestCaseGenerator off shared props"
```

---

### Task 5: Extract `useEnvironments` hook + EnvironmentManager

**Files:**
- Create: `frontend/src/hooks/useEnvironments.ts`
- Create: `frontend/src/components/EnvironmentManager.tsx` (optional companion; the three env modals + their handlers)
- Modify: `frontend/src/App.tsx` (remove env state/handlers, consume the hook)

**Interfaces:**
- Produces:
  ```typescript
  interface Environment { id: string; name: string; parameters: Array<{ key: string; value: string }>; is_default?: boolean }
  function useEnvironments(canManage: boolean): {
    environments: Environment[];
    currentEnvironmentId: string;
    getCurrentEnvironment: () => Environment;
    switchEnvironment: (id: string) => void;
    addEnvironment: (name: string) => Promise<void>;
    removeEnvironment: (id: string) => Promise<void>;
    editEnvironment: (env: Environment, name: string) => Promise<void>;
    saveEnvironmentToBackend: (env: Environment) => Promise<void>;
    addParameter: () => void;
    removeParameter: (index: number) => void;
    changeParameter: (index: number, field: 'key' | 'value', value: string) => void;
    refresh: () => Promise<void>;
  }
  ```
- Consumes: `globalParameterApi`, `localStorage` key `'currentEnvironmentId'`, antd `notification`.

**Why environments first among domain hooks:** the spec calls it out specifically (104 references, localStorage-persisted, `saveEnvironmentToBackend`), and it is the most self-contained domain — its state (`environments`, `currentEnvironmentId`, the four modal flags) and ~11 handlers (App.tsx lines 1222-1389) form a clean unit with a clear localStorage contract.

- [ ] **Step 1: Create the hook by moving the env logic**

Create `frontend/src/hooks/useEnvironments.ts`. Move the `Environment` interface (App.tsx L11-16), the state `environments` (L76), `currentEnvironmentId` (L77), and the functions `fetchGlobalParameters` (L1222), `getCurrentEnvironment` (L1254), `handleSwitchEnvironment` (L1259), `saveEnvironmentToBackend` (L1265), `handleAddEnvironmentParameter` (L1294), `handleRemoveEnvironmentParameter` (L1307), `handleEnvironmentParameterChange` (L1320), `handleAddEnvironment`/`handleConfirmAddEnvironment` (L1334/1340), `handleRemoveEnvironment` (L1359), `handleOpenEditEnvironment`/`handleConfirmEditEnvironment` (L1371/1378). Preserve the exact localStorage key `'currentEnvironmentId'` (read at L78, written at L1261) and the `'env-1'` default-id create-vs-update logic in `saveEnvironmentToBackend`.

The modal-open flags (`isGlobalParamsModalVisible`, `isAddEnvModalVisible`, `isEditEnvModalVisible`, `newEnvironmentName`, `editingEnvironment`, `editEnvironmentName`) are UI state — they can either stay in `App.tsx` (if the modals stay there) or move into the optional `EnvironmentManager` component. Decide based on Step 3.

- [ ] **Step 2: Verify the hook type-checks**

Run: `cd frontend; npx tsc --noEmit` → no errors (hook unused so far). Fix any import-path issues for `globalParameterApi` / `Environment`.

- [ ] **Step 3: Consume the hook in App.tsx**

In `App.tsx`, replace the removed state/handlers with `const env = useEnvironments(canManageGlobalParams);`. Update every reference (the spec counts ~104 — most are `getCurrentEnvironment().parameters` for `<VariableAssistant>` at L2043, and the three modals at L1871-2040) to use `env.*`. The `useEffect [canManageGlobalParams]` that called `fetchGlobalParameters()` (L1396) becomes a call to `env.refresh()` or is internalized into the hook's own `useEffect`.

**Decision point:** if the three env modals (global-params management L1871-2001, add-env L2004-2020, edit-env L2023-2040) make App.tsx's JSX materially smaller, extract them into `EnvironmentManager.tsx` taking the `env` hook's return as props (or calling `useEnvironments` itself). If they're tightly coupled to other App.tsx render state, leave them inline and just back them with `env.*`. Prefer extraction if it cleanly reduces App.tsx.

- [ ] **Step 4: Verify and commit**

Run: `cd frontend; npx tsc --noEmit` → no errors (strict mode confirms no dangling references to the moved handlers/state remain in App.tsx).
Run: `cd frontend; npm run lint` → clean.
Run: `cd frontend; npm run build` → success.
Manual: open global-params modal, switch/add/edit/remove an environment, add/edit/remove a parameter, confirm `VariableAssistant` sees the current env's parameters, and confirm `currentEnvironmentId` persists across reload.

```bash
git add frontend/src/hooks/useEnvironments.ts frontend/src/App.tsx
# include EnvironmentManager.tsx if created
git commit -m "refactor(frontend): extract useEnvironments hook from App.tsx"
```

---

### Task 6: Extract domain hooks — useSessions, useModules, useTestcases

**Files:**
- Create: `frontend/src/hooks/useSessions.ts`
- Create: `frontend/src/hooks/useModules.ts`
- Create: `frontend/src/hooks/useTestcases.ts`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- `useSessions()` → `{ sessions, selectedSession, loadSessions, selectSession, createSession, renameSession, deleteSession, ... }` (owns `sessions`, the session modal/edit/delete state from App.tsx L60/62/66/67/93/95/107/108). Writes `selectedSession` through the zustand store from Task 4.
- `useModules()` → `{ modules, selectedModule, loadModules, selectModule, createModule, editModule, deleteModule, ... }` (owns `modules` + module-form state L61/63/68-71/94/96/97). Writes `selectedModule` through the store.
- `useTestcases(selectedSession, filters)` → `{ testcases, testcasesResponse, loadTestcases, view/edit/complete/delete/move/batch handlers, selectedTestcaseIds, ... }` (owns the testcase block L64/65/86-92/98/548-564).

**Discipline:** extract one hook per step, verify between each. These are larger and more entangled than `useEnvironments`; go slowly. Use the zustand store (Task 4) for the shared selections so the hooks don't need to pass them back up through props.

- [ ] **Step 1: Extract useSessions**

Create `frontend/src/hooks/useSessions.ts` moving the session state + handlers. The hook writes `selectedSession` into the store via `useAppStore.getState().setSelectedSession` (or by subscribing). In `App.tsx`, replace with `const sess = useSessions();` and rewire references. Remove the now-redundant `selectedSession` `useState` + its mirror effect from Task 4 (the store is now written by the hook directly).

Run: `cd frontend; npx tsc --noEmit && npm run lint` → clean.

- [ ] **Step 2: Extract useModules**

Create `frontend/src/hooks/useModules.ts` moving module state + handlers (writes `selectedModule` to the store). Rewire `App.tsx`. Note the `useEffect [selectedModule]` (L136) that auto-switches the generate→manage tab — decide whether tab state belongs to App.tsx (likely yes, it's cross-cutting UI) and keep that effect in App.tsx reading `selectedModule` from the store.

Run: `cd frontend; npx tsc --noEmit && npm run lint` → clean.

- [ ] **Step 3: Extract useTestcases**

Create `frontend/src/hooks/useTestcases.ts` moving the testcase list state + CRUD/batch handlers + the `pipeline-completed` window-event reload effect (L1146). It takes `selectedSession` and `filters` as inputs and exposes `testcases`, `testcasesResponse`, `loadTestcases`, and the handler set. Rewire `App.tsx`.

Run: `cd frontend; npx tsc --noEmit && npm run lint` → clean.

- [ ] **Step 4: Migrate TestCaseManager off prop drilling**

Now that `useTestcases` + the store exist, reduce `TestCaseManager`'s 19-prop interface: have it read shared selections (`selectedSession`, `selectedModule`) from the store and consume the testcase data/handlers from a context or directly from `useTestcases` if `TestCaseManager` is rendered where the hook's values are in scope. Migrate incrementally — remove props only after the component reads each from the store/hook. Keep the genuinely manager-specific callbacks as props if they orchestrate App-level modals.

Run: `cd frontend; npx tsc --noEmit && npm run lint` → clean.

- [ ] **Step 5: Verify and commit**

Run: `cd frontend; npm run build` → success.
Manual: full click-through — list/create/rename/delete sessions; create/edit/delete modules; view/edit/complete/delete/move/batch-move testcases; trigger a generation and confirm the `pipeline-completed` reload still fires.

```bash
git add frontend/src/hooks/useSessions.ts frontend/src/hooks/useModules.ts frontend/src/hooks/useTestcases.ts frontend/src/App.tsx frontend/src/components/test-case/TestCaseManager.tsx
git commit -m "refactor(frontend): extract useSessions/useModules/useTestcases, cut TestCaseManager prop drilling"
```

---

### Task 7: Split ApiScenarioTestTool.tsx

**Files:**
- Create: `frontend/src/components/api/scenario/useScenario.ts`
- Create: `frontend/src/components/api/scenario/useScenarioRun.ts`
- Create: `frontend/src/components/api/scenario/scenarioUtils.ts` (the ~25 pure helpers at L51-453)
- Create: `frontend/src/components/api/scenario/ScenarioProjectSidebar.tsx`
- Create: `frontend/src/components/api/scenario/ScenarioAssetList.tsx`
- Create: `frontend/src/components/api/scenario/EndpointEditor.tsx`
- Create: `frontend/src/components/api/scenario/ScenarioEditor.tsx`
- Create: `frontend/src/components/api/scenario/ScenarioRunResultPanel.tsx`
- Create: `frontend/src/components/api/scenario/BodyPreviewModal.tsx`
- Modify: `frontend/src/components/api/ApiScenarioTestTool.tsx` (becomes the composition root)

**Interfaces:**
- `scenarioUtils.ts`: re-export the pure helpers `cloneRows`, `scenarioStepFromEndpoint`, `formatFailedAssertion`, `cleanScenarioSteps`, `scenarioToFormValues`, `endpointToFormValues` (and the others at L51-453). No state — straight move.
- `useScenario()`: owns `projects/endpoints/scenarios/selectedScenario/selectedEndpoint/selectedProjectId/environments` + CRUD (`loadProjects`, `loadProjectData`, `loadEnvironments`, `selectScenario`, `handleCreateScenario`, `handleCopyScenario`, `handleSaveScenario`, `handleAddStep`, selection helpers) + the form-population effects (L662-696).
- `useScenarioRun()`: owns `running/batchRunning/endpointRunning/result/endpointResult/scenarioResultHistory/selectedScenarioResultRecordId/selectedScenarioIds` + `handleRunScenario`, `handleRunScenarioBatch`, `mergeBatchResults`, `loadScenarioResults`, `loadLatestScenarioResults`, `handleRunEndpoint`, `handleCopyScenarioResult`, and the `selectedScenarioResultRecords` memo.

**Discipline:** this is the riskiest single file. Extract the pure utils first (zero behavior risk), then the presentational sub-components (props in, callbacks out), then the two hooks last. Verify after each extraction. Do NOT extract everything in one commit.

- [ ] **Step 1: Extract pure utils**

Create `frontend/src/components/api/scenario/scenarioUtils.ts` and move the module-level pure helpers (L51-453: `cloneRows`, `scenarioStepFromEndpoint`, `formatFailedAssertion`, `cleanScenarioSteps`, `scenarioToFormValues`, `endpointToFormValues`, and the others). Export each. In `ApiScenarioTestTool.tsx`, import them from the new file and delete the local definitions.

Run: `cd frontend; npx tsc --noEmit && npm run lint` → clean. Run `npm run build` → success. (Pure move; behavior unchanged.)

```bash
git add frontend/src/components/api/scenario/scenarioUtils.ts frontend/src/components/api/ApiScenarioTestTool.tsx
git commit -m "refactor(frontend): extract scenario pure utils"
```

- [ ] **Step 2: Extract BodyPreviewModal + ScenarioProjectSidebar (leaf presentational)**

Create `BodyPreviewModal.tsx` (JSX L2705-2726, owns `bodyPreview*` state passed as props) and `ScenarioProjectSidebar.tsx` (JSX L2031-2164: import form + project list + collapse; props: `projects`, `selectedProjectId`, import-form values + handlers `handleImport`/`handleSyncProject`/`handleSelectProject`/`handleDeleteProject`, `projectSidebarCollapsed`). Replace the inline JSX in `ApiScenarioTestTool.tsx` with `<ScenarioProjectSidebar ... />` and `<BodyPreviewModal ... />`.

Run: `cd frontend; npx tsc --noEmit && npm run lint` → clean. Build → success. Commit.

- [ ] **Step 3: Extract ScenarioRunResultPanel + ScenarioAssetList**

Create `ScenarioRunResultPanel.tsx` (JSX L2552-2644: result-record `<Select>` + steps `<Table>` + `renderStepDebugResult`) and `ScenarioAssetList.tsx` (JSX L2169-2392: endpoint search/grouping + scenario list + batch toolbar). Wire them as children with props for the data + callbacks they need.

Run: `cd frontend; npx tsc --noEmit && npm run lint` → clean. Build → success. Commit.

- [ ] **Step 4: Extract EndpointEditor + ScenarioEditor**

Create `EndpointEditor.tsx` (JSX L2400-2519, uses `endpointForm` + the `render*` editor helpers + endpoint handlers) and `ScenarioEditor.tsx` (JSX L2520-2678, uses `scenarioForm` + `watchedScenarioSteps`, embeds `<ScenarioRunResultPanel />`). The `render*` helper functions move with the editor that uses them. Pass forms and handlers as props.

Run: `cd frontend; npx tsc --noEmit && npm run lint` → clean. Build → success. Commit.

- [ ] **Step 5: Extract useScenario and useScenarioRun hooks**

Create the two hooks per the Interfaces above, moving the state + functions out of `ApiScenarioTestTool.tsx`. The component becomes a composition root: `const scenario = useScenario(); const run = useScenarioRun(scenario);` then renders the sidebar/asset-list/editor sub-components wired to those hook values. AI-generation handlers (`handleGenerateBody`, `handleGenerateScenarioStepBody`, `handleGenerateScenarioTests`, infer-dependency handlers) and variable-replacement (`handleBatchReplaceVariables`, `handleReplaceVariables`) can stay in the component or move to a `useScenarioAI` hook — extract only if it cleanly reduces the file; otherwise leave them in the root.

Run: `cd frontend; npx tsc --noEmit && npm run lint` → clean.
Run: `cd frontend; npm run build` → success.

- [ ] **Step 6: Full manual regression of the API scenario tool**

Start `npm run dev`. In the API scenario tool: import a Swagger spec, select a project, edit + save an endpoint, run an endpoint, create + edit + run a scenario, run a batch, view run-result history, preview an AI-generated body, run variable replacement. Confirm every panel behaves as before the split.

- [ ] **Step 7: Commit the hook extraction**

```bash
git add frontend/src/components/api/scenario/ frontend/src/components/api/ApiScenarioTestTool.tsx
git commit -m "refactor(frontend): split ApiScenarioTestTool into sub-components and useScenario/useScenarioRun hooks"
```

---

## Self-Review

**Spec coverage** (spec §阶段 3): 3.1 App.tsx domain hooks `useSessions`/`useModules`/`useTestcases`/`useEnvironments` (+ optional `EnvironmentManager`) ✓ (Tasks 5-6). 3.2 enable zustand for shared state (`selectedSession`, `selectedModule`, testcase selection) to cut `TestCaseManager`/`TestCaseGenerator` prop drilling ✓ (Tasks 4, 6). 3.3 split `ApiScenarioTestTool.tsx` into scenario list / editor / run-result panel sub-components + `useScenario`/`useScenarioRun` ✓ (Task 7). 3.4 cleanup: CLAUDE.md line-count + zustand fix ✓ (Task 2), api.ts mojibake L191/L229 ✓ (Task 1), `useApiCall` for try/catch + notification ✓ (Task 3), `any` in `scheduledTaskApi`/`mcpApi` ✓ (Task 1), strip `console.*` (App.tsx 25, api.ts generateTestcases debug) ✓ (Task 1).

**Placeholder scan:** the hook and store code (Tasks 3, 4) is fully written. The large extractions (Tasks 5-7) specify exact source line ranges, the target file, and the precise state/functions to move, plus a verify-and-commit gate per step — appropriate for behavior-preserving moves where reprinting hundreds of JSX lines would be noise. Each "decision point" (extract modal vs. leave inline; `useScenarioAI` or not) gives a concrete decision rule. No bare TBD/TODO.

**Type consistency:** `useApiCall().run` signature is consistent between definition (Task 3 Step 2) and adoption (Step 4). `useAppStore` shape is identical in Task 4 Step 1 and its consumers in Tasks 4/6. `Environment` interface in Task 5 matches the App.tsx original (L11-16). `useScenario`/`useScenarioRun` ownership lists in Task 7 match the agent's structural map (no function assigned to two hooks).

**Risk ordering:** cleanup (lowest risk) → docs → hook scaffolding → store (additive) → environments (most self-contained domain) → the entangled domain hooks → the 2731-line component split (highest risk, last), each with a tsc+lint+build gate and manual click-through where behavior could regress. The incremental store-adoption rule (mirror first, migrate one consumer, remove props last) is stated in Global Constraints and applied in Tasks 4 and 6.

**Verification reality:** there is no frontend test runner in this repo, so the gates are `npx tsc --noEmit` (strict mode catches dangling refs/unused symbols from every move), `npm run lint` (`--max-warnings 0`), `npm run build`, and explicit manual click-throughs. This is called out so the executor does not look for a missing `npm test`.
