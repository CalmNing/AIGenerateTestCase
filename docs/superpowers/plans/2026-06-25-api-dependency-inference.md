# API 接口依赖自动推断 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当用户通过接口生成测试用例时，自动推断并补全依赖的"新增"接口，并在 AI 提示词中注入依赖关系指导。

**Architecture:** 在 `testcase.py` 的生成接口中新增 `infer_endpoint_dependencies()` 函数，分析用户选中的接口与同项目其他接口的路径/参数关系，自动补全缺失的前置接口。同时在 `model_utils.py` 的系统提示词中追加依赖关系指导段落。

**Tech Stack:** Python 3.11, FastAPI, SQLModel, SQLAlchemy

## Global Constraints

- Python 3.11, FastAPI, SQLModel + SQLite
- 后端运行在 `backend/` 目录下，使用 `uvicorn main:app --reload`
- 不修改前端、数据库模型、执行引擎
- 现有 `post_actions` + `{{变量}}` 机制已支持变量传递，无需改动

---

## File Structure

| 文件 | 职责 |
|------|------|
| `backend/app/routes/testcase.py` | 新增 `infer_endpoint_dependencies()` 函数；在 `generate_testcases` 接口中调用 |
| `backend/utils/model_utils.py` | 系统提示词 `SYSTEM_PROMPT` 中追加依赖关系指导段落 |

---

### Task 1: 实现 `infer_endpoint_dependencies()` 推断函数

**Files:**
- Modify: `backend/app/routes/testcase.py` — 在文件顶部（`router` 定义之后）新增函数

**Interfaces:**
- Consumes: `ApiEndpoint` 模型（从 `db.models` 已导入）、`SessionDep` 数据库会话
- Produces: `infer_endpoint_dependencies(session, selected_ids, project_id) -> list[int]` — 返回需要补全的接口 ID 列表

- [ ] **Step 1: 在 `testcase.py` 中添加 `infer_endpoint_dependencies` 函数**

在 `router = APIRouter(...)` 行之后、`class TestCasePage` 之前插入：

```python
import re


def infer_endpoint_dependencies(
    session,
    selected_endpoint_ids: list[int],
    api_project_id: int | str | None,
) -> list[int]:
    """推断选中接口缺失的前置依赖接口，返回需要补全的接口 ID 列表。

    规则：
    1. 识别候选"新增"接口：POST 方法且路径不含 /{ 或 /:
    2. 识别"依赖方"接口：路径含 /{xxx} 且参数名含 id，或 body/parameters 含 id 字段但响应无该字段
    3. 如果存在依赖方但未选中任何候选新增接口，则按路径前缀匹配补全
    """
    if not api_project_id or not selected_endpoint_ids:
        return []

    try:
        project_id = int(api_project_id)
    except (ValueError, TypeError):
        return []

    # 加载同项目所有接口
    all_endpoints = session.exec(
        select(ApiEndpoint).where(ApiEndpoint.project_id == project_id)
    ).all()

    if not all_endpoints:
        return []

    selected_ids_set = set(selected_endpoint_ids)
    selected_eps = [ep for ep in all_endpoints if ep.id in selected_ids_set]
    non_selected_eps = [ep for ep in all_endpoints if ep.id not in selected_ids_set]

    # 1. 识别候选新增接口（POST + 非路径参数化）
    path_param_pattern = re.compile(r'/\{|/:')
    candidate_create_eps = [
        ep for ep in non_selected_eps
        if ep.method.upper() == 'POST' and not path_param_pattern.search(ep.path)
    ]

    if not candidate_create_eps:
        return []

    # 2. 检查是否已选中新增接口（路径前缀匹配）
    already_has_create = False
    for sel_ep in selected_eps:
        if sel_ep.method.upper() == 'POST' and not path_param_pattern.search(sel_ep.path):
            already_has_create = True
            break

    if already_has_create:
        return []

    # 3. 识别依赖方接口
    id_param_pattern = re.compile(r'\{[^}]*id[^}]*\}', re.IGNORECASE)
    has_dependency = False
    dependency_paths = []

    for ep in selected_eps:
        # 路径参数匹配
        if id_param_pattern.search(ep.path):
            has_dependency = True
            dependency_paths.append(ep.path)
            continue

        # 请求参数匹配：body/parameters 中含 id 字段
        has_id_field = False
        for field_list in [ep.parameters or [], _parse_body_fields(ep.body)]:
            for field in field_list:
                field_name = field.get('key', '') or field.get('name', '') or ''
                if 'id' in field_name.lower():
                    has_id_field = True
                    break
            if has_id_field:
                break

        if has_id_field:
            # 检查响应 schema 是否包含该字段
            resp_fields = _get_response_top_fields(ep.response_schema)
            has_id_in_response = any('id' in f.lower() for f in resp_fields)
            if not has_id_in_response:
                has_dependency = True
                dependency_paths.append(ep.path)

    if not has_dependency:
        return []

    # 4. 为每个依赖方匹配最合适的候选新增接口
    result_ids = set()
    for dep_path in dependency_paths:
        best_match = _best_matching_create_endpoint(dep_path, candidate_create_eps)
        if best_match:
            result_ids.add(best_match.id)

    return list(result_ids)


def _parse_body_fields(body: str | None) -> list[dict]:
    """从 body JSON 字符串中解析顶层字段列表。"""
    if not body:
        return []
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            return [{'key': k} for k in parsed.keys()]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _get_response_top_fields(response_schema: dict | None) -> list[str]:
    """从响应 schema 中提取顶层字段名。"""
    if not response_schema:
        return []
    props = response_schema.get('properties', {})
    if not props:
        # 尝试 items.properties（数组响应）
        items = response_schema.get('items', {})
        props = items.get('properties', {})
    return list(props.keys())


def _best_matching_create_endpoint(
    dependency_path: str,
    candidates: list,
):
    """为依赖方接口选择最匹配的候选新增接口。"""
    def path_segments(path: str) -> list[str]:
        return [s for s in path.strip('/').split('/') if s]

    dep_segments = path_segments(dependency_path)
    dep_prefix = '/'.join(dep_segments[:-1]) if len(dep_segments) > 1 else ''

    best = None
    best_score = -1

    for ep in candidates:
        cand_segments = path_segments(ep.path)
        cand_prefix = '/'.join(cand_segments)

        # 优先：路径前缀完全匹配
        if dep_prefix and cand_prefix == dep_prefix:
            return ep

        # 次优：前缀包含匹配
        if dep_prefix and cand_prefix.startswith(dep_prefix):
            score = len(dep_prefix)
            if score > best_score:
                best_score = score
                best = ep
        elif dep_prefix and dep_prefix.startswith(cand_prefix):
            score = len(cand_prefix)
            if score > best_score:
                best_score = score
                best = ep

    if best:
        return best

    # 兜底：选择路径 segments 数量最接近的
    dep_len = len(dep_segments)
    candidates_sorted = sorted(candidates, key=lambda ep: abs(len(path_segments(ep.path)) - dep_len))
    return candidates_sorted[0] if candidates_sorted else None
```

