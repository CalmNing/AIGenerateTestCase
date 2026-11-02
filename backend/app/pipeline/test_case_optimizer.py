"""Agent 4: test case optimization expert."""

import json
import logging
from typing import Any, Dict, List, Optional

from app.pipeline.prompts import (
    TEST_CASE_BATCH_OPTIMIZE_USER,
    TEST_CASE_OPTIMIZER_SYSTEM,
    build_stage_system,
)
from app.pipeline.schemas import ApiTestCaseOptimizationResult, TestCaseOptimizationResult
from utils.helpers import _TokenUsageCallback
from utils.llm_factory import build_chat_model

logger = logging.getLogger(__name__)


PRECONDITIONS_OUTPUT_RULE = """

Strict preconditions rule:
- The field name is preconditions.
- preconditions must remain a JSON array of strings.
- Each array item must contain exactly one precondition.
- Do not merge the array into a single concatenated string.
"""


class TestCaseOptimizer:
    """Optimize test case quality while preserving intent."""

    def __init__(
        self,
        api_key: str,
        api_base_url: str = "https://api.deepseek.com",
        api_model: str = "deepseek-v4-flash",
        api_proxy_url: Optional[str] = None,
    ):
        # max_tokens 显式设高：DeepSeek 服务器默认 8192，大批量优化输出可能被截断
        self.model = build_chat_model(
            api_key, api_base_url, api_model, api_proxy_url, max_tokens=16384
        )

    async def optimize(
        self,
        test_cases: List[Dict[str, Any]],
        *,
        is_api_test: bool = False,
        api_context: str = "",
        failure_feedback: str = "",
    ) -> List[Dict[str, Any]]:
        """Optimize one batch of test cases.

        `failure_feedback`（P3）：真实执行失败明细（来自 case_verifier）。
        非空时会把失败上下文追加到 user 消息，让模型按实际接口行为修正断言/参数，
        而不是凭空"优化"——这是把执行结果变成可用反馈信号的关键一步。
        """
        from utils.model_utils import _structured_output_robust

        simplified = []
        for tc in test_cases:
            item = {
                "id": tc.get("id"),
                "title": tc.get("title", ""),
                "description": tc.get("description", ""),
                "preconditions": tc.get("preconditions", []),
                "test_steps": tc.get("test_steps", []),
            }
            if is_api_test:
                item["assertions"] = tc.get("assertions", [])
            simplified.append(item)

        cases_json = json.dumps(simplified, ensure_ascii=False, indent=2)
        user_prompt = TEST_CASE_BATCH_OPTIMIZE_USER.format(test_cases=cases_json) + PRECONDITIONS_OUTPUT_RULE
        if failure_feedback:
            # 失败明细放在用例 JSON **之后**：既是动态内容，又紧邻任务说明，
            # 不会破坏 system 前缀的缓存命中。
            user_prompt = user_prompt + "\n\n" + failure_feedback
        # 使用共享前缀构造 system，保证与阶段1/2/3 的前缀可共享缓存命中
        system_prompt = build_stage_system(
            TEST_CASE_OPTIMIZER_SYSTEM, api_context=api_context, is_api_test=is_api_test
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        schema = ApiTestCaseOptimizationResult if is_api_test else TestCaseOptimizationResult
        mode_label = "接口测试" if is_api_test else "功能测试"

        logger.info(f"开始优化{mode_label}用例，共 {len(test_cases)} 个...")
        _diagnostic_cb = _TokenUsageCallback()
        result = await _structured_output_robust(
            self.model, schema, messages,
            config={"callbacks": [_diagnostic_cb]},
        )

        if result is None:
            raise ValueError(f"{mode_label}用例优化失败：模型返回空结果")

        if isinstance(result, (TestCaseOptimizationResult, ApiTestCaseOptimizationResult)):
            optimized = [c.model_dump(by_alias=True) for c in result.optimized_cases]
        elif isinstance(result, dict):
            optimized = result.get("optimized_cases", [])
        else:
            optimized = []

        original_map = {tc.get("id"): tc for tc in test_cases}

        final_cases = []
        for opt in optimized:
            opt_id = opt.get("id")
            original = original_map.get(opt_id)
            if original and opt.get("title"):
                merged = {**original}
                merged["title"] = opt.get("title", original.get("title", ""))
                merged["description"] = opt.get("description", original.get("description", ""))
                merged["preconditions"] = opt.get("preconditions", original.get("preconditions", []))
                if opt.get("test_steps"):
                    merged["test_steps"] = opt["test_steps"]
                if is_api_test and opt.get("assertions"):
                    merged["assertions"] = opt["assertions"]
                final_cases.append(merged)
            elif original:
                final_cases.append(original)

        returned_ids = {opt.get("id") for opt in optimized}
        for tc in test_cases:
            if tc.get("id") not in returned_ids:
                final_cases.append(tc)

        logger.info(f"{mode_label}用例优化完成，共 {len(final_cases)} 个用例")
        return final_cases
