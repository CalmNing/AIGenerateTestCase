# 项目长期记忆（MEMORY.md）

## 边界与偏好
- **`AGENTS.md` 只是开发指导文档**，不参与运行；项目真实功能只活在代码里，改代码+测过即生效。
- 提取飞书/外部文档只要**原文**：不改写、不重组结构，表格/图片用极简占位（可留 token 便于复取）。
- 沟通：结论先行 → 依据 → 可执行方案，不要冗长解释。

## UI 自动化（核心 feature）
抽取 `app/services/page_object_runner.py` + `page_extractor.py`（Playwright 注入 JS，跨 frame）。

### 登录模型：登录 = 一个配了执行步骤的页面对象
- `PageObject.is_login_page=True` 的对象即「登录页对象」，它 `steps`（执行/自愈步骤，已持久化）就是登录动作；
  其他页面对象用 `login_page_object_id` 关联它。
- 流程：抽取/执行前 `_ensure_session` → 有登录态直接用；没有就先跑登录页对象的步骤（或回退 browser-use AI 登录）→ 再继续。
- 登录态文件按页面对象隔离：`data/browser_sessions/pages/po{id}.json`（同时同步一份到 origin 通用缓存）。
- **铁律一（自我阻塞）**：`_ensure_session` 中若「本次主体就是登录页对象自己」（执行/刷新它自己的登录步骤），必须跳过前置 bootstrap
  —— 否则「先登录才能执行登录」自我阻塞。
- **铁律二（必须匿名，2026-09-21 修）**：该分支**恒 `return None`**，绝不能「有历史登录态就带上」。
  带登录态打开登录页 URL，站点会直接跳进业务页，登录页元素一个都不在 → 定位器全失效。
  实测 `http://192.168.13.178:8182/`：带缓存 → `#/home/workbench/workbench/index`（工作台，用户名/密码/按钮全 0 个）；
  匿名 → `/sso/?redirect=...`（登录页，各 1 个）。复现命令 `python backend/scripts/probe_login_landing.py --url <url>`。
  同一原理的**第二处**：`_bootstrap_login` 里 `_execute_in_browser(login_page.url, ...)` 的 `storage_state` 也必须是 `None`（登录步骤只能在登录页上跑）。
  登录态不是「读」出来的，是这次跑完登录步骤后由 `save_state_path` 落盘产生的。
- **登录后保持登录状态**靠 `_execute_in_browser(..., save_state_path=...)`：Playwright 结构化执行链路原本**从不保存**
  `context.storage_state()`，会话随浏览器关闭丢失；现在执行收尾落盘（失败只告警，不让执行变红）。
- **落盘前必须 `_settle_before_snapshot(page)`**（2026-09-21 修）：点完「登录」立刻 `storage_state()` 抓到的是空壳
  —— 实测 po514.json 仅 188B、只有站点标题、**没有 token**，下个页面对象复用等于没登录 → 永远停在「未登录」。
  实现＝等 `networkidle`(≤5s) + `SNAPSHOT_SETTLE_MS=1500` 固定延迟。
- 落盘后**顺带 `ba.sync_state_file(state_file, ba._cache_path(origin))`** → 未关联登录页对象的旧链路也能复用
  （目标就是通用缓存本身时跳过）。实测：跑完登录页对象后通用缓存里还是旧 token，业务页对象一用就被踢回登录页。
- **「本次主体是登录页对象自己」判据＝`_is_self_login_page(page_object)`**：`is_login_page=True` 或 `login_page_object_id == id`
  （两个都是 `None` 不算）。**对象必须显式标记**才走匿名路径，抽取弹窗不传 `is_login_page`（见下），所以漏标记就会踩坑。
- **失败诊断 `_diagnose_locator_failure(raw, had_session)`**：定位器全失效时不再只报一句「定位器全部失效且自愈失败」，
  按「重扫页面是不是登录页(含密码框)」×「本次有没有带登录态」给可操作指引（含「请到『编辑信息』勾选『作为登录页对象』」）。
- **抽取/重新生成弹窗不再有「登录入口/用户名/密码」输入框，也不再要「本页面作为登录页对象」复选框**（2026-09-21 按用户要求删除）：
  登录只能靠 `login_page_object_id` 关联登录页对象（有态复用，无态先跑其步骤），没有"临时填账号兜底"这条路了；
  标记/取消登录页对象只能在「编辑信息」弹窗（抽取环节不传 `is_login_page`，create 出来的对象恒 `False`）。
  后端 `login_url/login_username/login_password` 与 `mark_login_page` 参数仍保留（validate/login/run 在用，后者已无调用方）。
  ~~验证 tab 的「临时登录入口/用户名/密码」(`sessionForm`)~~（2026-09-22 按用户要求已删除：sessionForm state 及 validate/run 请求里的临时账号字段一并移除，验证/执行只靠关联登录页对象 + 全局参数回退）。
