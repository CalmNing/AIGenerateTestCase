"""Agent 3: test case design expert."""

import json
import logging
from typing import Any, Dict, List, Optional

from app.pipeline.prompts import (
    API_TEST_CASE_DESIGNER_SYSTEM,
    API_TEST_CASE_DESIGN_USER,
    TEST_CASE_DESIGNER_SYSTEM,
    TEST_CASE_DESIGN_USER,
    build_stage_system,
)
from app.pipeline.schemas import ApiTestCaseDesignResult, TestCaseDesignResult
from utils.helpers import _TokenUsageCallback
from utils.llm_factory import build_chat_model

logger = logging.getLogger(__name__)


PRECONDITIONS_OUTPUT_RULE = """

Strict preconditions rule:
- The field name is preconditions.
- preconditions must be a JSON array of strings.
- Each array item must contain exactly one precondition.
- Correct: [\"1. 用户已登录\", \"2. 用户有权限\", \"3. 已准备合法文件\"]
- Wrong: [\"1. 用户已登录 2. 用户有权限 3. 已准备合法文件\"]
"""


class TestCaseDesigner:
    """Design detailed test cases from generated test points."""

    def __init__(
        self,
        api_key: str,
        api_base_url: str = "https://api.deepseek.com",
        api_model: str = "deepseek-v4-flash",
        api_proxy_url: Optional[str] = None,
    ):
        self.model = build_chat_model(
            api_key, api_base_url, api_model, api_proxy_url, max_tokens=None
        )

    async def design_batch(
        self,
        test_points: List[Dict[str, Any]],
        requirement_content: str = "",
        *,
        is_api_test: bool = False,
        api_context: str = "",
    ) -> List[Dict[str, Any]]:
        """Design one batch of test cases."""
        from utils.model_utils import _structured_output_robust

        test_points_json = json.dumps(test_points, ensure_ascii=False, indent=2)

        if is_api_test:
            # api_context 通过 build_stage_system 注入 system 前缀（已从 user 模板移除）
            system_prompt = build_stage_system(
                API_TEST_CASE_DESIGNER_SYSTEM, api_context=api_context, is_api_test=True
            )
            user_prompt = API_TEST_CASE_DESIGN_USER.format(
                test_points=test_points_json,
                requirement_content=requirement_content or "(无需求文档)",
            ) + PRECONDITIONS_OUTPUT_RULE
            schema = ApiTestCaseDesignResult
        else:
            system_prompt = build_stage_system(TEST_CASE_DESIGNER_SYSTEM)
            user_prompt = TEST_CASE_DESIGN_USER.format(
                test_points=test_points_json,
                requirement_content=requirement_content or "(无需求文档)",
            ) + PRECONDITIONS_OUTPUT_RULE
            schema = TestCaseDesignResult

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        mode_label = "接口测试" if is_api_test else "功能测试"
        logger.info(f"开始设计{mode_label}用例，共 {len(test_points)} 个测试点...")
        _diagnostic_cb = _TokenUsageCallback()
        result = await _structured_output_robust(
            self.model, schema, messages,
            config={"callbacks": [_diagnostic_cb]},
        )

        if result is None:
            raise ValueError(f"{mode_label}用例设计失败：模型返回空结果")

        if isinstance(result, (TestCaseDesignResult, ApiTestCaseDesignResult)):
            cases = [c.model_dump(by_alias=True) for c in result.test_cases]
        elif isinstance(result, dict):
            cases = result.get("test_cases", [])
        else:
            cases = []

        if len(cases) != len(test_points):
            logger.warning(f"测试用例数量不匹配：期望 {len(test_points)}，实际 {len(cases)}")
            if len(cases) < len(test_points):
                while len(cases) < len(test_points):
                    cases.append({})
            elif len(cases) > len(test_points):
                cases = cases[:len(test_points)]

        for i, case in enumerate(cases):
            if i < len(test_points):
                tp = test_points[i]
                case["test_point_id"] = tp.get("id")
                case.setdefault("test_type", tp.get("test_type", "functional"))
                case.setdefault("design_method", tp.get("design_method", ""))
                case.setdefault("priority", tp.get("priority", "medium"))

        logger.info(f"{mode_label}用例设计完成，共 {len(cases)} 个用例")
        return cases
