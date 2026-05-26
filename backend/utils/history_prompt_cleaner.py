import json
import re


def _extract_content_json(text: str) -> str | None:
    """Extract the real document body from MCP JSON payloads like {"content": "..."}."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        prefix = '{"content":"'
        if stripped.startswith(prefix) and stripped.endswith('"}'):
            body = stripped[len(prefix):-2]
            try:
                return json.loads(f'"{body}"').strip()
            except json.JSONDecodeError:
                return body.replace("\\n", "\n").replace('\\"', '"').strip()
        return None
    content = data.get("content") if isinstance(data, dict) else None
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


def _is_history_noise_segment(text: str) -> bool:
    markers = (
        "MCP error -32602",
        "Input validation error",
        "Invalid arguments for tool",
        "Invalid input: expected",
        "Invalid access token for authorization",
        "Please make a request with token attached",
        '"code":99991663',
        '"troubleshooter"',
        "Returning structured response:",
        "response=[TestCase(",
    )
    return any(marker in text for marker in markers)


def _is_tool_metadata_segment(text: str) -> bool:
    """Drop Feishu/MCP node metadata blocks; they are not requirement text."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return False

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        if re.search(r"\n\s*-{3,}\s*\n", stripped):
            return False
        metadata_markers = (
            '"node"',
            '"node_token"',
            '"obj_token"',
            '"obj_type"',
            '"space_id"',
        )
        return all(marker in stripped for marker in metadata_markers[:3])

    if not isinstance(data, dict):
        return False
    if "content" in data:
        return False
    node = data.get("node")
    if isinstance(node, dict):
        node_markers = ("node_token", "obj_token", "obj_type", "space_id", "title")
        return any(marker in node for marker in node_markers)
    return False


def _strip_testcase_section(text: str) -> str:
    """History prompts should keep requirements only, not existing/generated test cases."""
    patterns = (
        r"\n\s*用例\s*\n",
        r"\n\s*测试用例\s*\n",
        r"\n\s*验证场景\s*\n\s*前置条件\s*\n\s*操作步骤\s*\n\s*预期结果\s*\n",
    )
    cut_positions = []
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            cut_positions.append(match.start())
    if not cut_positions:
        return text
    return text[:min(cut_positions)].rstrip()


def clean_history_prompt_content(content: str | None) -> str:
    """Remove MCP/tool diagnostics and structured-output logs before saving history."""
    if not content:
        return ""

    text = str(content).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    text = re.split(r"\n\s*-{3,}\s*\n\s*Returning structured response:", text, maxsplit=1)[0]
    text = re.sub(
        r"\n?\s*Returning structured response:\s*response=\[TestCase\(.*\)\]\s*$",
        "",
        text,
        flags=re.DOTALL,
    )

    content_json = _extract_content_json(text)
    if content_json is not None:
        return _strip_testcase_section(content_json).strip()
    if _is_tool_metadata_segment(text):
        return ""

    cleaned_parts: list[str] = []
    for raw_part in re.split(r"\n\s*-{3,}\s*\n", text):
        part = raw_part.strip()
        if not part:
            continue

        extracted = _extract_content_json(part)
        if extracted is not None:
            stripped = _strip_testcase_section(extracted).strip()
            if stripped:
                cleaned_parts.append(stripped)
            continue

        if _is_history_noise_segment(part):
            continue
        if _is_tool_metadata_segment(part):
            continue

        stripped = _strip_testcase_section(part).strip()
        if stripped:
            cleaned_parts.append(stripped)

    return "\n\n---\n\n".join(cleaned_parts).strip()
