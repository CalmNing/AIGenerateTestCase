# 接口场景批量执行实现计划

> **给智能体执行者：** 必须使用的子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项实现本计划。步骤使用 checkbox（`- [ ]`）语法，便于跟踪进度。

**目标：** 为接口场景增加按顺序批量执行能力，并在每个场景卡片上展示最新执行状态和执行时间。

**架构：** 新增一个项目级后端接口，根据项目和用户解析待执行场景，复用现有 `run_scenario()` 服务按顺序串行执行，为每个场景写入一条 `ApiScenarioResult`，并返回汇总结果。前端扩展类型和 API service，然后更新 `ApiScenarioTestTool`：跟踪已选场景 ID、加载最新执行记录、渲染状态标签，并触发“选中执行/全部执行”。

**技术栈：** FastAPI、SQLModel、React 18、TypeScript、Ant Design、Axios。

---

## 文件结构

- 修改 `backend/app/routes/api_test_tool.py`
  - 增加批量执行的 Pydantic 请求/响应模型。
  - 抽取保存场景执行结果和裁剪历史记录的辅助函数。
  - 新增 `POST /api-test/projects/{project_id}/scenarios/run-batch`。
- 修改 `frontend/src/types/index.ts`
  - 增加 `ApiScenarioBatchRunRequest`、`ApiScenarioBatchRunItem`、`ApiScenarioBatchRunResult`。
- 修改 `frontend/src/services/api.ts`
  - 导入新的批量执行类型。
  - 增加 `runScenarios(projectId, payload)` API 方法。
  - 为 `getScenarioResults()` 增加可选 `limit` 参数。
- 修改 `frontend/src/components/ApiScenarioTestTool.tsx`
  - 增加已选场景 ID 和批量执行 loading 状态。
  - 场景列表加载完成后加载每个场景的最新执行结果。
  - 在每个场景条目下渲染最新执行状态和时间。
  - 增加全选、取消选择、批量执行选中、执行全部控件。
  - 单场景和批量执行后刷新卡片状态、历史缓存和详情面板。

## 任务 1：后端批量执行接口

**文件：**
- 修改：`backend/app/routes/api_test_tool.py`

- [ ] **步骤 1：在现有请求模型附近增加批量请求/响应模型**

在 `class MatchEndpointRequest(BaseModel):` 后插入以下类：

```python
class RunScenarioBatchRequest(BaseModel):
    scenario_ids: List[int] = Field(default_factory=list)
    run_all: bool = False


class ScenarioBatchResultItem(BaseModel):
    scenario_id: int
    scenario_name: str
    record_id: int
    passed: bool
    created_at: str
    result: dict


class RunScenarioBatchResponse(BaseModel):
    total: int
    passed: int
    failed: int
    results: List[ScenarioBatchResultItem]
```

- [ ] **步骤 2：增加保存单个场景执行结果并裁剪历史的辅助函数**

在 `@router.post("/scenarios/{scenario_id}/run"...)` 前插入该辅助函数：

```python
def _store_scenario_result(
    session: SessionDep,
    scenario: ApiScenario,
    project: ApiProject,
    user_id: str,
    result: dict,
) -> ApiScenarioResult:
    record = ApiScenarioResult(
        scenario_id=scenario.id,
        project_id=project.id,
        scenario_name=scenario.name,
        passed=bool(result.get("passed")),
        result=result,
        user_id=user_id,
    )
    session.add(record)
    session.flush()

    records = session.exec(
        select(ApiScenarioResult)
        .where(ApiScenarioResult.scenario_id == scenario.id)
        .where(ApiScenarioResult.user_id == user_id)
        .order_by(ApiScenarioResult.created_at.desc(), ApiScenarioResult.id.desc())
    ).all()
    for old_record in records[MAX_SCENARIO_RESULT_RECORDS:]:
        session.delete(old_record)

    return record
```

- [ ] **步骤 3：重构单场景执行接口，复用辅助函数**

在 `run_api_scenario()` 中，将 `result = await run_scenario(session, scenario, project)` 之后的内容替换为：

```python
    record = _store_scenario_result(session, scenario, project, user.user_id, result)
    session.commit()
    session.refresh(record)
    return Response(data=record, message="场景执行完成")
```

完整函数结尾应如下：

