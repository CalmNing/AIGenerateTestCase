# 接口测试用例自动前置依赖补全 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在选择接口生成测试用例时，自动分析接口间的依赖关系，为每个"需要前置数据"的接口（详情/编辑/删除）自动注入"先调用新增接口创建记录"的前置步骤，并捕获返回的主键 id 绑定到依赖请求，确保每个接口的用例可独立、完整执行。

**Architecture:** 复用现有的依赖推断能力（`infer_endpoint_dependencies` 已把"新增"接口并入选中集合），新增两层纯函数：`map_endpoint_dependencies()` 计算 `{依赖接口id: 新增接口id}` 映射；`ensure_prerequisite_steps()` 在 `generate_testcases()` 模型返回后、`_persist_testcases()` 之前，对每条依赖用例注入 `preset_conditions` 的 `ApiCallStep`（调用新增接口 + `post_actions` 提取 id），并把依赖步骤的路径 `{id}` 参数绑定到该变量。提示词中补充"前置依赖必须"的硬规则。运行时已有的 `scenario_runner.infer_step_dependencies()` 作为安全网兜底。

**Tech Stack:** Python 3.11 / FastAPI / SQLModel + SQLite；Pydantic；pytest（已有 `backend/tests/`、`pytest.ini`，使用内存 SQLite fixture）。

---

## Task 1：新增 `map_endpoint_dependencies`（依赖映射，纯函数 + TDD）

**Files:**
- Modify: `backend/app/services/testcase_service.py`
- Test: `backend/tests/test_prerequisite_steps.py`（新建）

**Step 1：写失败测试**

`backend/tests/test_prerequisite_steps.py`：
```python
"""TDD for endpoint dependency -> create mapping and pre-step injection."""
from app.services.testcase_service import map_endpoint_dependencies, ensure_prerequisite_steps
from db.models import ApiProject, ApiEndpoint
from utils.models import TestCase, ApiCallStep


def _project(db_session):
    p = ApiProject(name="proj")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _ep(db_session, project_id, **kwargs):
    ep = ApiEndpoint(project_id=project_id, **kwargs)
    db_session.add(ep)
    db_session.commit()
    db_session.refresh(ep)
    return ep


def test_map_returns_create_for_path_id_dependency(db_session):
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders",
                 response_schema={"properties": {"data": {"properties": {"id": {"type": "integer"}}}}})
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    mapping = map_endpoint_dependencies(db_session, [create.id, detail.id], p.id)
    assert mapping.get(detail.id) == create.id


def test_map_empty_when_no_candidate_create(db_session):
    p = _project(db_session)
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    assert map_endpoint_dependencies(db_session, [detail.id], p.id) == {}


def test_map_empty_for_list_collection(db_session):
    # 列表查询（无 {id}）不应被判定为依赖
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders")
    lst = _ep(db_session, p.id, name="订单列表", method="GET", path="/orders")
    assert map_endpoint_dependencies(db_session, [create.id, lst.id], p.id) == {}
```

**Step 2：运行测试确认失败**
Run: `cd backend && python -m pytest tests/test_prerequisite_steps.py -v`
Expected: FAIL（`ImportError: cannot import name 'map_endpoint_dependencies'`）

**Step 3：最小实现**
在 `backend/app/services/testcase_service.py` 末尾新增（复用已存在的 `_best_matching_create_endpoint`、`_parse_body_fields`、`_get_response_top_fields`、`parse_json_field`）：

```python
def map_endpoint_dependencies(
    session: Session,
    endpoint_ids: list[int],
    api_project_id: int | str | None,
) -> dict[int, int]:
    """计算 {依赖接口id: 新增接口id} 映射。

    依赖方：路径含 {id}，或 body/params 含 id 字段且响应不含 id（详情/编辑/删除类）。
    新增候选：POST 且路径不含 /{ 或 : （整个项目范围，含已选中的）。
    """
    if not api_project_id or not endpoint_ids:
        return {}
    try:
        project_id = int(api_project_id)
    except (ValueError, TypeError):
        return {}

    all_endpoints = session.exec(
        select(ApiEndpoint).where(ApiEndpoint.project_id == project_id)
    ).all()
    if not all_endpoints:
        return {}

    selected_ids = set(endpoint_ids)
    selected_eps = [e for e in all_endpoints if e.id in selected_ids]
    path_param_pattern = re.compile(r'/\{|/:')
    candidate_create_eps = [
        e for e in all_endpoints
        if e.method.upper() == 'POST' and not path_param_pattern.search(e.path)
    ]
    if not candidate_create_eps:
        return {}

    id_param_pattern = re.compile(r'\{[^}]*id[^}]*\}', re.IGNORECASE)
    mapping: dict[int, int] = {}
    for ep in selected_eps:
        if id_param_pattern.search(ep.path or ""):
            best = _best_matching_create_endpoint(ep.path, candidate_create_eps)
            if best:
                mapping[ep.id] = best.id
            continue
        has_id_field = False
        for field_list in [ep.parameters or [], _parse_body_fields(ep.body)]:
            for field in field_list:
                fn = field.get('key', '') or field.get('name', '') or ''
                if 'id' in fn.lower():
                    has_id_field = True
                    break
            if has_id_field:
                break
        if has_id_field:
            resp_fields = _get_response_top_fields(ep.response_schema)
            if not any('id' in f.lower() for f in resp_fields):
                best = _best_matching_create_endpoint(ep.path, candidate_create_eps)
                if best:
                    mapping[ep.id] = best.id
    return mapping
```

