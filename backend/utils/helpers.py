"""工具函数和辅助类。"""

import json
import logging
import os
import re
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler
from sqlmodel import select, desc

from utils.history_prompt_cleaner import clean_history_prompt_content

logger = logging.getLogger(__name__)


def _split_numbered_text_items(text: str) -> list[str]:
    """Split inline numbered text like '1. foo 2. bar' into separate items."""
    stripped = (text or "").strip()
    if not stripped:
        return []

    line_parts = [part.strip() for part in re.split(r"\r?\n+", stripped) if part.strip()]
    if len(line_parts) > 1:
        items: list[str] = []
        for part in line_parts:
            items.extend(_split_numbered_text_items(part))
        return items

    matches = list(re.finditer(r"(?:^|\s)(\d+)\.\s+", stripped))
    if len(matches) < 2:
        return [stripped]

    items: list[str] = []
    for index, match in enumerate(matches):
        start = match.start(1)
        end = matches[index + 1].start(1) if index + 1 < len(matches) else len(stripped)
        part = stripped[start:end].strip()
        if part:
            items.append(part)
    return items or [stripped]


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


def _upsert_history_prompt(db_session, *, content: str | None, module_id, session_id: int, user_id: str | None = None):
    """实体合一后的统一入口（薄委托）。

    历史需求描述已并入需求管理实体：生成输入日志写入 requirement_doc
    （source_type='generation'），HistoryPrompt 表停写（保留作回滚备份）。
    """
    from app.services.requirement_doc import upsert_generation_entry

    return upsert_generation_entry(
        db_session,
        content=content,
        module_id=module_id,
        session_id=session_id,
        user_id=user_id,
    )


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
        result['case_level'] = 1  # 默认为功能测试

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
                normalized_pc.extend(_split_numbered_text_items(item))
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
        self.last_llm_response: str | None = None  # 捕获最后一次 LLM 原始响应
        # 缓存命中埋点
        self.prompt_tokens: int = 0
        self.cache_hit_tokens: int = 0
        self.cache_creation_tokens: int = 0

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

    def _record_cache_usage(self, response):
        """解析 LLM 返回的 usage，记录 prompt 缓存命中/创建 token 与命中率。

        兼容两种字段命名：
        - DeepSeek / OpenAI 兼容：prompt_cache_hit_tokens / prompt_cache_creation_tokens
        - Anthropic / LangChain usage_metadata：cache_read_input_tokens / cache_creation_input_tokens
        """
        usage = None
        try:
            if response is not None and getattr(response, "llm_output", None):
                usage = response.llm_output.get("token_usage") or response.llm_output.get("usage")
            if not usage and getattr(response, "usage_metadata", None):
                um = response.usage_metadata
                usage = {
                    "prompt_tokens": um.get("input_tokens", 0),
                    "completion_tokens": um.get("output_tokens", 0),
                    "total_tokens": um.get("total_tokens", 0),
                    "prompt_cache_hit_tokens": um.get("cache_read_input_tokens", 0),
                    "prompt_cache_creation_tokens": um.get("cache_creation_input_tokens", 0),
                }
        except Exception:
            return
        if not usage:
            return
        try:
            prompt = int(usage.get("prompt_tokens", 0) or 0)
            hit = int(
                usage.get("prompt_cache_hit_tokens")
                or usage.get("cache_read_input_tokens")
                or 0
            )
            creation = int(
                usage.get("prompt_cache_creation_tokens")
                or usage.get("cache_creation_input_tokens")
                or 0
            )
            self.prompt_tokens = prompt
            self.cache_hit_tokens = hit
            self.cache_creation_tokens = creation
            if prompt > 0:
                rate = hit / prompt * 100
                logger.info(
                    f"[缓存命中] prompt_tokens={prompt} cache_hit={hit} "
                    f"cache_creation={creation} 命中率={rate:.1f}%"
                )
            else:
                logger.info(
                    f"[缓存命中] prompt_tokens={prompt} cache_hit={hit} cache_creation={creation}"
                )
        except Exception as e:
            logger.warning(f"[缓存命中] 解析 usage 失败: {e}")

    def on_llm_end(self, response, **kwargs):
        """捕获 LLM 原始响应，用于调试结构化输出失败；同时记录缓存命中。"""
        self._record_cache_usage(response)
        try:
            if response and hasattr(response, 'generations') and response.generations:
                for gen_list in response.generations:
                    for gen in gen_list:
                        text = gen.text if hasattr(gen, 'text') else str(gen)
                        if text:
                            self.last_llm_response = text
                            # 检查是否有 tool_calls
                            if hasattr(gen, 'message') and gen.message:
                                msg = gen.message
                                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                    for tc in msg.tool_calls:
                                        logger.info(f"[诊断] LLM tool_call: name={tc.get('name', '?')}, args长度={len(str(tc.get('args', '')))}")
                                        args_str = str(tc.get('args', ''))
                                        logger.info(f"[诊断] LLM tool_call args 前500字符: {args_str[:500]}")
                                if hasattr(msg, 'content') and msg.content:
                                    content = str(msg.content)
                                    logger.info(f"[诊断] LLM content 前500字符: {content[:500]}")
        except Exception as e:
            logger.warning(f"[诊断] on_llm_end 记录响应异常: {e}")

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

