"""
Agent 3: 测试用例设计专家
为每个测试点生成详细测试用例
"""
import json
import logging
from typing import List, Dict, Any, Optional

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.pipeline.prompts import TEST_CASE_DESIGNER_SYSTEM, TEST_CASE_DESIGN_USER
from app.pipeline.schemas import TestCaseDesignResult

logger = logging.getLogger(__name__)


class TestCaseDesigner:
    """测试用例设计 Agent"""

    def __init__(
        self,
        api_key: str,
        api_base_url: str = "https://api.deepseek.com",
        api_model: str = "deepseek-v4-flash",
        api_proxy_url: Optional[str] = None,
    ):
        extra_kwargs = {}
        if api_proxy_url:
            import httpx
            extra_kwargs["http_async_client"] = httpx.AsyncClient(
                proxy=api_proxy_url,
                timeout=httpx.Timeout(None, connect=30.0, read=None, write=None, pool=None),
            )

        self.model = ChatOpenAI(
            model=api_model,
            temperature=0,
            api_key=SecretStr(api_key),
            base_url=api_base_url or "https://api.deepseek.com",
            max_tokens=None,
            timeout=None,
            max_retries=2,
            model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
            **extra_kwargs,
        )

    async def design_batch(
        self,
        test_points: List[Dict[str, Any]],
        requirement_content: str = "",
    ) -> List[Dict[str, Any]]:
        """
        为一批测试点设计测试用例

        Args:
            test_points: 测试点列表，每个含 id, content, test_type, design_method, priority
            requirement_content: 原始需求文档内容（提供业务上下文）

        Returns:
            List[dict]: 测试用例列表
        """
        from utils.model_utils import _structured_output_robust

        test_points_json = json.dumps(test_points, ensure_ascii=False, indent=2)
        user_prompt = TEST_CASE_DESIGN_USER.format(
            test_points=test_points_json,
            requirement_content=requirement_content or "（无需求文档）",
        )
        messages = [
            {"role": "system", "content": TEST_CASE_DESIGNER_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]

        logger.info(f"开始设计测试用例，共 {len(test_points)} 个测试点...")
        result = await _structured_output_robust(
            self.model, TestCaseDesignResult, messages
        )

        if result is None:
            raise ValueError("测试用例设计失败：模型返回空结果")

        if isinstance(result, TestCaseDesignResult):
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

        logger.info(f"测试用例设计完成，共 {len(cases)} 个用例")
        return cases
