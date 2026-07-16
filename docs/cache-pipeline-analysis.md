# 缓存命中率低：根因分析（pipeline 路径）

> 用户假设："是不是因为 HTTP 调用方式，多个需求点/测试点多次循环调用，不一个会话，导致缓存命中低？"
> 结论：**机制判断有误；真正的根因是 4 阶段 pipeline 把一次生成拆成几十次独立调用，而每个阶段的可缓存前缀（system 提示词）太短，低于 DeepSeek 可靠缓存门槛（≈1024 token）。**

## 1. 先纠正机制：HTTP / "一个会话" 不是原因

DeepSeek 的前缀缓存（disk KV cache）是：

- **服务端、按账号（API key）隔离**，按「token 0 起的 prompt 前缀」做 hash 命中；
- **与 TCP 连接是否复用、是否存在"会话"无关**——同账号下无论发 1000 次无状态 HTTP 请求，只要前缀逐字节一致就能命中；
- `with_structured_output` 之后的 `extra_body`（`thinking`、`enable_prompt_cache`）会保留，调用方式（LangChain / 直连）不影响缓存；
- agent 路径里 `thread_id = f"{session_id}-{uuid4()}"`（LangGraph 概念）对 DeepSeek 前缀缓存**零影响**。

所以"HTTP 无状态 / 没有用单一会话"这个方向是错的，不要在这上面花力气。

## 2. 真正的根因：pipeline 前缀太短，没过缓存门槛

一次"生成"在主路径 `generate_testcases` 只有 1 次 LLM 调用（之前已把 `system_text` 拼成 4000+ token，能稳定命中）。
但 **4 阶段 pipeline（`pipeline_runner.py`）才是真正吃 token 的路径**，它把一次生成拆成几十次独立调用：

| 阶段 | 调用次数 | system 提示词 | 估算 token | 是否过 1024 门槛 |
|------|-----------|---------------|-------------|----------------|
| 1 需求拆分 | 1 | `REQUIREMENT_SPLITTER_SYSTEM` | ~400–600 | 否 |
| 2 测试点生成 | **N（每需求点 1 次，并行）** | `TEST_POINT_GENERATOR_SYSTEM` / `API_TEST_POINT_GENERATOR_SYSTEM` | ~600–800 | 否 |
| 3 用例设计 | **M/3（每批 3 测试点）** | `TEST_CASE_DESIGNER_SYSTEM` / `API_TEST_CASE_DESIGNER_SYSTEM` | ~600–800 | 否 |
| 4 用例优化 | 若干批次 | `TEST_CASE_BATCH_OPTIMIZE_SYSTEM` | ~600–800 | 否 |

**这些 system 提示词全部 < 1024 token。** 而 DeepSeek 的可靠缓存门槛实测在 **≈1024 token**（存储单元 64 token，但短前缀命中不可靠、经常为 0）。

→ 因此阶段 2/3/4 每发一次请求，前缀都太短、压根没被缓存，**每次都是全价重算**。这才是"命中率可怜"的真凶。你之前觉得"开了缓存还是低"，八成是被这条 pipeline 拖的——它比单路径生成更频繁地被打，却几乎享受不到缓存。

> 注：阶段内 system 提示词本身是相同的（N 次测试点生成共用同一份 `TEST_POINT_GENERATOR_SYSTEM`），理论上"应该"能命中 system 前缀——但因为它只有几百 token、没过门槛，DeepSeek 直接不缓存。

## 3. 次要原因：前缀碎片化 + 并行竞态 + 静态内容放错位置

- **跨阶段零共享**：4 个阶段用 4 套不同的 system 提示词，彼此前缀不同，无法复用。
- **阶段 2 的 N 次调用是 `asyncio.gather` 同时发出**（`pipeline_runner.py:512`）。DeepSeek 对前缀 P 的缓存要在"第一个带 P 前缀的请求完成并落盘"之后才形成；N 个请求同发，第一个落盘后其余在途的可能赶不上。串行/轻微错开会更好，但杠杆仍是前缀长度。
- **最大的静态块 `api_context`（接口 Schema）被放进 user 消息**，且阶段 2 每个需求点的 user 消息都不同（`test_point_generator.py:62-67`）。这块大静态内容既没进可缓存前缀，又因每点不同而无法复用——双重浪费。
- 阶段 3 的 user 是 `test_points_json`（每批不同），同理只有前缀可被缓存。

## 4. 测量盲点（很可能让你"觉得低"的原因之一）

`[缓存命中]` 日志只在**主路径** `generate_testcases` 打印——因为它传了 `config={"callbacks": [_diagnostic_cb]}`。

而 pipeline 的两个调用处：
- `test_point_generator.py:77` → `_structured_output_robust(self.model, TestPointGenerationResult, messages)` **无 callbacks**
- `test_case_designer.py:80` → `_structured_output_robust(self.model, schema, messages)` **无 callbacks**

→ **pipeline 路径完全不触发 `_TokenUsageCallback`**，你对其命中率是"盲"的。你所看到的命中率数字只来自主路径，可能反而高估了 pipeline 的真实情况（或反过来，因埋点字段读取问题而误报 0）。

> 补充风险：埋点读 `response.llm_output['token_usage']` 的 `prompt_cache_hit_tokens`。langchain_openai 是否透传 DeepSeek 该字段取决于版本——若 SDK 只暴露 `prompt_tokens_details.cached_tokens` 而回调没读到，会误报 0。需实测确认。

## 5. 杠杆（按收益排序）

1. **A. 给每个 pipeline 阶段塞一个 ≥1024 token 的共享静态前缀**
   把接口 Schema / `api_context`、方法论 few-shot、项目约定放进 system 前缀，且**所有同阶段调用逐字节一致**。这样阶段 2 的 N 次、阶段 3 的 M/3 次调用，前缀都 >1024 且相同 → 稳定命中。这是最大的赢面。
2. **B. `api_context` 从 user 消息移到共享 system 前缀**（主路径已做，pipeline 还没做）。
3. **C. 给 pipeline 的 `TestPointGenerator` / `TcDesigner` 也接 `_TokenUsageCallback`**，否则永远看不到真实命中率。
4. **D. 实测一次 pipeline 调用的 usage 字段**，确认埋点读到的是真值而非 0。

## 6. 一句话结论

别在"复用连接 / 单会话"上花力气。**真正要动的是：前缀长度 ≥1024 + 跨调用逐字节一致 + 静态内容前置。** 当前 pipeline 三个条件都不满足，所以命中率才低得可怜。