**Step 4：运行测试确认通过**
Run: `cd backend && python -m pytest tests/test_prerequisite_steps.py -v`
Expected: PASS

**Step 5：提交**
```bash
git add backend/app/services/testcase_service.py backend/tests/test_prerequisite_steps.py
git commit -m "feat: add map_endpoint_dependencies for create-step inference"
```

---

## Task 2：新增 `ensure_prerequisite_steps`（注入前置步骤 + TDD）

**Files:**
- Modify: `backend/app/services/testcase_service.py`（新增导入与函数）
- Test: `backend/tests/test_prerequisite_steps.py`（追加用例）

**Step 1：写失败测试（追加到同一文件）**

```python
from utils.models import ApiCallKeyValue, PostAction


def _build_case(db_session, ep_by_ordinal, api_project_id, *, with_create_preset=False):
    """构造：endpoint_index_to_id = {1: create, 2: detail}。"""
    create, detail = ep_by_ordinal[1], ep_by_ordinal[2]
    endpoint_index_to_id = {1: create.id, 2: detail.id}
    presets = []
    if with_create_preset:
        presets.append(ApiCallStep(
            endpoint_ref=1, description="已存在的新增前置",
            post_actions=[PostAction(type="extract_jsonpath", key="record_id", jsonpath="$.data.id")],
        ))
    tc = TestCase(
        case_name="查询订单-成功",
        api_endpoint_ref=[2],
        steps=[ApiCallStep(endpoint_ref=2, description="查询订单")],
        preset_conditions=presets,
        expected_results=["返回订单详情"],
    )
    return tc, endpoint_index_to_id


def test_injects_create_preset_and_binds_id(db_session):
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders",
                 response_schema={"properties": {"data": {"properties": {"id": {"type": "integer"}}}}})
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    tc, e2i = _build_case(db_session, {1: create, 2: detail}, p.id)

    out = ensure_prerequisite_steps([tc], endpoint_index_to_id=e2i,
                                    db_session=db_session, api_project_id=p.id)
    case = out[0]
    # 前置注入了 1 个新增步骤
    api_call_presets = [s for s in case.preset_conditions if isinstance(s, ApiCallStep)]
    assert len(api_call_presets) == 1
    preset = api_call_presets[0]
    assert preset.endpoint_ref == 1
    assert preset.post_actions[0].key == "created_id"
    assert preset.post_actions[0].jsonpath == "$.data.id"
    # 依赖步骤的 {id} 路径参数绑定到 {{created_id}}
    dep_step = case.steps[0]
    id_param = next(p for p in dep_step.parameters if p.key == "id")
    assert id_param.value == "{{created_id}}"
    assert id_param.in_field == "path"


def test_no_injection_when_create_already_present(db_session):
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders",
                 response_schema={"properties": {"data": {"properties": {"id": {"type": "integer"}}}}})
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    tc, e2i = _build_case(db_session, {1: create, 2: detail}, p.id, with_create_preset=True)

    out = ensure_prerequisite_steps([tc], endpoint_index_to_id=e2i,
                                    db_session=db_session, api_project_id=p.id)
    case = out[0]
    api_call_presets = [s for s in case.preset_conditions if isinstance(s, ApiCallStep)]
    # 不重复注入（仍只有模型已有的 1 个）
    assert len(api_call_presets) == 1
    # 绑定复用已有变量名 record_id
    dep_step = case.steps[0]
    id_param = next(p for p in dep_step.parameters if p.key == "id")
    assert id_param.value == "{{record_id}}"


def test_no_injection_for_create_own_case(db_session):
    p = _project(db_session)
    create = _ep(db_session, p.id, name="创建订单", method="POST", path="/orders")
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    tc = TestCase(case_name="创建订单-成功", api_endpoint_ref=[1],
                  steps=[ApiCallStep(endpoint_ref=1, description="创建")],
                  preset_conditions=[], expected_results=["创建成功"])
    e2i = {1: create.id, 2: detail.id}
    out = ensure_prerequisite_steps([tc], endpoint_index_to_id=e2i,
                                    db_session=db_session, api_project_id=p.id)
    assert out[0].preset_conditions == []


def test_no_injection_when_no_candidate_create(db_session):
    p = _project(db_session)
    detail = _ep(db_session, p.id, name="查询订单", method="GET", path="/orders/{id}")
    tc = TestCase(case_name="查询订单-成功", api_endpoint_ref=[2],
                  steps=[ApiCallStep(endpoint_ref=2, description="查询")],
                  preset_conditions=[], expected_results=["返回详情"])
    e2i = {2: detail.id}
    out = ensure_prerequisite_steps([tc], endpoint_index_to_id=e2i,
                                    db_session=db_session, api_project_id=p.id)
    assert out[0].preset_conditions == []
```

