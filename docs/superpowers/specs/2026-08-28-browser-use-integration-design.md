# Browser-Use UI 自动化测试执行集成 — 设计文档

- 日期:2026-08-28
- 状态:已评审(对话中逐节确认)
- 范围:MVP — 单用例 UI 执行闭环

## 1. 背景与目标

本项目(AI 测试用例生成与管理平台)目前只能执行**接口测试用例**(`api_test_tool.py` 引擎)。功能测试用例虽然生成了自然语言步骤(如"点击「登录」按钮 → 登录成功"),却没有执行手段,`status` 永远停留在 `NOT_RUN`。

本设计引入 [browser-use](https://github.com/browser-use/browser-use)(MIT 开源,免费,底层 Playwright)补上 **UI 自动化执行** 环节:AI Agent 按用例的自然语言步骤操作无头浏览器,自主判定 PASS/FAIL,结果写回用例状态——与现有 API 执行形成对称闭环。

**已确认的边界决策**:

| 决策点 | 结论 |
|---|---|
| 核心场景 | 执行已生成的功能测试用例(非通用浏览器工作台) |
| 判定机制 | Agent 自主判定(done 动作携带 success + 逐条预期核对),MVP 无规则断言 |
| 浏览器形态 | 服务端无头 Chromium,后台任务,用户看结果日志 |
| 范围 | 单用例执行;不做批量、不做定时调度 UI 回归 |
| 集成形态 | 进程内直连(方案 A);所有 browser-use API 收敛到单一服务文件 |

## 2. 总体架构

```
┌─ FastAPI 主进程 ──────────────────────────────────────┐
│                                                        │
│  app/routes/ui_execution.py (新增路由)                 │
│    POST /api/ui-execution/run         发起执行         │
│    GET  /api/ui-execution/tasks/{id}  查进度/结果      │
│    GET  /api/ui-execution/records     历史(按用例)     │
│         │                                              │
│         ▼ asyncio.create_task(复用 pipeline 后台模式)  │
│  app/services/browser_agent.py (新增服务层)            │
│    ├─ UIExecutionRunner.run(record)                    │
│    │    ├─ config.py 读 LLM 配置 → build_chat_model()  │
│    │    ├─ browser-use Agent + Playwright 无头 Chromium│
│    │    ├─ asyncio.Semaphore(1) 串行闸                 │
│    │    └─ 任务状态:内存字典(同构 pipeline_tasks)     │
│    └─ agent_factory 参数:测试注入 stub,生产默认真实现  │
│         │                                              │
│         ▼ 执行完毕                                     │
│  写 UIExecutionRecord + 回写 TestCase.status           │
└────────────────────────────────────────────────────────┘
```

**前端不新增平台**:执行入口放在现有 `ai-testcase` 平台的用例操作中(「UI 执行」按钮 + 弹窗),因为这是用例生命周期动作,不是独立工作台。

**依赖**:`requirements.txt` 新增 `browser-use`(含 playwright 传递依赖;需 `playwright install chromium` 装浏览器二进制)。

## 3. 数据模型

### 3.1 新表 `UIExecutionRecord`(db/models.py)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| testcase_id | int FK→testcase.id, index | 关联用例 |
| user_id | str, index | 发起人(Keycloak sub) |
| target_url | str | 拼接后的完整页面地址 |
| status | str | `RUNNING / PASSED / FAILED / ERROR / STOPPED` |
| steps_trace | JSON | Agent 每步 {step_no, action, url, result} |
| agent_summary | str | done 动作的 text(逐条预期核对说明) |
| error | str \| None | 仅 ERROR 时有值 |
| created_at / finished_at | datetime | 上海时区,与现有模型一致 |

存量表自动迁移由 `db/db.py` 现有 auto-migration 机制处理。

### 3.2 用例状态回写规则

只有 Agent 给出明确判定才写 `TestCase.status`(PASSED/FAILED);ERROR(环境故障)与 STOPPED(用户停止)均不回写,避免污染用例统计。

## 4. 用例兼容性(已核对现有数据格式)

数据库中实际存在 3 种 steps 格式,处理策略:

| steps 格式 | 来源 | UI 执行处理 |
|---|---|---|
| 纯字符串数组 | 单次生成路径功能用例 | `_normalize_ui_steps()` 直接取原值 |
| `{step, action, expected}` dict | 4-Phase 流水线功能用例(pipeline_runner.py 从 `TestStep` schema 存入,expected_results 按 steps 下标逐条对应) | 取 `action` 字段 |
| `{type:"api_call", ...}` dict | 接口测试用例(两条路径) | **拒绝执行**:POST /run 返回 400「接口测试用例不支持 UI 执行」 |

`_normalize_ui_steps()`:后端纯函数,字符串取原值、dict 取 `action`、其余类型跳过;全为空 → 400「用例无可执行步骤」。

`preset_conditions`(前置条件):不自动满足,注入 prompt 作上下文,前端弹窗展示提醒用户确保环境就绪。

## 5. 目标地址(URL)设计

URL 是**执行时上下文**,不存于用例,落在 `UIExecutionRecord.target_url`。前端地址与 API 地址分离:

| 全局参数名 | 存什么 | 示例 | 用途 |
|---|---|---|---|
| `base_url`(既有约定,不动) | API 网关,带 /service | `http://192.168.13.178:8182/service` | API 测试 |
| `web_url`(新增约定) | 前端站点,**仅 origin** | `http://192.168.13.178:8182` | UI 执行预填 |

**执行弹窗两段式输入**:

- 站点地址(origin)— 预填链:①该用例上次执行的 origin(UIExecutionRecord 反解)→ ②默认环境 `web_url`(复用既有 `GET /api/global-parameters/default`)→ ③空
- 页面路径(hash 路由路径,如 `#/home/cmp/contract-web/main/ent/contractRegistration`)— 预填链:①该用例上次执行的 path → ②空

第一次由用户从浏览器地址栏复制路径,执行后存入 record,同用例后续执行零输入。

**拼接规则**(后端,过 `substitute_variables()` 支持 `{{var}}`/`${var}`/`{{$fn}}`/`{{@expr}}`):

- path 以 `#` 或 `/` 开头 → `origin + path`
- path 为空 → 仅 origin;prompt 告知 Agent「目标页面未给直链,可通过系统菜单导航到用例标题所述页面」
- origin 为空 → 400

## 6. 登录态共享(Storage State 缓存)

无头浏览器每次全新会话,按 origin 缓存登录态避免每用例重复登录:

```
首次执行(站点 S):无缓存 → Agent 按步骤执行(含登录,prompt 已注入
  test_username/test_password)→ 结束导出 storage_state →
  写 backend/data/browser_sessions/{origin}.json
后续执行(同 S):读缓存 → context 注入 → 已登录状态直接执行,零登录开销
缓存失效:注入后仍落登录页 → prompt 指令重登 → 结束覆盖缓存(自愈)
```

- 缓存 key = origin;每次执行结束**无条件导出覆盖**(有效时等值无害,失效时刷新,一个动作两种情况)
- browser-use 写回时保留 `.json.bak`;若主缓存被一次失败执行(页面停在 about:blank)写为空,
  启动前会从 `.bak` 恢复上次有效登录态,避免反复重新登录
- 文件存储不入库(敏感 JSON),目录加入 `.gitignore`
- 无 TTL、无手动清除接口(自愈机制使其多余)
- 并发安全:Semaphore(1) 保证同一时刻仅一个浏览器读写缓存
- prompt 登录段按缓存命中与否切换措辞(见 §7)
- `agent_summary` 记录是否发生登录,用户可从日志辨别

登录键查找容忍大小写与首尾空白(如 `"test_password "` 手误仍可读到)。

### LLM 结构化输出兼容(DeepSeek 网关)

browser-use 需要模型返回可解析的 `AgentOutput` JSON。实测 DeepSeek 网关
`response_format=json_schema` 返回 400「This response_format type is unavailable now」,
故关闭强制结构化输出、把 schema 注入 system prompt;同时对结构化调用
(system prompt 含 `<json_schema>`)补 `response_format={"type":"json_object"}`,
强制模型返回合法 JSON,避免「无法以正确格式输出操作指令」导致浏览器停在 about:blank。

登录参数:复用全局参数,约定 `test_username` / `test_password`(凭证),以及
`test_login_url`(登录入口,SSO 或登录页直链)/ `test_login_method`(登录方式名称,
如「鲁班通行证登录」)/ `test_company`(登录后企业/身份选择弹窗中要选择的企业)。
显式配置登录入口后,prompt 按「登录入口 → 登录方式 → 账号/密码」给出的确切配置执行,
并**禁止 Agent 猜测或尝试未配置的账号密码**;无凭证时要求将「前置条件不满足(未登录)」
写入判定说明而非绕过登录。未配置 `test_login_url` 时退回原有「账号/密码 + 缓存命中措辞」分支。

登录成功后的收尾(有登录配置时注入):① 若弹出企业/身份选择窗口,选择 `test_company`
及对应身份并确定;② 若登录后被跳转到工作台/首页,必须**重新访问目标页面地址**再执行
用例步骤——修复「登录后停留工作台、回不到用例目标页」的问题。

## 7. 执行流程

### 7.1 状态机

```
queued → launching_browser → executing → judging → persisting → done
```

- `executing`:browser-use 每步回调,追加 steps_trace,同步内存任务 message(「执行步骤 3/6」)
- `judging`:Agent 调 done(success, reason)
- `persisting`:写 record + 按 §3.2 规则回写用例状态

### 7.2 Prompt 模板

```
目标页面地址(完整 URL,仅用于导航,不要与后续文字连读):{target_url}

依次完成以下测试步骤。这是软件测试执行,严格按步骤顺序操作:
1. {step 1 action}
...
[前置条件提示(若有)]
[登录段(优先级从高到低):
  配置了 test_login_url:「…请按以下配置登录: 登录入口 {test_login_url} /
          登录方式 {test_login_method} / 账号 {test_username} 密码 {test_password};
          凭证以此为准,不要猜测其他账号密码」(无凭证时禁止猜测并要求写明前置条件不满足)
  未配置 test_login_url 但有凭证:
    有缓存:「当前会话应已持有登录态,若打开页面仍被重定向到登录页,
           使用账号 {test_username} 密码 {test_password} 重新登录后继续」
    无缓存:「若用例步骤依赖已登录状态而当前未登录,先用账号 {test_username}
           密码 {test_password} 登录,再开始用例步骤」]
[登录成功后的收尾(仅当有登录配置时):
  - 若弹出企业/身份选择窗口,选择企业 {test_company}(未配置时选对应企业)及身份,确定
  - 若登录后跳转到工作台/首页,重新访问目标页面地址 {target_url},再从第 1 步开始]

全部步骤完成后,对照以下预期结果逐条核对:
1. {expected 1}
...
最后调用 done:若所有步骤成功且预期全部满足,success=true,否则 false,
text 中逐条说明每条预期的实际表现。
```

### 7.3 闸门(构造参数,带默认值;MVP 不暴露 UI)

| 闸门 | 默认 | 到达行为 |
|---|---|---|
| max_steps | 25 | Agent 强制结束,按 FAIL 落库(附「步骤未完成」) |
| 整体超时 | 10 分钟 | asyncio.wait_for 取消,record 记 ERROR「执行超时」 |
| LLM 重试 | browser-use 内置(3 次) | 用尽记 ERROR |

## 8. 错误处理

| 情形 | record.status | error | TestCase.status |
|---|---|---|---|
| Agent 判定 success=false | FAILED | None | FAILED |
| 崩溃/超时/LLM 用尽 | ERROR | 异常摘要 | 不变 |
| target_url 不可达 | ERROR | 「无法访问目标站点」 | 不变 |
| 用户停止 | STOPPED | None(摘要「用户已停止执行」) | 不变 |
| 成功 | PASSED | None | PASSED |

入口校验(POST /run,均 400):用例不存在/不属于用户;steps 含 api_call;归一化后为空;origin 缺失。

**资源控制**:

- 模块级 `asyncio.Semaphore(1)`:单实例串行,后续请求排队于 queued(SQLite 单写 + Chromium ~300-500MB 内存决定串行正确)
- 浏览器生命周期 `try/finally` + 进程退出钩子兜底,超时取消保证 finally 执行
- `backend/data/browser_sessions/` 加入 `.gitignore`

## 9. API 契约

```
POST /api/ui-execution/run
  body: { testcase_id: int, site_origin: str, page_path: str,
          login_url?: str, login_method?: str,
          login_username?: str, login_password?: str, login_company?: str }
  # 登录字段可选,本次执行覆盖全局参数
  → Response[{ task_id: str, record_id: int }]   # 立即返回,后台执行
  site_origin/page_path/login_url 均经变量替换;后端拼接为 target_url
  凭据仅经内存传给 runner,不落库、不写入 /tasks 状态(避免泄露)

GET /api/ui-execution/tasks/{task_id}
  → Response[{ status, phase, progress, message }]
  # 内存态,同构 pipeline_tasks;任务完成后仍可查(persisting 结果含 record_id)

POST /api/ui-execution/tasks/{task_id}/stop
  → Response[{ stopped: bool, message: str }]
  # 停止执行:标记 stop_requested + cancel 后台 asyncio.Task;
  # record 落 STOPPED、不回写用例状态;仅发起人本人可停止(否则 404)

GET /api/ui-execution/records?testcase_id={id}
  → Response[List[UIExecutionRecord]]   # 倒序,含 steps_trace/summary
```

鉴权:注册进 api_router 时挂 `CurrentUser` 依赖,同其他业务路由。

## 10. 前端设计

改动集中在 `ai-testcase` 平台用例操作区:

1. **「UI 执行」按钮**(TestCaseTable/Manager 操作列;接口用例不显示该按钮——前端已有 test_type/steps 结构可判)
2. **UIExecutionModal**(新组件,模式同现有 modals/):
   - 两段式地址输入(预填链见 §5)
   - 前置条件只读提醒区
   - 登录配置区(登录入口 / 登录方式 / 用户名 / 密码 / 企业):从默认环境全局参数预填,
     可直接修改覆盖本次执行;留空字段回退全局参数。下方按「显式入口 或 完整账号密码」
     展示已配置/未配置提示。「企业」用于登录后企业/身份选择弹窗
   - 「开始执行」→ 轮询 tasks/{id} → 进度条(阶段 + message)→ 终态展示判定结果
   - 运行中显示「停止执行」按钮 → POST tasks/{id}/stop,轮询拿到 STOPPED 终态后提示「执行已停止」
3. **执行历史**:UIExecutionModal 内 Tab 展示该用例的 records 列表(状态/时间/摘要,可展开 steps_trace)
4. localStorage 记住每个用例最近一次的 origin/path 作为预填兜底(主数据源仍是 record + 全局参数)

## 11. 测试策略(遵守项目约定:pytest,不碰真 LLM/浏览器)

| 层 | 覆盖 | 隔离手段 |
|---|---|---|
| `_normalize_ui_steps()` | 3 种格式 + 混合 + 空 | 纯函数直测 |
| 路由校验 | api_call 拒绝/URL 拼接/缺参/鉴权 | stub agent_factory |
| Runner 编排 | 状态机流转、record 写入、ERROR 不回写用例状态、登录缓存读写/覆盖 | 假 Agent(固定 success/steps_trace) |
| 前端 | `npm run lint` 通过 | 手动验收 |

不做真浏览器冒烟测试(CI 无 Chromium);真实验证 = 本地手跑一条用例。

## 12. 成本量级(供运维预期)

- browser-use 库与 Chromium:¥0(MIT 开源,本地运行)
- LLM:每步 1 次调用(3-10k input tokens);5 步用例含重试 ≈ 50k-200k tokens ≈ DeepSeek 下单次执行 ¥0.1-0.4;Ollama 本地模型 ¥0 但成功率显著降低
- max_steps=25 封顶失控风险

## 13. 不做的(YAGNI)与后继

后继候选(本期不实现):批量执行/用例集、定时 UI 回归(挂 APScheduler)、规则断言(URL/文本包含)、截图留痕、有头浏览器实时观看、独立 worker 服务。当前薄封装(`agent_factory` + 单文件服务)已为其中多数预留了演进空间。
