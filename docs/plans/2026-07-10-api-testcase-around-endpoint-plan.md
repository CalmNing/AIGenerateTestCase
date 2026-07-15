# 接口测试用例"围绕接口本身"生成 — 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让"通过接口生成接口测试用例"的产出围绕接口本身（描述/字段/上下文依赖）展开，而非围绕用户输入的提示词；功能测试用例生成完全不受影响。

**Architecture:** 四块改动收敛在 API 测试链路——(1) `ApiEndpoint` 增加 `description` 业务描述字段（DB 自动迁移加列）；(2) `api_context.py` 拼接 description 并新增字段级依赖分析；(3) `API_PROMPT` 强化"接口为主角"并加逐接口/逐字段/依赖覆盖约束；(4) 路由与技能注入在 API 场景下把"用户需求"降权为"补充背景"。前端类型与接口编辑表单补充 description 录入。

**Tech Stack:** Python 3.11 / FastAPI / SQLModel(SQLite) / React 18 + TypeScript + Ant Design。

**约束:** 只改接口测试路径；`FUNCTIONAL_PROMPT`、功能测试路由、`BASE_PROMPT` 通用部分不动。所有改动仅在 `is_api_test=True` 时生效于提示词。

---

### Task 1: 后端 `ApiEndpoint` 增加 `description` 字段

**Files:**
- Modify: `backend/db/models.py:256-274`（`class ApiEndpoint`）

**Step 1: 在 `ApiEndpoint` 中新增字段**

在 `name` 字段之后插入 `description`：

```python
class ApiEndpoint(BaseModel, table=True):
    """接口测试工具中的单个接口定义"""
    project_id: int = Field(foreign_key="apiproject.id", description="接口项目ID")
    name: str = Field(default="", description="接口名称")
    description: Optional[str] = Field(default=None, description="接口业务描述")
    method: str = Field(default="GET", description="HTTP 方法")
    path: str = Field(default="", description="接口路径")
    # ... 其余字段保持不变 ...
```

（注意：文件顶部需 `from typing import Optional`，若尚未导入则补上。）

**Step 2: 验证自动迁移**

```bash
cd backend && .venv\Scripts\python -c "from db.db import create_db_and_tables; create_db_and_tables(); print('ok')"
```

Expected: 打印 `ok`，且日志中出现 `自动迁移: 为表 apicendpoint 添加列 description`（首次运行；其后不会再出现，因为列已存在）。

**Step 3: 语法校验**

```bash
cd backend && .venv\Scripts\python -m py_compile db/models.py && echo OK
```

---

### Task 2: `api_context.py` 拼接 description + 新增字段级依赖分析

**Files:**
- Modify: `backend/app/services/api_context.py:32-125`（`build_api_context`）
- Add: `backend/app/services/api_context.py`（`analyze_field_dependencies` 函数，建议在 `build_api_context` 之前）
- Test (可选): `backend/tests/test_analyze_field_dependencies.py`

**Step 1: 提取响应顶层字段的小工具**

在 `api_context.py` 顶部（`parse_endpoint_ids` 之后）新增：

```python
def _get_response_top_fields(schema) -> list[str]:
    """返回 response_schema 的顶层字段名列表。"""
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return []
    return [str(k) for k in props.keys()]
```

**Step 2: 新增 `analyze_field_dependencies` 函数**