```python
@router.post("/scenarios/{scenario_id}/run", response_model=Response[ApiScenarioResult])
async def run_api_scenario(scenario_id: int, session: SessionDep, user: CurrentUser):
    scenario = session.get(ApiScenario, scenario_id)
    if not scenario or scenario.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="场景不存在")
    project = session.get(ApiProject, scenario.project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")
    result = await run_scenario(session, scenario, project)
    record = _store_scenario_result(session, scenario, project, user.user_id, result)
    session.commit()
    session.refresh(record)
    return Response(data=record, message="场景执行完成")
```

- [ ] **步骤 4：在单场景执行接口前增加批量执行接口**

在 `list_scenario_results()` 之后、`run_api_scenario()` 之前插入该接口：

```python
@router.post("/projects/{project_id}/scenarios/run-batch", response_model=Response[RunScenarioBatchResponse])
async def run_api_scenarios_batch(
    project_id: int,
    request: RunScenarioBatchRequest,
    session: SessionDep,
    user: CurrentUser,
):
    project = session.get(ApiProject, project_id)
    if not project or project.user_id != user.user_id:
        return Response(code=status.HTTP_404_NOT_FOUND, message="接口项目不存在")

    if not request.run_all and not request.scenario_ids:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="请选择要执行的场景")

    query = (
        select(ApiScenario)
        .where(ApiScenario.project_id == project_id)
        .where(ApiScenario.user_id == user.user_id)
        .order_by(ApiScenario.updated_at.desc(), ApiScenario.id.desc())
    )
    scenarios = session.exec(query).all()

    if not request.run_all:
        requested_ids = set(request.scenario_ids)
        scenarios = [scenario for scenario in scenarios if scenario.id in requested_ids]
        found_ids = {scenario.id for scenario in scenarios}
        missing_ids = requested_ids - found_ids
        if missing_ids:
            missing = ", ".join(str(item) for item in sorted(missing_ids))
            return Response(code=status.HTTP_400_BAD_REQUEST, message=f"场景不存在或无权限: {missing}")

    results: List[ScenarioBatchResultItem] = []
    passed_count = 0

    for scenario in scenarios:
        try:
            result = await run_scenario(session, scenario, project)
        except Exception as exc:
            result = {
                "passed": False,
                "error": str(exc),
                "steps": [],
            }

        record = _store_scenario_result(session, scenario, project, user.user_id, result)
        session.commit()
        session.refresh(record)

        if record.passed:
            passed_count += 1

        results.append(
            ScenarioBatchResultItem(
                scenario_id=scenario.id,
                scenario_name=record.scenario_name,
                record_id=record.id,
                passed=record.passed,
                created_at=record.created_at.isoformat(),
                result=record.result,
            )
        )

    total = len(results)
    return Response(
        data=RunScenarioBatchResponse(
            total=total,
            passed=passed_count,
            failed=total - passed_count,
            results=results,
        ),
        message="批量执行完成",
    )
```

- [ ] **步骤 5：运行后端语法检查**

从仓库根目录运行：

```powershell
python -m py_compile backend/app/routes/api_test_tool.py
```

预期：命令退出码为 0，不输出语法错误。

- [ ] **步骤 6：提交后端改动**

```powershell
git add backend/app/routes/api_test_tool.py
git commit -m @'
feat(api-test): add scenario batch execution endpoint

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

## 任务 2：前端类型和 API 客户端

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/services/api.ts`

- [ ] **步骤 1：增加批量执行 TypeScript 接口**

在 `frontend/src/types/index.ts` 中，在 `export interface ApiScenarioResult { ... }` 之后插入：

```ts
export interface ApiScenarioBatchRunRequest {
  scenario_ids?: number[];
  run_all: boolean;
}

export interface ApiScenarioBatchRunItem {
  scenario_id: number;
  scenario_name: string;
  record_id: number;
  passed: boolean;
  created_at: string;
  result: Record<string, any>;
}

export interface ApiScenarioBatchRunResult {
  total: number;
  passed: number;
  failed: number;
  results: ApiScenarioBatchRunItem[];
}
```

- [ ] **步骤 2：在 API service 中导入新类型**

在 `frontend/src/services/api.ts` 中，更新来自 `../types` 的 import，加入：

```ts
ApiScenarioBatchRunRequest, ApiScenarioBatchRunResult
```

import 列表末尾应包含：

