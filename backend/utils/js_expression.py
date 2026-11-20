import json
import logging
import random
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DATE_METHOD_RE = re.compile(
    r"^new\s+Date\(\)\.(getFullYear|getMonth|getDate|getDay|getHours|getMinutes|getSeconds|getMilliseconds|getTime)(\s*\(\))?$",
    re.IGNORECASE,
)
_ARRAY_INDEX_RE = re.compile(r"^(\[[\s\S]+\])\s*\[\s*(\d+)\s*\]$")
_ARRAY_RANDOM_INDEX_RE = re.compile(
    r"^(\[[\s\S]+\])\s*\[\s*(?:Math\.random\(\)\s*\*\s*(\d+)\s*\|\s*0|Math\.floor\(\s*Math\.random\(\)\s*\*\s*(\d+)\s*\))\s*\]$"
)
_ARRAY_RANDOM_PROP_RE = re.compile(
    r"^(\[[\s\S]+\])\s*\[\s*"
    r"(?:Math\.random\(\)\s*\*\s*(\d+)\s*\|\s*0|Math\.floor\(\s*Math\.random\(\)\s*\*\s*(\d+)\s*\))"
    r"\s*\]\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*)$"
)
_RANDOM_INT_RE = re.compile(r"^Math\.random\(\)\s*\*\s*(\d+)\s*\|\s*0$")
_MATH_FLOOR_RANDOM_RE = re.compile(r"^Math\.floor\(\s*Math\.random\(\)\s*\*\s*(\d+)\s*\)$")
_DATE_NOW_OFFSET_RE = re.compile(r"^Date\.now\(\)\s*([+-])\s*([0-9]+(?:\s*\*\s*[0-9]+)*)$")
_DATE_NOW_FLOOR_SEC_RE = re.compile(r"^Math\.floor\(\s*Date\.now\(\)\s*/\s*1000\s*\)$")
_ISO_STRING_RE = re.compile(r"^new\s+Date\(\s*\)\s*\.\s*toISOString\(\s*\)$")
_ISO_STRING_OFFSET_RE = re.compile(
    r"^new\s+Date\(\s*Date\.now\(\)\s*([+-])\s*([0-9]+(?:\s*\*\s*[0-9]+)*)"
    r"\s*\)\s*\.\s*toISOString\(\s*\)$"
)
_LOCALE_STRING_RE = re.compile(
    r"^new\s+Date\(\s*\)\s*\.\s*toLocaleString\(\s*'sv-SE'\s*\)$"
)
_LOCALE_STRING_OFFSET_RE = re.compile(
    r"^new\s+Date\(\s*Date\.now\(\)\s*([+-])\s*([0-9]+(?:\s*\*\s*[0-9]+)*)"
    r"\s*\)\s*\.\s*toLocaleString\(\s*'sv-SE'\s*\)$"
)
_PREFIX_RANDOM_RE = re.compile(
    r"^(?P<quote>['\"])(?P<prefix>[^'\"]*)(?P=quote)\s*\+\s*"
    r"Math\.floor\(\s*Math\.random\(\)\s*\*\s*(?P<upper>[0-9]+)\s*\)$"
)

_JS_EXPRESSION_MARKER = "{{@"


