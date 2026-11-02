# P3 执行反馈闭环：生成 → 执行 → 按失败修复

> 对应 [docs/research/agent-fit-report.md](docs/research/agent-fit-report.md) 的 P3。
> 目标：把"执行结果"从**只有写侧**变成**可回灌的反馈信号**——
> 这是把 agent 适配性判据 3（可验证反馈信号）从"部分契合"推到"契合"的唯一路径。

---

## 一、改造前的问题

| # | 问题 | 证据 |
|---|---|---|
| 1 | 执行能力**早就齐全**（`scenario_runner.run_scenario` 真发 HTTP + `assertion_engine` 逐条判定断言），但**生成链路从不调用** | `app/pipeline/` grep `scenario_runner|api_test_tool` 命中 **0** |
| 2 | `validation_status` **只有写侧没有读侧**：写入 `testcase.py:125`，读取仅回显前端，生成侧 0 引用 | 全库 grep |
| 3 | LLM 优化阶段（阶段 4）是**纯文本重写**，没有"实际发生了什么"的输入 | `test_case_optimizer.py:44` |
| 4 | 接口用例做错了（断言写错/参数写错），系统**无从发现**，只能等用户手工点"验证" | 同上 |

结论：项目里最值钱的能力（真实执行 + 客观 oracle）**没有接入生成闭环**。

---

## 二、实现

### 2.1 新增 `backend/app/services/case_verifier.py`

| 能力 | 说明 |
|---|---|
| `verify_testcase()` | 执行单条接口用例并**归因**失败；任何异常都吞掉转成 error 结果，不打断生成 |
| `verify_batch()` | 批量执行并汇总 |
| `build_failure_feedback()` | 把失败转成**可直接喂给 LLM 的修复指令** |
| `summarize_verification()` | 汇总统计（通过率/失败数/可修复数） |

**失败归因分类**（决定该失败是否值得让 LLM 修）：

| cause | 含义 | 可修复 |
|---|---|---|
| `assertion_mismatch` | 断言不匹配（预期≠实际） | ✅ |
| `unexpected_status` | 状态码不符 | ✅ |
| `invalid_request` | 4xx，请求构造问题 | ✅ |
| `connection_error` / `timeout` | 连不上/超时 → **环境问题** | ❌ |
| `no_scenario` / `no_project` / `readonly_skipped` | 跳过 | — |

> 关键设计：**环境类失败不进修复指令**。否则 LLM 会反复尝试修一个它根本控制不了的问题
> （连不上服务），白白消耗轮次。

### 2.2 优化器支持接收失败反馈

`TestCaseOptimizer.optimize(..., failure_feedback=...)`：非空时把失败明细追加到
**user 消息末尾**（不破坏 system 前缀的缓存命中），让模型按**实际接口行为**修正断言/参数，
而不是凭空"优化"。

### 2.3 pipeline 阶段 4 之后的闭环

`PipelineRunner._run_verification_loop()`：

```
执行（有 scenario 的接口用例）
  → 归因失败
  → 登记 warning（P0 联动：失败可见）
  → 回写用例 validation_status / status / validation_detail
  → 生成修复指令（只含可修复失败）
  → 喂回优化器重修一轮
  → 重新执行复验
  → 统计写入 result.verification
```

### 2.4 失败可见性联动（P0）

- 执行失败 → 登记 warning `verification_failed` → 任务状态降为 `partial`；
- 重修复批次失败 → 登记 warning `verification_repair_failed`；
- 验证结果回写用例，前端可查（用例详情 + 执行日志）。

### 2.5 安全防护（真实请求，必须有）

| 防护 | 配置项 | 默认 | 说明 |
|---|---|---|---|
| 总开关 | `verification_enabled` | **False** | 关闭时完全不执行，避免在环境未就绪时误发请求 |
| 只读保护 | `verification_read_only` | **True** | 只执行 GET/HEAD/OPTIONS；POST/PUT/PATCH/DELETE 一律跳过 |
| 重修轮数 | `verification_max_rounds` | 1 | 上限 3，防无限循环 |

> 只读保护是**必须**的：`run_scenario` 会真实发 DELETE/PUT 等写请求，
> 默认放开会真实改动被测系统的数据。

---

## 三、验证结果

集成测试（`tests/test_p3_verification_loop.py`）用 mock 执行器跑通完整回路：

```
首轮：执行失败（jsonpath_equals 期望 0 实际 500）
  → warning verification_failed，任务 partial
  → 失败明细回灌优化器（断言 "执行验证失败明细" 出现在 feedback 里）
  → 重修
复验：执行通过
  → 用例 validation_status = passed、status = PASSED
  → result.verification = {passed:1, failed:1, repair_rounds:1, after_repair:{passed:1}}
```

---

## 四、自检发现并修复的问题

| # | 严重度 | 问题 | 修复 |
|---|---|---|---|
| 1 | **严重** | **真实请求无保护**：闭环会向被测接口发真实写请求（POST/PUT/DELETE），可能改动被测系统数据 | 新增 `verification_read_only`（默认 True）+ `_is_read_only_scenario()`：只读方法才执行；step 无 method 时回查 endpoint；查不到时**保守视为只读** |
| 2 | 中 | **总开关缺失**：无法在环境未就绪时临时关闭 | 新增 `verification_enabled`（默认 False） |
| 3 | 中 | **pass_rate 分母错误**：把 skipped（没场景/环境未就绪）算作失败，低估真实通过率 | 改为按 `executed` 计算 |
| 4 | 中 | **失败明细可能被"环境问题"污染**：连不上也进修复指令，LLM 反复修不可控问题 | 归因分类 + `_ENVIRONMENT_CAUSES` 过滤，只把可修复失败写进反馈 |
| 5 | 低 | 测试用例步骤不带 endpoint 引用 → pipeline 不建 ApiScenario → 闭环静默跳过（易误判为"功能正常"） | 修正测试数据；并在集成测试中断言场景确实被创建 |

---

## 五、测试

新增 30 个用例（3 个文件），全部不触网：

- `tests/test_case_verifier.py`（19）：失败归因分类、修复指令生成（含"环境失败被排除"）、
  汇总统计、单条验证（通过/失败/异常/跳过）；
- `tests/test_p3_verification_loop.py`（4）：开关关闭不执行、执行失败可见、
  **失败→重修→复验通过**的完整回路、验证结果写回用例；
- `tests/test_p3_readonly_guard.py`（7）：只读方法判定、写方法被跳过、
  **只读模式下不得发出任何写请求**、关闭只读后可执行。

回归：`backend pytest` **1071 passed**；前端 build/lint 通过（新增验证通过率展示）。

---

## 六、这一版刻意**没做**的事

1. **没有做覆盖率反馈**：目前只有"断言是否通过"，没有"覆盖了哪些需求点/分支"。
   覆盖率信号需要 B6 的 eval 集与需求点映射，属另一块工作。
2. **没有让 agent 自主决定何时停止**：重修轮数由配置固定，不做"何时算修好了"的自主判断。
3. **没有覆盖功能测试（非接口）的执行验证**：功能测试用例没有可执行的 oracle
   （这是 B 类任务的固有难点），只能靠人工评审。
4. **没有做 mutation testing 作为奖励信号**：外部有成熟实践（Meta ACH），
   但需要代码级变异注入，与本项目的"步骤+预期"产物形态不匹配，投入产出比不明。
5. **默认关闭**：要让这条闭环真正在生产跑起来，需要用户显式开启并确认执行环境可写/可读。
