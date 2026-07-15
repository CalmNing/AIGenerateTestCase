"""Shared API-context builder for test-case generation.

Builds the human-readable API-endpoint information block injected into the
generation prompt, plus the ordinal->endpoint-id map. Extracted from
duplicate copies in routes/pipeline.py and routes/testcase.py.
"""
import json
import logging
import re

from db.models import ApiEndpoint, ApiProject

logger = logging.getLogger(__name__)


def _get_response_top_fields(schema) -> list[str]:
    """返回 response_schema 的顶层字段名列表。"""
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return []
    return [str(k) for k in props.keys()]


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
    provided = {
        ep.id: set(f.lower() for f in _get_response_top_fields(ep.response_schema))
        for ep in eps
    }

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


def parse_endpoint_ids(value) -> list[int]:
    """Parse a comma-separated endpoint-id string (or int) into a list of ints."""
    if not value:
        return []
    if isinstance(value, int):
        return [value]
    ids: list[int] = []
    for part in str(value).split(","):
        part = part.strip()
        if part:
            try:
                ids.append(int(part))
            except ValueError:
                pass
    return ids


def build_api_context(
    session,
    endpoint_ids: list[int],
    project_id,
    overrides: dict,
    dependency_labels=None,
) -> "tuple[str, dict[int, int]]":
    """Build the API info text and the ordinal->endpoint-id map.

    `dependency_labels` optionally tags an endpoint's header with a label
    (e.g. {"依赖补全"}) for endpoints auto-added by dependency inference.
    """
    if not endpoint_ids:
        return "", {}

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
    api_sections: list[str] = []

    for idx, eid in enumerate(endpoint_ids, 1):
        try:
            endpoint_db = session.get(ApiEndpoint, eid)
            if not endpoint_db:
                continue
            endpoint_index_to_id[idx] = eid
            project_db = session.get(ApiProject, project_id or endpoint_db.project_id)
            ep_lines = []
            dep_label = f" [{dependency_labels[eid]}]" if eid in dependency_labels else ""
            ep_lines.append(f'===== [{idx}] {endpoint_db.name}{dep_label} =====')
            if endpoint_db.description:
                ep_lines.append('接口描述: ' + endpoint_db.description)
            ep_lines.append('所属项目: ' + (project_db.name if project_db else '未知'))
            ep_lines.append('请求方法: ' + endpoint_db.method)
            ep_lines.append('请求路径: ' + endpoint_db.path)
            if endpoint_db.tags:
                ep_lines.append('标签: ' + ', '.join(endpoint_db.tags))
            if endpoint_db.assertions:
                ep_lines.append('默认断言: ' + json.dumps(endpoint_db.assertions, ensure_ascii=False, indent=2))
            override = overrides.get(eid, {})
            ep_headers = override.get("headers") if override.get("headers") is not None else endpoint_db.headers
            ep_parameters = override.get("parameters") if override.get("parameters") is not None else endpoint_db.parameters
            ep_body = override.get("body") if override.get("body") is not None else endpoint_db.body
            if ep_headers:
                ep_lines.append('请求头: ' + json.dumps(ep_headers, ensure_ascii=False, indent=2))
            if ep_parameters:
                ep_lines.append('请求参数: ' + json.dumps(ep_parameters, ensure_ascii=False, indent=2))
            if endpoint_db.request_schema:
                ep_lines.append('请求 Schema:\n' + json.dumps(endpoint_db.request_schema, ensure_ascii=False, indent=2))
                # ---- 请求骨架（硬约束，供 LLM 严格遵循）----
                try:
                    from app.services.swagger_parser import build_body_from_schema
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
            if endpoint_db.response_schema:
                ep_lines.append('响应 Schema:\n' + json.dumps(endpoint_db.response_schema, ensure_ascii=False, indent=2))
            if ep_body:
                ep_lines.append('请求体示例:\n' + ep_body)
            if endpoint_db.pre_actions:
                ep_lines.append('前置操作: ' + json.dumps(endpoint_db.pre_actions, ensure_ascii=False, indent=2))
            if endpoint_db.post_actions:
                ep_lines.append('后置提取: ' + json.dumps(endpoint_db.post_actions, ensure_ascii=False, indent=2))
            api_sections.append('\n'.join(ep_lines))
            logger.info('已加载 API 端点: ' + endpoint_db.name + ' (' + endpoint_db.method + ' ' + endpoint_db.path + ')')
        except Exception as e:
            logger.warning('加载端点 ' + str(eid) + ' 失败: ' + str(e))

    if not api_sections:
        return "", {}

    all_api_info = '\n\n'.join(api_sections)
    if dep_descs:
        all_api_info += '\n\n## 接口间依赖关系\n' + '\n'.join(dep_descs)
    api_context = '\n\n===== 以下 API 接口信息（每个接口前的[数字]为接口编号）=====\n' + all_api_info
    return api_context, endpoint_index_to_id
