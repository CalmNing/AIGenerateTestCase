# 接口测试用例生成准确性保证 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在「选中接口 → 生成接口测试用例」链路中，端到端保证四维度准确性（请求构造 / 断言 / 测试数据 / 依赖顺序），通过四道关：生成时 schema 强约束 → 生成后一致性检查 → 置信度分级 → 手动「验证」按钮真实执行回写。

**Architecture:** 复用现有 `api_context.build_api_context`（注入请求骨架）、`pipeline_runner._run_pipeline`（生成后处理挂载一致性+置信度）、`scenario_runner.run_scenario`（验证按钮实跑）。新增 `app/services/api_consistency.py`（一致性+置信度，纯规则无 LLM），TestCase 模型加 6 字段（自动迁移补齐），新增 `POST /api/testcases/{id}/validate` 路由与前端展示。

**Tech Stack:** FastAPI + SQLModel + SQLite（自动迁移）、LangChain 异步 agent、React 18 + TypeScript + Ant Design + axios。

**测试约定（来自现有 `backend/tests/`）：**
- 测试在 `backend/` 目录运行，使用 venv 解释器：
  `cd backend && $env:PYTHONPATH="."; .venv/Scripts/python -m pytest tests/<file>.py -v`
- `db_session` fixture 由 `backend/tests/conftest.py` 提供（in-memory DB）。新测试沿用该 fixture。

---

## Task 1：TestCase 模型加 6 字段（自动迁移）

**Files:**
- Modify: `backend/db/models.py`（`TestCase` 约 77-99 行）
- Test: `backend/tests/test_tc_accuracy_fields.py`

**Step 1: 写失败测试（验证字段存在 + 自动迁移）**

```python
from sqlmodel import Session, select
from db.models import TestCase
from sqlalchemy import inspect


def test_testcase_has_accuracy_fields(db_session: Session):
    cols = {c["name"] for c in inspect(db_session.get_bind()).get_columns("testcase")}
    for field in ("consistency_report", "confidence_score", "confidence_level",
                  "validation_status", "validation_detail", "validation_at"):
        assert field in cols, f"缺少字段 {field}"

    tc = TestCase(case_name="acc", confidence_score=0.9, confidence_level="high",
                  validation_status="pending")
    db_session.add(tc)
    db_session.commit()
    db_session.refresh(tc)
    assert tc.id is not None
    assert tc.confidence_level == "high"
```

**Step 2: 运行确认失败**
Run: `cd backend && $env:PYTHONPATH="."; .venv/Scripts/python -m pytest tests/test_tc_accuracy_fields.py -v`
Expected: FAIL（`consistency_report` 等不在列中）。

**Step 3: 实现（在 `TestCase` 类末尾、`session` 关系前插入）**

在 `backend/db/models.py` 第 96 行（`design_method` 字段）之后、第 98 行（`# 定义与会话的多对一关系`）之前插入：

```python
    # ===== 准确性保证字段 =====
    consistency_report: Optional[str] = Field(default=None, description="一致性检查报告(JSON字符串)")
    confidence_score: Optional[float] = Field(default=None, description="置信度评分 0~1")
    confidence_level: Optional[str] = Field(default=None, description="置信度等级: high/medium/low")
    validation_status: Optional[str] = Field(default=None, description="验证状态: pending/passed/failed/error")
    validation_detail: Optional[str] = Field(default=None, description="验证结果详情(JSON字符串)")
    validation_at: Optional[datetime] = Field(default=None, description="验证执行时间(UTC+8)")
```

**Step 4: 运行确认通过**
Run: 同 Step 2。Expected: PASS。

**Step 5: Commit**
```bash
git add backend/db/models.py backend/tests/test_tc_accuracy_fields.py
git commit -m "feat(models): add accuracy fields to TestCase (consistency/confidence/validation)"
```

---

## Task 2：生成时强约束（请求骨架 + prompt 硬约束）

