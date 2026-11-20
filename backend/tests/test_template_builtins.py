"""模板内置函数（{{$function}}）的单元测试，重点覆盖 $randomFloat 随机浮点数。"""
import re

import pytest

from utils.template_builtins import random_float, substitute_builtins


def _float_of(text: str) -> float:
    return float(text)


def test_random_float_default_range_and_precision():
    """无参时返回 0~1 之间、保留 2 位小数的随机浮点数。"""
    for _ in range(200):
        value = substitute_builtins("{{$randomFloat}}")
        assert re.match(r"^\d+\.\d{2}$", value), value
        assert 0.0 <= _float_of(value) <= 1.0


def test_random_float_with_explicit_range():
    """$randomFloat(min,max) 在指定区间内取值。"""
    for _ in range(200):
        value = substitute_builtins("{{$randomFloat(10,20)}}")
        assert re.match(r"^\d+\.\d{2}$", value), value
        assert 10.0 <= _float_of(value) <= 20.0


def test_random_float_with_custom_digits():
    """第三个参数指定小数位数。"""
    for _ in range(50):
        value = substitute_builtins("{{$randomFloat(1,2,4)}}")
        assert re.match(r"^\d+\.\d{4}$", value), value


def test_random_float_supports_decimal_bounds():
    """上下界可以是小数。"""
    value = substitute_builtins("{{$randomFloat(0.5,1.5)}}")
    assert re.match(r"^\d+\.\d{2}$", value), value
    assert 0.5 <= _float_of(value) <= 1.5


def test_random_float_supports_negative_range():
    """负数区间同样可用。"""
    value = substitute_builtins("{{$randomFloat(-10,-5)}}")
    assert re.match(r"^-?\d+\.\d{2}$", value), value
    assert -10.0 <= _float_of(value) <= -5.0


def test_random_float_swaps_reversed_bounds():
    """下界大于上界时自动交换。"""
    value = substitute_builtins("{{$randomFloat(20,10)}}")
    assert 10.0 <= _float_of(value) <= 20.0


def test_random_float_is_actually_random():
    """多次调用应产生不同结果。"""
    values = {substitute_builtins("{{$randomFloat}}") for _ in range(50)}
    assert len(values) > 1


def test_random_float_matches_javascript_to_fixed_rounding():
    """四舍五入规则与 JavaScript toFixed 一致（中点取绝对值更大者）。"""
    # JavaScript: (0.625).toFixed(2) === "0.63"，而 Python 内置格式化会得到 "0.62"
    assert random_float(0.625, 0.625, 2) == "0.63"
    assert random_float(-0.125, -0.125, 2) == "-0.13"
    assert random_float(0.005, 0.005, 2) == "0.01"


def test_random_float_keeps_negative_zero():
    """JavaScript 会保留负零，这里行为一致。"""
    assert random_float(-0.001, -0.001, 2) == "-0.00"


def test_random_float_clamps_digit_count():
    """小数位数为负时按 0 处理，超过 20 时按 20 处理。"""
    assert re.match(r"^\d+$", random_float(1, 2, -1))
    value = random_float(1, 2, 25)
    assert re.match(r"^\d+\.\d{20}$", value), value


def test_existing_builtins_still_work():
    """既有内置函数不受影响。"""
    assert re.match(r"^\d{13}$", substitute_builtins("{{$timestamp}}"))
    assert re.match(r"^\d{10}$", substitute_builtins("{{$now}}"))
    assert re.match(r"^[0-9a-f-]{36}$", substitute_builtins("{{$uuid}}"))
    assert 0 <= int(substitute_builtins("{{$randomInt}}")) <= 100
    assert 1 <= int(substitute_builtins("{{$randomInt(1,10)}}")) <= 10
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", substitute_builtins("{{$date}}"))
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
        substitute_builtins("{{$date(YYYY-MM-DD HH:mm:ss)}}"),
    )


def test_unknown_builtin_is_left_untouched():
    """无法识别的内置函数原样保留。"""
    assert substitute_builtins("{{$notExist}}") == "{{$notExist}}"
    assert substitute_builtins("{{$randomFloatX}}") == "{{$randomFloatX}}"


def test_builtin_substitution_inside_text():
    """可与其它文本混排。"""
    result = substitute_builtins("value={{$randomFloat(1,2)}} end")
    assert result.startswith("value=")
    assert result.endswith(" end")
    assert 1.0 <= _float_of(result[len("value="):-len(" end")]) <= 2.0


def test_proxy_and_mock_share_the_same_builtin():
    """代理与 Mock 两条链路使用同一份内置函数实现。"""
    from app.routes.mock_server import _substitute_builtins
    from app.routes.proxy import _substitute_builtins as proxy_builtins

    # 两者委托给同一个实现，因此对确定性内置函数输出必然一致
    assert proxy_builtins("{{$date}}") == _substitute_builtins("{{$date}}")
    assert proxy_builtins("{{$notExist}}") == _substitute_builtins("{{$notExist}}")

    # 随机浮点数在各自调用时都会重新取值，只校验格式与区间
    for result in (proxy_builtins("{{$randomFloat(1,2)}}"), _substitute_builtins("{{$randomFloat(1,2)}}")):
        assert re.match(r"^\d+\.\d{2}$", result), result
        assert 1.0 <= _float_of(result) <= 2.0
