# B6 质量评估基座：零成本确定性指标 + 质量基线

> 目标：不调 LLM、不需要人工标注，就能算出**可复现**的质量指标，
> 作为"改动到底有没有让用例变好"的判据。

---

## 一、为什么先做这个（决策依据）

在 P0~P5 改了约 1500 行之后，有一个绕不开的问题：
**没有任何东西能证明这些改动有效**。检索召回准不准？执行闭环修没修好？
上下文裁剪是否损害质量？——全都无法回答。

实测数据支撑这个判断（本次采集）：

| 项目 | 值 | 含义 |
|---|---|---|
| 测试用例 | 1574 条 | eval 样本充足 |
| 需求点 | 382（297 有测试点） | 链路完整，可做覆盖度 |
| 带接口引用用例 | 215（189 有场景） | 可做 Schema 一致性 |
| 带断言用例 | 1445 | 可做断言密度 |
| `validation_status` | **全部 None** | 从未验证过，零质量反馈 |
| 执行日志 | 32 条 | 几乎没有真实执行数据 |

**结论**：数据足够、反馈为零 → 先建度量，再谈优化。

---

## 二、做的三件事

### 2.1 修两个让指标失真的真 bug

#### Bug 1：接口引用格式不兼容（严重）

`check_testcase_consistency` 只认 `endpoint_ref`（序号），
但**落库后的用例步骤里存的是 `endpoint_id`（数据库主键）**。

后果（实测 200 条接口用例）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 平均一致率 | **0.388** | **0.762** |
| 完全通过 | **0 条（0%）** | 91%（按旧口径）|

修复：`_resolve_step_endpoint_id()` 兼容三种格式——
`endpoint_id`（主键）/ `endpoint_ref`（序号映射）/ `endpoint_ref`（兜底当主键）。

#### Bug 2：必填字段只看 body 顶层

嵌套 body（如 `{"actionPayload": {"comment": {"value": "x"}}}`）会被误报"缺必填字段"。

修复：`_schema_path_present()` 递归下钻（支持对象/数组嵌套）。

> 修复后复核：266 条 `required_fields_present` 失败中，**0 条是误报**——
> 全是真实的"生成用例漏了接口要求的必填字段"（如 `toAssignee`、`instanceId`）。

### 2.2 新增 `backend/app/services/quality_metrics.py`

纯规则、零 API 成本、确定性输出。指标清单：

| 指标 | 含义 | 判定 |
|---|---|---|
| `completeness_rate` | 完整性 | 有步骤且有效预期 |
| `duplicate_rate` | 重复率 | 标题归一后重复的用例占比 |
| `level_valid_rate` | 等级合法率 | `case_level ∈ {1,2,3,4}` |
| `assertion_coverage_rate` | 断言覆盖率 | 带断言的用例占比 |
| `structure_ok_rate` | 结构规范率 | 步骤有描述/接口引用可解析 |
| `executable_rate` | 可执行率 | 有 `scenario_id` 或 `api_endpoint_id` |
| `consistency_pass_rate` | Schema 一致性 | 复用 `api_consistency`（仅接口用例） |

还提供：
- `save_baseline_snapshot()` / `compare_baselines()`：**前后对比**能力；
- `format_baseline_report()`：可读报告生成。

### 2.3 新增 `backend/scripts/quality_baseline.py`

```bash
cd backend
python scripts/quality_baseline.py                          # 打印基线
python scripts/quality_baseline.py --json out.json          # 导出明细
python scripts/quality_baseline.py --save-snapshot snap.json  # 保存快照
python scripts/quality_baseline.py --compare snap.json      # 与快照对比
```

### 2.4 接入 pipeline（质量信号实时可见）

每次生成完成后自动计算质量指标：
- 写入 `result.quality`；
- 明显不达标时登记 **quality 级警告**（不改变任务状态）：
  - 结构规范率 < 80% → `low_structure_quality`
  - 重复率 > 10% → `high_duplicate_rate`
  - Schema 一致性 < 70% → `low_schema_consistency`
- 前端进度面板展示质量行。

---

## 三、质量基线（2026-11-02，1574 条用例）

| 指标 | 值 |
|---|---|
| 用例总数 | **1574** |
| 完整率（步骤+预期） | 100.0% |
| 重复率 | 4.2%（32 组 / 66 条）|
| 等级合法率 | 100.0% |
| 断言覆盖率 | **13.9%**（均 0.8 条/用例）|
| 结构规范率 | 91.5% |
| 可执行率 | **13.7%** |
| 接口用例一致性（180 条已检）| 平均 **76.2%**，全通过 **21.1%** |

等级分布：`{1: 407, 2: 431, 3: 332, 4: 404}`（四档均衡）

### 基线暴露的三个真实问题

1. **断言覆盖率仅 13.9%** —— 绝大多数用例没有断言，无法自动判定对错。
   这是 B 类用例的固有难点，但 13.9% 明显偏低。
2. **Schema 一致性只有 76.2%**，主因是 **266 条用例漏了必填字段**
   （如 `toAssignee`、`instanceId`、`cmpId`）。生成器没有严格遵循
   `request_schema.required`。
3. **可执行率仅 13.7%** —— 大部分用例没有关联接口或场景，就没法进入 P3 执行闭环。

> 这三条都是**之前完全不知道**的。P0~P5 的改动如果没解决它们，
> 就不能说"让用例变好了"。

---

## 四、测试

新增 2 个测试文件：

- `tests/test_quality_metrics.py`（25）：两个 bug 的回归（`endpoint_id` 格式、
  嵌套必填字段、数组嵌套）+ 指标计算 + 汇总口径 + 报告格式化；
  - 其中专门锁死了"一致性通过率分母必须是已检查数"这个口径 bug；
- `tests/test_quality_metrics_pipeline.py`（4）：pipeline 结果带 quality 指标、
  低质量时登记 quality 级警告且不改变任务状态。

回归：`backend pytest` **1131 passed**；前端 build/lint 通过。

---

## 五、怎么用这个基座

```bash
# 1) 改动前，存一份快照
python scripts/quality_baseline.py --save-snapshot before.json

# 2) 改代码（调提示词 / 换模型 / 改检索策略）

# 3) 重新生成一批用例，再跑一次对比
python scripts/quality_baseline.py --compare before.json
```

输出会给出每个指标的 `old / new / delta / 是否变好`，
`duplicate_rate` 自动按"越低越好"判定。

---

## 六、这一版刻意**没做**的事

1. **没有做 golden set（人工标注）**：先把零成本指标做扎实；
   人工标注 30~50 条留到下一步（那时才知道该标什么）。
2. **没有做 LLM-as-judge**：纯规则，避免引入新的不确定性与成本。
3. **没有把指标接进 CI**：当前是手动脚本；等指标稳定后再考虑卡阈值。
4. **没有做覆盖率度量**（需求点 → 用例的覆盖映射）：
   需要先定义"一个需求点算被覆盖"的判定标准，属独立课题。
5. **没有动断言覆盖率低的问题**：基线已暴露（13.9%），
   但提升它涉及改提示词，需要先用本基座做 A/B 才能证明有效。