**Files:**
- Modify: `backend/app/services/api_context.py`（`build_api_context` 追加骨架段）
- Modify: `backend/app/pipeline/prompts.py`（`API_TEST_CASE_DESIGNER_SYSTEM` 增加硬约束规则）
- Test: `backend/tests/test_api_context.py`（追加骨架断言）

**Step 1: 写失败测试（验证骨架注入）**

在 `backend/tests/test_api_context.py` 末尾追加：

```python
def test_build_api_context_includes_request_skeleton(db_session):
    from app.services.swagger_parser import build_body_from_schema
    project, ep1, _ = _make_project_with_endpoints(db_session)
    # 给 ep1 一个 request_schema
    ep1.request_schema = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "status": {"type": "string", "enum": ["active", "disabled"]},
        },
    }
    db_session.add(ep1)
    db_session.commit()
    ctx, _ = _build_api_context(db_session, [ep1.id], project.id, {})
    assert "请求骨架" in ctx
    assert "必填字段" in ctx
    assert "name" in ctx
    assert "enum" in ctx or "active" in ctx
```

**Step 2: 运行确认失败**
Expected: FAIL（`请求骨架` 未出现）。

**Step 3: 实现——修改 `build_api_context`**

在 `backend/app/services/api_context.py` 顶部 `import` 区补充：
```python
from app.services.swagger_parser import build_body_from_schema
```
并在 `build_api_context` 函数内、`api_sections.append('\n'.join(ep_lines))` 之前（约第 86 行前），插入骨架构建：

```python
            # ---- 请求骨架（硬约束，供 LLM 严格遵循）----
            if endpoint_db.request_schema:
                try:
                    skeleton_body = build_body_from_schema(endpoint_db.request_schema)
                    schema = endpoint_db.request_schema
                    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
                    required = schema.get("required", []) if isinstance(schema, dict) else []
                    lines = ["[请求骨架-硬约束，必须严格遵循，不得新增 schema 外字段]"]
                    lines.append(f"方法: {endpoint_db.method}")
                    lines.append(f"路径: {endpoint_db.path}")
                    if required:
                        lines.append("必填字段: " + ", ".join(str(r) for r in required))
                    for pname, pdef in props.items():
                        if not isinstance(pdef, dict):
                            continue
                        ptype = pdef.get("type", "")
                        extra = []
                        if "enum" in pdef:
                            extra.append("enum=" + "/".join(str(v) for v in pdef["enum"]))
                        for c in ("minLength", "maxLength", "minimum", "maximum", "pattern"):
                            if c in pdef:
                                extra.append(f"{c}={pdef[c]}")
                        suffix = f" ({ptype}" + (", " + ", ".join(extra) if extra else "") + ")"
                        lines.append(f"- {pname}{suffix}")
                    lines.append("请求体模板(JSON):")
                    lines.append(skeleton_body)
                    ep_lines.append("请求骨架:\n" + "\n".join(lines))
                except Exception:
                    pass
```

**Step 4: 修改 `API_TEST_CASE_DESIGNER_SYSTEM` 提示词**

在 `backend/app/pipeline/prompts.py` 的 `API_TEST_CASE_DESIGNER_SYSTEM` 末尾 `**约束：**` 段（第 243 行起）追加一条规则：

```python
- 严格遵循「请求骨架-硬约束」：请求方法/路径/字段名/类型必须与骨架一致，禁止新增 schema 中不存在的字段；必填字段必须出现；枚举字段取值必须合法
```

**Step 5: 运行确认通过**
Run: `cd backend && $env:PYTHONPATH="."; .venv/Scripts/python -m pytest tests/test_api_context.py -v`
Expected: PASS。

**Step 6: Commit**
```bash
git add backend/app/services/api_context.py backend/app/pipeline/prompts.py backend/tests/test_api_context.py
git commit -m "feat(api-context): inject request skeleton as hard constraint for API case design"
```

---

## Task 3：一致性检查服务（纯规则，对照 Swagger）

