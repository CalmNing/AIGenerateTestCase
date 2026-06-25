"""工具函数和辅助类。"""

import json
import logging
import os
import re
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from sqlmodel import select, desc

from db.models import HistoryPrompt
from utils.history_prompt_cleaner import clean_history_prompt_content

logger = logging.getLogger(__name__)


def _load_json_object(text: str) -> dict | None:
    import json as _json

    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        data = _json.loads(stripped)
        return data if isinstance(data, dict) else None
    except _json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            return None
        try:
            data = _json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except _json.JSONDecodeError:
            return None


class _McpPermissionError(ValueError):
    """MCP tool returned a permission error that should be shown to the API caller."""


class _McpToolValidationError(ValueError):
    """MCP tool schema validation repeatedly failed and should stop the agent loop."""


class ModelServiceUnavailableError(ValueError):
    """Upstream LLM service is temporarily unavailable or overloaded."""


def _is_model_service_unavailable(error: Exception) -> bool:
    cause = getattr(error, "__cause__", None) or getattr(error, "__context__", None)
    text = f"{error!r}\n{cause!r}"
    markers = (
        "503",
        "Service Temporarily Unavailable",
        "service_unavailable_error",
        "Service is too busy",
        "temporarily switch to alternative LLM API service providers",
    )
    return any(marker in text for marker in markers)


def _extract_mcp_permission_error(output: object) -> str | None:
    text = str(output).strip()
    # 只在 MCP 错误响应（JSON 格式）中检测权限错误，避免误伤正常文档内容
    if not text or not text.startswith("{"):
        return None
    markers = (
        "权限不足",
        "缺少以下权限",
        "Access denied",
        "One of the following scopes is required",
        "应用尚未开通所需",
    )
    if not any(marker in text for marker in markers):
        return None

    for prefix in ("content='", 'content="'):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text[:4000]


def _extract_mcp_validation_error(output: object) -> str | None:
    text = str(output).strip()
    markers = (
        "MCP error -32602",
        "Input validation error",
        "Invalid arguments for tool",
        "Expected string",
        "Expected object",
        "Expected number",
        "不能同时提供",
    )
    if not text or not any(marker in text for marker in markers):
        return None
    return text[:2000]


def _extract_content_json(text: str) -> str | None:
    """Extract the real document body from MCP JSON payloads like {"content": "..."}."""
    import json as _json

    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = _json.loads(stripped)
    except _json.JSONDecodeError:
        prefix = '{"content":"'
        if stripped.startswith(prefix) and stripped.endswith('"}'):
            body = stripped[len(prefix):-2]
            try:
                return _json.loads(f'"{body}"').strip()
            except _json.JSONDecodeError:
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


def _clean_history_prompt_content(content: str | None) -> str:
    return clean_history_prompt_content(content)


def _normalize_history_module_id(module_id) -> int | None:
    if module_id in (None, "", 0, "0"):
        return None
    try:
        return int(module_id)
    except (TypeError, ValueError):
        return None


def _upsert_history_prompt(db_session, *, content: str | None, module_id, session_id: int, user_id: str | None = None) -> HistoryPrompt | None:
    """Save one cleaned history prompt per request scope/content."""
    cleaned_content = _clean_history_prompt_content(content)
    if not cleaned_content or db_session is None:
        return None

    normalized_module_id = _normalize_history_module_id(module_id)
    filters = [
        HistoryPrompt.content == cleaned_content,
        HistoryPrompt.session_id == session_id,
    ]
    if normalized_module_id is None:
        filters.append(HistoryPrompt.module_id.is_(None))
    else:
        filters.append(HistoryPrompt.module_id == normalized_module_id)
    if user_id is None:
        filters.append(HistoryPrompt.user_id.is_(None))
    else:
        filters.append(HistoryPrompt.user_id == user_id)

    existing = db_session.exec(select(HistoryPrompt).where(*filters)).first()
    if existing:
        return existing

    history = HistoryPrompt(
        content=cleaned_content,
        module_id=normalized_module_id,
        session_id=session_id,
        user_id=user_id,
    )
    db_session.add(history)
    db_session.commit()
    db_session.refresh(history)
    return history


