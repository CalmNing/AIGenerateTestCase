# 项目全面优化方案设计文档

日期：2026-06-30
范围：后端（FastAPI）+ 前端（React 18 + TypeScript）
策略：先建测试安全网 → 后端消除重复 → 后端性能与大文件拆分 → 前端优化

---

## 背景与目标

AI 测试用例生成工具经过多轮迭代，积累了可维护性与性能债务。本次优化目标：

1. 消除后端严重的代码重复（ChatOpenAI 初始化 7+ 份拷贝、API 上下文构建/MCP 加载/文档预取各 2-4 份）
2. 拆分职责混杂的超大文件（`api_test_tool.py` 2006 行、`generate_testcases` 单函数 600 行、前端 `ApiScenarioTestTool.tsx` 2731 行）
3. 修复明确的性能点（列表接口 5 次 count 查询、pipeline 串行调用 LLM）
4. 在重构前建立测试安全网，保证行为不回归

### 事实勘误

CLAUDE.md 声称 `App.tsx` 有 "62K+ lines"，**实际仅 2054 行**，整个 `frontend/src` 共约 15,379 行。真正最大的前端文件是 `components/api/ApiScenarioTestTool.tsx`（2731 行）。CLAUDE.md 还声称使用了 zustand，实际 `src/` 下零引用。这两处文档失真将在本次优化中一并修正。

---

## 总体阶段划分

按"风险低→高、收益快→慢"排序，分 4 个阶段。每阶段完成后跑测试 + 导入检查 + 手动验证一次生成流程，确认行为不变再进入下一阶段。

| 阶段 | 内容 | 风险 |
|------|------|------|
| 阶段 0 | 测试安全网 | 低（新增，不改现有） |
| 阶段 1 | 后端消除重复 | 低 |
| 阶段 2 | 后端性能 + 大文件拆分 | 中 |
| 阶段 3 | 前端优化 | 中 |

---

## 阶段 0：测试安全网

### 目的
后端目前零自动化测试。在重构前为"将要改动的区域"补最小化测试，重构前后跑同一套测试对比行为。目标不是高覆盖率，而是覆盖重构热点。

### 结构
新建 `backend/tests/`：

```
backend/tests/
├── conftest.py              # pytest fixtures（内存 SQLite session）
├── test_helpers.py          # JSON 修复、_normalize_testcase_dict、级别标准化
├── test_pipeline_schemas.py # ApiTestCaseOut/TestCaseOut schema 解析与校验
├── test_api_context.py      # build_api_context / parse_endpoint_ids
└── test_dependency_infer.py # infer_endpoint_dependencies（内存 DB 造数据）
```

### 约束
- 只测纯函数或接近纯函数，不调用真实 LLM、不连真实数据库（用内存 SQLite）
- `pytest` 加入 `requirements.txt`
- 运行命令写入 CLAUDE.md（`cd backend && pytest`）

---

## 阶段 1：后端消除重复

### 1.1 LLM 模型工厂
新建 `backend/utils/llm_factory.py`：

```python
def build_chat_model(api_key, api_base_url=None, api_model=None,
                     api_proxy_url=None, **overrides) -> ChatOpenAI:
    """统一的 ChatOpenAI 构造，消除 7+ 份重复，统一 extra_body 写法。"""
```

替换点：
- `app/pipeline/requirement_splitter.py`、`test_point_generator.py`、`test_case_designer.py`、`test_case_optimizer.py` 四个 `__init__`
- `utils/model_utils.py` 三处（`create_testcase_agent` 约 617 行、约 829 行、约 890 行）

**关键修复**：当前 pipeline 用 `model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}}`，model_utils 用顶层 `extra_body=...`，两种写法不一致是隐藏 bug。工厂统一为一种写法。

同时把 `RequirementSplitter` 与 `TestPointGenerator` 中重复的 `_normalize_priority` 静态方法提取到共享位置（`llm_factory.py` 或 `utils/helpers.py`）。

### 1.2 API 上下文服务
新建 `backend/app/services/api_context.py`：

```python
def parse_endpoint_ids(s: str | int | None) -> list[int]: ...
def build_api_context(session, endpoint_ids, project_id, overrides,
                      dependency_labels: dict[int, str] | None = None
                      ) -> tuple[str, dict[int, int]]: ...
```

替换 `app/routes/pipeline.py` 与 `app/routes/testcase.py` 各一份拷贝（约 60 行 ×2）。`testcase.py` 版本带"依赖补全"标签，通过可选 `dependency_labels` 参数兼容。

### 1.3 MCP 配置加载
在 `app/services/` 增加（与 api_context 同级或新建 `mcp_loader.py`）：

```python
def load_user_mcp_configs(session, user) -> list[dict]:
    """含权限判断 + 查询 + model_dump，集中处理。"""
```

替换 `app/routes/testcase.py` 与 `app/routes/pipeline.py` 各一份。

