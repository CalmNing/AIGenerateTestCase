# P2 检索（RAG 第一步）：FTS5 + 中文二元分词 + 相关性召回

> 对应 [docs/research/agent-fit-report.md](docs/research/agent-fit-report.md) 的 P2。
> 目标：把"最近 20 条时间序"换成"按相关性召回"，并用真实数据替代 skills 里那个**后端并不存在**的工具。

---

## 一、改造前的问题

| # | 问题 | 证据 |
|---|---|---|
| 1 | 唯一的"检索"是 `_build_history_context`：**最近 20 条 + 精确 module 过滤 + 无字符截断** | `utils/helpers.py:736`（改造前） |
| 2 | 召回归因于"最近"而非"相关"：模块积累到几百条后，最相关的早期需求会被挤掉 | 同上 `limit(_HISTORY_PROMPT_LIMIT)` |
| 3 | 跨模块/跨会话经验完全不可复用（只按 module_id 过滤） | 同上 |
| 4 | 落在 **human 消息**（动态段）→ 100% 不命中前缀缓存 | `model_utils.py:1138`（改造前） |
| 5 | **pipeline 完全不读历史上下文**（只有保存侧） | `app/pipeline/` grep |
| 6 | 全库无检索依赖，无 embedding/向量/BM25 | `requirements.txt` |
| 7 | skills 教模型"调用 `get_existing_coverage`"，但该工具**后端 0 实现** | `skills/coverage-analysis/SKILL.md:8,24` |

---

## 二、实现

### 2.1 选型：SQLite FTS5 + 中文二元分词（零新依赖）

SQLite 3.49.1 内置 FTS5，无需新增依赖。但：

| 分词器 | 中文表现 | 结论 |
|---|---|---|
| `unicode61`（默认） | **不切中文**，"账号"命中 0 | 不可用 |
| `trigram` | 只支持 ≥3 字符，"账号"（2 字）命中 0 | 不可用 |

**方案**：中文按**二元组（bigram）**预切分 + ASCII 按词切分，索引仍用 `unicode61`：

```
tokenize_cjk("用户登录")   -> ["用户", "户登", "登录"]
tokenize_cjk("导出 Excel") -> ["导出", "文件", "excel"]
```

召回时把 query 的二元组用 `OR` 连成 FTS5 MATCH 表达式，BM25 排序。
实测中文召回、模块过滤、跨模块召回、同义词扩展全部正常。

### 2.2 新增 `backend/utils/retrieval.py`

| 组件 | 说明 |
|---|---|
| `tokenize_cjk()` | 中文二元组 + ASCII 词切分 |
| `build_match_query()` | 构造 FTS5 MATCH 表达式（每个 token 加引号，防语法错误） |
| `FtsIndex` | 索引封装：`ensure_index/upsert/delete/sync_from_source/indexed_count/search/set_terms/get_terms` |
| `retrieve_history()` | 对外主入口，**任何异常都降级为空列表**，绝不阻断生成 |
| `format_retrieved_context()` | 召回结果 → 可注入 prompt 的文本（带字符截断） |

表结构（独立索引文件 `backend/data/retrieval_index.db`）：

```
retrieval_fts   -- FTS5(tokens, content)
retrieval_meta  -- rowid → module_id / user_id / session_id / created_at
retrieval_term  -- 同义词词典
```

### 2.3 接入单次生成路径

- `_build_history_context(db, module_id, query=..., user_id=..., session_id=...)`：
  - 有 query → **先走相关性召回**；
  - 召回不可用/无命中 → **回退时间序**（行为与改造前完全一致）；
  - 无论哪条路径，都做**字符级截断**（`_HISTORY_CONTENT_MAX_CHARS=600`，旧实现无截断）。
- 新增 `_build_coverage_summary()`：**「本模块已有测试用例（已覆盖清单）」**——
  标题 + 等级 + 验证要点摘要，用于避免重复设计、识别覆盖缺口。
- `_build_generation_messages(..., coverage_summary=...)`：已覆盖清单放进 **system 前缀**
  （按模块内容稳定 → 可命中前缀缓存），不是 human 消息。

### 2.4 接入 pipeline 主路径（前端主按钮走的就是它）

- `RequirementSplitter.analyze(..., coverage_summary=...)` → `build_stage_system(..., coverage_summary=...)`，
  注入 **system 前缀**（阶段 1 就避开已覆盖内容）；
- `_run_pipeline` 阶段 1 组装：`相关历史需求（召回） + 已覆盖清单`。

### 2.5 索引同步