def _repair_json(text: str) -> str:
    """Repair common JSON formatting issues from model output.

    Handles unescaped double quotes within Chinese text content,
    which is a common issue when models generate JSON with Chinese quotations.
    """
    import json as _json

    # Fast path: if it already parses, return as-is
    try:
        _json.loads(text)
        return text
    except _json.JSONDecodeError:
        pass

    # Try to fix unescaped quotes within CJK context.
    # Pattern: a CJK char or common Chinese punctuation, followed by ",
    # then text without structural chars, then " followed by CJK char.
    # We only fix quotes that are clearly inside string content.
    def _fix_inner_quotes(s: str) -> str:
        """Replace unescaped double quotes that appear between CJK characters
        with Unicode full-width quotation marks (U+201C/U+201D)."""
        import unicodedata

        chars = list(s)
        result = []
        i = 0
        while i < len(chars):
            ch = chars[i]
            if ch == '"':
                # Check if this looks like an inner quote surrounded by CJK context
                prev_char = chars[i - 1] if i > 0 else None
                next_char = chars[i + 1] if i + 1 < len(chars) else None

                def _is_cjk(c: str | None) -> bool:
                    if c is None:
                        return False
                    cp = ord(c)
                    return bool(
                        (0x4E00 <= cp <= 0x9FFF)
                        or (0x3000 <= cp <= 0x303F)  # CJK symbols/punctuation
                        or (0xFF00 <= cp <= 0xFFEF)  # Fullwidth forms
                        or c in "，。、；：？！）】】》」'"
                    )

                prev_is_cjk = _is_cjk(prev_char)
                next_is_cjk = _is_cjk(next_char)

                if prev_is_cjk:
                    # " after CJK text → opening quote or emphasis
                    result.append("“")  # left double quotation
                    i += 1
                    continue
                elif next_is_cjk:
                    # " before CJK text → closing quote
                    result.append("”")  # right double quotation
                    i += 1
                    continue

            result.append(ch)
            i += 1
        return "".join(result)

    # Apply fix iteratively: after replacing CJK quotes, the JSON structure
    # around the fix (the outer quotes) may now be clean.
    fixed = _fix_inner_quotes(text)
    try:
        _json.loads(fixed)
        return fixed
    except _json.JSONDecodeError:
        pass

    # Last resort: return original and let the caller handle the error
    return text


def _extract_json_with_response(text: str) -> dict | None:
    """从文本中提取最外层包含 'response' 或 'test_cases' 键的 JSON 对象（或数组）。

    使用括号/方括号深度跟踪来正确处理嵌套 JSON，比正则表达式更可靠。
    支持对象（{"response":...} / {"test_cases":...}）和数组（[...]）两种顶层格式。
    """
    import json as _json

    # 先尝试查找对象格式 {"response":...} 或 {"test_cases":...}
    _TARGET_KEYS = ('"response"', '"test_cases"')
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i+1]
                if any(k in candidate for k in _TARGET_KEYS):
                    try:
                        data = _json.loads(candidate)
                        if isinstance(data, dict) and ('response' in data or 'test_cases' in data):
                            return data
                    except _json.JSONDecodeError:
                        pass
                start = -1

    # 未找到对象格式，尝试查找顶层数组 [tc1, tc2, ...]
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '[':
            if depth == 0:
                start = i
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i+1]
                # 检查是否是对象数组（至少有一个元素是对象）
                if '{"' in candidate and '"name"' in candidate:
                    try:
                        arr = _json.loads(candidate)
                        if isinstance(arr, list) and len(arr) > 0 and isinstance(arr[0], dict):
                            return {"test_cases": arr}
                    except _json.JSONDecodeError:
                        pass
                start = -1

    return None