### 1.4 文档预取
新建 `backend/utils/document_prefetch.py`：
- 统一 `_LANHU_URL_RE` / `_FEISHU_URL_RE` 正则常量（当前两个文件各定义一遍）
- `async def prefetch_document(text, mcp_configs) -> str`

替换 `utils/model_utils._fetch_feishu_document_content` 与 `app/pipeline/pipeline_runner._prefetch_document`（约 100 行重复）。

---

## 阶段 2：后端性能 + 大文件拆分

### 2.1 get_testcases 统计查询优化
`app/routes/testcase.py:368-457`
- 现状：5 次独立 `func.count()`（total/passed/failed/not_run/totalBugs）
- 改为：1 次 `select(TestCase.status, func.count()).group_by(TestCase.status)` 拿各状态计数，Python 侧分配；bug count 单独 1 次。5 次 → 2 次往返。

### 2.2 pipeline 并发
`app/pipeline/pipeline_runner.py`
- 第 2 阶段（每需求点串行生成测试点）与第 3 阶段（批次串行设计用例）改用 `asyncio.gather + Semaphore(N)`，复用 `model_utils.py:265` 已有并发范式。
- **关键约束**：SQLModel `Session` 非协程安全 → 只并发 LLM 调用，结果收集后统一 `add`/`commit`（当前已是"先收集后提交"，改造安全）。
- 并发度 N 可配置，默认 3-5，避免 API 限流。

### 2.3 拆分 generate_testcases（约 600 行）
`utils/model_utils.py` 按阶段抽私有函数：
- `_prepare_requirement()` — 清洗 + 预取 + 技能注入
- `_invoke_model()` — agent / 直连分支
- `_parse_response()` — dict 的 5 种分支 + tool_calls/invalid_tool_calls 修复（抽成可单测纯函数，由阶段 0 测试覆盖）
- `_persist_testcases()` — Scenario 创建 + 历史提示词保存

### 2.4 拆分 api_test_tool.py（2006 行，80+ 函数）
`app/services/` 按职责拆分：
- `swagger_parser.py` — Swagger/OpenAPI 解析 + endpoint 同步
- `assertion_engine.py` — 断言执行（`_run_assertions` 等）
- `scenario_runner.py` — 多步场景执行 + 变量替换
- `step_runner.py` — 单步/单 endpoint 执行

风险最高，放阶段 2 最后做。每抽一组函数跑一次测试 + 导入检查。

---

## 阶段 3：前端优化

### 3.1 App.tsx 抽领域 hooks
64 个 useState 拆成：
- `useSessions` — 会话管理
- `useModules` — 模块管理
- `useTestcases` — 测试用例 CRUD
- `useEnvironments` — 环境/全局参数（104 处引用、localStorage 持久化、saveEnvironmentToBackend），可配套独立 `EnvironmentManager` 组件

### 3.2 启用 zustand 承载共享态
决策：**启用 zustand**（已在依赖中，比 Context 省样板、避免 Context 额外重渲染）。
- 用 store 承载跨领域共享态：`selectedSession`、`selectedModule`、`testcases` 选中态
- 目标：消除 `TestCaseManager`（38 props）与 `TestCaseGenerator`（13 props）的 prop drilling，让组件直接订阅 store

### 3.3 拆分 ApiScenarioTestTool.tsx（2731 行）
真正的前端巨石。拆成：
- 场景列表 / 场景编辑器 / 运行结果面板 子组件
- `useScenario` / `useScenarioRun` hooks 管理 API 调用与状态

### 3.4 清理项
- 修正 CLAUDE.md 失真行数与 zustand 描述
- 修 `services/api.ts` 乱码注释（第 191、229 行 `?????API??ID?`）
- 抽 `useApiCall` 收敛 19 处 try/catch + 50 处 notification 模板
- 补 `scheduledTaskApi` / `mcpApi` 等处的 `any` 类型
- 移除生产代码残留 `console.*`（App.tsx 25 处、`api.ts` generateTestcases 调试日志）

---

## 验证策略

- 每个阶段、每个改动点完成后：
  1. 跑阶段 0 的 pytest 测试
  2. 后端导入检查（`python -c "from ... import ..."`）
  3. 前端 `npx tsc --noEmit`
  4. 关键改动手动跑一次生成流程（功能测试 + 接口测试两条路径）
- 重构遵循"行为不变"原则：抽取/移动函数时不改变逻辑，仅改变组织方式
- 拆分大文件时每抽一组就验证一次，不一次性大改

---

## 不在本次范围内

- 数据库迁移机制统一（`db.py` 自动加列 vs alembic 并存）— 记录为已知债务，本次不动
- 配置加密存储（`config.json` 明文 api_key）— 安全改进，单独评估
- 多 worker 部署下 ConfigManager 单例问题 — 需求未明确，暂不处理
