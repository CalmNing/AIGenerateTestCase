import json
import logging
import random
import re
import subprocess
from datetime import datetime
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
_RANDOM_INT_RE = re.compile(r"^Math\.random\(\)\s*\*\s*(\d+)\s*\|\s*0$")
_MATH_FLOOR_RANDOM_RE = re.compile(r"^Math\.floor\(\s*Math\.random\(\)\s*\*\s*(\d+)\s*\)$")


def _console_string(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


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


def _try_python_expression_fallback(expression: str) -> str | None:
    expr = expression.strip()

    date_result = _try_python_date_fallback(expr)
    if date_result is not None:
        return date_result

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

    return None


def eval_js_expression(expression: str) -> str | None:
    """Evaluate supported JS-like template expressions, with Node as fallback."""
    py_result = _try_python_expression_fallback(expression)
    if py_result is not None:
        return py_result

    try:
        proc = subprocess.run(
            ["node", "-e", f"console.log(({expression}))"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()

        logger.warning(
            "JS eval returned non-zero or empty: expr=%s, rc=%s, stderr=%s",
            expression,
            proc.returncode,
            proc.stderr.strip(),
        )
    except FileNotFoundError:
        logger.warning("Node.js not found, cannot evaluate JS expression: %s", expression)
    except Exception as exc:
        logger.warning("JS expression eval failed: %s, error: %s", expression, exc)

    return None
