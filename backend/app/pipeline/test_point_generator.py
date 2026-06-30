"""
Agent 2: 测试点生成专家
为每个需求点生成测试点（含测试类别和设计方法）
"""
import logging
from typing import List, Dict, Any, Optional

from app.pipeline.prompts import TEST_POINT_GENERATOR_SYSTEM, TEST_POINT_USER
from app.pipeline.schemas import TestPointGenerationResult
from utils.llm_factory import build_chat_model, normalize_priority

logger = logging.getLogger(__name__)


class TestPointGenerator:
    """测试点生成 Agent"""

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

    async def generate(self, requirement_content: str) -> List[Dict[str, Any]]:
        """
        为单个需求点生成测试点列表

        Args:
            requirement_content: 需求点描述文本

        Returns:
            List[dict]: 每个 dict 包含 content, test_type, design_method, priority
        """
        from utils.model_utils import _structured_output_robust

        user_prompt = TEST_POINT_USER.format(content=requirement_content)
        messages = [
            {"role": "system", "content": TEST_POINT_GENERATOR_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]

        logger.info("开始生成测试点...")
        result = await _structured_output_robust(
            self.model, TestPointGenerationResult, messages
        )

        if result is None:
            raise ValueError("测试点生成失败：模型返回空结果")

        if isinstance(result, TestPointGenerationResult):
            points = [p.model_dump() for p in result.test_points]
        elif isinstance(result, dict):
            points = result.get("test_points", [])
        else:
            points = []

        # 标准化优先级
        for p in points:
            p["priority"] = normalize_priority(p.get("priority", "medium"))

        logger.info(f"测试点生成完成，共 {len(points)} 个测试点")
        return points