**Files:**
- Create: `backend/app/services/api_consistency.py`
- Test: `backend/tests/test_api_consistency.py`

该服务是机制 2 与机制 3 的核心。

**Step 1: 写失败测试**

```python
import json
from app.services.api_consistency import check_testcase_consistency, score_confidence


def _ep(request_schema=None, response_schema=None, method="POST"):
    class _E:
        pass
    e = _E()
    e.method = method
    e.request_schema = request_schema
    e.response_schema = response_schema
    return e


def test_consistency_no_extra_fields_and_required():
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "status": {"type": "string", "enum": ["active", "disabled"]},
        },
    }
    ep = _ep(request_schema=schema, response_schema={"type": "object", "properties": {"code": {"type": "integer"}}})
    index_to_id = {1: "epA"}
    endpoints = {"epA": ep}
    # 含多余字段 foo + 缺必填 name
    case = {
        "steps": [
            {"endpoint_ref": 1, "body": json.dumps({"status": "active", "foo": 1})}
        ],
        "assertions": [{"type": "jsonpath_exists", "jsonpath": "$.code"}],
    }
    report = check_testcase_consistency(case, index_to_id, endpoints)
    assert report["passed"] is False
    kinds = {c["check"] for c in report["checks"]}
    assert "no_extra_body_fields" in kinds
    assert "required_fields_present" in kinds
    assert "enum_valid" in kinds
    assert "assertion_fields_exist" in kinds
    # foo 是多余字段
    extra = next(c for c in report["checks"] if c["check"] == "no_extra_body_fields")
    assert extra["passed"] is False


def test_consistency_clean_case_passes():
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}, "status": {"type": "string", "enum": ["active"]}},
    }
    ep = _ep(request_schema=schema, response_schema={"type": "object", "properties": {"code": {"type": "integer"}}})
    index_to_id = {1: "epA"}
    endpoints = {"epA": ep}
    case = {
        "steps": [{"endpoint_ref": 1, "body": json.dumps({"name": "x", "status": "active"})}],
        "assertions": [{"type": "jsonpath_exists", "jsonpath": "$.code"}],
    }
    report = check_testcase_consistency(case, index_to_id, endpoints)
    assert report["passed"] is True
    assert report["pass_rate"] == 1.0
```

**Step 2: 运行确认失败**
Expected: FAIL（`module not found`）。

**Step 3: 实现 `backend/app/services/api_consistency.py`**

