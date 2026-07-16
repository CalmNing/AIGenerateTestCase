# 缓存命中率低的根因排查与提升方案（基于 DeepSeek 官方规则）

> 目标：仅依据 DeepSeek 公开文档的缓存语义，排查本项目生成用例时缓存命中率低的原因，并给出提升方案。
> 范围：纯分析 + 建议，**未改动任何 token / 缓存计算逻辑**。

---

## 一、结论先行（TL;DR）

1. **"HTTP 无状态 / 不在一个会话"不是原因。** DeepSeek 缓存是**按账号（API key）**、**按 prompt 前缀**哈希命中的，与 TCP 连接复用、会话 ID、LangGraph thread_id 全部无关。同账号下无论发多少无状态请求，只要前缀逐字节一致就能命中。
2. 真正导致命中低的是两条结构性问题：
   - **可复用的大块内容（`api_context` 接口规格、技能正文）没有放在「从 token 0 起的可缓存前缀」里**，且 pipeline 各阶段把它放在了 user 消息的不同位置（阶段2 在头部、阶段3 在尾部），**阶段3 那块直接成了不可复用的尾部**。
   - **pipeline 各阶段 system 提示词普遍 < ~1024 token**，低于 V4 可靠命中的实用门槛，且 4 个阶段用 4 套不同 system → **跨阶段/跨点零共享**。
3. 还有一个**测量盲点**：pipeline 路径根本没接缓存埋点回调，你对真正吃 token 的"多循环"路径命中率是**盲的**，"觉得低"部分来自测量本身。

---

## 二、DeepSeek 官方缓存规则（硬约束清单）

来源：`api-docs.deepseek.com/news/news0802`（Context Caching on Disk）+ 定价页。

| 规则 | 说明 | 对本题的含义 |
|------|------|--------------|
| **自动开启** | 缓存对所有用户自动可用，**无需任何代码/接口改动** | 我们之前加的 `enable_prompt_cache` flag 本质是冗余的，开没开都缓 |
| **前缀匹配从 token 0 起** | 只有 `从开头（第 0 个 token）起完全相同的请求` 才算重复；**中间部分匹配不算命中** | 可复用内容必须放在消息最前面，且后面跟可变内容；可变内容绝不能插在前面或中间 |
| **逐字节一致** | 前缀必须完全一致（含空格、顺序、序列化） | system/user 顺序、字段序列化、工具列表顺序一旦变化即断前缀 |
| **存储单位 64 token** | 以 64 token 为最小缓存块 | 小于 64 token 的内容不缓存 |
| **可靠命中实用门槛 ≈ 1024 token（V4）** | 官方仅说"best-effort 不保证 100%"，多家第三方实测 V4 上**前缀需 ≥ 1024 token 才稳定命中** | system 前缀太短（<1024）的阶段基本吃不到缓存 |
| **缓存构建需秒级 + best-effort** | 首个前缀落盘需要时间，并发突发可能都 miss | `asyncio.gather` 同时发 N 个点时，只有首个能"暖"缓存，其余首轮可能 miss |
| **空闲自动清除（TTL）** | 一段时间无重复请求，缓存条目被回收 | 跨长间隔 / 稀疏 / 在不同项目间切换 → 前缀被淘汰 |
| **按账号隔离** | 每个用户的缓存逻辑隔离、互不可见 | 与连接/session 无关；换 API key 即换缓存空间 |
| **监控字段** | `usage.prompt_cache_hit_tokens` / `usage.prompt_cache_miss_tokens` | 我们的埋点字段名正确（已在主路径实现） |
| **计费** | 命中价 ≈ 未命中价的 **1/50**（V4 Flash：命中 ¥0.02/M，未命中 ¥1/M） | 命中率每提升一点，输入成本量级下降 |

> 注：64-token 单位是官方明说的；"≈1024 token 可靠门槛"是第三方在 V4 上的实测经验值，非 DeepSeek 硬性下限，但被广泛验证，应作为设计目标。

---

## 三、项目当前请求结构（实测）

### 主路径 `generate_testcases`（单次生成，已做过前缀拆分）
```
system = selected_prompt(~4000 token，含 base+skill+api_context)
tools  = 静态 function-calling schema
human  = requirement（每次不同，末尾）
```
→ system 前缀已超 1024，**这部分现在本应稳定命中**。

### Pipeline 路径（4 阶段，你说的"多个需求点/测试点循环调用"就在这里）
实测各阶段消息构造（`backend/app/pipeline/`）：