def _normalize_level(level: object) -> int:
    """将模型输出中的用例等级标准化为整数 1-4。

    支持格式：
    - 整数: 1-4
    - 字符串: "P0"-"P4", "1"-"4"
    - 中文: "功能测试", "边界测试", "异常测试", "场景测试"
    """
    if isinstance(level, int):
        return level if 1 <= level <= 4 else 4
    if isinstance(level, str):
        s = level.strip()
        # P0/P1/P2/P3/P4 (P0和P1都是功能测试→级别1)
        if s.upper().startswith('P'):
            try:
                n = int(s[1:])
                if n <= 1:
                    return 1
                if n == 2:
                    return 2
                if n == 3:
                    return 3
                return 4
            except (ValueError, IndexError):
                pass
        # 中文名称
        if '功能' in s:
            return 1
        if '边界' in s:
            return 2
        if '异常' in s:
            return 3
        if '场景' in s:
            return 4
        # 纯数字字符串
        try:
            n = int(s)
            return n if 1 <= n <= 4 else 4
        except ValueError:
            pass
    return 4


def _normalize_testcase_dict(d: dict) -> dict:
    """将模型输出的测试用例字段名标准化为框架期望的格式。

    处理模型常见的字段名差异，例如 name→case_name、level→case_level 等。
    同时处理中文键名（如 用例名称、前置条件、用例步骤、预期结果）。
    """
    from utils.models import ApiCallStep

    result: dict = {}

    # case_name（支持 用例名称、测试名称、场景名称 等中文键名）
    raw_name = (
        d.get('case_name')
        or d.get('name')
        or d.get('用例名称')
        or d.get('测试名称')
        or d.get('场景名称')
        or ''
    )
    result['case_name'] = raw_name if isinstance(raw_name, str) else str(raw_name) if raw_name else ''

    # case_level（支持 用例级别、级别、优先级、Priority 等）
    raw_level = d.get('case_level') or d.get('level') or d.get('用例级别') or d.get('级别')
    if raw_level is not None:
        result['case_level'] = _normalize_level(raw_level)
    else:
        result['case_level'] = 4

    # preset_conditions（支持 前置条件、前置条件、前提 等中文键名）
    raw_pc = (
        d.get('preset_conditions')
        or d.get('precondition')
        or d.get('preconditions')
        or d.get('前置条件')
        or d.get('前置步骤')
        or d.get('前提')
    )
    if raw_pc is not None:
        if not isinstance(raw_pc, list):
            raw_pc = [raw_pc]
        normalized_pc = []
        for item in raw_pc:
            if isinstance(item, str):
                normalized_pc.append(item)
            elif isinstance(item, ApiCallStep):
                # 已标准化过的 ApiCallStep 对象，保持原样
                normalized_pc.append(item)
            elif isinstance(item, dict):
                # 模型将 api_call 参数包裹在 {"api_call": {...}} 中
                inner = item.get('api_call', item)
                if isinstance(inner, dict):
                    try:
                        # 转换 endpoint_ref: "[1]" → 1
                        if 'endpoint_ref' in inner:
                            ref = inner['endpoint_ref']
                            if isinstance(ref, str):
                                m = re.search(r'\d+', ref)
                                if m:
                                    inner['endpoint_ref'] = int(m.group())
                        # 转换 dict 格式的 headers/parameters/variables 为列表
                        for field in ('headers', 'parameters', 'variables'):
                            if field in inner and isinstance(inner[field], dict):
                                inner[field] = [
                                    {"key": k, "value": str(v), "in": "query"}
                                    if field == 'parameters' else {"key": k, "value": str(v)}
                                    for k, v in inner[field].items()
                                ]
                        step_obj = ApiCallStep(**inner)
                        normalized_pc.append(step_obj)
                    except Exception:
                        # 转换失败时保留为字符串描述
                        normalized_pc.append(str(item))
                else:
                    normalized_pc.append(str(item))
            else:
                normalized_pc.append(str(item))
        result['preset_conditions'] = normalized_pc
    else:
        result['preset_conditions'] = []

    # expected_results（支持 预期结果、期望结果、验证点 等中文键名）
    raw_er = (
        d.get('expected_results')
        or d.get('expected_result')
        or d.get('预期结果')
        or d.get('期望结果')
        or d.get('验证点')
    )
    if raw_er is not None:
        if isinstance(raw_er, list):
            result['expected_results'] = [str(e) for e in raw_er]
        elif isinstance(raw_er, str):
            result['expected_results'] = [raw_er]
        else:
            result['expected_results'] = [str(raw_er)]
    else:
        result['expected_results'] = []

    # steps（支持 用例步骤、测试步骤、步骤 等中文键名）
    raw_steps = d.get('steps') or d.get('用例步骤') or d.get('测试步骤') or d.get('步骤') or []
    normalized_steps = []
    for step in (raw_steps if isinstance(raw_steps, list) else [raw_steps]):
        if isinstance(step, str):
            normalized_steps.append(step)
        elif isinstance(step, dict):
            # 模型将 api_call 参数包裹在 {"api_call": {...}} 中
            inner = step.get('api_call', step)
            if isinstance(inner, dict):
                try:
                    # 转换 endpoint_ref: "[1]" → 1
                    if 'endpoint_ref' in inner:
                        ref = inner['endpoint_ref']
                        if isinstance(ref, str):
                            m = re.search(r'\d+', ref)
                            if m:
                                inner['endpoint_ref'] = int(m.group())
                    # 转换 dict 格式的 headers/parameters/variables 为列表
                    for field in ('headers', 'parameters', 'variables'):
                        if field in inner and isinstance(inner[field], dict):
                            inner[field] = [
                                {"key": k, "value": str(v), "in": "query"}
                                if field == 'parameters' else {"key": k, "value": str(v)}
                                for k, v in inner[field].items()
                            ]
                    step_obj = ApiCallStep(**inner)
                    normalized_steps.append(step_obj)
                except Exception:
                    # 转换失败时保留为字符串描述
                    normalized_steps.append(str(step))
            else:
                normalized_steps.append(str(step))
        else:
            normalized_steps.append(str(step))
    result['steps'] = normalized_steps

    # api_endpoint_ref: 模型可能返回字符串 "[1]"、"[1,2]"、整数 1 或列表 [1,2]
    if 'api_endpoint_ref' in d:
        ref = d['api_endpoint_ref']
        if isinstance(ref, str):
            result['api_endpoint_ref'] = [int(x) for x in re.findall(r'\d+', ref)]
        elif isinstance(ref, list):
            result['api_endpoint_ref'] = ref
        elif isinstance(ref, int):
            result['api_endpoint_ref'] = [ref]
        else:
            result['api_endpoint_ref'] = None

    # assertions 字段保持原样
    if 'assertions' in d:
        result['assertions'] = d['assertions']

    return result


