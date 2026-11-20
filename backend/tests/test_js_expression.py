"""JS 表达式求值的单元测试：覆盖 UTF-8 编码修复、平衡括号扫描与 Python 快路径。"""
import re
import shutil
import subprocess
import time

import pytest

from utils.js_expression import (
    eval_js_expression,
    iter_js_expressions,
    substitute_js_expressions,
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js not available"
)

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_LOCALE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# --- A1: UTF-8 编码修复 ---------------------------------------------------


@requires_node
def test_chinese_string_is_not_mojibake():
    assert eval_js_expression("'你好'") == "你好"


@requires_node
def test_chinese_prefix_random_is_not_mojibake():
    result = eval_js_expression("'测试_' + Math.floor(Math.random()*1000)")
    assert result is not None
    assert result.startswith("测试_")
    assert result[len("测试_"):].isdigit()


@requires_node
def test_chinese_inside_object_is_not_mojibake():
    assert eval_js_expression("({a:'你好'})") == '{"a":"你好"}'


# --- A2: 平衡括号扫描器 ---------------------------------------------------


def test_iter_js_expressions_supports_nested_braces():
    assert iter_js_expressions("{{@Date.now()}}") == [(0, 15, "Date.now()")]
    assert iter_js_expressions("{{@[{a:1},{b:2}][0]}}") == [(0, 21, "[{a:1},{b:2}][0]")]


def test_iter_js_expressions_ignores_unclosed_and_plain():
    assert iter_js_expressions("{{@unclosed") == []
    assert iter_js_expressions("no js {{plain}}") == []


def test_iter_js_expressions_handles_multiple():
    assert len(iter_js_expressions("{{@Date.now()}}{{@Math.random()}}")) == 2


def test_iter_js_expressions_offsets_are_correct():
    text = "prefix {{@Date.now()}} middle {{@Math.random()}} suffix"
    matches = iter_js_expressions(text)
    assert len(matches) == 2
    for start, end, expr in matches:
        assert text[start:end] == "{{@" + expr + "}}"


def test_object_array_expression_is_matched():
    assert substitute_js_expressions("{{@[{a:1},{b:2}][0]}}") == '{"a":1}'


def test_balanced_braces_expression_is_evaluated():
    assert substitute_js_expressions("{{@JSON.stringify({a:{b:1}})}}") == '{"a":{"b":1}}'


def test_substitute_keeps_plain_variables_untouched():
    assert substitute_js_expressions("{{token}}") == "{{token}}"


def test_substitute_keeps_unclosed_marker():
    assert substitute_js_expressions("{{@unclosed") == "{{@unclosed"


def test_substitute_handles_multiple_expressions():
    result = substitute_js_expressions("{{@Date.now()}}{{@Date.now()}}")
    assert result != "{{@Date.now()}}{{@Date.now()}}"
    assert len(result.split("}}")) == 1
    assert result.isdigit()


def test_scanner_is_linear_on_pathological_input():
    payload = "{{@" + "{a:" * 200
    started = time.perf_counter()
    substitute_js_expressions(payload)
    assert (time.perf_counter() - started) < 0.1


def test_scanner_is_linear_on_many_unclosed_markers():
    payload = "{{@unclosed " * 200
    started = time.perf_counter()
    iter_js_expressions(payload)
    assert (time.perf_counter() - started) < 0.1


# --- A3: Python 快路径 ----------------------------------------------------


def test_date_now_offset_fast_path():
    now_ms = int(time.time() * 1000)
    result = eval_js_expression("Date.now() + 3600000")
    assert result is not None
    assert now_ms + 3600000 - 2000 <= int(result) <= now_ms + 3600000 + 2000


def test_date_now_offset_with_multiplication():
    now_ms = int(time.time() * 1000)
    result = eval_js_expression("Date.now() - 7*86400000")
    assert result is not None
    assert now_ms - 7 * 86400000 - 2000 <= int(result) <= now_ms - 7 * 86400000 + 2000


def test_date_now_seconds_fast_path():
    now_ms = int(time.time() * 1000)
    result = eval_js_expression("Math.floor(Date.now()/1000)")
    assert result is not None
    assert now_ms // 1000 - 2 <= int(result) <= now_ms // 1000 + 2


def test_iso_offset_format():
    result = eval_js_expression("new Date(Date.now() + 3600000).toISOString()")
    assert result is not None
    assert _ISO_RE.match(result), result


def test_iso_plain_format():
    result = eval_js_expression("new Date().toISOString()")
    assert result is not None
    assert _ISO_RE.match(result), result


def test_locale_string_format():
    result = eval_js_expression("new Date().toLocaleString('sv-SE')")
    assert result is not None
    assert _LOCALE_RE.match(result), result


def test_locale_string_offset_format():
    result = eval_js_expression("new Date(Date.now() + 3600000).toLocaleString('sv-SE')")
    assert result is not None
    assert _LOCALE_RE.match(result), result


def test_random_uuid_fast_path():
    result = eval_js_expression("crypto.randomUUID()")
    assert result is not None
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        result,
    ), result


def test_list_random_covers_all_candidates():
    seen = {
        eval_js_expression("['a','b','c'][Math.floor(Math.random()*3)]")
        for _ in range(100)
    }
    assert seen == {"a", "b", "c"}


def test_object_array_random_property():
    seen = {
        eval_js_expression("[{name:'张三'},{name:'李四'}][Math.floor(Math.random()*2)].name")
        for _ in range(100)
    }
    assert seen == {"张三", "李四"}


def test_prefix_random_fast_path():
    result = eval_js_expression("'测试_' + Math.floor(Math.random()*1000)")
    assert result is not None
    assert result.startswith("测试_")
    assert result[len("测试_"):].isdigit()


def test_existing_expressions_still_supported():
    assert eval_js_expression("['a','b'][Math.floor(Math.random()*2)]") in {"a", "b"}
    assert eval_js_expression("Math.random()*100|0").isdigit()
    assert eval_js_expression("[1,2,3][0]") == "1"
    assert eval_js_expression("new Date().getFullYear()").isdigit()


# --- Node 缺失兜底 -------------------------------------------------------


def test_missing_node_returns_none(monkeypatch):
    def _boom(*args, **kwargs):
        raise FileNotFoundError("node")

    monkeypatch.setattr(subprocess, "run", _boom)
    # "1+1" is not covered by the Python fast path, so it falls through to Node.
    assert eval_js_expression("1+1") is None
    assert substitute_js_expressions("{{@1+1}}") == "{{@1+1}}"
    assert substitute_js_expressions("x={{@1+1}}") == "x={{@1+1}}"