```python
"""接口用例一致性检查与置信度打分（纯规则，无 LLM）。

对照 Swagger/OpenAPI schema 验证生成的 API 测试用例：
- 请求体字段是否都在 schema 内（无多余字段）
- 必填字段是否齐全
- 枚举值是否合法
- 断言引用的响应字段是否存在于 response_schema
并基于一致性通过率 + 用例完整度 + 测试类别给出置信度分级。
"""
import json
from typing import Any, Dict, List, Optional


def _load_json(text: Any) -> Any:
    if text is None:
        return None
    if isinstance(text, (dict, list)):
        return text
    text = str(text).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _schema_properties(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _schema_required(schema: Any) -> List[str]:
    if not isinstance(schema, dict):
        return []
    req = schema.get("required")
    return list(req) if isinstance(req, list) else []


def _jsonpath_field_key(jsonpath: str) -> Optional[str]:
    """从 jsonpath 取末级字段名用于粗匹配，如 $.data.id -> id。"""
    if not jsonpath:
        return None
    parts = [p for p in str(jsonpath).replace("$", "").split(".") if p and not p[0].isdigit() and "[" not in p]
    return parts[-1] if parts else None


def check_testcase_consistency(
    case: dict,
    endpoint_index_to_id: Dict[int, Any],
    endpoints: Dict[Any, Any],
) -> dict:
    """检查一个 API 测试用例与 schema 的一致性。

    Args:
        case: 生成的用例 dict，含 steps（api_call 步骤，含 endpoint_ref/body/assertions）、assertions
        endpoint_index_to_id: 接口编号(ordinal,1-based) -> endpoint key（与 endpoints 的 key 对应）
        endpoints: endpoint key -> endpoint 对象（需有 method/request_schema/response_schema）
    Returns:
        {passed, pass_rate, checks:[{check, passed, detail, skipped?}]}
    """
    checks: List[dict] = []
    steps = case.get("steps") if isinstance(case.get("steps"), list) else []

    if not steps:
        checks.append({"check": "has_steps", "passed": False, "detail": "用例没有 API 调用步骤"})
    else:
        checks.append({"check": "has_steps", "passed": True, "detail": f"共 {len(steps)} 个步骤"})

    # 1. endpoint_ref 有效性 + 逐步骤字段校验
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        ref = step.get("endpoint_ref")
        eid = endpoint_index_to_id.get(ref) if isinstance(ref, int) else None
        ep = endpoints.get(eid) if eid is not None else None
        if ep is None:
            checks.append({"check": "endpoint_ref_valid", "passed": False,
                           "detail": f"步骤{i} endpoint_ref={ref} 无对应接口"})
            continue
        checks.append({"check": "endpoint_ref_valid", "passed": True,
                       "detail": f"步骤{i} -> 接口 {getattr(ep, 'method', '')} {getattr(ep, 'path', '')}"})

        schema = getattr(ep, "request_schema", None)
        props = _schema_properties(schema)
        required = _schema_required(schema)
        body = _load_json(step.get("body"))

        if props or required:
            if not isinstance(body, dict):
                checks.append({"check": "no_extra_body_fields", "passed": False,
                               "detail": f"步骤{i} 请求体缺失或非 JSON 对象"})
                checks.append({"check": "required_fields_present", "passed": False,
                               "detail": f"步骤{i} 请求体缺失，必填字段无法校验"})
            else:
                extra = [k for k in body.keys() if k not in props]
                checks.append({
                    "check": "no_extra_body_fields",
                    "passed": not extra,
                    "detail": (f"步骤{i} 字段均合法" if not extra else f"步骤{i} 含 schema 外字段: {extra}"),
                })
                missing = [r for r in required if r not in body]
                checks.append({
                    "check": "required_fields_present",
                    "passed": not missing,
                    "detail": (f"步骤{i} 必填字段齐全" if not missing else f"步骤{i} 缺必填字段: {missing}"),
                })
                # 枚举校验
                for k, v in body.items():
                    pdef = props.get(k)
                    if isinstance(pdef, dict) and "enum" in pdef and v not in pdef["enum"]:
                        checks.append({"check": "enum_valid", "passed": False,
                                       "detail": f"步骤{i} 字段 {k}={v} 不在枚举 {pdef['enum']}"})
        else:
            # 无 request_schema：跳过字段级检查，避免误判
            checks.append({"check": "no_extra_body_fields", "passed": True,
                           "detail": f"步骤{i} 接口无 request_schema，跳过字段校验", "skipped": True})
            checks.append({"check": "required_fields_present", "passed": True,
                           "detail": f"步骤{i} 接口无 request_schema，跳过必填校验", "skipped": True})

    # 2. 断言引用的响应字段是否存在于 response_schema
    resp_schema = None
    for step in steps:
        if isinstance(step, dict):
            eid = endpoint_index_to_id.get(step.get("endpoint_ref"))
            ep = endpoints.get(eid)
            if ep is not None:
                resp_schema = getattr(ep, "response_schema", None)
                if resp_schema:
                    break
    resp_props = _schema_properties(resp_schema)
    assertions = []
    if isinstance(case.get("assertions"), list):
        assertions.extend(case["assertions"])
    for step in steps:
        if isinstance(step, dict) and isinstance(step.get("assertions"), list):
            assertions.extend(step["assertions"])
    if not assertions:
        checks.append({"check": "assertion_fields_exist", "passed": True,
                       "detail": "无断言", "skipped": True})
    else:
        bad = []
        for a in assertions:
            if not isinstance(a, dict):
                continue
            if a.get("type") in ("jsonpath_exists", "jsonpath_equals"):
                key = _jsonpath_field_key(a.get("jsonpath", ""))
                if key and resp_props and key not in resp_props and "id" not in key:
                    # 末级字段不在 schema（排除泛化 id 字段），记录但不强制失败
                    bad.append(a.get("jsonpath"))
        checks.append({
            "check": "assertion_fields_exist",
            "passed": not bad,
            "detail": ("断言字段均存在于响应 schema" if not bad else f"断言引用字段不在响应 schema: {bad}"),
            "skipped": not bool(resp_props),
        })

    effective = [c for c in checks if not c.get("skipped")]
    passed_count = sum(1 for c in effective if c["passed"])
    total = len(effective) if effective else len(checks)
    pass_rate = (passed_count / total) if total else 1.0
    return {
        "passed": all(c["passed"] for c in effective) if effective else True,
        "pass_rate": round(pass_rate, 3),
        "checks": checks,
    }


def score_confidence(consistency: dict, case: dict) -> tuple[float, str]:
    """规则打分：一致性通过率(60%) + 完整度(30%) + 类别权重(10%)。"""
    cons_rate = float(consistency.get("pass_rate", 1.0))
    # 完整度：preconditions / steps / expected 是否齐全
    completeness = 0.0
    if case.get("preconditions"):
        completeness += 0.33
    steps = case.get("steps") if isinstance(case.get("steps"), list) else []
    if steps:
        completeness += 0.34
        # 步骤是否有 assertions / body 之类实质内容
        if any(isinstance(s, dict) and (s.get("assertions") or s.get("body") or s.get("parameters")) for s in steps):
            completeness += 0.33
    # 类别权重：异常/边界天然不确定性高
    level = case.get("case_level")
    cat_factor = 0.9 if level in (2, 3) else 1.0
    score = (cons_rate * 0.6 + completeness * 0.3) * cat_factor + 0.1 * cat_factor
    score = max(0.0, min(1.0, score))
    level_str = "high" if score >= 0.8 else ("medium" if score >= 0.5 else "low")
    return round(score, 3), level_str
```

