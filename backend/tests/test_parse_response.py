"""Tests for the extracted _parse_response pure function."""
import pytest

from utils.model_utils import _parse_response
from utils.models import FunctionalTestCase, ResponseFormat, TestCase


def test_parse_response_dict_with_response_key():
    tc = TestCase(case_name="x", steps=["s1"], preset_conditions=[],
                  expected_results=["e1"], case_level=1)
    out = _parse_response({"response": [tc]})
    assert out == [tc]


def test_parse_response_structured_response_attr():
    rf = ResponseFormat(response=[
        TestCase(case_name="y", steps=[], preset_conditions=[],
                 expected_results=[], case_level=4)
    ])
    out = _parse_response({"structured_response": rf})
    assert out[0].case_name == "y"


def test_parse_response_response_format_object():
    rf = ResponseFormat(response=[
        TestCase(case_name="z", steps=[], preset_conditions=[],
                 expected_results=[], case_level=2)
    ])
    out = _parse_response(rf)
    assert out[0].case_name == "z"


def test_parse_response_none_raises():
    with pytest.raises(ValueError):
        _parse_response(None)


def test_parse_response_unsupported_type_raises():
    with pytest.raises(ValueError):
        _parse_response(12345)


def test_parse_response_messages_json_content():
    # Simulate an agent response dict whose last message content is JSON.
    class _Msg:
        def __init__(self, content):
            self.content = content
            self.tool_calls = []
    payload = '{"response": [{"case_name": "from_msg", "steps": [], "preset_conditions": [], "expected_results": [], "case_level": 1}]}'
    out = _parse_response({"messages": [_Msg(payload)]})
    assert out[0].case_name == "from_msg"


def test_testcase_schema_splits_inline_numbered_preconditions():
    tc = TestCase(
        case_name="x",
        steps=["s1"],
        preset_conditions=["1. 用户已登录 2. 用户有权限 3. 已准备合法文件"],
        expected_results=["e1"],
        case_level=1,
    )
    assert tc.preset_conditions == [
        "1. 用户已登录",
        "2. 用户有权限",
        "3. 已准备合法文件",
    ]


def test_functional_testcase_schema_splits_inline_numbered_preconditions():
    tc = FunctionalTestCase(
        case_name="x",
        steps=["s1"],
        preset_conditions=["1. 用户已登录 2. 用户有权限 3. 已准备合法文件"],
        expected_results=["e1"],
        case_level=1,
    )
    assert tc.preset_conditions == [
        "1. 用户已登录",
        "2. 用户有权限",
        "3. 已准备合法文件",
    ]