```ts
ApiProject, ApiEndpoint, ApiEndpointRunPayload, ApiScenario, ApiScenarioResult, ApiScenarioBatchRunRequest, ApiScenarioBatchRunResult, ApiImportResult, ApiSyncResult
```

- [ ] **步骤 3：为场景结果 API 方法增加可选 limit 参数**

将：

```ts
  getScenarioResults: (scenarioId: number): Promise<ApiResponse<ApiScenarioResult[]>> =>
    api.get(`/api-test/scenarios/${scenarioId}/results`),
```

替换为：

```ts
  getScenarioResults: (scenarioId: number, limit?: number): Promise<ApiResponse<ApiScenarioResult[]>> =>
    api.get(`/api-test/scenarios/${scenarioId}/results${limit ? `?limit=${limit}` : ''}`),
```

- [ ] **步骤 4：增加批量执行 API 方法**

在 `runScenario` 后插入：

```ts
  runScenarios: (projectId: number, payload: ApiScenarioBatchRunRequest): Promise<ApiResponse<ApiScenarioBatchRunResult>> =>
    api.post(`/api-test/projects/${projectId}/scenarios/run-batch`, payload),
```

场景执行相关方法应如下：

```ts
  getScenarioResults: (scenarioId: number, limit?: number): Promise<ApiResponse<ApiScenarioResult[]>> =>
    api.get(`/api-test/scenarios/${scenarioId}/results${limit ? `?limit=${limit}` : ''}`),
  runScenario: (scenarioId: number): Promise<ApiResponse<ApiScenarioResult>> => api.post(`/api-test/scenarios/${scenarioId}/run`),
  runScenarios: (projectId: number, payload: ApiScenarioBatchRunRequest): Promise<ApiResponse<ApiScenarioBatchRunResult>> =>
    api.post(`/api-test/projects/${projectId}/scenarios/run-batch`, payload),
```

- [ ] **步骤 5：运行前端 lint，提前发现类型错误**

运行：

```powershell
Set-Location frontend; npm run lint
```

预期：通过；如果失败，只允许是已有的无关问题。若报错指向本任务改动的类型或 API service，先修复再继续。

- [ ] **步骤 6：提交前端 API/类型改动**

```powershell
git add frontend/src/types/index.ts frontend/src/services/api.ts
git commit -m @'
feat(api-test): add scenario batch API client types

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

## 任务 3：加载并渲染场景最新执行状态

**文件：**
- 修改：`frontend/src/components/ApiScenarioTestTool.tsx`

- [ ] **步骤 1：增加结果状态辅助函数**

在 `formatScenarioResultRecordLabel()` 后插入：

```tsx
function formatScenarioResultTime(record: ApiScenarioResult | undefined) {
  if (!record) return '';
  return new Date(record.created_at).toLocaleString();
}

function latestScenarioResultLabel(record: ApiScenarioResult | undefined) {
  if (!record) return '未执行';
  return record.passed ? '通过' : '不通过';
}

function latestScenarioResultColor(record: ApiScenarioResult | undefined) {
  if (!record) return 'default';
  return record.passed ? 'success' : 'error';
}
```

- [ ] **步骤 2：增加加载最新结果的辅助函数**

在 `loadScenarioResults` 后插入：

```tsx
  const loadLatestScenarioResults = async (items: ApiScenario[]) => {
    if (items.length === 0) return;
    const pairs = await Promise.all(
      items.map(async (scenario) => {
        const res = await apiTestApi.getScenarioResults(scenario.id, 1);
        if (res.code !== 200) return [scenario.id, []] as const;
        return [scenario.id, res.data || []] as const;
      }),
    );
    setScenarioResultHistory((history) => {
      const next = { ...history };
      pairs.forEach(([scenarioId, records]) => {
        next[scenarioId] = records;
      });
      return next;
    });
  };
```

- [ ] **步骤 3：项目数据加载后调用最新结果加载函数**

在 `loadProjectData` 中，将：

```tsx
    if (scenarioRes.code === 200) setScenarios(scenarioRes.data || []);
```

替换为：

```tsx
    if (scenarioRes.code === 200) {
      const nextScenarios = scenarioRes.data || [];
      setScenarios(nextScenarios);
      loadLatestScenarioResults(nextScenarios).catch(() => message.error('加载场景最新执行结果失败'));
    }
