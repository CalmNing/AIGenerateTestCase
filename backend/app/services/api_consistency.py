"""接口用例一致性检查与置信度打分（纯规则，无 LLM）。

对照 Swagger/OpenAPI schema 验证生成的 API 测试用例：
- 请求体字段是否都在 schema 内（无多余字段）
- 必填字段是否齐全
- 枚举值是否合法
- 断言引用的响应字段是否存在于 response_schema
并基于一致性通过率 + 用例完整度 + 测试类别给出置信度分级。
"""
import json
from typing import Any, Dict, List, Optional, Tuple


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


def _schema_path_present(schema: Any, required_name: str, body: Any) -> bool:
    """判断 body 里是否存在 schema 要求的字段（**递归**支持嵌套对象/数组）。

    `required` 列表里的名字可能是：
      - 顶层字段：`"username"`
      - 嵌套对象的字段：`"comment"` 出现在 `actionPayload.comment`
      - 数组元素的字段：`"value"` 出现在 `items[].value`

    判定规则：只要在 body 的**任意层级**能找到一个同名键即视为存在
    （schema 里的 required 是"该对象内必须有"，但不同层级同名键不冲突）。
    找不到时再按 schema 的 properties 递归下钻做精确匹配。
    """
    if body is None:
        return False
    if isinstance(body, dict):
        if required_name in body:
            return True
        # 递归下钻：schema 的 properties 里若有名为 required_name 的对象，
        # 尝试在 body 的同名子对象里找到它
        props = _schema_properties(schema)
        pdef = props.get(required_name)
        if isinstance(pdef, dict):
            if _schema_path_present(pdef, required_name, body.get(required_name)):
                return True
        # 任意层级同名键都算存在（宽松，避免误报）
        for value in body.values():
            if _schema_path_present(schema, required_name, value):
                return True
        return False
    if isinstance(body, list):
        return any(
            _schema_path_present(schema, required_name, item) for item in body
        )
    return False


def _jsonpath_field_key(jsonpath: str) -> Optional[str]:
    """从 jsonpath 取末级字段名用于粗匹配，如 $.data.id -> id。"""
    if not jsonpath:
        return None
    parts = [p for p in str(jsonpath).replace("$", "").split(".")
             if p and not p[0].isdigit() and "[" not in p]
    return parts[-1] if parts else None


def _get_steps(case: dict) -> List[dict]:
    """用例步骤支持 steps / test_steps 两种 key（pipeline 用 test_steps）。"""
    raw = case.get("steps")
    if not isinstance(raw, list):
        raw = case.get("test_steps")
    return raw if isinstance(raw, list) else []


def _resolve_step_endpoint_id(
    step: dict,
    endpoint_index_to_id: Dict[int, Any],
) -> Tuple[Optional[int], Optional[str]]:
    """解析步骤引用的接口 ID，返回 (endpoint_id, 解析方式)。

    兼容三种引用格式（这是**真 bug 修复**）：
      - `endpoint_ref`：序号（1-based），pipeline 生成时的格式；
      - `endpoint_id`：数据库主键，**落库后的实际格式**（单次生成路径 /
        pipeline 落库后写回的都是这个）；
      - 两者都无但有 `endpoint_ref_value`：兼容手工编辑。

    修复前只认 `endpoint_ref`，导致存量用例（steps 里是 `endpoint_id`）
    全部被判为"接口步骤不存在"，一致性通过率被严重低估（实测 200 条 0 条通过）。
    """
    if not isinstance(step, dict):
        return None, None

    # 1) 显式 endpoint_id（落库后的标准格式）
    raw_id = step.get("endpoint_id")
    if raw_id not in (None, ""):
        try:
            return int(raw_id), "endpoint_id"
        except (TypeError, ValueError):
            pass

    # 2) endpoint_ref：可能是序号（映射到 ID），也可能本身就是 ID
    raw_ref = step.get("endpoint_ref")
    if raw_ref not in (None, ""):
        try:
            ref_int = int(raw_ref)
        except (TypeError, ValueError):
            return None, None
        # 优先当序号解释（1-based 映射）
        mapped = endpoint_index_to_id.get(ref_int)
        if mapped is not None:
            return mapped, "endpoint_ref"
        # 否则当数据库主键解释
        return ref_int, "endpoint_ref_as_id"

    return None, None


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
    steps = _get_steps(case)

    if not steps:
        checks.append({"check": "has_steps", "passed": False, "detail": "用例没有 API 调用步骤"})
    else:
        checks.append({"check": "has_steps", "passed": True, "detail": f"共 {len(steps)} 个步骤"})

    # 1. 接口引用有效性 + 逐步骤字段校验
    #    （兼容 endpoint_ref 序号 / endpoint_id 主键两种格式，见 _resolve_step_endpoint_id）
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        eid, ref_kind = _resolve_step_endpoint_id(step, endpoint_index_to_id)
        ep = endpoints.get(eid) if eid is not None else None
        if ep is None:
            checks.append({"check": "endpoint_ref_valid", "passed": False,
                           "detail": f"步骤{i} 引用的接口({ref_kind})={eid} 无对应接口定义"})
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
                # 必填字段校验：**递归**比对嵌套结构。
                # 修复前只看顶层（`r not in body`），对 `{actionPayload:{comment:{value:..}}}`
                # 这类嵌套 body 会全部误报"缺必填字段"（实测 266 条误报）。
                missing = [
                    r for r in required
                    if not _schema_path_present(schema, r, body)
                ]
                checks.append({
                    "check": "required_fields_present",
                    "passed": not missing,
                    "detail": (f"步骤{i} 必填字段齐全" if not missing else f"步骤{i} 缺必填字段: {missing}"),
                })
                # 枚举校验（字段存在且 schema 含 enum 时，合法/非法都记录）
                for k, v in body.items():
                    pdef = props.get(k)
                    if isinstance(pdef, dict) and "enum" in pdef:
                        if v not in pdef["enum"]:
                            checks.append({"check": "enum_valid", "passed": False,
                                           "detail": f"步骤{i} 字段 {k}={v} 不在枚举 {pdef['enum']}"})
                        else:
                            checks.append({"check": "enum_valid", "passed": True,
                                           "detail": f"步骤{i} 字段 {k}={v} 枚举值合法"})
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
            eid, _ = _resolve_step_endpoint_id(step, endpoint_index_to_id)
            ep = endpoints.get(eid) if eid is not None else None
            if ep is not None:
                resp_schema = getattr(ep, "response_schema", None)
                if resp_schema:
                    break
    resp_props = _schema_properties(resp_schema)
    assertions: List[dict] = []
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


def score_confidence(consistency: dict, case: dict) -> Tuple[float, str]:
    """规则打分：一致性通过率(60%) + 完整度(30%) + 类别权重(10%)。"""
    cons_rate = float(consistency.get("pass_rate", 1.0))
    # 完整度：preconditions / steps / expected 是否齐全
    completeness = 0.0
    if case.get("preconditions"):
        completeness += 0.33
    steps = _get_steps(case)
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