```python
def analyze_field_dependencies(
    session, endpoint_ids: list[int], project_id
) -> "tuple[list[str], dict[int, str]]":
    """字段级数据流依赖分析。

    返回 (dependency_descriptions, labels)：
    - dependency_descriptions: 人类可读的依赖描述列表，如
      "[接口2] 依赖 [接口1] 的字段 'id'（用于路径参数 {id}）"
    - labels: {endpoint_id: 标记文本}，用于在被依赖/依赖接口段打标
    """
    if not endpoint_ids:
        return [], {}
    eps = []
    for eid in endpoint_ids:
        ep = session.get(ApiEndpoint, eid)
        if ep:
            eps.append(ep)

    # 每个接口"提供的字段" = response_schema 顶层字段名（小写）
    provided = {ep.id: set(f.lower() for f in _get_response_top_fields(ep.response_schema)) for ep in eps}

    deps: list[str] = []
    labels: dict[int, str] = {}
    for ep in eps:
        # 收集该接口引用到的字段名：路径 {field}、parameters.key、body JSON 顶层 key
        refs: set[str] = set()
        for m in re.findall(r"\{([^}]+)\}", ep.path or ""):
            refs.add(m.split(":")[0].lower())
        for p in (ep.parameters or []):
            k = (p.get("key") or p.get("name") or "").lower()
            if k:
                refs.add(k)
        try:
            body_obj = json.loads(ep.body) if ep.body else {}
            if isinstance(body_obj, dict):
                refs.update(str(k).lower() for k in body_obj.keys())
        except Exception:
            pass

        for other in eps:
            if other.id == ep.id:
                continue
            common = refs & provided.get(other.id, set())
            if common:
                field_names = ", ".join(sorted(common))
                idx_ep = endpoint_ids.index(ep.id) + 1
                idx_other = endpoint_ids.index(other.id) + 1
                deps.append(
                    f"[接口{idx_ep}] 依赖 [接口{idx_other}] 的字段 {field_names}（用于路径/参数/请求体）"
                )
                labels.setdefault(ep.id, f"[依赖: 接口{idx_other}的字段 {field_names}]")
                labels.setdefault(other.id, f"[提供字段: {field_names}]")
    return deps, labels
```

> 说明：函数依赖 `re` 与 `json`，`api_context.py` 顶部已 `import json`；需 `import re`（若未导入则补上）。

**Step 3: 修改 `build_api_context` 拼接 description 与依赖**

(a) 在 `ep_lines.append(f'===== [{idx}] {endpoint_db.name}{dep_label} =====')` 之后插入 description：

```python
            ep_lines.append(f'===== [{idx}] {endpoint_db.name}{dep_label} =====')
            if endpoint_db.description:
                ep_lines.append('接口描述: ' + endpoint_db.description)
            ep_lines.append('所属项目: ' + (project_db.name if project_db else '未知'))
```

(b) 在 `for idx, eid in enumerate(...)` 循环之前（即 `endpoint_index_to_id = {}` 附近）之后，调用依赖分析并合并标签：

```python
    dependency_labels = dependency_labels or {}
    # 字段级依赖分析（接口测试专用，失败不影响主流程）
    try:
        dep_descs, field_labels = analyze_field_dependencies(session, endpoint_ids, project_id)
        for eid, lab in field_labels.items():
            dependency_labels.setdefault(eid, lab)  # 不覆盖已有的"依赖补全"标签
    except Exception as e:
        logger.warning(f"字段级依赖分析失败（已忽略）: {e}")
        dep_descs = []
    endpoint_index_to_id: dict[int, int] = {}
```

> 注意：`analyze_field_dependencies` 需要在 `endpoint_ids` 上做 `index()`，故调用位置应在 `endpoint_ids` 解析完成后、循环前。原 `dependency_labels = dependency_labels or {}` 与 `endpoint_index_to_id = {}` 在此处已存在，按上述调整后顺序放置即可。

(c) 在末尾拼接依赖章节（替换原 `all_api_info` 拼接处）：

```python
    all_api_info = '\n\n'.join(api_sections)
    if dep_descs:
        all_api_info += '\n\n## 接口间依赖关系\n' + '\n'.join(dep_descs)
    api_context = '\n\n===== 以下 API 接口信息（每个接口前的[数字]为接口编号）=====\n' + all_api_info
    return api_context, endpoint_index_to_id
```

**Step 4: （可选）写失败测试并运行**

`backend/tests/test_analyze_field_dependencies.py`：

```python
import pytest
from sqlmodel import SQLModel, Session, create_engine
from db.models import ApiEndpoint, ApiProject
from app.services.api_context import analyze_field_dependencies


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        proj = ApiProject(name="p")
        s.add(proj)
        s.commit()
        s.refresh(proj)
        creator = ApiEndpoint(project_id=proj.id, name="创建", method="POST", path="/users",
                               response_schema={"properties": {"id": {"type": "integer"}}})
        getter = ApiEndpoint(project_id=proj.id, name="查询", method="GET", path="/users/{id}")
        s.add(creator)
        s.add(getter)
        s.commit()
        s.refresh(creator)
        s.refresh(getter)
        s.expunge_all()
        # 重新载入（脱离会话）
        yield s
        SQLModel.metadata.drop_all(engine)


def test_detects_path_dependency(session):
    eps = session.exec(__import__("sqlmodel").select(ApiEndpoint)).all()
    ids = [e.id for e in eps]
    descs, labels = analyze_field_dependencies(session, ids, eps[0].project_id)
    assert any("依赖" in d for d in descs)
    assert any("提供字段" in v for v in labels.values())
```

