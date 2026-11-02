# B6 后续：必填字段确定性补全（修复头号质量问题）

> 质量基线实测出 442 处必填字段缺失（cmpId 214 次等），导致接口用例与
> Schema 一致性只有 76.2%。本步用**确定性代码**兜底，而不是继续靠提示词。

---

## 效果（基线对比，一键可复现）

| 指标 | 修复前 | 修复后 | Δ |
|---|---|---|---|
| consistency_pass_rate | 0.7616 | **0.9249** | **+0.1633 ↑** |
| consistency_passed_rate（全通过率）| 0.2111 | **0.6778** | **+0.4667 ↑** |
| required_fields_present 失败 | 266 条 | **0 条** | 全部消除 |

复现方式：

```bash
cd backend
python scripts/quality_baseline.py --compare ../docs/research/quality-baseline-snapshot.json
```

---

## 修复方式

### 1. 新增 `backend/app/services/required_field_filler.py`

- `fill_missing_required(schema, body)`：按 `schema.required` **递归**补齐缺失字段
  （对象/数组嵌套都支持）；
- 值由既有的 `swagger_parser._schema_example` 派生 → **类型合法、枚举合法、
  尊重 example/default/format/min/max**，失败时用类型默认值兜底；
- **幂等**：只补缺失，绝不覆盖已有值；
- `dependency_candidates()`：补出来的 `xxxId` 类字段单独标注为"依赖候选"
  （理想值应来自上游接口响应，当前是占位值）。

### 2. 接入两个生成路径

| 路径 | 位置 | 行为 |
|---|---|---|
| pipeline 阶段 3 | `pipeline_runner.py` 落库前 | 步骤归一化后立即补全；补了多少计数进 `result.required_fields_autofilled`，并登记 quality 级警告 |
| 单次生成 | `model_utils.py` 解析后 | Pydantic 步骤转 dict 后补全（与 pipeline 对齐） |

> 关键取舍：占位值（如 `"string"`、`1.0`）让请求**结构合法**，
> 但语义上仍需替换——已有依赖推断机制（`infer_step_dependencies`）
> 恰好把 `"string"` 视为可填充占位符，会尝试用上游响应替换它，两个机制自然衔接。

### 3. 存量数据修复脚本 `scripts/fix_required_fields.py`

```bash
cd backend
python scripts/fix_required_fields.py            # dry-run，只报告
python scripts/fix_required_fields.py --apply    # 实际写入
```

- 默认 dry-run，必须显式 `--apply`；
- 只补缺失、不覆盖；写库前有备份（`testcases.db.bak-before-fieldfix`）；
- 实测：修复 66 条用例 / 442 个字段，依赖候选 `cmpId`(214)、`id`(47)、`resId`(26)。

---

## 剩余问题（基线仍暴露）

| 问题 | 量 | 性质 | 建议 |
|---|---|---|---|
| 悬空接口引用（endpoint=51 已被删除） | 51 条中的 40 处步骤 | **数据完整性**，不是生成质量 | 需产品决策：清理引用还是恢复接口记录；**不自动删用户数据** |
| `no_extra_body_fields` | 22 | 真实质量问题 | 生成器加了 schema 外字段 |
| `assertion_fields_exist` | 13 | 真实质量问题 | 断言引用了响应 schema 没有的字段 |
| 断言覆盖率 13.9% | 全局 | 结构性缺失 | 需改提示词 + 用基线做 A/B |

---

## 测试

- `tests/test_required_field_filler.py`（20）：幂等、不覆盖、类型/枚举/example 派生、
  嵌套对象与数组递归、body 缺失时新建、序号/主键双格式、依赖候选判定；
  - 测试中锁死了两个实现细节：数组元素必须递归补（此前漏掉）、
    补出来的 body 统一存 JSON 字符串（与存量格式一致）；
- `tests/test_quality_metrics_pipeline.py`（4）：pipeline 集成；
- 回归：`backend pytest` **1151 passed**；前端 build/lint 通过。

---

## 剩余动作（按价值排序）

1. **断言覆盖率 13.9% → 提升**：改提示词要求每条用例至少 1 条断言，
   用 `--save-snapshot`/`--compare` 做 A/B 量化；
2. **悬空引用的产品决策**：删除接口时是否级联清理用例引用（需产品确认）；
3. **golden set 人工标注**：等确定性指标稳定后，挑 30~50 条做人工标注，
   覆盖规则指标测不到的维度（步骤可操作性、预期可验证性）。