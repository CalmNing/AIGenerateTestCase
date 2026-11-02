# P0 失败可见性 + P1 接通 checkpointer：改动说明

> 对应调研文档 [docs/research/agent-fit-report.md](docs/research/agent-fit-report.md) 的 P0 / P1 两步。
> 本次只做"地基"，未引入 agent 循环，也未改任何业务数据模型。

---

## 一、P0 失败可见性：把"降级"从日志里搬到 API 里

### 改造前的问题

- 阶段 2 单个需求点失败 → `logger.error` + 返回空列表，任务仍标 `completed`；
- 阶段 3 整批用例设计失败 → 同上；
- 阶段 4 优化批次失败 → `continue` 跳过；
- 三个去重函数一律"保守保留"（不删/全保留），只写 warning 日志；
- 前端 `GET /pipeline/{task_id}/status` 只能看到 `status=completed` + "生成完成！"。

结果：**部分失败被伪装成完全成功**，用户与后续 agent 都拿不到 error signal。

### 改造内容

1. **任务级 `warnings` 列表**（`app/pipeline/pipeline_runner.py`）
   - `_create_task` 初始化 `"warnings": []`；
   - 新增 `_add_warning(task_id, phase, code, message, detail, severity)`：
     - `severity="warning"`：结果确实缺内容（批次失败、去重降级、历史保存失败…）；
     - `severity="quality"`：结果完整但质量存疑（等级分布异常…），**不改变任务状态**。
2. **任务状态新增 `partial`**
   - `_complete_task`：有 `warning` 级条目 → `status="partial"`，`message` 明确写"有 N 项降级"，`result.partial=true`、`result.warnings` 一并返回；
   - 只有 `quality` 级 → 保持 `completed`，`message` 标"（N 项质量提示）"；
   - `_fail_task` 保留已有 warnings，便于定位"失败前已降级"。
3. **覆盖的降级点**

| 位置 | code | 说明 |
|---|---|---|
| 阶段2 单需求点测试点生成失败 | `test_point_generation_failed` | 带需求点内容与异常类型 |
| 阶段3 单批用例设计失败 | `case_design_batch_failed` | 带受影响的 test_point_ids |
| 阶段4 单批优化失败 | `optimization_batch_failed` | 带 case_ids，并统计 `optimization_failed_batches` |
| 阶段4 整体优化失败 | `optimization_stage_failed` | 此前只记日志 |
| 需求点去重降级 | `requirement_dedup_failed` / `_empty` / `_invalid_indices` | 通过 `on_fallback` 回调上报 |
| 测试点去重降级 | `test_point_dedup_failed` / `_empty` | 同上 |
| 用例冗余审查降级 | `test_case_dedup_failed` / `_empty` / `_delete_all_blocked` | 同上 |
| 等级分布异常 | `uniform_case_level` / `invalid_case_level` | `severity=quality` |
| 单次生成：历史提示词保存失败 | `history_prompt_save_failed` | `warnings_out` 传给路由 |
| 单次生成：前置依赖补全失败 | `prerequisite_steps_failed` | 此前只记日志 |

4. **单次生成路径同样可见**
   - `generate_testcases(..., warnings_out=[...])` 收集；
   - `POST /api/testcases/{session_id}/testcases` 在有降级时返回
     `{code, message: "...有 N 项降级...", data: {partial: true, warnings, testcases_count}}`。
5. **等级分布校验**（此前提示词要求"级别不能全是同一个"，无任何代码校验）
   - `compute_level_distribution()` 统计 1/2/3/4/unknown；
   - `check_level_distribution()`：用例数 ≥4 且全部同一等级 → `uniform_case_level` 提示；
   - 分布写入 `result.level_distribution`，供前端/统计使用。
6. **前端展示**（`frontend/src/components/test-case/TestCaseGenerator.tsx`）
   - 轮询识别 `partial`：完成但**橙色警告提示**"有 N 项降级"；
   - 进度面板新增降级明细列表（阶段 + 文案）与质量提示行；
   - `PipelineWarning` 类型导出，`severity` 区分展示。

### 向后兼容

- `status=completed` 语义不变；`partial` 是**新增**取值（旧前端只会走 `completed`/`failed` 分支，不会破坏）。
- `result` 只新增 `warnings/partial/level_distribution/optimization_failed_batches` 字段，不删改既有字段。

---

## 二、P1 接通 checkpointer：把"已实例化但从未使用"的死代码接上

### 改造前的问题

- `utils/model_utils.py:146` 有 `checkpointer = InMemorySaver()`，但 `create_agent(...)` 从不传它（全库 grep `checkpointer` 仅 2 行）；
- `thread_id = f"{session_id}-{uuid4()}"`：每次都是新 thread，即使挂上 checkpointer 也读不到历史。

### 改造内容

1. **新增 `backend/utils/agent_memory.py`**
   - `build_thread_id(scope, user_id, session_id, module_id)`：稳定键，如 `testcase:u1:7:3`；
     非法字符归一化（`abc/def` → `abc_def`），None → `none`。
   - `agent_memory_context()`：异步上下文管理器，产出 `(checkpointer, store)`；
     - 生产默认 `AsyncSqliteSaver` + `AsyncSqliteStore`，落盘 `backend/data/agent_memory.db`（进程重启不丢）；
     - 任何异常/目录不可写/依赖缺失 → 自动降级 `InMemorySaver` + `InMemoryStore`；
     - 最终兜底 `NullCheckpointer`（不存检查点但接口可用）。
2. **真正传给 `create_agent`**
   - `create_testcase_agent(..., checkpointer=None, store=None)`：非 None 才透传
     （`**({"checkpointer": checkpointer} if ... else {})`），**不传时行为与改造前完全一致**。
