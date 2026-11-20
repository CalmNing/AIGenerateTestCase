"""模板内置函数（{{$function}}）的共享实现。

代理转发（app/routes/proxy.py）与 Mock 服务器（app/routes/mock_server.py）都需要
替换 {{$function}} 形式的内置函数，两者历史上各自维护一份 _substitute_builtins。
本模块把实现收敛到一处，避免两份代码行为漂移。

支持的内置函数：
    $timestamp             毫秒级时间戳（13 位）
    $now                   秒级时间戳（10 位）
    $uuid                  UUID v4
    $randomInt             0 ~ 100 的随机整数
    $randomInt(min, max)   指定范围的随机整数
    $randomFloat           0 ~ 1 的随机浮点数，保留 2 位小数
    $randomFloat(min,max)  指定范围的随机浮点数，保留 2 位小数
    $randomFloat(min,max,d) 指定范围的随机浮点数，保留 d 位小数
    $date                  当前日期，格式 YYYY-MM-DD
    $date(format)          自定义格式日期
"""
import random
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

_RANDOM_FLOAT_RE = re.compile(
    r"^\$randomFloat\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*"
    r"(?:,\s*(\d+)\s*)?\)$"
)


def _to_fixed(value: float, digits: int) -> str:
    """按 JavaScript Number.prototype.toFixed 的规则格式化浮点数。

    与 Python 内置格式化有两点差异，这里都做了对齐：
    1. JS 用「四舍五入到最近，遇中点取绝对值更大者」，Python 的格式化用银行家舍入，
       例如 0.625 在 JS 中是 "0.63"，在 Python 中是 "0.62"。
    2. JS 保留负零，例如 (-0.001).toFixed(2) 得到 "-0.00"。
    """
    quantum = Decimal(1).scaleb(-digits)
    magnitude = Decimal(abs(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    return sign + format(magnitude, f".{digits}f")


def random_float(minimum: float, maximum: float, digits: int = 2) -> str:
    """生成指定范围的随机浮点数，格式化后返回字符串。

    对应 JavaScript 的 (Math.random() * (max - min) + min).toFixed(digits)。
    """
    if digits < 0:
        digits = 0
    if digits > 20:
        digits = 20
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return _to_fixed(random.uniform(minimum, maximum), digits)


def substitute_builtins(text: str) -> str:
    """替换文本中的 {{$function}} 内置函数。

    无法识别的表达式原样保留，与既有行为一致。
    """

    def _replace(match):
        expr = match.group(1).strip()

        # $timestamp
        if expr == "$timestamp":
            return str(int(datetime.now().timestamp() * 1000))

        # $now
        if expr == "$now":
            return str(int(datetime.now().timestamp()))

        # $uuid
        if expr == "$uuid":
            import uuid

            return str(uuid.uuid4())

        # $randomInt(min,max)
        rand_match = re.match(r"^\$randomInt\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)$", expr)
        if rand_match:
            lo, hi = int(rand_match.group(1)), int(rand_match.group(2))
            if lo > hi:
                lo, hi = hi, lo
            return str(random.randint(lo, hi))

        # $randomInt (0~100)
        if expr == "$randomInt":
            return str(random.randint(0, 100))

        # $randomFloat(min,max[,digits])
        float_match = _RANDOM_FLOAT_RE.match(expr)
        if float_match:
            lo = float(float_match.group(1))
            hi = float(float_match.group(2))
            digits = int(float_match.group(3)) if float_match.group(3) else 2
            return random_float(lo, hi, digits)

        # $randomFloat (0~1，保留 2 位小数)
        if expr == "$randomFloat":
            return random_float(0.0, 1.0, 2)

        # $date(format)
        date_match = re.match(r"^\$date\((.+)\)$", expr)
        if date_match:
            fmt = date_match.group(1).strip().strip("'\"")
            now = datetime.now()
            fmt_map = {
                "YYYY": str(now.year),
                "MM": f"{now.month:02d}",
                "DD": f"{now.day:02d}",
                "HH": f"{now.hour:02d}",
                "mm": f"{now.minute:02d}",
                "ss": f"{now.second:02d}",
                "SSS": f"{now.microsecond // 1000:03d}",
            }
            result = fmt
            for key, val in fmt_map.items():
                result = result.replace(key, val)
            return result

        # $date (默认 YYYY-MM-DD)
        if expr == "$date":
            return datetime.now().strftime("%Y-%m-%d")

        return match.group(0)

    return re.sub(r"\{\{(\$[^}]+)\}\}", _replace, text)