def _normalize_extracted_json(data: dict) -> dict:
    """将模型输出的 JSON 标准化为包含 response 数组的格式。

    处理 test_cases → response 的映射，并对每条 case 做字段名标准化。
    """
    if 'response' in data:
        if isinstance(data['response'], list):
            data['response'] = [_normalize_testcase_dict(tc) for tc in data['response']]
        return data

    if 'test_cases' in data:
        raw_list = data['test_cases']
        if isinstance(raw_list, list):
            data['response'] = [_normalize_testcase_dict(tc) for tc in raw_list]
        else:
            data['response'] = []
        return data

    return data


class _TokenUsageCallback(BaseCallbackHandler):
    """在 LLM 调用前记录消息总字符数，用于诊断上下文超限问题。"""
    def __init__(self):
        super().__init__()
        self.raise_error = True
        self.mcp_permission_error: str | None = None
        self.mcp_validation_error: str | None = None
        self._mcp_validation_error_count = 0
        self._last_mcp_validation_error: str | None = None

    def on_chat_model_start(self, serialized, messages, **kwargs):
        total = 0
        for msg_list in messages:
            for m in msg_list:
                total += len(m.content) if isinstance(m.content, str) else len(str(m.content))
        logger.warning(f"[诊断] 即将发送到 LLM 的消息总字符数: {total} (约 {total//4} tokens)")
        kw = kwargs.get("kwargs", {})
        if "tools" in kw:
            tools_str = str(kw["tools"])
            logger.warning(f"[诊断] tools 定义大小: {len(tools_str)} chars")
            total += len(tools_str)
        logger.warning(f"[诊断] 预估总计: {total} chars / {total//4} tokens / {total//2} CJK tokens")

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = serialized.get("name") if isinstance(serialized, dict) else None
        logger.info(f"[诊断] MCP 工具调用开始: tool={name} input={str(input_str)[:500]}")

    def on_tool_end(self, output, **kwargs):
        logger.info(f"[诊断] MCP 工具调用结束: output_preview={str(output)[:800]}")
        permission_error = _extract_mcp_permission_error(output)
        if permission_error:
            self.mcp_permission_error = permission_error
            raise _McpPermissionError(permission_error)
        validation_error = _extract_mcp_validation_error(output)
        if validation_error:
            self._mcp_validation_error_count += 1
            self._last_mcp_validation_error = validation_error
            if self._mcp_validation_error_count >= 3:
                self.mcp_validation_error = validation_error
                raise _McpToolValidationError(
                    "MCP 工具参数校验连续失败，已停止继续调用工具。"
                    f"最后一次错误：{validation_error}"
                )
        else:
            # 调用成功后重置计数，只有连续错误才累积
            self._mcp_validation_error_count = 0