**Step 4: 运行确认通过**
Run: `cd backend && $env:PYTHONPATH="."; .venv/Scripts/python -m pytest tests/test_api_consistency.py -v`
Expected: PASS。

**Step 5: Commit**
```bash
git add backend/app/services/api_consistency.py backend/tests/test_api_consistency.py
git commit -m "feat(consistency): add API testcase consistency check + confidence scoring"
```

---

## Task 4：将一致性 + 置信度挂载到生成后处理

**Files:**
- Modify: `backend/app/pipeline/pipeline_runner.py`（`_run_pipeline` 写库后计算并写入）
- Test: `backend/tests/test_pipeline_accuracy.py`

**背景**：`_run_pipeline` 在 `is_api_test` 时，循环处理每个 `case_data`（见约 509-553 行），构造 `tc_kwargs` 并 `TestCase(**tc_kwargs)` 写库，随后为接口用例创建 `ApiScenario`。`endpoint_index_to_id` 在同一作用域可用，且 `self.db` 是 Session。

**Step 1: 写失败测试（验证 pipeline 后处理写入字段）**

由于完整 pipeline 需要 LLM，这里用单测直接调用一个待抽取的辅助函数 `_compute_accuracy(case_data, endpoint_index_to_id, session)`（在 Task 实现中抽出）。测试：

```python
from app.pipeline.pipeline_runner import _compute_accuracy


def test_compute_accuracy_writes_report(db_session):
    from db.models import ApiEndpoint, ApiProject
    project = ApiProject(name="p")
    db_session.add(project)
    db_session.commit()
    ep = ApiEndpoint(project_id=project.id, name="c", method="POST", path="/x",
                     request_schema={"type": "object", "required": ["name"],
                                     "properties": {"name": {"type": "string"}}})
    db_session.add(ep)
    db_session.commit()
    case = {"steps": [{"endpoint_ref": 1, "body": '{"name": "ok"}'}], "preconditions": ["已登录"]}
    index_to_id = {1: ep.id}
    report, score, level = _compute_accuracy(case, index_to_id, db_session)
    assert report["passed"] is True
    assert score >= 0.8
    assert level == "high"
```