- **后端不对称（已绕开，未修）**：`page_object_runner.py:232` 重新生成分支的 `if self.mark_login_page: existing.is_login_page = True`
  只能置 True；取消勾选不会把登录页对象改回普通对象。创建分支 248 行才是直接赋值。
- **`skip_login` = 「本次不使用登录态」**（2026-09-21 加）：抽取/重新生成弹窗没选登录页对象时前端传 `skip_login=True`，
  贯穿 `ExtractRequest`/`RegenerateRequest` → 路由 → `launch_extraction` → `PageObjectRunner` → `_ensure_session` **开头直接 `return None`**，
  既不读该 origin 通用缓存、也不触发 AI 登录。路由层同时**跳过 `_resolve_login_page_object_id` 的回退解析**
  （否则会自动回退用页面对象自身已关联的登录页对象）。
  **前端预填注意**：`openRegenerate` 必须写成 `selected.login_page_object_id || (selected.is_login_page ? selected.id : 0)` ——
  `_resolve_login_page` 里还有「自己就是登录页对象 → 返回自己」一条，登录页对象自己重新生成时预填 0 会变匿名抽取、被重定向到登录页。
  下拉 options 拆两份：编辑弹窗 `loginPageOptions`（0=不关联）/ 抽取弹窗 `extractLoginPageOptions`（0=不使用登录态）。
- **登录路径有三处要短路，加登录开关时全都要碰**（2026-09-21 踩过两次）：
  ① 前置 `_ensure_session`；② 抽取后置探测 `_run_extract._phase()` → `_looks_like_login_page(raw)`
  （采到 `input[type=password]` 即判「登录态过期」）→ `_relogin()`，**`_relogin` 不走 `_ensure_session`**。
  只改 ① 时匿名抽取仍会弹「检测到登录页(登录态可能已过期),正在重新登录...」并真的去登录。
  两处都要 `skip_login` 短路（`_relogin` 开头再加一道防御）。该提示全库只此一处。
  ③ **`_is_self_login_page(page_object)` 也要短路 ②**：登录页对象自己抽取必然采到密码框，会被误判成「登录态过期」
  → 重登录 → **带新登录态重抽 → 抽到的是登录后的业务页**。`_phase()` 的 if 与 `_relogin` 开头各加一道。

### 本机跑后端测试的环境坑
- **pytest 统计行缺失、EXIT 恒 1**：`harmo` 插件在 `pytest_terminal_summary` 访问已改名的 `terminalreporter._sessionstarttime`
  抛 `AttributeError`。`-p no:harmo` **无效**。可靠读法 → `--junitxml=res.xml`，再 grep `errors="0" failures="0" tests="N"`。
- 用 `.venv/Scripts/python.exe -m pytest`（**不是** `F:\env\Python313`）跑后端测试。2026-09-21 全量 **644 passed / 0 failed**
  （此前记录的 `test_ui_execution_runner.py` 13 个 `browser_use.llm` 缺失失败已随环境修复消失）。

### 抽到的元素 ≠ 可见元素（框架/低代码把真控件藏起来）
- 隐藏元素单独扫：`isVisible` 会滤掉 `input[type=file]`（必 `display:none`）、低代码 `textRadio` 原生 radio、`opacity:0` 覆盖层。
  判据 `isVisuallyReplaced`＝自身不可见但父链未隐藏。
- div/span 模拟控件（`textRadio`/`lc-input`）靠 `hasInteractiveHint`（role / tabindex / class 语义词）识别，
  排除 `-text/-label/-title/-display/-placeholder/-wrap/-wrapper` 结尾的展示层。
- shadow DOM 用 `queryAllDeep` 递归 open shadow root（`querySelectorAll` 整片漏采）；closed 拿不到。
- 坑：`el.closest(UPLOAD_WRAPPER)` 会命中 input 自己，要从 `el.parentElement` 起找。
- 执行侧：`get_by_role` 默认不含隐藏元素 → 被视觉替换的控件不生成 role 定位器；等 `visible` 对 `display:none` 必超时，
  统一 `wait_for(state="attached")`；连 `check(force=True)` 都点不动时改点 `trigger`（可见壳 cssPath，单独存放）。
- 诊断：`EXTRACT_JS` 返回 `{elements, skipped}`，`reason=visible-not-collected` 才是真缺口；
  工具 `backend/scripts/probe_page_extraction.py`（`--url` 抽真实页并列出未采集清单）。
- 已知限制：`normalize_elements` 去重键含主定位器，同页同名按钮会被合并成一条。

