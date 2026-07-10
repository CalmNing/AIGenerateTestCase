"""测试用例相关的纯业务逻辑与服务函数。

这些函数原本位于 ``app/routes/testcase.py``，属于与请求/响应对象无关的
纯数据逻辑。抽取到 service 层后，路由保持轻量，且可被路由、pipeline
以及测试直接复用，无需经由 ``app.routes.testcase`` 触发整个路由模块。
"""
import json
import re
from typing import Optional

from sqlmodel import Session, select

from db.models import ApiEndpoint
from utils.models import ApiCallStep, ApiCallKeyValue, PostAction


def parse_json_field(raw: Optional[str], default=None):
    """安全解析 JSON 字符串：解析失败或为空时返回 ``default``。

    替代散落的 ``json.loads`` + try/except 片段，统一处理空字符串、
    非法 JSON 等边界情况（与数据库 JSON 字符串列的存储约定一致）。
    """
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def _parse_body_fields(body: str | None) -> list[dict]:
    """从 body JSON 字符串中解析顶层字段列表。"""
    if not body:
        return []
    parsed = parse_json_field(body)
    if isinstance(parsed, dict):
        return [{'key': k} for k in parsed.keys()]
    return []


def _get_response_top_fields(response_schema: dict | None) -> list[str]:
    """从响应 schema 中提取顶层字段名。"""
    if not response_schema:
        return []
    props = response_schema.get('properties', {})
    if not props:
        items = response_schema.get('items', {})
        props = items.get('properties', {})
    return list(props.keys())


def _best_matching_create_endpoint(dependency_path: str, candidates: list):
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

        if dep_prefix and cand_prefix == dep_prefix:
            return ep

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

    dep_len = len(dep_segments)
    candidates_sorted = sorted(candidates, key=lambda ep: abs(len(path_segments(ep.path)) - dep_len))
    return candidates_sorted[0] if candidates_sorted else None


def _parse_endpoint_ids(endpoint_id_str: str | int | None) -> list[int]:
    """Parse comma-separated endpoint IDs string into int list."""
    if not endpoint_id_str:
        return []
    if isinstance(endpoint_id_str, int):
        return [endpoint_id_str]
    ids = []
    for part in str(endpoint_id_str).split(","):
        part = part.strip()
        if part:
            try:
                ids.append(int(part))
            except ValueError:
                pass
    return ids


def infer_endpoint_dependencies(
    session: Session,
    selected_endpoint_ids: list[int],
    api_project_id: int | str | None,
) -> list[int]:
    """推断选中接口缺失的前置依赖接口，返回需要补全的接口 ID 列表。

    规则：
    1. 识别候选"新增"接口：POST 方法且路径不含 ``/{`` 或 ``:``
    2. 识别"依赖方"接口：路径含 ``/{xxx}`` 且参数名含 id，或 body/parameters 含 id 字段但响应无该字段
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

    # 2. 检查是否已选中新增接口
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
        if id_param_pattern.search(ep.path):
            has_dependency = True
            dependency_paths.append(ep.path)
            continue

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


def map_endpoint_dependencies(
    session: Session,
    endpoint_ids: list[int],
    api_project_id: int | str | None,
) -> dict[int, int]:
    """计算 {依赖接口id: 新增接口id} 映射。

    依赖方判定（与 ``infer_endpoint_dependencies`` 一致）：
    - 路径含 ``{id}``；或
    - body/parameters 含 id 字段且响应 schema 不含 id（详情/编辑/删除类）。
    新增候选：POST 且路径不含 ``/{`` 或 ``:``（整个项目范围，含已选中的）。
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


_PATH_ID_RE = re.compile(r'\{([^}]+)\}', re.IGNORECASE)


def _find_id_jsonpath(schema) -> str:
    """从响应 schema 中找到 id 字段的 JSONPath；找不到则返回默认 ``$.data.id``。"""
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
    """返回第一个 extract_jsonpath 动作的变量名。"""
    for a in (post_actions or []):
        if isinstance(a, PostAction):
            return a.key
        if isinstance(a, dict) and a.get("type") == "extract_jsonpath":
            return a.get("key")
    return None


def _bind_id_param(step: ApiCallStep, endpoint, var_name: str) -> None:
    """把步骤路径中的 ``{id}`` 参数绑定到 ``{{var_name}}``。"""
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

        # 该用例中已存在的新增步骤 -> 复用其变量名（避免重复注入）
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
                continue
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
