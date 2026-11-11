"""提示词精简规则防回退断言：用例生成必须遵守「正向优先 + 单场景 + 3-6 步」。

这些断言锁定关键规则文案，防止后续改动悄悄回退到"覆盖全面/5-8步"的旧行为。
"""
from utils.prompts import PromptConfig
from app.pipeline import prompts as pipe


def test_base_prompt_has_positive_first_rule():
    assert "正向优先" in PromptConfig.BASE_PROMPT
    assert "60%" in PromptConfig.BASE_PROMPT
    assert "无效等价类合并" in PromptConfig.BASE_PROMPT


def test_base_prompt_has_concise_rules():
    assert "用例精简规则" in PromptConfig.BASE_PROMPT
    assert "3-6 步" in PromptConfig.BASE_PROMPT
    assert "禁止把正向流程和异常输入合并在同一条用例里" in PromptConfig.BASE_PROMPT


def test_base_prompt_dropped_old_full_coverage_wording():
    # 旧的"覆盖全面性"原则已被"正向优先"取代
    assert "覆盖全面性" not in PromptConfig.BASE_PROMPT


def test_pipeline_designer_single_scene_and_step_limit():
    assert "一条用例只验证一个场景" in pipe.TEST_CASE_DESIGNER_SYSTEM
    assert "3-6步" in pipe.TEST_CASE_DESIGNER_SYSTEM
    # 旧合并写法已删除
    assert "正向+1-2个关键异常" not in pipe.TEST_CASE_DESIGN_USER


def test_pipeline_optimizer_trims_to_six_steps():
    assert "3-6步" in pipe.TEST_CASE_OPTIMIZER_SYSTEM
    assert "不超过6步" in pipe.TEST_CASE_OPTIMIZER_SYSTEM
    assert "裁剪" in pipe.TEST_CASE_BATCH_OPTIMIZE_USER


def test_shared_prefix_positive_first():
    assert "以正向为主" in pipe.SHARED_CACHE_PREFIX
    assert "一条用例只验证一个场景" in pipe.SHARED_CACHE_PREFIX


def test_api_designer_single_scene():
    assert "一条用例只验证一个场景" in pipe.API_TEST_CASE_DESIGN_USER
    assert "禁止夹带无效输入" in pipe.API_TEST_CASE_DESIGN_USER