# 单条历史需求注入 prompt 的最大字符数（P2：旧实现无截断，长历史会撑爆上下文）
_HISTORY_CONTENT_MAX_CHARS = 600

# 「已覆盖清单」最多展示的用例条数（P2：替代不存在的 get_existing_coverage 工具）
_COVERAGE_SUMMARY_LIMIT = 30

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


def _build_history_context(
    db_session,
    module_id: int,
    *,
    query: str = "",
    user_id: Optional[str] = None,
    session_id: Optional[int] = None,
    top_k: int = _HISTORY_PROMPT_LIMIT,
) -> str:
    """获取历史需求描述作为上下文（P2：相关性召回优先，时间序兜底）。

    改造前：只按 `module_id` 精确过滤 + 时间倒序取最近 20 条——"最近"而非"相关"，
    模块积累到几百条后最相关的早期需求会被挤掉，且跨模块经验完全不可复用。

    现在：
      1. 有 `query` 时先走 FTS5 相关性召回（utils/retrieval.py）；
      2. 召回不可用/无命中 → 回退到原来的时间序（行为与改造前一致）；
      3. 无论哪条路径，都做**字符级截断**（旧实现无截断，长历史会撑爆上下文）。

    历史需求仅用于辅助理解，不参与用例生成。
    无历史记录或 db_session 不可用时返回空字符串。
    """
    if db_session is None or module_id is None:
        return ""

    contents: list[str] = []

    # 1) 相关性召回（失败静默降级到时间序）
    if query:
        try:
            from utils.retrieval import retrieve_history

            items = retrieve_history(
                db_session,
                query=query,
                module_id=module_id,
                user_id=user_id,
                session_id=session_id,
                top_k=top_k,
            )
            contents = [item.content for item in items]
        except Exception:
            logger.warning("历史相关性召回失败，回退时间序", exc_info=True)
            contents = []

    # 2) 时间序兜底（源实体 = requirement_doc：登记文档 + 生成日志 + 手动录入）
    if not contents:
        try:
            from db.models import RequirementDoc
            prompts = db_session.exec(
                select(RequirementDoc.content_snapshot)
                .where(
                    RequirementDoc.module_id == module_id,  # type: ignore[attr-defined]
                    RequirementDoc.source_type.in_(["generation", "manual", "feishu"]),  # type: ignore[attr-defined]
                )
                .order_by(desc(RequirementDoc.updated_at))  # type: ignore[attr-defined]
                .limit(_HISTORY_PROMPT_LIMIT)
            ).all()
        except Exception:
            logger.warning("查询历史需求失败", exc_info=True)
            return ""
        contents = list(prompts)

    if not contents:
        return ""

    lines = ["## 历史需求上下文（仅供参考，不作为本次用例生成的需求）"]
    idx = 0
    for content in contents:
        cleaned_content = _clean_history_prompt_content(content)
        if not cleaned_content:
            continue
        idx += 1
        if len(cleaned_content) > _HISTORY_CONTENT_MAX_CHARS:
            cleaned_content = cleaned_content[:_HISTORY_CONTENT_MAX_CHARS] + "…"
        lines.append(f"{idx}. {cleaned_content}")
    if idx == 0:
        return ""
    return "\n".join(lines) + "\n\n"


