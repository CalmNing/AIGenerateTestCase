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