| 阶段 | system 提示词 | 体量(估) | `api_context` 落点 |
|------|---------------|----------|---------------------|
| 1 需求拆分 | `REQUIREMENT_SPLITTER_SYSTEM` / `API_REQUIREMENT_SPLITTER_SYSTEM` | ~500 | 接口测试：未进 system，由调用方拼到 user |
| 2 测试点生成 | `TEST_POINT_GENERATOR_SYSTEM` / `API_TEST_POINT_GENERATOR_SYSTEM` | ~500–800 | **user 消息开头**（见 `test_point_generator.py:62`） |
| 3 用例设计 | `TEST_CASE_DESIGNER_SYSTEM` / `API_TEST_CASE_DESIGNER_SYSTEM` | ~1000–1700 | **user 消息末尾**（见 `test_case_designer.py:59` 的 `API_TEST_CASE_DESIGN_USER`） |
| 4 用例优化 | `TEST_CASE_OPTIMIZER_SYSTEM` | ~750 | 无 |

关键事实：
- **`api_context`（接口 Schema，往往数千~上万 token，是最大的可复用块）在所有 pipeline 阶段都不在 system 消息里。**
- 阶段2 它在 user 头部 → 同一批 N 个点间，前缀 `[system + "API接口信息"头 + api_context]` **理论可共享**（但受并发/门槛影响）。
- 阶段3 它在 user **尾部**（前面是每批不同的 `test_points` JSON）→ **跨批次完全不可共享**，每批都重新付费。
- 4 个阶段用 4 套 system，且 `api_context` 在 user 而非 system → **同一份接口规格在阶段2与阶段3之间无法复用**，每次都从零重算。

---

## 四、命中率低的根因（逐条对照官方规则）

### 🔴 根因 A：最大可复用块 `api_context` 不在可缓存前缀，且阶段3 把它放在尾部
- 违反规则：**"前缀须从 token 0 起 + 中间匹配不算命中"**。
- 阶段3（`test_case_designer.py:59`）把 `api_context` 放在 `test_points`（每批不同）之后 → 它是**尾部可变内容之后的尾块**，永远成不了"从 0 起的前缀" → **M/3 批次之间零命中**。这是接口测试命中率低的头号原因。
- 阶段2 虽在头部，但前面还跟着一个 <1024 的短 system，且 `asyncio.gather` 并发把 N 个点同时打出，首个暖缓存、其余首轮 miss。

### 🔴 根因 B：pipeline 各阶段 system 普遍 < 1024 token，跨阶段零共享
- 违反规则：**"可靠命中门槛 ≈ 1024 token"**。
- 阶段1/2/4 的 system 仅 ~500–800 token，低于门槛 → 基本不缓存。
- 4 套不同 system → 即便 api_context 进了 system，不同阶段的"system 前缀"也不同，无法跨阶段共享同一份接口规格。
- 功能性测试（无 api_context）时，system 更短 → 几乎永远 miss。

### 🟠 根因 C：主路径与 pipeline 路径的 `api_context` 布局不一致，缓存被碎片化
- 主路径把 `api_context` 放进 system；pipeline 放进 user。同一项目的同一份接口规格会被缓存成**两个不同前缀** → 互不命中，白白浪费缓存空间。

### 🟠 根因 D：TTL + best-effort + 并发突发导致"首轮必 miss"
- 违反规则：**"缓存构建需秒级，空闲清除"**。
- 单次 pipeline 运行中，阶段2 的 N 个点是 `asyncio.gather` 同时发出的，第一个请求刚触发缓存构建，其余很可能赶不上 → 首轮大面积 miss。
- 若生成请求稀疏、或在功能/接口/不同项目间切换，前缀单元来不及稳定形成即被淘汰。

### 🟡 根因 E：测量盲点——pipeline 路径没接缓存埋点
- `test_point_generator.py:77` 与 `test_case_designer.py:80` 调用 `_structured_output_robust` 时**未传 `config={"callbacks":[_diagnostic_cb]}`** → `[缓存命中]` 日志从不打印。
- 你对这条**真正吃 token** 的多循环路径命中率是完全看不见的；你"觉得低"很可能部分来自只看得到主路径日志、或凭账单臆测。

### （已排除的假设）
- ❌ "HTTP 无状态 / 不在一个会话" → 与 DeepSeek 前缀缓存机制无关，排除。
- ❌ "缓存开关没生效" → 上一轮已用运行时探针确认 `enable_prompt_cache` 送达、tools schema 稳定、配置默认自动开启，排除。
- ❌ "system 里有动态内容（时间戳/uuid）" → `prompts.py` 三段均为写死常量，排除。

---

## 五、提升命中率的解决方案（按收益排序）

> 标注：`[代码]` 需改 `backend/`；`[用法]` 仅靠调用方式/配置；`[观测]` 仅用于看清真实命中率。