def _retrieve_related_history(
    db_session,
    *,
    query: str,
    module_id: Optional[int],
    user_id: Optional[str] = None,
    session_id: Optional[int] = None,
    top_k: int = 8,
) -> str:
    """按相关性召回历史需求，返回可直接注入 prompt 的文本（P2）。

    与 `_build_history_context` 的区别：后者是给"单次生成"用的完整上下文块，
    这里给 pipeline 用（阶段 1 需要轻量、可控长度的参考）。

    检索不可用/无命中时返回空字符串（调用方继续，不报错）。
    """
    if db_session is None or not (query or "").strip():
        return ""
    try:
        from utils.retrieval import format_retrieved_context, retrieve_history

        items = retrieve_history(
            db_session,
            query=query,
            module_id=module_id,
            user_id=user_id,
            session_id=session_id,
            top_k=top_k,
        )
        if not items:
            return ""
        return format_retrieved_context(
            items,
            title="## 相关历史需求（按相关性召回，仅供参考，避免重复设计）",
        )
    except Exception:  # noqa: BLE001 - 检索失败绝不能影响生成
        logger.warning("召回相关历史需求失败（已忽略）", exc_info=True)
        return ""


def _build_coverage_summary(
    db_session,
    module_id: int,
    *,
    limit: int = _COVERAGE_SUMMARY_LIMIT,
) -> str:
    """生成「本模块已覆盖清单」：已有用例的标题 + 验证要点摘要。

    作用：替代 skills 里那个**后端并不存在**的 `get_existing_coverage` 工具
    （P2 修复）。技能教模型"先查已有覆盖再补差"，但该工具全库 0 实现，
    等于给模型注入了一段无法执行的指令；这里用真实数据满足同一意图。

    只读，不抛异常：失败返回空字符串。
    """
    if db_session is None or module_id is None:
        return ""

    try:
        from db.models import TestCase as DBTestCase

        rows = db_session.exec(
            select(DBTestCase.case_name, DBTestCase.expected_results, DBTestCase.case_level)
            .where(DBTestCase.module_id == module_id)
            .limit(limit * 3)
        ).all()
    except Exception:
        logger.warning("查询已有用例失败（已忽略）", exc_info=True)
        return ""

    if not rows:
        return ""

    seen: set[str] = set()
    items: list[str] = []
    for case_name, expected_results, case_level in rows:
        name = (case_name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        points: list[str] = []
        if isinstance(expected_results, list):
            for item in expected_results[:3]:
                text = str(item).strip()
                if text:
                    points.append(text)
        level_text = f"[等级{case_level}]" if case_level else ""
        suffix = f"（验证要点：{'；'.join(points)}）" if points else ""
        items.append(f"- {name} {level_text}{suffix}".strip())
        if len(items) >= limit:
            break

    if not items:
        return ""

    header = (
        "## 本模块已有测试用例（已覆盖清单，用于避免重复并识别覆盖缺口）\n"
        f"（共展示 {len(items)} 条，请勿重复设计已覆盖的场景）\n"
    )
    return header + "\n".join(items) + "\n\n"