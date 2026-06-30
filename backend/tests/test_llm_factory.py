"""Tests for the unified ChatOpenAI factory and priority normalization."""
from utils.llm_factory import build_chat_model, normalize_priority


def test_build_chat_model_basic_attributes():
    model = build_chat_model("sk-test", api_model="deepseek-v4-flash")
    assert model.model_name == "deepseek-v4-flash"
    assert model.temperature == 0
    # base_url falls back to deepseek default when not provided
    assert "deepseek.com" in str(model.openai_api_base)


def test_build_chat_model_passes_max_tokens():
    model = build_chat_model("sk-test", api_model="m", max_tokens=16384)
    assert model.max_tokens == 16384


def test_build_chat_model_thinking_disabled_in_extra_body():
    model = build_chat_model("sk-test", api_model="m")
    # extra_body carries the thinking-disabled flag uniformly
    extra = getattr(model, "extra_body", None) or {}
    assert extra.get("thinking") == {"type": "disabled"}


def test_normalize_priority_mapping():
    assert normalize_priority("P0") == "high"
    assert normalize_priority("HIGH") == "high"
    assert normalize_priority("p1") == "medium"
    assert normalize_priority("P2") == "low"
    assert normalize_priority("") == "medium"
    assert normalize_priority(None) == "medium"
    assert normalize_priority("unknown") == "medium"
