"""Pipeline 路径 prompt 缓存前缀的单元测试。

验证目标（对应 docs/cache-hit-deepseek-analysis.md 方案①⑤②③）：
- 大块可复用内容 api_context 进入 system 前缀，而非 user 消息（尤其不能落在尾部）；
- 4 个阶段共享同一个静态前缀（SHARED_CACHE_PREFIX），跨阶段/跨点/跨批次前缀逐字节一致；
- SHARED_CACHE_PREFIX 体量跨过 DeepSeek 可靠命中门槛（≈1024 token）。
"""

import pytest

from app.pipeline import prompts as P


SAMPLE_API_CONTEXT = "POST /api/login {username, password}"


def _prefix_before_stage(system_text: str) -> str:
    """取出「本阶段专项指令」之前的部分（即跨阶段应共享的前缀）。"""
    return system_text.split("## 本阶段专项指令")[0]


def test_shared_prefix_crosses_threshold():
    # 中文约 1 token/字，1200+ 字 ≈ 1200+ token，稳过 1024 门槛；
    # 这里给足余量，避免临界值下缓存不稳定。
    assert len(P.SHARED_CACHE_PREFIX) > 1800
    assert P.SHARED_CACHE_PREFIX.startswith("你是一位严谨的软件测试专家")


def test_api_context_into_system_not_user():
    system = P.build_stage_system(
        P.API_TEST_POINT_GENERATOR_SYSTEM,
        api_context=SAMPLE_API_CONTEXT,
        is_api_test=True,
    )
    # api_context 必须在 system 前缀里
    assert SAMPLE_API_CONTEXT in system
    # 阶段3 的 user 模板已移除 api_context 占位，构造出的 user 不应再含规格
    user = P.API_TEST_CASE_DESIGN_USER.format(
        test_points="[]", requirement_content="(无需求文档)"
    )
    assert SAMPLE_API_CONTEXT not in user
    assert "{api_context}" not in user


def test_stage2_user_has_no_api_context_leak():
    # 阶段2 的 user 由调用方用 API_TEST_POINT_USER.format(content=...) 构造，
    # api_context 不再 prepend 到 user。验证模板本身不含规格。
    assert SAMPLE_API_CONTEXT not in P.API_TEST_POINT_USER


def test_cross_stage_shared_prefix_identical_api():
    s_a = P.build_stage_system(
        P.API_TEST_POINT_GENERATOR_SYSTEM, api_context=SAMPLE_API_CONTEXT, is_api_test=True
    )
    s_b = P.build_stage_system(
        P.API_TEST_CASE_DESIGNER_SYSTEM, api_context=SAMPLE_API_CONTEXT, is_api_test=True
    )
    s_c = P.build_stage_system(
        P.TEST_CASE_OPTIMIZER_SYSTEM, api_context=SAMPLE_API_CONTEXT, is_api_test=True
    )
    # 三个接口测试阶段共享的前缀（含 api_context）必须逐字节一致
    assert _prefix_before_stage(s_a) == _prefix_before_stage(s_b)
    assert _prefix_before_stage(s_b) == _prefix_before_stage(s_c)


def test_cross_stage_shared_prefix_identical_func():
    f_a = P.build_stage_system(P.REQUIREMENT_SPLITTER_SYSTEM)
    f_b = P.build_stage_system(P.TEST_POINT_GENERATOR_SYSTEM)
    f_c = P.build_stage_system(P.TEST_CASE_DESIGNER_SYSTEM)
    f_d = P.build_stage_system(P.TEST_CASE_OPTIMIZER_SYSTEM)
    # 功能性测试（无 api_context）四个阶段共享前缀必须一致
    assert _prefix_before_stage(f_a) == _prefix_before_stage(f_b)
    assert _prefix_before_stage(f_b) == _prefix_before_stage(f_c)
    assert _prefix_before_stage(f_c) == _prefix_before_stage(f_d)


def test_api_context_absent_in_functional_prefix():
    system = P.build_stage_system(P.TEST_POINT_GENERATOR_SYSTEM)
    assert SAMPLE_API_CONTEXT not in system
    assert "## 接口上下文" not in system


def test_api_context_placeholder_removed_from_template():
    # 确认 user 模板不再残留 {api_context} 占位，否则 .format 会报错/漏填
    assert "{api_context}" not in P.API_TEST_CASE_DESIGN_USER