```

后续状态重置逻辑保持不变。

- [ ] **步骤 4：在场景步骤数下方增加最新结果 UI**

在场景列表的 `List.Item.Meta` description 中，将：

```tsx
                                <span className="scenario-item-steps">
                                  <PlayCircleOutlined style={{ fontSize: 11 }} />
                                  {scenario.steps?.length || 0} 个步骤
                                </span>
```

替换为：

```tsx
                                <Space direction="vertical" size={2}>
                                  <span className="scenario-item-steps">
                                    <PlayCircleOutlined style={{ fontSize: 11 }} />
                                    {scenario.steps?.length || 0} 个步骤
                                  </span>
                                  {(() => {
                                    const latestRecord = scenarioResultHistory[scenario.id]?.[0];
                                    return (
                                      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                                        <Tag color={latestScenarioResultColor(latestRecord)} style={{ margin: 0 }}>
                                          {latestScenarioResultLabel(latestRecord)}
                                        </Tag>
                                        {latestRecord && <span style={{ color: 'var(--color-text-tertiary)' }}>{formatScenarioResultTime(latestRecord)}</span>}
                                      </span>
                                    );
                                  })()}
                                </Space>
```

- [ ] **步骤 5：运行前端 lint**

```powershell
Set-Location frontend; npm run lint
```

预期：`ApiScenarioTestTool.tsx` 中没有新增 TypeScript/ESLint 错误。

- [ ] **步骤 6：提交最新结果展示改动**

```powershell
git add frontend/src/components/ApiScenarioTestTool.tsx
git commit -m @'
feat(api-test): show latest scenario execution status

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

## 任务 4：增加场景选择控件

**文件：**
- 修改：`frontend/src/components/ApiScenarioTestTool.tsx`

- [ ] **步骤 1：增加已选场景状态**

在 `scenarioResultHistory` 附近的已有 state 声明处添加：

```tsx
  const [selectedScenarioIds, setSelectedScenarioIds] = useState<number[]>([]);
```

- [ ] **步骤 2：项目变化或数据重载时重置选择**

在 `loadProjectData` 中，在 `setSelectedScenarioResultRecordId(null);` 后添加：

```tsx
    setSelectedScenarioIds([]);
```

在 `handleSelectProject` 中，在 `setSelectedScenarioResultRecordId(null);` 后添加：

```tsx
    setSelectedScenarioIds([]);
```

在删除项目成功分支中，在 `setSelectedScenarioResultRecordId(null);` 后添加：

```tsx
          setSelectedScenarioIds([]);
```

- [ ] **步骤 3：增加选择辅助函数**

在 `selectScenario` 后插入：

```tsx
  const toggleScenarioSelection = (scenarioId: number, checked: boolean) => {
    setSelectedScenarioIds((ids) => {
      if (checked) return ids.includes(scenarioId) ? ids : [...ids, scenarioId];
      return ids.filter((id) => id !== scenarioId);
    });
  };

  const selectAllScenarios = () => {
    setSelectedScenarioIds(scenarios.map((scenario) => scenario.id));
  };

  const clearScenarioSelection = () => {
    setSelectedScenarioIds([]);
  };
```

- [ ] **步骤 4：为每个场景列表项增加 Checkbox**

在场景 `List.Item` 内部、`<List.Item.Meta` 之前插入：

```tsx
                            <Checkbox
                              checked={selectedScenarioIds.includes(scenario.id)}
                              onClick={(event) => event.stopPropagation()}
                              onChange={(event) => toggleScenarioSelection(scenario.id, event.target.checked)}
                              style={{ marginRight: 8 }}
                            />
```

列表项 children 开头应如下：

```tsx
                          >
                            <Checkbox
                              checked={selectedScenarioIds.includes(scenario.id)}
                              onClick={(event) => event.stopPropagation()}
                              onChange={(event) => toggleScenarioSelection(scenario.id, event.target.checked)}
                              style={{ marginRight: 8 }}
                            />
                            <List.Item.Meta
```

- [ ] **步骤 5：在场景列表上方增加选择工具条**

将单个按钮：

```tsx
                      <Button icon={<PlusOutlined />} onClick={handleCreateScenario} block>新建场景</Button>
```

替换为：

