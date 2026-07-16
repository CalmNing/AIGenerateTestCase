"""TDD tests for static/dynamic prefix splitting (cache hit rate improvement).

These tests pin the contract of `model_utils._build_generation_messages`:
reusable context (api_context + skill bodies) must live in the cacheable
SYSTEM prefix, while the user's actual instruction stays in the variable HUMAN
message. Identical (api_context + skill + base prompt) across calls must yield a
byte-identical SYSTEM prefix so DeepSeek's prefix KV cache can hit.
"""
import pytest

import utils.model_utils as mu


@pytest.fixture()
def _fake_skill_bodies(monkeypatch):
    """Make skill loading deterministic and hermetic."""
    def _fake(names):
        if not names:
            return ""
        return "SKILL_BODY:" + "|".join(names)
    monkeypatch.setattr(mu, "_load_skill_bodies", _fake)


def test_api_context_goes_to_system_not_human(_fake_skill_bodies):
    spec = "POST /api/login {username,password} -> 200"
    system, human = mu._build_generation_messages(
        selected_prompt="BASE_PROMPT",
        core_requirement="请为登录接口补充边界用例",
        selected_skill_names=[],
        api_context=spec,
        is_api_test=True,
    )
    assert spec in system
    assert spec not in human
    # human keeps only the user's dynamic instruction (+ supplemental label)
    assert "请为登录接口补充边界用例" in human


def test_skill_body_goes_to_system(_fake_skill_bodies):
    system, human = mu._build_generation_messages(
        selected_prompt="BASE_PROMPT",
        core_requirement="普通需求",
        selected_skill_names=["testcase-design"],
        api_context="",
        is_api_test=False,
    )
    assert "SKILL_BODY:testcase-design" in system
    assert "SKILL_BODY:testcase-design" not in human


def test_stable_prefix_is_byte_identical_across_calls(_fake_skill_bodies):
    kwargs = dict(
        selected_prompt="BASE_PROMPT",
        core_requirement="需求A",  # variable part differs -> human differs
        selected_skill_names=["boundary-testing"],
        api_context="POST /api/login",
        is_api_test=True,
    )
    s1, h1 = mu._build_generation_messages(**kwargs)
    s2, h2 = mu._build_generation_messages(**kwargs)
    assert s1 == s2  # cacheable prefix stable
    assert h1 != "same"  # sanity: human may differ (here identical since same kwargs)
    # Different user requirement must NOT perturb the system prefix
    kwargs2 = dict(kwargs)
    kwargs2["core_requirement"] = "完全不同的需求B"
    s3, h3 = mu._build_generation_messages(**kwargs2)
    assert s3 == s1  # prefix unchanged despite different requirement
    assert h3 != h1


def test_functional_test_has_no_supplemental_label(_fake_skill_bodies):
    system, human = mu._build_generation_messages(
        selected_prompt="BASE_PROMPT",
        core_requirement="用户应能登录",
        selected_skill_names=[],
        api_context="",
        is_api_test=False,
    )
    assert human == "用户应能登录"
    assert "补充背景" not in human


def test_no_api_context_system_equals_base_prompt(_fake_skill_bodies):
    system, human = mu._build_generation_messages(
        selected_prompt="BASE_PROMPT",
        core_requirement="任意需求",
        selected_skill_names=[],
        api_context="",
        is_api_test=False,
    )
    assert system == "BASE_PROMPT"
    assert human == "任意需求"


def test_full_system_composes_base_skill_and_api(_fake_skill_bodies):
    system, _ = mu._build_generation_messages(
        selected_prompt="BASE",
        core_requirement="x",
        selected_skill_names=["s1", "s2"],
        api_context="API_SPEC_CONTENT",
        is_api_test=True,
    )
    # All three stable blocks present and base first (so base is always cached)
    assert system.startswith("BASE")
    assert "SKILL_BODY:s1|s2" in system
    assert "API_SPEC_CONTENT" in system
    assert system.index("BASE") < system.index("SKILL_BODY") < system.index("API_SPEC_CONTENT")
