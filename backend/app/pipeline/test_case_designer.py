"""
Agent 3: 测试用例设计专家
为每个测试点生成详细测试用例，支持功能测试和接口测试两种模式
"""
import json
import logging
from typing import List, Dict, Any, Optional

from app.pipeline.prompts import (
    TEST_CASE_DESIGNER_SYSTEM, TEST_CASE_DESIGN_USER,
    API_TEST_CASE_DESIGNER_SYSTEM, API_TEST_CASE_DESIGN_USER,
)
from app.pipeline.schemas import TestCaseDesignResult, ApiTestCaseDesignResult
from utils.llm_factory import build_chat_model

logger = logging.getLogger(__name__)


class TestCaseDesigner:
    """测试用例设计 Agent（支持功能测试和接口测试）"""

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
        """
        为一批测试点设计测试用例

        Args:
            test_points: 测试点列表，每个含 id, content, test_type, design_method, priority
            requirement_content: 原始需求文档内容（提供业务上下文）
            is_api_test: 是否为接口测试模式
            api_context: API 接口信息文本（接口测试模式下必填）

        Returns:
            List[dict]: 测试用例列表
        """
        from utils.model_utils import _structured_output_robust

        test_points_json = json.dumps(test_points, ensure_ascii=False, indent=2)

        if is_api_test:
            system_prompt = API_TEST_CASE_DESIGNER_SYSTEM
            user_prompt = API_TEST_CASE_DESIGN_USER.format(
                test_points=test_points_json,
                requirement_content=requirement_content or "（无需求文档）",
                api_context=api_context or "（无接口信息）",
            )
            schema = ApiTestCaseDesignResult
        else:
            system_prompt = TEST_CASE_DESIGNER_SYSTEM
            user_prompt = TEST_CASE_DESIGN_USER.format(
                test_points=test_points_json,
                requirement_content=requirement_content or "（无需求文档）",
            )
            schema = TestCaseDesignResult

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        mode_label = "接口测试" if is_api_test else "功能测试"
        logger.info(f"开始设计{mode_label}用例，共 {len(test_points)} 个测试点...")
        result = await _structured_output_robust(
            self.model, schema, messages
        )

        if result is None:
            raise ValueError(f"{mode_label}用例设计失败：模型返回空结果")

        if isinstance(result, (TestCaseDesignResult, ApiTestCaseDesignResult)):
            cases = [c.model_dump() for c in result.test_cases]
        elif isinstance(result, dict):
            cases = result.get("test_cases", [])
        else:
            cases = []

        # 验证数量匹配
        if len(cases) != len(test_points):
            logger.warning(f"测试用例数量不匹配：期望 {len(test_points)}，实际 {len(cases)}")
            # 补齐或截断
            if len(cases) < len(test_points):
                while len(cases) < len(test_points):
                    cases.append({})
            elif len(cases) > len(test_points):
                cases = cases[:len(test_points)]

        # 继承测试点的属性
        for i, case in enumerate(cases):
            if i < len(test_points):
                tp = test_points[i]
                case["test_point_id"] = tp.get("id")
                case.setdefault("test_type", tp.get("test_type", "functional"))
                case.setdefault("design_method", tp.get("design_method", ""))
                case.setdefault("priority", tp.get("priority", "medium"))

        logger.info(f"{mode_label}用例设计完成，共 {len(cases)} 个用例")
        return cases