**Step 2: 运行确认失败**
Expected: FAIL（`_compute_accuracy` 未定义）。

**Step 3: 实现——在 `pipeline_runner.py` 新增模块级辅助函数**

在 `backend/app/pipeline/pipeline_runner.py` 顶部 import 区补充：
```python
from app.services.api_consistency import check_testcase_consistency, score_confidence
```
并新增函数（放在文件工具函数区）：

```python
def _compute_accuracy(case_data: dict, endpoint_index_to_id: dict, session) -> tuple[dict, float, str]:
    """计算用例一致性与置信度，返回 (report_dict, score, level)。无 LLM。"""
    endpoints = {}
    for ordinal, eid in endpoint_index_to_id.items():
        ep = session.get(ApiEndpoint, eid)
        if ep is not None:
            endpoints[eid] = ep
    report = check_testcase_consistency(case_data, endpoint_index_to_id, endpoints)
    score, level = score_confidence(report, case_data)
    return report, score, level
```

**Step 4: 在 `_run_pipeline` 写库处挂载**

在 `_run_pipeline` 构造 `tc_kwargs` 之后、`TestCase(**tc_kwargs)` 之前（约 553 行附近，`if is_api_test and api_project_id and all_test_cases:` 块之前），插入：

```python
                    # 准确性后处理（一致性 + 置信度），仅接口测试
                    if is_api_test and api_endpoint_ids and endpoint_index_to_id:
                        try:
                            report, score, level = _compute_accuracy(case_data, endpoint_index_to_id, self.db)
                            tc_kwargs["consistency_report"] = json.dumps(report, ensure_ascii=False)
                            tc_kwargs["confidence_score"] = score
                            tc_kwargs["confidence_level"] = level
                        except Exception as _acc_err:
                            logger.warning("[Pipeline] 准确性计算失败: %s", _acc_err)
```

注意：`pipeline_runner.py` 顶部需确保 `import json`（通常已存在，否则补充）。

**Step 5: 运行确认通过**
Run: `cd backend && $env:PYTHONPATH="."; .venv/Scripts/python -m pytest tests/test_pipeline_accuracy.py -v`
Expected: PASS。

**Step 6: Commit**
```bash
git add backend/app/pipeline/pipeline_runner.py backend/tests/test_pipeline_accuracy.py
git commit -m "feat(pipeline): compute consistency+confidence after API case generation"
```

---

## Task 5：手动「验证」按钮后端路由

**Files:**
- Modify: `backend/app/routes/testcase.py`（新增 `POST /{testcase_id}/validate`）
- Test: `backend/tests/test_validate_route.py`

复用 `scenario_runner.run_scenario`。若 TestCase 已有 `scenario_id` 直接复用；否则基于 `api_endpoint_id` + `steps` 临时构建 `ApiScenario`。

**Step 1: 写失败测试（respx 拦截真实请求）**

```python
import pytest
import respx
import httpx
from db.models import ApiEndpoint, ApiProject, ApiScenario, TestCase


def test_validate_route_runs_scenario_and_writes_status(db_session, client):
    project = ApiProject(name="p", base_url="http://api.test")
    db_session.add(project)
    db_session.commit()
    ep = ApiEndpoint(project_id=project.id, name="c", method="POST", path="/x",
                     request_schema={"type": "object", "properties": {"name": {"type": "string"}}})
    db_session.add(ep)
    db_session.commit()
    tc = TestCase(case_name="t", api_project_id=project.id,
                  api_endpoint_id=str(ep.id),
                  steps=[{"endpoint_ref": 1, "body": '{"name":"ok"}',
                          "assertions": [{"type": "status_code", "value": 200}]}],
                  validation_status="pending")
    db_session.add(tc)
    db_session.commit()
    db_session.refresh(tc)

    with respx.mock(assert_all_called=False) as mock:
        mock.post("http://api.test/x").mock(return_value=httpx.Response(200, json={"code": 0}))
        resp = client.post(f"/api/testcases/{tc.id}/validate")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["validation_status"] == "passed"
    db_session.refresh(tc)
    assert tc.validation_status == "passed"
```