- [ ] **Step 2: 运行语法检查**

```bash
cd backend && python -c "import ast; ast.parse(open('app/routes/testcase.py', encoding='utf-8').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/routes/testcase.py
git commit -m "feat: add infer_endpoint_dependencies() function for auto-inferring create endpoint dependencies"
```

---

### Task 2: 在生成接口中调用依赖推断

**Files:**
- Modify: `backend/app/routes/testcase.py:370-440` — 在 API 上下文构建之前插入依赖推断调用

**Interfaces:**
- Consumes: `infer_endpoint_dependencies()` from Task 1
- Produces: 修改后的 `api_endpoint_ids` 列表（包含补全的接口 ID），以及 `dependency_labels` 字典（标记哪些是补全的）

- [ ] **Step 1: 在 `generate_testcases` 接口中插入依赖推断调用**

找到 `testcase.py` 中的这段代码（约第 373-383 行）：

```python
    # 解析 API 端点 Schema 和定义
    api_context = ""
    api_endpoint_ids = []
    if api_endpoint_id:
        for part in api_endpoint_id.split(','):
            part = part.strip()
            if part:
                try:
                    api_endpoint_ids.append(int(part))
                except ValueError:
                    pass
```

替换为：

```python
    # 解析 API 端点 Schema 和定义
    api_context = ""
    api_endpoint_ids = []
    if api_endpoint_id:
        for part in api_endpoint_id.split(','):
            part = part.strip()
            if part:
                try:
                    api_endpoint_ids.append(int(part))
                except ValueError:
                    pass

    # 自动推断依赖接口：补全缺失的前置"新增"接口
    dependency_labels: dict[int, str] = {}  # {endpoint_id: "依赖补全"}
    if api_endpoint_ids and api_project_id:
        extra_ids = infer_endpoint_dependencies(session, api_endpoint_ids, api_project_id)
        if extra_ids:
            new_ids = [eid for eid in extra_ids if eid not in api_endpoint_ids]
            for eid in new_ids:
                api_endpoint_ids.append(eid)
                dependency_labels[eid] = "依赖补全"
            if new_ids:
                logger.info(f"依赖推断：自动补全 {len(new_ids)} 个前置接口 {new_ids}")
```

- [ ] **Step 2: 在 API 上下文构建中标记依赖补全接口**

找到 `testcase.py` 中构建 `ep_lines` 的代码（约第 407-408 行）：

