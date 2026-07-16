"""
Agent 2: 测试点生成专家
为每个需求点生成测试点（含测试类别和设计方法）
"""
import logging
from typing import List, Dict, Any, Optional

from app.pipeline.prompts import (
    TEST_POINT_GENERATOR_SYSTEM,
    TEST_POINT_USER,
    API_TEST_POINT_GENERATOR_SYSTEM,
    API_TEST_POINT_USER,
    build_stage_system,
)
from app.pipeline.schemas import TestPointGenerationResult
from utils.helpers import _TokenUsageCallback
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

    async def generate(
        self,
        requirement_content: str,
        *,
        is_api_test: bool = False,
        api_context: str = "",
    ) -> List[Dict[str, Any]]:
        """
        为单个需求点生成测试点列表

        API 测试模式（is_api_test=True 且提供 api_context）下，接口
        Schema 作为测试点生成的主要依据，需求点内容降为范围界定。

        Args:
            requirement_content: 需求点描述文本
            is_api_test: 是否接口测试模式
            api_context: 接口 Schema 文本（API 模式下作为主体注入）

        Returns:
            List[dict]: 每个 dict 包含 content, test_type, design_method, priority
        """
        from utils.model_utils import _structured_output_robust

        if is_api_test and api_context:
            # api_context 注入 system 前缀（见 build_stage_system），user 仅保留动态内容
            system_prompt = build_stage_system(
                API_TEST_POINT_GENERATOR_SYSTEM, api_context=api_context, is_api_test=True
            )
            req_block = API_TEST_POINT_USER.format(content=requirement_content)
            user_prompt = req_block
        else:
            system_prompt = build_stage_system(TEST_POINT_GENERATOR_SYSTEM)
            user_prompt = TEST_POINT_USER.format(content=requirement_content)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.info("开始生成测试点...")
        _diagnostic_cb = _TokenUsageCallback()
        result = await _structured_output_robust(
            self.model, TestPointGenerationResult, messages,
            config={"callbacks": [_diagnostic_cb]},
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