（注：`client` 为 FastAPI TestClient fixture，需在 `conftest.py` 提供；若项目已有 `client` fixture 则直接复用，否则在 `conftest.py` 增加。）

**Step 2: 运行确认失败**
Expected: FAIL（路由不存在 404）。

**Step 3: 实现路由**

在 `backend/app/routes/testcase.py` 顶部 import 补充：
```python
from datetime import datetime
from app.services.scenario_runner import run_scenario
from db.models import ApiScenario, ApiEndpoint as ApiEndpointModel, ApiProject as ApiProjectModel
from utils.helpers import get_beijing_now  # 若项目无此辅助则改用 datetime.now()
```
（若 `utils.helpers` 无 `get_beijing_now`，用 `datetime.now()` 并注释 UTC+8 约定。）

在文件末尾新增路由：

```python
@router.post("/{testcase_id}/validate", response_model=Response)
async def validate_testcase(
    testcase_id: int,
    payload: Optional[dict] = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动触发真实执行验证：复用 scenario_runner 实跑并回写结果。"""
    tc = db.get(TestCase, testcase_id)
    if not tc:
        raise HTTPException(status_code=404, detail="测试用例不存在")
    if not tc.api_project_id or not tc.api_endpoint_id:
        raise HTTPException(status_code=400, detail="仅接口测试用例支持验证")

    project = db.get(ApiProjectModel, tc.api_project_id)
    if not project:
        raise HTTPException(status_code=404, detail="关联 API 项目不存在")

    env_id = None
    if isinstance(payload, dict):
        env_id = payload.get("environment_id")
    env_id = env_id or project.environment_id

    # 复用已有场景，或基于用例 steps 临时构建
    scenario = None
    if tc.scenario_id:
        scenario = db.get(ApiScenario, tc.scenario_id)
    if scenario is None:
        endpoint_ids = [int(x) for x in str(tc.api_endpoint_id).split(",") if x.strip().isdigit()]
        index_to_id = {i + 1: eid for i, eid in enumerate(endpoint_ids)}
        scenario_steps = []
        for step in (tc.steps or []):
            if not isinstance(step, dict):
                continue
            eid = index_to_id.get(step.get("endpoint_ref"))
            ep = db.get(ApiEndpointModel, eid) if eid else None
            if not ep:
                continue
            scenario_steps.append({
                "endpoint_id": ep.id,
                "description": step.get("description", ""),
                "headers": step.get("headers"),
                "parameters": step.get("parameters"),
                "body": step.get("body"),
                "assertions": step.get("assertions") or tc.assertions,
                "post_actions": step.get("post_actions"),
            })
        scenario = ApiScenario(
            name=f"verify-{tc.id}",
            project_id=project.id,
            environment_id=env_id,
            steps=scenario_steps,
            user_id=tc.user_id,
        )
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        tc.scenario_id = scenario.id

    try:
        result = await run_scenario(db, scenario, project)
        passed = bool(result.get("passed"))
        tc.validation_status = "passed" if passed else "failed"
        tc.validation_detail = json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        tc.validation_status = "error"
        tc.validation_detail = json.dumps({"error": str(exc)}, ensure_ascii=False)

    tc.validation_at = datetime.now()
    db.add(tc)
    db.commit()
    db.refresh(tc)
    return Response(data={
        "validation_status": tc.validation_status,
        "validation_detail": tc.validation_detail,
        "validation_at": str(tc.validation_at),
    })
```