**Step 2：运行测试确认失败**
Run: `cd backend && python -m pytest tests/test_prerequisite_steps.py -v`
Expected: FAIL（`ImportError: cannot import name 'ensure_prerequisite_steps'`）

**Step 3：最小实现**
在 `backend/app/services/testcase_service.py` 顶部导入补充：
```python
from utils.models import ApiCallStep, ApiCallKeyValue, PostAction
```
并在文件末尾新增以下辅助函数与主体函数：

```python
_PATH_ID_RE = re.compile(r'\{([^}]+)\}', re.IGNORECASE)


def _find_id_jsonpath(schema) -> str:
    """从响应 schema 中找到 id 字段的 JSONPath，找不到则返回默认 $.data.id。"""
    GENERIC = {"code", "msg", "message", "success", "data", "result",
               "results", "list", "items", "rows", "total", "page", "size", "status"}

    def search(props, prefix):
        for name, prop in (props or {}).items():
            if not isinstance(prop, dict):
                continue
            if name.lower().endswith("id") and name.lower() not in GENERIC:
                return f"{prefix}.{name}"
            sub = prop.get("properties")
            if isinstance(sub, dict):
                r = search(sub, f"{prefix}.{name}")
                if r:
                    return r
            if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
                items_props = prop["items"].get("properties")
                if isinstance(items_props, dict):
                    r = search(items_props, f"{prefix}.{name}[0]")
                    if r:
                        return r
        return None

    if isinstance(schema, dict):
        top = schema.get("properties")
        if isinstance(top, dict):
            r = search(top, "$")
            if r:
                return r
    return "$.data.id"


def _var_from_post_actions(post_actions) -> str | None:
    for a in (post_actions or []):
        if isinstance(a, PostAction):
            return a.key
        if isinstance(a, dict) and a.get("type") == "extract_jsonpath":
            return a.get("key")
    return None


def _bind_id_param(step: ApiCallStep, endpoint, var_name: str) -> None:
    """把步骤路径中的 {id} 参数绑定到 {{var_name}}。"""
    if not endpoint or not endpoint.path:
        return
    param_name = None
    for m in _PATH_ID_RE.finditer(endpoint.path or ""):
        name = m.group(1).split(":")[0]
        if 'id' in name.lower():
            param_name = name
            break
    if not param_name:
        return
    params = list(step.parameters) if step.parameters else []
    for p in params:
        if p.key == param_name:
            p.value = "{{" + var_name + "}}"
            if getattr(p, "in_field", None) is None:
                p.in_field = "path"
            step.parameters = params
            return
    params.append(ApiCallKeyValue(key=param_name, value="{{" + var_name + "}}", in_field="path"))
    step.parameters = params


def ensure_prerequisite_steps(
    local_testcases: list,
    *,
    endpoint_index_to_id: dict,
    db_session: Session,
    api_project_id,
) -> list:
    """为依赖接口用例注入"新增"前置步骤并绑定 id，保证独立可执行。

    仅当 endpoint_index_to_id / db_session / api_project_id 齐备时生效。
    直接修改并返回 local_testcases（pydantic 用例列表）。
    """
    if not endpoint_index_to_id or not db_session or not api_project_id:
        return local_testcases
    all_ids = list(endpoint_index_to_id.values())
    mapping = map_endpoint_dependencies(db_session, all_ids, api_project_id)
    if not mapping:
        return local_testcases

    id_to_ordinal = {eid: ordinal for ordinal, eid in endpoint_index_to_id.items()}
    needed_ids = set(mapping.keys()) | set(mapping.values())
    endpoint_map: dict[int, ApiEndpoint] = {}
    if needed_ids:
        rows = db_session.exec(
            select(ApiEndpoint).where(ApiEndpoint.id.in_(list(needed_ids)))
        ).all()
        endpoint_map = {e.id: e for e in rows}

    # 跨用例收集"新增"接口的可用 body（优先复用模型生成的新增用例体）
    create_body_by_id: dict[int, str] = {}
    for tc in local_testcases:
        for step in (getattr(tc, "steps", []) or []):
            if isinstance(step, ApiCallStep) and step.endpoint_ref in endpoint_index_to_id:
                eid = endpoint_index_to_id[step.endpoint_ref]
                if eid in mapping.values() and step.body and eid not in create_body_by_id:
                    create_body_by_id[eid] = step.body

    for tc in local_testcases:
        referenced: set[int] = set()
        if getattr(tc, "api_endpoint_ref", None):
            for ref in tc.api_endpoint_ref:
                if ref in endpoint_index_to_id:
                    referenced.add(endpoint_index_to_id[ref])
        for step in (getattr(tc, "steps", []) or []):
            if isinstance(step, ApiCallStep) and step.endpoint_ref in endpoint_index_to_id:
                referenced.add(endpoint_index_to_id[step.endpoint_ref])
        for pc in (getattr(tc, "preset_conditions", []) or []):
            if isinstance(pc, ApiCallStep) and pc.endpoint_ref in endpoint_index_to_id:
                referenced.add(endpoint_index_to_id[pc.endpoint_ref])

        dependents = [did for did in referenced if did in mapping]
        if not dependents:
            continue

        # 该用例中已存在的新增步骤 -> 复用其变量名
        existing_var: dict[int, str] = {}
        for step in (list(getattr(tc, "preset_conditions", []) or []) + list(getattr(tc, "steps", []) or [])):
            if isinstance(step, ApiCallStep) and step.endpoint_ref in endpoint_index_to_id:
                eid = endpoint_index_to_id[step.endpoint_ref]
                if eid in mapping.values():
                    v = _var_from_post_actions(step.post_actions)
                    if v:
                        existing_var[eid] = v

        preset_conditions = list(getattr(tc, "preset_conditions", []) or [])
        for dep_id in dependents:
            create_id = mapping[dep_id]
            if create_id not in id_to_ordinal:
                continue
            if create_id in existing_var:
                continue  # 已存在新增步骤，无需注入
            has_preset = any(
                isinstance(pc, ApiCallStep) and pc.endpoint_ref == id_to_ordinal[create_id]
                for pc in preset_conditions
            )
            if has_preset:
                continue
            create_ordinal = id_to_ordinal[create_id]
            create_ep = endpoint_map.get(create_id)
            jsonpath = _find_id_jsonpath(create_ep.response_schema if create_ep else None)
            body = create_body_by_id.get(create_id) or (create_ep.body if create_ep else None)
            preset_conditions.insert(0, ApiCallStep(
                endpoint_ref=create_ordinal,
                description="前置：调用新增接口创建测试数据",
                post_actions=[PostAction(type="extract_jsonpath", key="created_id", jsonpath=jsonpath)],
                body=body,
            ))
            existing_var[create_id] = "created_id"

        # 绑定：依赖步骤的路径 {id} 参数 -> {{变量名}}
        bind_steps = list(getattr(tc, "steps", []) or []) + list(preset_conditions)
        for step in bind_steps:
            if isinstance(step, ApiCallStep) and step.endpoint_ref in endpoint_index_to_id:
                eid = endpoint_index_to_id[step.endpoint_ref]
                if eid in mapping:
                    var = existing_var.get(mapping[eid]) or "created_id"
                    _bind_id_param(step, endpoint_map.get(eid), var)

        tc.preset_conditions = preset_conditions
    return local_testcases
```