### 登录态文件与状态的坑
- `_normalize_state_file_encoding`：**内容一致就不要重写文件** —— mtime 是新鲜度判据，每次重写会把它抹成「刚刚」。
- `refresh_status` 无参收尾刷新**不能把已记录的 FAILED 抹成 NONE**（否则任务结束覆盖真实原因）。
- browser-use / Playwright 写 storage_state **不会自建父目录** → 落盘前先 `mkdir(parents=True, exist_ok=True)`。
- `resolve_login_context().logged_in_cache` 语义＝**文件存在且非空**（空 `{}` 不算已登录）。

### 实测站点：鲁班数字工程管理平台（192.168.13.178:8182）
- React 17 + **无界(wujie)微前端**：主文档不穿透 shadow root 只命中 1 个控件，穿透后 80 → `queryAllDeep` 必用。
- 自研表单库（非 antd）：fiber 链上 `FormItem` props 解构出 name/label/rules/required 等，压缩后属性名可读。
- 登录态是 **localStorage 型**（`LBuilderToken` 等，cookies 为 0），缓存文件在 `backend/data/browser_sessions/`。
- 抽取稳定性靠 `_wait_for_settle()`：穿透 shadow root 计数连续 3 轮不变 + 预热期 + 网络安静（700ms），兜底 15s。
- **多子应用只穿透当前路由那个**：页面上并存多个 `<wujie-app>`（本页 `/sg-security/` 与 `mmt-ai`，**两个都 visible 且铺满视口**）。`WUJIE_SCOPE_JS`（`EXTRACT_JS`/`PAGE_COUNT_JS` 共用）判定：先看 `data-wujie-id` 去斜杠后是否出现在 `location.href`，否则取 shadow 节点最多的；旁支子应用用 `isOtherWujie` **整片跳过**。0/1 个 wujie-app 时不影响普通页面。
- 探针浮层关闭 `_close_overlay()`：三级降级 `Escape` → 点「取消/关闭」→ **重载页面兜底**（原实现关不掉就终止整轮探针，命中 306→153）。`_loose_text_pattern` 容忍「取 消」这类字符间空格。
- **坑：`_probe_trigger` 不持有 frame 句柄** —— 重载会重建页面让旧句柄失效，必须每次在当前 `page.frames` 重新找按钮；外层也改成「先收集触发文案列表再逐个探」。
- **坑：`FIND_POINT_JS` 必须同时校验文案相等**（`t === want`，两边去空白）。只靠 pattern 会点到白名单里第一个按钮，每个触发词都重复点它，后续弹窗表单全探不到（命中掉到 275）。
- 命名：`_clean_name()` 压空白 + 去尾部冒号；`heuristic_name` 回退链插入 `title`；无名元素兜底 `_TYPE_LABELS` 中文类型名（原 `f"{tag}元素"` → 「button元素」）；`_disambiguate_names()` 给真重名加序号（`按钮1/2/3`），表格逐行重复的已在去重阶段合并、不会误加。

### fiber 逆向（PoC 在 `.poc_fiber/`，未接入生产）
- 只有「运行时框架对象读取」可行；静态 bundle AST 不可行，CDP `DOMDebugger.getEventListeners` 对 React 无效。
- 生产构建键名 `__reactFiber$<随机后缀>`，必须遍历 `Object.keys` 前缀匹配。深度 ≤40，`memoizedProps` 要白名单提取。
- 红线：只能做「可选增强 + 失败静默降级」，绝不能成主路径。

## 其他
- 需求文件上传：`.docx`（python-docx，含嵌入 Excel）、`.pdf`（pdfplumber，仅文本型）、`.txt/.md`；
  入口 `app/routes/pipeline.py::generate_v2` → `utils/file_parser.py::parse_uploaded_file`，单文件 10MB。
  后端实际跑系统 Python `F:\env\Python313`（非 venv）。
- SQLModel 不显式设 `__tablename__` 会推断无下划线表名（`PageObject`→`pageobject`）；新增列由
  `db/db.py::_migrate_missing_columns()` 启动时自动 ALTER，删列用同文件 `_try_drop_column`。

### 站点地址支持 {{变量}}（2026-09-22 定规则）
- **页面对象 site_origin/page_path/url 落库存原文**，执行（extract/regenerate/validate/run）时才按默认环境参数解析（`_resolve_or_400`，未定义变量 400 快速失败）；runner 不用已解析 URL 回写 `existing.url`，新建对象用 spec 原文。鲁班代理 base_url 同样解析。详见 2026-09-22 日志。

### 登录态空壳判定（2026-09-22 修）
- **有效性判据 = `ba.state_meaningful()`**：有 cookie，或任一 origin 有 value≥20 字符的 localStorage 条目。「有 origin 没 localStorage」「只剩 LBuilderTitle 短展示键」都是空壳，不得当已登录复用（鲁班 localStorage 型站点，cookies 恒 0）。
- `_execute_in_browser` 收尾保存时空壳**跳过落盘**，防止覆盖好文件；`has_valid_state_file`/`has_valid_session`/`session_view.ready` 全走该判据。