（确认路由前缀：`router = APIRouter(prefix="/testcases", ...)` 且 main 挂载到 `/api`，故完整路径为 `/api/testcases/{id}/validate`。需核对 `routes/testcase.py` 是否使用 `/{session_id}/testcases` 风格——若是，则把路由改为 `/{session_id}/testcases/{testcase_id}/validate` 以匹配现有风格，并在测试中相应调整 URL。）

**Step 4: 运行确认通过**
Run: `cd backend && $env:PYTHONPATH="."; .venv/Scripts/python -m pytest tests/test_validate_route.py -v`
Expected: PASS。

**Step 5: Commit**
```bash
git add backend/app/routes/testcase.py backend/tests/test_validate_route.py
git commit -m "feat(validate): add POST /testcases/{id}/validate real-execution route"
```

---

## Task 6：前端展示（徽章 + 验证按钮）

**Files:**
- Modify: `frontend/src/types/index.ts`（TestCase 接口补 6 字段）
- Modify: `frontend/src/services/api.ts`（`validateTestcase(id, envId?)`）
- Modify: `frontend/src/components/test-case/TestCaseTable.tsx` 或 `TestcaseDetailView.tsx`（一致性/置信度/验证状态徽章 + 验证按钮）
- Test: `frontend` 跑 `npm run lint`

**Step 1: types 补字段**

在 `frontend/src/types/index.ts` 的 `TestCase` 接口加：
```ts
consistency_report?: string | null;
confidence_score?: number | null;
confidence_level?: 'high' | 'medium' | 'low' | null;
validation_status?: 'pending' | 'passed' | 'failed' | 'error' | null;
validation_detail?: string | null;
validation_at?: string | null;
```

**Step 2: api.ts 加方法**

在 `api.ts` 的 `testcaseApi` 对象加：
```ts
validateTestcase: (id: number, environmentId?: number) =>
  api.post(`/testcases/${id}/validate`, environmentId ? { environment_id: environmentId } : {}),
```
（注意与实际路由前缀一致，若后端用 `/{session_id}/testcases/{id}/validate` 则调整为对应路径。）

**Step 3: 列表/详情加展示与按钮**

在用例表格列中（仅 `api_endpoint_id` 非空时）：
- 一致性：解析 `consistency_report` 的 `pass_rate`，显示 `通过率 80%` 徽章，<100% 用 `warning` 色。
- 置信度：`confidence_level` 显示 `high/medium/low` 徽章，`low` 用 `error` 色并 tooltip 展示扣分原因（来自 `consistency_report.checks` 中 `passed=false` 的项）。
- 验证状态：`validation_status` 显示 `pending/passed/failed/error` 徽章。
- 「验证」按钮：点击调 `validateTestcase`，loading 态，完成后刷新该行并提示结果。

**Step 4: lint**
Run: `cd frontend && npm run lint`
Expected: 0 warnings。

**Step 5: Commit**
```bash
git add frontend/src/types/index.ts frontend/src/services/api.ts frontend/src/components/test-case/TestCaseTable.tsx frontend/src/components/test-case/TestcaseDetailView.tsx
git commit -m "feat(frontend): show consistency/confidence/validation + validate button"
```

---

## Task 7：集成验证与收尾

**Step 1: 后端全量测试**
Run: `cd backend && $env:PYTHONPATH="."; .venv/Scripts/python -m pytest tests/ -v`
Expected: 全部通过（重点 `test_api_consistency.py` / `test_pipeline_accuracy.py` / `test_validate_route.py`）。

**Step 2: 前端构建**
Run: `cd frontend && npm run build`
Expected: 构建成功。

**Step 3: 手动冒烟（可选）**
启动后端 + 前端，导入一个 Swagger，选中接口生成用例，确认：生成后用例带一致性通过率/置信度；点击「验证」实跑并回写 passed/failed。

**Step 4: 提交收尾**
```bash
git add -A
git commit -m "chore: finalize API testcase accuracy guarantee (4 mechanisms)"
```
（若仅需部分提交，按各 Task 已分步提交，此步可省略。）