**Step 4：运行测试确认通过**
Run: `cd backend && python -m pytest tests/test_prerequisite_steps.py -v`
Expected: PASS（含 Task 1 的用例，共 6 个）

**Step 5：提交**
```bash
git add backend/app/services/testcase_service.py backend/tests/test_prerequisite_steps.py
git commit -m "feat: inject create pre-step + bind id for dependent endpoints"
```

---

## Task 3：在生成流程中接线 `ensure_prerequisite_steps`

**Files:**
- Modify: `backend/utils/model_utils.py:1228-1231`（在 `_parse_response` 校验后、`_persist_testcases` 前）

**Step 1：写失败测试（集成层）**
在 `backend/tests/test_prerequisite_steps.py` 追加（验证 `generate_testcases` 已接线；无需真实 LLM，构造最小桩代价高，故改测服务层已覆盖；此处仅做 smoke 断言不破坏现有 `test_generate_testcases.py`）。可跳过独立测试，依赖 Task 2 单测 + 后续全量测试。

**Step 2：最小实现**
在 `backend/utils/model_utils.py` 第 1228 行（`if not isinstance(local_testcases, list): raise ...` 之后）插入：

```python
        # 自动补全依赖接口的前置"新增"步骤（仅接口测试且已加载接口映射时）
        if is_api_test and kwargs.get("endpoint_index_to_id"):
            try:
                from app.services.testcase_service import ensure_prerequisite_steps
                local_testcases = ensure_prerequisite_steps(
                    local_testcases,
                    endpoint_index_to_id=kwargs.get("endpoint_index_to_id", {}),
                    db_session=kwargs.get("db_session"),
                    api_project_id=kwargs.get("api_project_id"),
                )
            except Exception as e:
                logger.warning(f"前置依赖补全失败（已忽略，不影响生成）: {e}")
```