```tsx
                      <Space direction="vertical" style={{ width: '100%' }} size={8}>
                        <Button icon={<PlusOutlined />} onClick={handleCreateScenario} block>新建场景</Button>
                        <Space wrap size={6}>
                          <Button size="small" onClick={selectAllScenarios} disabled={scenarios.length === 0}>全选</Button>
                          <Button size="small" onClick={clearScenarioSelection} disabled={selectedScenarioIds.length === 0}>取消选择</Button>
                          <Tag style={{ margin: 0 }}>已选 {selectedScenarioIds.length}</Tag>
                        </Space>
                      </Space>
```

- [ ] **步骤 6：删除场景时同步从选择中移除**

在场景删除 `onOk` 处理函数中，在 `setScenarios(...)` 后添加：

```tsx
                    setSelectedScenarioIds((ids) => ids.filter((id) => id !== deletedScenarioId));
```

- [ ] **步骤 7：运行前端 lint**

```powershell
Set-Location frontend; npm run lint
```

预期：`ApiScenarioTestTool.tsx` 中没有新增错误。

- [ ] **步骤 8：提交选择 UI**

```powershell
git add frontend/src/components/ApiScenarioTestTool.tsx
git commit -m @'
feat(api-test): add scenario selection controls

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

## 任务 5：增加批量执行 UI 和状态更新

**文件：**
- 修改：`frontend/src/components/ApiScenarioTestTool.tsx`

- [ ] **步骤 1：增加批量执行 loading 状态**

在已有 `running` state 附近添加：

```tsx
  const [batchRunning, setBatchRunning] = useState(false);
```

- [ ] **步骤 2：增加将批量结果合并进历史缓存的辅助函数**

在 `handleRunScenario` 后插入：

```tsx
  const mergeBatchResults = (records: ApiScenarioResult[]) => {
    setScenarioResultHistory((history) => {
      const next = { ...history };
      records.forEach((record) => {
        const existing = next[record.scenario_id] || [];
        next[record.scenario_id] = [record, ...existing.filter((item) => item.id !== record.id)].slice(0, MAX_SCENARIO_RESULT_RECORDS);
      });
      return next;
    });

    const selectedRecord = selectedScenarioIdRef.current
      ? records.find((record) => record.scenario_id === selectedScenarioIdRef.current)
      : undefined;
    if (selectedRecord) {
      setSelectedScenarioResultRecordId(selectedRecord.id);
      setResult(selectedRecord.result);
    }
  };
```

- [ ] **步骤 3：增加批量执行处理函数**

在 `mergeBatchResults` 后插入：

```tsx
  const handleRunScenarioBatch = async (runAll: boolean) => {
    if (!selectedProjectId) return;
    if (!runAll && selectedScenarioIds.length === 0) {
      message.warning('请选择要执行的场景');
      return;
    }

    setBatchRunning(true);
    try {
      const res = await apiTestApi.runScenarios(selectedProjectId, {
        run_all: runAll,
        scenario_ids: runAll ? [] : selectedScenarioIds,
      });
      if (res.code !== 200 || !res.data) {
        message.error(res.message || '批量执行失败');
        return;
      }

      const records: ApiScenarioResult[] = res.data.results.map((item) => ({
        id: item.record_id,
        scenario_id: item.scenario_id,
        project_id: selectedProjectId,
        scenario_name: item.scenario_name,
        passed: item.passed,
        result: item.result,
        created_at: item.created_at,
        updated_at: item.created_at,
      }));
      mergeBatchResults(records);
      message.success(`批量执行完成：共 ${res.data.total} 个，通过 ${res.data.passed} 个，不通过 ${res.data.failed} 个`);
    } catch (error: any) {
      message.error(error.response?.data?.message || error.message || '批量执行失败');
    } finally {
      setBatchRunning(false);
    }
  };
```

- [ ] **步骤 4：批量执行期间禁用单场景执行按钮**

将单场景执行按钮：

```tsx
<Button icon={<PlayCircleOutlined />} loading={running} onClick={handleRunScenario}>串行执行</Button>
```

替换为：

```tsx
<Button icon={<PlayCircleOutlined />} loading={running} disabled={batchRunning} onClick={handleRunScenario}>串行执行</Button>
```

- [ ] **步骤 5：批量执行期间禁用复制按钮**

在场景列表的复制按钮中添加 `disabled={batchRunning}`：

```tsx
                                  disabled={batchRunning}