_HISTORY_PROMPT_LIMIT = 20

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills")


def _load_skill_bodies(skill_names: list[str]) -> str:
    """Load markdown bodies of selected skills for prompt injection."""
    if not skill_names:
        return ""

    parts = []
    for name in skill_names:
        safe_name = os.path.basename(name)
        md_path = os.path.join(_SKILLS_DIR, safe_name, "SKILL.md")
        if not os.path.isfile(md_path):
            logger.warning(f"Skill file not found: {md_path}")
            continue
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            logger.warning(f"Failed to read skill file: {md_path}")
            continue

        m = re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL)
        body = content[m.end():].strip() if m else content

        display = name.replace("-", " ").title()
        parts.append(f"## Skill: {display}\n{body}")

    if not parts:
        return ""

    header = (
        "## 激活的测试方法论技能\n\n"
        "请在设计测试用例时严格遵循以下选中的方法论。"
        "各技能的方法论是互补的，请综合运用：\n\n"
    )
    return header + "\n\n---\n\n".join(parts)


def _build_history_context(db_session, module_id: int) -> str:
    """获取当前模块下最近 N 条历史需求描述，作为上下文供 agent 理解功能背景。

    历史需求仅用于辅助理解，不参与用例生成。
    无历史记录或 db_session 不可用时返回空字符串。
    """
    if db_session is None or module_id is None:
        return ""

    try:
        prompts = db_session.exec(
            select(HistoryPrompt.content)
            .where(HistoryPrompt.module_id == module_id)
            .order_by(desc(HistoryPrompt.created_at))
            .limit(_HISTORY_PROMPT_LIMIT)
        ).all()
    except Exception:
        logger.warning("查询历史提示词失败", exc_info=True)
        return ""

    if not prompts:
        return ""

    lines = ["## 历史需求上下文（仅供参考，不作为本次用例生成的需求）"]
    for i, content in enumerate(reversed(prompts), 1):
        cleaned_content = _clean_history_prompt_content(content)
        if cleaned_content:
            lines.append(f"{i}. {cleaned_content}")
    return "\n".join(lines) + "\n\n"