```python
                ep_lines = []
                ep_lines.append(f'===== [{idx}] {endpoint_db.name} =====')
```

替换为：

```python
                ep_lines = []
                dep_label = f" [{dependency_labels[eid]}]" if eid in dependency_labels else ""
                ep_lines.append(f'===== [{idx}] {endpoint_db.name}{dep_label} =====')
```

- [ ] **Step 3: 运行语法检查**

```bash
cd backend && python -c "import ast; ast.parse(open('app/routes/testcase.py', encoding='utf-8').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/routes/testcase.py
git commit -m "feat: integrate dependency inference into testcase generation flow"
```

---

### Task 3: 增强系统提示词中的依赖关系指导

**Files:**
- Modify: `backend/utils/model_utils.py:924-960` — 在现有 API 相关提示词之后追加依赖关系段落

**Interfaces:**
- Consumes: 无（纯文本修改）
- Produces: 增强后的 `SYSTEM_PROMPT` 字符串

- [ ] **Step 1: 在 `SYSTEM_PROMPT` 中追加依赖关系指导**

找到 `model_utils.py` 中 `SYSTEM_PROMPT` 里的这段文字（约第 960 行）：

```
覆盖率要求：
- 每个接口至少覆盖：1 个成功场景 + 2 个异常场景
- 包含输入参数的边界值测试（如字符串长度、数值范围）
- 包含必填字段缺失、字段类型错误的异常测试
- 对关联的多个接口，设计场景测试覆盖端到端流程
```

在"端到端流程"之后、"## API 测试用例示例"之前，追加：

```
当接口信息中包含 [依赖补全] 标记时，表示该接口是系统自动添加的前置依赖。请遵循以下规则：
- 被标记为 [依赖补全] 的接口（通常是 POST 新增接口）需要在依赖方的测试用例中作为前置步骤使用
- 在 preset_conditions 中生成 api_call 步骤调用新增接口
- 在新增接口步骤的 variables 中定义记录 ID 变量（如 {"key": "record_id", "value": "$.data.id"}）
- 在后续编辑/删除/详情步骤的 body 或路径参数中，使用 {{record_id}} 引用该 ID
- 为每个依赖场景至少生成 1 个成功用例和 1 个异常用例（如使用不存在的 ID）
```

具体来说，在 `SYSTEM_PROMPT` 字符串中找到：

```python
- 对关联的多个接口，设计场景测试覆盖端到端流程
```

在其后追加（注意保持缩进在三引号字符串内）：

```python
- 对关联的多个接口，设计场景测试覆盖端到端流程

当接口信息中包含 [依赖补全] 标记时，表示该接口是系统自动添加的前置依赖。请遵循以下规则：
- 被标记为 [依赖补全] 的接口（通常是 POST 新增接口）需要在依赖方的测试用例中作为前置步骤使用
- 在 preset_conditions 中生成 api_call 步骤调用新增接口
- 在新增接口步骤的 variables 中定义记录 ID 变量（如 {"key": "record_id", "value": "$.data.id"}）
- 在后续编辑/删除/详情步骤的 body 或路径参数中，使用 {{record_id}} 引用该 ID
- 为每个依赖场景至少生成 1 个成功用例和 1 个异常用例（如使用不存在的 ID）
```

- [ ] **Step 2: 运行语法检查**

```bash
cd backend && python -c "import ast; ast.parse(open('utils/model_utils.py', encoding='utf-8').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add backend/utils/model_utils.py
git commit -m "feat: add dependency inference guidance to system prompt"
```

---

### Task 4: 端到端验证

**Files:**
- None (验证任务，不修改文件)

- [ ] **Step 1: 启动后端服务**

```bash
cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: 验证函数可导入**

```bash
cd backend && python -c "from app.routes.testcase import infer_endpoint_dependencies; print('Import OK')"
```

Expected: `Import OK`

- [ ] **Step 3: 验证提示词包含依赖指导**

```bash
cd backend && python -c "from utils.model_utils import SYSTEM_PROMPT; assert '[依赖补全]' in SYSTEM_PROMPT; print('Prompt check OK')"
```

Expected: `Prompt check OK`

- [ ] **Step 4: 手动测试场景**

准备一个包含以下接口的项目：
- `POST /api/users`（新增用户）
- `PUT /api/users/{id}`（编辑用户）
- `DELETE /api/users/{id}`（删除用户）

只选中编辑和删除接口，调用生成接口，观察：
1. 后端日志应输出"依赖推断：自动补全 1 个前置接口"
2. 生成的 API 上下文中应包含 `[依赖补全]` 标记
3. 生成的测试用例的 `preset_conditions` 中应有调用新增接口的步骤

- [ ] **Step 5: 最终 Commit**

```bash
git add -A
git commit -m "feat: API dependency inference - auto-add create endpoints and inject prompt guidance"
```