| 时机 | 动作 |
|---|---|
| 写历史需求（`_upsert_history_prompt`） | 增量 `upsert` 进索引 |
| 删历史需求（`DELETE /api/history-prompts/{id}`） | 同步 `idx.delete()`，避免召回已删内容 |
| 应用启动（`create_db_and_tables`） | 条目数与源表不一致时**全量重建**；一致则跳过（避免每次启动 O(N)） |

### 2.6 修正 skills（消除"不存在的工具"）

- `coverage-analysis/SKILL.md`：`allowed-tools: get_existing_coverage` 与
  "Call `get_existing_coverage`" 全部改为读取 prompt 中的**「已覆盖清单」**；
- `testcase-design/SKILL.md`：同步去掉 `allowed-tools`，改为"若 prompt 含已覆盖清单则用于识别缺口"。
- 全库已无 `get_existing_coverage` 引用。

### 2.7 索引与业务库隔离

FTS5 虚表会生成 **6 张 shadow 表**（`*_data/*_idx/*_content/*_docsize/*_config/*_docsize`）。
最初把索引建在 `testcases.db` 里，实测已污染业务库（8 张表），
并与 alembic 迁移、DB 结构巡检相互干扰。

→ 改为**独立文件** `backend/data/retrieval_index.db`（与业务库同目录，便于容器挂载），
并已清理业务库里的残留表；`_rebuild_retrieval_index` 改为走独立索引。

---

## 三、自检发现并修复的问题

| # | 严重度 | 问题 | 修复 |
|---|---|---|---|
| 1 | **严重** | **并发写丢数据**：`sqlite3.Connection` 事务状态是连接级的，多线程共享时 A 的 BEGIN 会让 B 的 COMMIT 报 "cannot commit - no transaction is active"，实测 400 条只写入 283 条 | `FtsIndex` 加 `threading.RLock`，所有连接操作在锁内完成；修复后 400/400 完整写入 |
| 2 | **严重** | **污染业务库**：FTS5 的 6 张 shadow 表建进了 `testcases.db`，与 alembic 迁移/DB 巡检相互干扰 | 索引改为独立文件 `retrieval_index.db`；已清理业务库残留表 |
| 3 | 中 | **bm25 解析不健壮**：并发瞬时状态下 bm25 可能返回 NULL 或短行 → `float(None)` 抛 TypeError，导致整次检索失败 | 逐行校验长度与可转性，跳过异常行而非整批失败 |
| 4 | 中 | **删除历史后索引残留**：删除 `HistoryPrompt` 未同步索引 → 召回会返回已删需求 | 删除路由同步 `idx.delete()` |
| 5 | 低 | **启动每次全量重建**：O(N) 清空重建 | 仅当索引条目数与源表不一致时才重建 |
| 6 | 低 | 同义词扩展的 `build_match_query` 对 list 做了脆弱的 `isinstance(item, list)` 判断 | 改为显式展平列表 |

---

## 四、测试

新增 40 个用例（3 个文件），全部不触网、不调真实 LLM：

- `tests/test_retrieval.py`（25）：分词、MATCH 构造、索引增删改、同步、模块/用户过滤、
  同义词扩展、格式化截断、降级路径；
- `tests/test_retrieval_integration.py`（9）：已覆盖清单内容、检索优先于时间序、
  **检索异常时回退时间序**、单条截断、写入即索引；
- `tests/test_retrieval_concurrency.py`（6）：并发写不丢数据、读写并发不抛错、
  **索引必须与业务库分离**、业务库无残留检索表。

回归：`backend pytest` **1041 passed**；前端 build/lint 未受影响（本步无前端改动）。

---

## 五、这一版刻意**没做**的事

1. **没有做向量检索**：先用 FTS5 + 同义词词典把"相关而非最近"落地；
   等有 eval 集证明召回不足再引入 embedding（届时是明确的范围扩张）。
2. **没有做语义重排（cross-encoder）**：当前 BM25 + 二元组已能满足"相关而非最近"的目标。
3. **没有把召回结果用于去重**：去重仍是生成后的 LLM 语义判断（P2 只提供"已覆盖清单"给模型参考）。
4. **没有把检索接进 chat**：chat 是自由问答，检索什么目前没有明确语义边界。
5. **没有做 token 预算器**（P5）：本步只解决了"召回什么"，还没解决"注入多少"。

> 提醒：`docs/research/agent-fit-report.md` 记录的外部量化（相似用例 few-shot 使编译成功率
> +25.97% / 通过率 +28.22% / 覆盖率 +45.99%）来自 **A 类任务（生成测试代码）**。
> 本项目是 B 类（步骤+预期），**没有可信的公开量化**；本步属于"方向正确的工程改进"，
> 其收益需要用本项目自己的 eval 集（B6）来度量。