```

按钮 props 应包含：

```tsx
                                  loading={copyingScenarioId === scenario.id}
                                  disabled={batchRunning}
```

- [ ] **步骤 6：在场景工具条中增加批量执行按钮**

在选择工具条的 `Space wrap` 中，在已选数量 `Tag` 后添加：

```tsx
                          <Button
                            size="small"
                            type="primary"
                            icon={<PlayCircleOutlined />}
                            loading={batchRunning}
                            disabled={selectedScenarioIds.length === 0}
                            onClick={() => handleRunScenarioBatch(false)}
                          >
                            批量执行
                          </Button>
                          <Button
                            size="small"
                            icon={<SyncOutlined />}
                            loading={batchRunning}
                            disabled={scenarios.length === 0}
                            onClick={() => handleRunScenarioBatch(true)}
                          >
                            执行全部
                          </Button>
```

- [ ] **步骤 7：让单场景执行复用合并逻辑**

在 `handleRunScenario` 中，将成功分支中的：

```tsx
        setScenarioResultHistory((history) => ({
          ...history,
          [runningScenario.id]: [record, ...(history[runningScenario.id] || [])].slice(0, MAX_SCENARIO_RESULT_RECORDS),
        }));
        setSelectedScenarioResultRecordId(record.id);
        setResult(record.result);
```

替换为：

```tsx
        mergeBatchResults([record]);
```

- [ ] **步骤 8：运行前端 lint**

```powershell
Set-Location frontend; npm run lint
```

预期：`ApiScenarioTestTool.tsx` 中没有新增错误。

- [ ] **步骤 9：提交批量执行 UI**

```powershell
git add frontend/src/components/ApiScenarioTestTool.tsx
git commit -m @'
feat(api-test): add scenario batch execution UI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

## 任务 6：最终验证

**文件：**
- 验证：`backend/app/routes/api_test_tool.py`
- 验证：`frontend/src/types/index.ts`
- 验证：`frontend/src/services/api.ts`
- 验证：`frontend/src/components/ApiScenarioTestTool.tsx`

- [ ] **步骤 1：运行后端语法检查**

```powershell
python -m py_compile backend/app/routes/api_test_tool.py
```

预期：退出码为 0。

- [ ] **步骤 2：运行前端 lint**

```powershell
Set-Location frontend; npm run lint
```

预期：退出码为 0。若失败，先修复报告的问题再继续。

- [ ] **步骤 3：运行前端构建**

```powershell
Set-Location frontend; npm run build
```

预期：TypeScript 编译和 Vite 构建成功完成。

- [ ] **步骤 4：手动验证清单**

如果后端和前端尚未运行，按项目命令启动：

```powershell
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

```powershell
Set-Location frontend; npm run dev
```

在浏览器中验证：

1. 打开接口场景测试工具。
2. 选择一个包含场景的接口项目。
3. 确认每个场景卡片显示 `未执行`，或显示最新 `通过` / `不通过` 标签和时间戳。
4. 勾选两个场景并点击 `批量执行`。
5. 确认成功提示为 `批量执行完成：共 N 个，通过 X 个，不通过 Y 个`。
6. 确认已执行场景卡片的状态和时间已更新。
7. 点击 `执行全部`，确认所有场景按顺序执行并更新。
8. 选中某个场景，执行包含它的批量任务，确认结果 tab 更新到最新记录。

- [ ] **步骤 5：检查 git 状态**

```powershell
git status --short
```

预期：工作区干净，除非手动验证生成了不应提交的本地环境文件。

- [ ] **步骤 6：提交最终修复（仅当验证产生修复时）**

仅在验证过程中需要额外修改时执行：

```powershell
git add backend/app/routes/api_test_tool.py frontend/src/types/index.ts frontend/src/services/api.ts frontend/src/components/ApiScenarioTestTool.tsx
git commit -m @'
fix(api-test): polish scenario batch execution

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

## 自检

- 规格覆盖：后端批量接口、选中/全部两种模式、串行执行、失败不中断、最新状态/时间展示、批量汇总、历史/详情刷新和校验均已映射到任务。
- 占位扫描：没有 TBD/TODO/fill-in-later 指令；代码片段和命令均明确。
- 类型一致性：后端响应使用 `record_id`；前端映射为 `ApiScenarioResult.id`。请求字段在 Python 和 TypeScript 中一致使用 `scenario_ids` 和 `run_all`。