> `is_api_test` 是 `generate_testcases` 的命名参数（line 968），`db_session`/`api_project_id`/`endpoint_index_to_id` 经 `kwargs` 传入。

**Step 3：运行相关测试确认无回归**
Run: `cd backend && python -m pytest tests/test_generate_testcases.py tests/test_dependency_infer.py tests/test_prerequisite_steps.py -v`
Expected: PASS

**Step 4：提交**
```bash
git add backend/utils/model_utils.py
git commit -m "feat: wire ensure_prerequisite_steps into generate_testcases"
```

---

## Task 4：提示词补充"前置依赖必须"规则

**Files:**
- Modify: `backend/utils/prompts.py`（在 `### 变量使用` 块之后、`### 断言规则` 之前，约 line 272）

**Step 1：定位插入点**
在 `API_PROMPT` 字符串中，`**\`variables\` vs \`post_actions\` 的区别：**` 段（现 line 269-271）之后、`### 断言规则`（line 273）之前插入。

**Step 2：插入内容**
```python
        - **前置依赖（接口测试必须）**：对于路径含 `{id}`（或请求需要某个前置资源 id）的接口（如详情、编辑、删除），该用例**必须**先在 `preset_conditions` 中调用对应的"新增"接口创建前置数据，并通过 `post_actions` 提取返回的 id，再在后续步骤用 `{{变量名}}` 引用该 id（路径参数写 `in: path`）。
          - 每个需要前置数据的接口，都应拥有自己的"新增"前置步骤，确保该用例可独立、完整执行；
          - 新增前置步骤放在 `preset_conditions` 的**最前面**；
          - 若选中的接口集合里没有"新增"接口，系统会自动推断并补全，但你仍应在用例中显式安排它的前置调用与变量提取。
```

**Step 3：运行测试确认无语法/格式回归**
Run: `cd backend && python -c "import utils.prompts; print('prompts OK')"`
Expected: 输出 `prompts OK`

**Step 4：提交**
```bash
git add backend/utils/prompts.py
git commit -m "docs: add mandatory pre-dependency rule to API_PROMPT"
```

---

## Task 5：全量测试 + Lint + 收尾

**Files:**
- 全仓库后端测试

**Step 1：运行完整后端测试套件**
Run: `cd backend && python -m pytest -q`
Expected: 全部 PASS（或仅与本次无关的已知失败，需 review 说明）

**Step 2：前端无关；确认后端无新增 lint 阻断**
（后端无 ESLint；如项目有 ruff/flake8 配置则运行，否则跳过）

**Step 3：提交收尾（如分步提交已完成可合并为特性分支）**
```bash
git log --oneline -5   # 复核本次 4 个 feat 提交
```

---

## 已知限制（YAGNI，暂不处理）
- 链式依赖（新增接口自身还需别的前置数据）不递归处理。
- 注入的前置"新增"步骤 body 优先复用模型生成的新增用例体，否则用接口 DB 示例 body；若两者皆空，依赖运行时 schema 默认值。
- 默认开启，无前端开关；重复执行会产生多条记录，不自动清理。
- 若模型已在 `steps`（非 `preset_conditions`）里放了新增步骤且顺序在依赖步骤之后，确定性层会复用其变量名但不会重排顺序（极端情况由运行时 `infer_step_dependencies` 兜底）。