def _console_string(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _iso_timestamp(epoch_ms: int) -> str:
    """把毫秒时间戳格式化为与 JavaScript toISOString() 一致的字符串。"""
    moment = datetime.fromtimestamp(epoch_ms // 1000, tz=timezone.utc).replace(
        microsecond=(epoch_ms % 1000) * 1000
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _parse_js_array(text: str) -> list[Any] | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        import json5

        parsed = json5.loads(text)
    except Exception:
        return None

    return parsed if isinstance(parsed, list) else None


def _try_python_date_fallback(expr: str) -> str | None:
    match = _DATE_METHOD_RE.match(expr.strip())
    if not match:
        return None

    method = match.group(1).lower()
    now = datetime.now()
    mapping = {
        "getfullyear": str(now.year),
        "getmonth": str(now.month - 1),
        "getdate": str(now.day),
        "getday": str((now.weekday() + 1) % 7),
        "gethours": str(now.hour),
        "getminutes": str(now.minute),
        "getseconds": str(now.second),
        "getmilliseconds": str(int(now.microsecond / 1000)),
        "gettime": str(int(now.timestamp() * 1000)),
    }
    return mapping.get(method)


def _parse_offset_amount(amount_text: str) -> int:
    """解析 "3600000" 或 "7*86400000" 形式的偏移量。"""
    total = 1
    for part in amount_text.split("*"):
        part = part.strip()
        if not part.isdigit():
            return 0
        total *= int(part)
    return total


def _try_python_expression_fallback(expression: str) -> str | None:
    expr = expression.strip()

    date_result = _try_python_date_fallback(expr)
    if date_result is not None:
        return date_result

    now = datetime.now()
    now_ms = int(now.timestamp() * 1000)

    # Date.now()
    if expr == "Date.now()":
        return str(now_ms)

    # Date.now() + 3600000 / Date.now() - 7*86400000
    offset_match = _DATE_NOW_OFFSET_RE.match(expr)
    if offset_match:
        amount = _parse_offset_amount(offset_match.group(2))
        sign = 1 if offset_match.group(1) == "+" else -1
        return str(now_ms + sign * amount)

    # Math.floor(Date.now()/1000)
    if _DATE_NOW_FLOOR_SEC_RE.match(expr):
        return str(now_ms // 1000)

    # new Date().toISOString()
    if _ISO_STRING_RE.match(expr):
        return _iso_timestamp(now_ms)

    # new Date(Date.now() + N).toISOString()
    iso_match = _ISO_STRING_OFFSET_RE.match(expr)
    if iso_match:
        amount = _parse_offset_amount(iso_match.group(2))
        sign = 1 if iso_match.group(1) == "+" else -1
        return _iso_timestamp(now_ms + sign * amount)

    # new Date().toLocaleString('sv-SE')
    if _LOCALE_STRING_RE.match(expr):
        return now.strftime("%Y-%m-%d %H:%M:%S")

    # new Date(Date.now() + N).toLocaleString('sv-SE')
    locale_match = _LOCALE_STRING_OFFSET_RE.match(expr)
    if locale_match:
        amount = _parse_offset_amount(locale_match.group(2))
        sign = 1 if locale_match.group(1) == "+" else -1
        moment = now + timedelta(milliseconds=sign * amount)
        return moment.strftime("%Y-%m-%d %H:%M:%S")

    # crypto.randomUUID()
    if expr == "crypto.randomUUID()":
        return str(uuid.uuid4())

    # 'prefix' + Math.floor(Math.random()*N)
    prefix_match = _PREFIX_RANDOM_RE.match(expr)
    if prefix_match:
        upper = int(prefix_match.group("upper"))
        if upper > 0:
            return prefix_match.group("prefix") + str(random.randrange(upper))

    match = _RANDOM_INT_RE.match(expr) or _MATH_FLOOR_RANDOM_RE.match(expr)
    if match:
        upper = int(match.group(1))
        if upper > 0:
            return str(random.randrange(upper))

    match = _ARRAY_INDEX_RE.match(expr)
    if match:
        values = _parse_js_array(match.group(1))
        index = int(match.group(2))
        if values is not None and 0 <= index < len(values):
            return _console_string(values[index])

    match = _ARRAY_RANDOM_INDEX_RE.match(expr)
    if match:
        values = _parse_js_array(match.group(1))
        upper_text = match.group(2) or match.group(3)
        upper = int(upper_text)
        if values and upper > 0:
            index = random.randrange(min(upper, len(values)))
            return _console_string(values[index])

    # [ ... ][Math.floor(Math.random()*N)].prop
    prop_match = _ARRAY_RANDOM_PROP_RE.match(expr)
    if prop_match:
        values = _parse_js_array(prop_match.group(1))
        upper_text = prop_match.group(2) or prop_match.group(3)
        upper = int(upper_text)
        if values and upper > 0:
            index = random.randrange(min(upper, len(values)))
            item = values[index]
            prop = prop_match.group(4)
            if isinstance(item, dict) and prop in item:
                return _console_string(item[prop])

    return None


def iter_js_expressions(text: str) -> list[tuple[int, int, str]]:
    """返回 {{@ ... }} 的 (start, end, expr) 区间列表，支持表达式内嵌套 {}。

    使用线性扫描而非正则：正则遇到表达式内部的 } 会提前中断匹配，且带嵌套量词的
    正则会在病态输入上产生灾难性回溯（直接挂死）。
    """
    results: list[tuple[int, int, str]] = []
    index = 0
    length = len(text)
    marker = _JS_EXPRESSION_MARKER
    while True:
        start = text.find(marker, index)
        if start < 0:
            break
        expr_start = start + len(marker)
        depth = 0
        cursor = expr_start
        while cursor < length:
            char = text[cursor]
            if char == "{":
                depth += 1
            elif char == "}":
                if depth == 0:
                    break
                depth -= 1
            cursor += 1
        if (
            cursor + 1 < length
            and text[cursor] == "}"
            and text[cursor + 1] == "}"
        ):
            results.append((start, cursor + 2, text[expr_start:cursor]))
            index = cursor + 2
        else:
            index = start + len(marker)
    return results


def substitute_js_expressions(text: str) -> str:
    """把 {{@expr}} 替换为求值结果；求值失败时保留原文。"""
    if not text or _JS_EXPRESSION_MARKER not in text:
        return text

    matches = iter_js_expressions(text)
    if not matches:
        return text

    pieces: list[str] = []
    cursor = 0
    for start, end, expr in matches:
        pieces.append(text[cursor:start])
        value = eval_js_expression(expr.strip())
        if value is None:
            logger.warning("{{@%s}} 表达式解析失败，保留原值", expr.strip())
            pieces.append(text[start:end])
        else:
            pieces.append(value)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def eval_js_expression(expression: str) -> str | None:
    """Evaluate supported JS-like template expressions, with Node as fallback."""
    py_result = _try_python_expression_fallback(expression)
    if py_result is not None:
        return py_result

    try:
        proc = subprocess.run(
            [
                "node",
                "-e",
                "const v=(" + expression + ");"
                "process.stdout.write(typeof v==='object'&&v!==null?JSON.stringify(v):String(v))",
            ],
            capture_output=True,
            timeout=5,
        )
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode == 0 and stdout:
            return stdout

        logger.warning(
            "JS eval returned non-zero or empty: expr=%s, rc=%s, stderr=%s",
            expression,
            proc.returncode,
            stderr,
        )
    except FileNotFoundError:
        logger.warning("Node.js not found, cannot evaluate JS expression: %s", expression)
    except Exception as exc:
        logger.warning("JS expression eval failed: %s, error: %s", expression, exc)

    return None