运行：

```bash
cd backend && .venv\Scripts\python -m pytest tests/test_analyze_field_dependencies.py -v
```

Expected: PASS。

**Step 5: 语法校验**

```bash
cd backend && .venv\Scripts\python -m py_compile app/services/api_context.py && echo OK
```

---

### Task 3: 强化 `API_PROMPT`（接口为主角）

**Files:**
- Modify: `backend/utils/prompts.py:185-449`（`API_PROMPT`）

**Step 1: 在 `API_PROMPT` 开头加入核心原则**

将：

```python
    API_PROMPT = """
## API 测试专项规则
```

改为：

```python
    API_PROMPT = """
## API 测试专项规则

### 核心原则（最重要）

当上方提供了接口信息时，**接口信息（接口描述、字段、Schema、接口间依赖关系）是生成测试用例的主要依据**，用户需求仅作为补充背景。
- 不得脱离接口信息凭空编造用例；
- 每条用例应明确围绕一个或多个具体接口（用 endpoint_ref 引用）；
- 优先覆盖接口的请求字段、必填项、枚举与约束，而不是复述用户需求文本。
"""
```

**Step 2: 在 `### 接口引用` 之前加入"逐接口覆盖"与"逐字段覆盖"**

在 `### 接口引用` 章节前插入：

```python
"""
### 逐接口覆盖（必须）

对每个接口 [i]，必须至少生成：
- 1 个成功场景（case_level=1，使用完整有效参数）
- 针对其必填字段的异常场景（case_level=3，如缺失/类型错误/唯一性冲突）
- 边界场景（case_level=2，基于字段约束：空值/极值/超出长度/特殊字符）

### 逐字段覆盖（必须）

基于每个接口请求 Schema 的字段定义生成用例：
- 必填字段：缺失、空字符串、类型错误
- 有 enum 的字段：枚举外非法值 + 每个枚举值各一个
- 有 min/max/minLength/maxLength/pattern 的字段：边界值及上/下越界
- 字符串字段：超长、特殊字符（如 `<script>`、`' OR 1=1 --`）
```
```

**Step 3: 强化"接口依赖关系处理"章节**

将原有：

```python
当接口信息中包含 [依赖补全] 标记时，表示该接口需要先调用前置接口获取数据（如记录 ID）。
```

附近追加（在其后加入）：

```python
当存在"## 接口间依赖关系"章节时，必须生成至少一个跨接口端到端场景（case_level=4）：
1. 在 preset_conditions 中调用"提供字段"的接口（如创建记录）
2. 通过 post_actions + extract_jsonpath 提取被依赖字段（如 id）
3. 在后续步骤用 {{变量名}} 引用该字段
4. 每个依赖场景至少 1 成功 + 1 异常（如使用不存在的 ID）
```

**Step 4: 语法校验**

```bash
cd backend && .venv\Scripts\python -m py_compile utils/prompts.py && echo OK
```

---

### Task 4: 路由层在 API 场景将"用户需求"降权

**Files:**
- Modify: `backend/app/routes/testcase.py:507-510`

**Step 1: 修改 `effective_requirement` 拼接**

将：

```python
    if api_context:
        effective_requirement = api_context + "\n\n## 用户需求\n\n" + requirement
    else:
        effective_requirement = requirement
```

改为：

```python
    if api_context:
        # 接口测试中用户需求降为补充背景，避免模型脱离接口信息凭空生成用例
        effective_requirement = (
            api_context + "\n\n## 补充背景（次要，仅用于理解业务语境）\n\n" + requirement
        )
    else:
        effective_requirement = requirement
```

**Step 2: 语法校验**

```bash
cd backend && .venv\Scripts\python -m py_compile app/routes/testcase.py && echo OK
```

---

### Task 5: 技能注入在 API 场景下降权

**Files:**
- Modify: `backend/utils/model_utils.py:1063-1067`

**Step 1: 按 `is_api_test` 分支拼接**

将：