3. **稳定 thread + 每次全新生成**
   - 生成链路 thread_id 改为 `build_thread_id(scope="testcase", ...)`；
   - 关键取舍：生成是"一次性任务"，若直接复用稳定 thread，第二次生成会把上一轮
     ReAct 轨迹整体带入上下文（token 膨胀 + 结论串味）。因此：
     **同一 thread 已有历史 → 先 `adelete_thread` 清空再跑**（轨迹可追溯、可清理，
     未来支持断点续跑只需去掉这一步；Store 的长期记忆不受影响）。
4. **chat 路由**
   - `ChatRequest` 新增 `client_session_id`；`create_agent` 传 checkpointer/store；
   - 命中已有轨迹时**只发最新一条消息**（否则前端每次回传 20 条会被 `add_messages` 重复追加，
     导致持久化状态无限膨胀）；
   - 未传 `client_session_id` 时退化为原行为（`session_id="anonymous"`，单次会话）。
   - 前端 `ChatAssistant` 生成一次性 `clientSessionId` 并随请求发送。
5. **配置开关**（`backend/config.py`）
   - `agent_memory_enabled`（默认 `true`）：置 false 则完全不接触记忆；
   - `agent_memory_db_path`（默认空 → `backend/data/agent_memory.db`）。
6. **依赖**：`requirements.txt` 增补 `langgraph-checkpoint-sqlite`、`aiosqlite`
   （缺失时自动降级内存，不影响主流程）。

### 验证过的行为

```
thread_id 稳定：build_thread_id(...) 两次调用结果一致
checkpointer 生效：同一 thread 第二次 ainvoke 能看到历史消息（2 → 4 条）
落盘生效：AsyncSqliteSaver / AsyncSqliteStore 正常读写 backend/data/agent_memory.db
关闭开关：agent_memory_enabled=false → 仍返回可用 checkpointer（内存降级）
```

---

## 三、测试

新增 `backend/tests/test_agent_memory_and_visibility.py`（21 个用例，全部不触网）：

- `build_thread_id` 稳定性 / None 与非法字符归一化 / 不同 module 不同 key；
- `agent_memory_context` 产出可用 checkpointer，配置关闭时降级不崩溃；
- `_add_warning` 记录字段与 severity；task 不存在时不抛异常；
- `quality` 级 warning → 任务仍 `completed`；`warning` 级 → `partial`；
- `_fail_task` 保留 warnings；
- 等级分布统计 / 全同级告警 / 少于 4 条不误报 / 混合等级不告警；
- 去重 `on_fallback`：异常与"删除全部被拦截"都会上报；回调自身抛异常被吞；不传回调时向后兼容；
- 端到端：阶段2 单点失败 → 任务 `status=partial` 且 `result.partial=true`。

回归：`backend pytest` **1001 passed**；前端 `npm run build` + `npm run lint` 通过。

---

## 四、审查修复记录（第二轮自检发现的问题）

> 首轮实现后的自检（pyflakes + 行为探针 + 前端渲染路径走查）发现 4 个问题，已全部修复：

| # | 严重度 | 问题 | 修复 |
|---|---|---|---|
| 1 | **严重** | 前端进度面板渲染条件是 `pipelinePolling && pipelineStatus`——轮询结束 `setPipelinePolling(false)` 会把面板**卸载**，partial 降级明细用户根本看不到（toast 说"详见面板"但面板已消失） | 渲染条件改为 `pipelineStatus && (pipelinePolling || status==='partial')`，partial 状态在轮询结束后保留展示 |
| 2 | **严重** | chat 匿名请求（未传 `client_session_id`）固定 `session_id="anonymous"` → 所有匿名用户**共享同一条 thread**，且匿名请求跳过 has_history 检查、每次全量发消息，被 `add_messages` 反复追加。实测：两次请求后线程 8 条消息（无限膨胀 + 跨用户上下文泄漏） | 匿名请求改为每次生成 `ephemeral-{uuid}` 独立 thread（当次有效，不跨请求）；已实测两用户隔离、增量会话不膨胀（4 条 = q1,a,q2,b） |
| 3 | 中 | 前端"清空对话"若只清 messages 不换 `clientSessionId`，后端还留着旧轨迹 → "只发最新一条"的增量逻辑会把新对话接在被清掉的旧上下文后面（前后端记忆错位） | `_handleClear` 清空时同步轮换 `clientSessionId` |
| 4 | 低 | `model_utils.py` 的 `uuid`/`Union` 死导入（thread_id 不再用 uuid4）；测试文件一个未用导入 | 已删除 |

**审查过但判定无需修改的疑点**：
- `pipeline_runner.py` 的 `_is_empty_case(tc: "TestCase")` 字符串前向注解——改动前就存在（HEAD:515），全库无 `get_type_hints` 调用，运行时不求值，安全；
- `AsyncSqliteSaver` 未显式 `setup()`——实测 `from_conn_string` 首次写入自动建表（4096 字节落盘、checkpoint 可读）；
- `adelete_thread` 对 SQLite saver 有效——实测删除后 `aget_tuple` 返回 None，重跑从干净状态开始；
- pipeline `GET /{task_id}/status` 直接返回 task dict，`warnings` 在顶层，前端可读。

---

## 五、这一版刻意**没做**的事（留给后续）

1. 没有把 Store 的长期记忆真正写入（目前只接通道，未定义"什么该记"）。写什么需要配合 P2 检索与 P3 执行反馈一起设计。
2. 没有做 token 预算、api_context 分级截断（P5）。
3. 没有做 RAG 检索（P2）。
4. 没有让 agent 用执行结果自我修复（P3）。
5. pipeline 仍是确定性 DAG，本次只让它**失败可见**，没有改成 agent 循环。