### ✅ 方案 1（最大赢面，[代码]）：把 `api_context` 提进 system 前缀，且所有阶段布局一致
- **核心**：让每个 pipeline 阶段都 `system = 静态base + 方法论few-shot + api_context`，`user = 仅可变内容（每点/每批内容）`。
- 这样阶段2 的 N 个点和阶段3 的 M/3 批次、以及跨阶段，都共享**同一个超长（≥1024）且逐字节一致的前缀** → 稳定命中。
- 阶段3 必须把 `api_context` 从 user 尾部移到 system 前缀（修复根因 A）。
- 同时让主路径与 pipeline 路径用**同一套 system 模板**，消除根因 C 的碎片。

### ✅ 方案 2（[代码]）：给每个阶段补足 ≥1024 token 的静态前缀
- 对功能性测试（无 api_context），短 system 不缓存 → 在 system 里加一段**所有阶段共享的静态「测试方法论 + 约束 + few-shot 示例」块**（这也能顺带提升输出一致性）。
- 这样功能性测试的 system 前缀也跨过 1024 门槛，被缓存命中。

### ✅ 方案 3（[用法]）：保持"可变内容一律放末尾"
- 任何动态内容（每点 content、每批 test_points、requirement）**绝不可插在 `api_context`/静态块之前或中间**，否则破坏从 0 起的前缀。
- 阶段3 现状（test_points 在 api_context 前）正是反例，需随方案 1 一并修正。

### ✅ 方案 4（[用法]）：先"暖"缓存再并发
- 阶段2 的 N 个点当前是 `asyncio.gather` 同时发。可在每批开头**先串行发 1 个点暖缓存**，其余再并发；或接受"首轮必 miss、重复运行才命中"的现实，把优化目标放在**重复运行/同项目连续生成**上。

### ✅ 方案 5（[代码]）：统一前缀布局，消除主路径/pipeline 双前缀
- 主路径已把 `api_context` 放进 system；让 pipeline 也用 `system = base + skill + api_context` 的同一构造（即复用上一轮加的 `_build_generation_messages` 思路），使同一项目无论走哪条路径都命中同一个缓存条目。

### ✅ 方案 6（[观测]）：给 pipeline 接上 `_TokenUsageCallback`
- 在 `test_point_generator.py:77`、`test_case_designer.py:80`、以及阶段1/4 的调用处补上 `config={"callbacks":[_diagnostic_cb]}`，关闭测量盲点。否则方案 1–5 的效果你永远看不见、也无法验证。

### ⚠️ 方案 7（[代码]，低优先级）：MCP 工具列表确定性排序
- 若用 agent/MCP 路径，工具名+描述被拼进 system。按 `t.name` 排序（上一轮主路径已做），保证相同工具集时前缀一致；不同工具集仍会分前缀。

---

## 六、预期收益与理论上限

- **接口测试（同项目反复生成 / pipeline 重复运行）**：前缀从 ~500 token 提升到 `base + 方法论 + api_context`（数千~上万 token）→ 阶段2 的 N 点、阶段3 的 M/3 批、跨 4 阶段全部共享 → 命中率可显著上升（具体数值取决于 `api_context` 占比，接口规格越大收益越高）。
- **功能性测试（无 api_context）**：靠方案 2 的静态方法论块跨过 1024 门槛 → 同类型连续生成可命中，但自由文本需求本身可变，**命中率上限 = 前缀token / 总输入token**，不可能 100%。
- **理论封顶**：DeepSeek 只缓存前缀，每次 `requirement`/每点内容不同，故命中率永远 < 100%；优化目标是把"可复用部分"尽量前置并做大。

---

## 七、怎么验证（看清真实命中率）

1. 先执行**方案 6**，让 pipeline 也打印 `[缓存命中]`（含 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 及命中率）。
2. 用**同一项目、同一接口**连续跑两次 pipeline：第一次是"写缓存"（hit 低），第二次应明显上升——这是判断方案 1–5 是否生效的直接证据。
3. 在日志里对比 `prompt_cache_hit_tokens` 随连续同前缀请求的增长；若第二次仍低，说明前缀仍有不一致（回到第四节逐条查）。
4. 账单侧核对 `prompt_cache_hit_tokens × ¥0.02/M` vs `prompt_cache_miss_tokens × ¥1/M` 占比变化。

---

## 八、一句话总结

命中低不是"HTTP/会话"问题，而是**可复用大块（`api_context`/技能）没放在从 token 0 起、且 ≥1024 token 的稳定 system 前缀里**，加上 pipeline 4 阶段短 system、跨阶段零共享、以及 pipeline 无埋点导致的"看不见"。按方案 1+2+5 重构前缀布局、方案 6 补埋点，即可结构性提升命中率。
