"""AI 对话模式与业务上下文注入测试。

覆盖点：
1. mode 选择与非法值回退 general；
2. context 注入 system prompt 与超长截断；
3. REQUIREMENT_LIST_TEMPLATE 含 14 个小节标题；
4. ChatRequest 缺省字段向后兼容。

全部用例不触网、不调真实 LLM。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routes.chat import (
    ChatRequest,
    _build_chat_system_prompt,
    _normalize_mode,
    _truncate_context,
)
from app.services.chat_prompts import (
    CHAT_GENERAL_PROMPT,
    MCP_TOOL_HEADER,
    MODE_PROMPTS,
    REQUIREMENT_CLARIFY_PROMPT,
    REQUIREMENT_LIST_TEMPLATE,
    SKILLS_HEADER,
    TESTCASE_OPTIMIZE_PROMPT,
    VALID_MODES,
)


# ===== mode 规范化 =====

def test_normalize_mode_valid():
    for mode in VALID_MODES:
        assert _normalize_mode(mode) == mode


@pytest.mark.parametrize("bad", [None, "", "unknown", "CLARIFY", "  requirement_clarify"])
def test_normalize_mode_fallback(bad):
    assert _normalize_mode(bad) == "general"


# ===== 模式提示词 =====

def test_mode_prompts_cover_all_modes():
    assert set(MODE_PROMPTS.keys()) == {"general", "requirement_clarify", "testcase_optimize"}


def test_build_prompt_default_is_general():
    prompt = _build_chat_system_prompt()
    assert prompt == CHAT_GENERAL_PROMPT


def test_build_prompt_requirement_clarify_contains_template():
    prompt = _build_chat_system_prompt(mode="requirement_clarify")
    assert REQUIREMENT_CLARIFY_PROMPT in prompt
    assert REQUIREMENT_LIST_TEMPLATE in prompt
    # general 模式不注入清单模板
    assert REQUIREMENT_LIST_TEMPLATE not in _build_chat_system_prompt()


def test_build_prompt_testcase_optimize():
    prompt = _build_chat_system_prompt(mode="testcase_optimize")
    assert TESTCASE_OPTIMIZE_PROMPT in prompt
    assert REQUIREMENT_LIST_TEMPLATE not in prompt


def test_build_prompt_invalid_mode_falls_back_to_general():
    prompt = _build_chat_system_prompt(mode="no-such-mode")
    assert prompt == CHAT_GENERAL_PROMPT


# ===== Skills / 工具说明注入（原有行为保持） =====

def test_build_prompt_with_skills(monkeypatch):
    import app.routes.chat as chat_mod

    monkeypatch.setattr(
        "utils.helpers._load_skill_bodies",
        lambda names: "- 边界值分析",
    )
    prompt = chat_mod._build_chat_system_prompt(selected_skills=["boundary-testing"])
    assert SKILLS_HEADER in prompt
    assert "- 边界值分析" in prompt


def test_build_prompt_with_tools():
    prompt = _build_chat_system_prompt(has_tools=True)
    assert MCP_TOOL_HEADER in prompt


# ===== 业务上下文注入 =====

def test_build_prompt_with_context():
    prompt = _build_chat_system_prompt(context="新增收货地址需求正文")
    assert "## 当前业务上下文" in prompt
    assert "新增收货地址需求正文" in prompt


def test_build_prompt_blank_context_ignored():
    with_ctx = _build_chat_system_prompt(context="   \n  ")
    assert with_ctx == CHAT_GENERAL_PROMPT


def test_truncate_context_within_limit_unchanged():
    text = "a" * 12000
    assert _truncate_context(text) == text


def test_truncate_context_over_limit_keeps_head_tail():
    text = "H" * 8000 + "M" * 4000 + "T" * 3000
    out = _truncate_context(text)
    assert len(text) == 15000
    assert out.startswith("H" * 100)
    assert out.endswith("T" * 100)
    assert "……（内容过长已截断）……" in out
    assert "M" not in out
    # 头 + 标记 + 尾 的总长度可控
    assert len(out) <= 8000 + 3000 + 40


# ===== 需求清单模板结构 =====

def test_requirement_list_template_has_14_sections():
    sections = [
        "基本信息",
        "功能描述",
        "前置条件",
        "页面和入口",
        "输入字段",
        "正常流程",
        "业务规则",
        "异常情况",
        "数据和状态",
        "兼容性要求",
        "性能要求",
        "安全要求",
        "可靠性要求",
        "不确定事项",
    ]
    assert len(sections) == 14
    for sec in sections:
        assert ("## " + sec) in REQUIREMENT_LIST_TEMPLATE, f"缺少章节: {sec}"


def test_requirement_clarify_prompt_rules():
    assert "不确定事项" in REQUIREMENT_CLARIFY_PROMPT
    assert "不编造" in REQUIREMENT_CLARIFY_PROMPT
    assert "需求清单" in REQUIREMENT_CLARIFY_PROMPT


# ===== 请求模型向后兼容 =====

def test_chat_request_backward_compatible():
    req = ChatRequest(messages=[{"role": "user", "content": "你好"}])
    assert req.mode is None
    assert req.context is None
    assert req.client_session_id is None
    assert req.selected_skills is None


def test_chat_request_accepts_mode_and_context():
    req = ChatRequest(
        messages=[{"role": "user", "content": "开始"}],
        mode="requirement_clarify",
        context="源需求正文",
        client_session_id="chat-req-clarify-1",
    )
    assert req.mode == "requirement_clarify"
    assert req.context == "源需求正文"


def test_chat_request_rejects_missing_messages():
    with pytest.raises(ValidationError):
        ChatRequest()