```python
    if selected_skill_names:
        skill_context = _load_skill_bodies(selected_skill_names)
        if skill_context:
            requirement = skill_context + "\n\n---\n\n## 用户需求\n\n" + requirement
            logger.info(f"已注入 {len(selected_skill_names)} 个技能到提示词: {selected_skill_names}")
```

改为：

```python
    if selected_skill_names:
        skill_context = _load_skill_bodies(selected_skill_names)
        if skill_context:
            if is_api_test:
                requirement = (
                    skill_context
                    + "\n\n---\n\n## 补充背景（次要，仅用于理解业务语境）\n\n"
                    + requirement
                )
            else:
                requirement = skill_context + "\n\n---\n\n## 用户需求\n\n" + requirement
            logger.info(f"已注入 {len(selected_skill_names)} 个技能到提示词: {selected_skill_names}")
```

> `is_api_test` 是 `generate_testcases` 的参数（第 968 行），此处可直接使用；功能测试时仍保持"## 用户需求"，不受影响。

**Step 2: 语法校验**

```bash
cd backend && .venv\Scripts\python -m py_compile utils/model_utils.py && echo OK
```

---

### Task 6: 前端类型增加 `description`

**Files:**
- Modify: `frontend/src/types/index.ts:267-287`（`interface ApiEndpoint`）

**Step 1: 增加字段**

在 `name: string;` 之后加入：

```typescript
export interface ApiEndpoint {
  id: number;
  project_id: number;
  name: string;
  description?: string;   // 接口业务描述（生成接口测试用例时使用）
  method: string;
  // ... 其余保持不变 ...
}
```

---

### Task 7: 前端接口编辑表单录入 `description`

**Files:**
- Modify: `frontend/src/components/api/ApiScenarioTestTool.tsx:850-866`（`buildEndpointUpdatePayload`）
- Modify: `frontend/src/components/api/ApiScenarioTestTool.tsx:2410-2412`（`Form.Item name="name"` 之后）
- （检查）若全仓库存在独立"新增接口" payload（如 `apiTestApi.createEndpoint`），同样补 `description`

**Step 1: payload 透传 description**

在 `buildEndpointUpdatePayload` 的返回对象中，`name: values.name,` 后加入：

```typescript
      name: values.name,
      description: values.description ?? selectedEndpoint.description ?? '',
      path: values.path,
```

**Step 2: 表单增加 description 输入**

在 `Form.Item name="name" label="接口名称"` 之后插入：

```tsx
            <Form.Item name="name" label="接口名称">
              <Input />
            </Form.Item>
            <Form.Item name="description" label="接口描述">
              <Input.TextArea rows={2} placeholder="接口的业务用途说明，如：该接口用于创建用户，写入用户表并返回用户ID" />
            </Form.Item>
```

**Step 3: 检查新增接口入口（如存在）**

搜索 `apiTestApi.createEndpoint`（全仓库）。若存在独立的创建 payload 构建，按 Step 1 同样加入 `description`。（经探查，当前组件以编辑/导入为主，若无独立 create 入口则此步可跳过。）

**Step 4: 前端类型检查**

```bash
cd frontend && npm run lint
```

Expected: 无新增 error / warning（`--max-warnings 0` 通过）。

---

### Task 8: 整体验证（隔离性确认）

**Files:** 无新增，纯验证。

**Step 1: 后端语法总校验**

```bash
cd backend && .venv\Scripts\python -m py_compile db/models.py app/services/api_context.py utils/prompts.py app/routes/testcase.py utils/model_utils.py && echo OK
```

**Step 2: 接口测试链路冒烟（可选，需模型可用）**

选 2~3 个已填 `description` 的接口生成用例，确认：
- 用例名/步骤围绕接口字段、描述、依赖（而非用户提示词）；
- 存在跨接口端到端场景（case_level=4）且使用了 `post_actions` 提取变量。

**Step 3: 功能测试不受影响确认**

使用纯功能测试入口（不传 `api_endpoint_id`）生成，确认：
- 用例为纯文本步骤（`steps` 为字符串数组），无 `api_call` 结构；
- 提示词未被"补充背景/接口间依赖"等改动影响（这些仅在 `is_api_test=True` 时生效）。

---

## 提交策略（遵循仓库 git 规则）

本计划**不自动执行 git commit**。完成以上 Task 后，将改动（含 `docs/plans/2026-07-10-api-testcase-around-endpoint-design.md` 与 `...-plan.md`）交由用户确认后再提交。
