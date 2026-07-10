"""Shared API-context builder for test-case generation.

Builds the human-readable API-endpoint information block injected into the
generation prompt, plus the ordinal->endpoint-id map. Extracted from
duplicate copies in routes/pipeline.py and routes/testcase.py.
"""
import json
import logging

from db.models import ApiEndpoint, ApiProject

logger = logging.getLogger(__name__)


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
    api_context = '\n\n===== 以下 API 接口信息（每个接口前的[数字]为接口编号）=====\n' + all_api_info
    return api_context, endpoint_index_to_id
