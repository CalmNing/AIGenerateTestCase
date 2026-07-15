"""
Agent 1: 需求拆分专家
将需求文档拆分为原子化需求点
"""
import json
import logging
from typing import List, Dict, Any, Optional

from app.pipeline.prompts import (
    REQUIREMENT_SPLITTER_SYSTEM,
    REQUIREMENT_ANALYSIS_USER,
    API_REQUIREMENT_SPLITTER_SYSTEM,
    API_REQUIREMENT_ANALYSIS_USER,
)
from app.pipeline.schemas import RequirementAnalysisResult
from utils.llm_factory import build_chat_model, normalize_priority

logger = logging.getLogger(__name__)


class RequirementSplitter:
    """需求拆分 Agent"""

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

    async def analyze(
        self,
        requirement_text: str,
        *,
        is_api_test: bool = False,
        api_context: str = "",
    ) -> List[Dict[str, Any]]:
        """
        分析需求文档，提取需求点列表。

        API 测试模式（is_api_test=True 且提供 api_context）下，接口 Schema
        作为需求拆分的主要依据，用户文本降为辅助背景。

        Returns:
            List[dict]: 每个 dict 包含 content, module, priority, order_index
        """
        from utils.model_utils import _structured_output_robust

        if is_api_test and api_context:
            system_prompt = API_REQUIREMENT_SPLITTER_SYSTEM
            # 注意：api_context 含 JSON 大括号，必须用字符串拼接，禁用 .format()
            user_prompt = (
                "===== API 接口信息（需求拆分的主要依据）=====\n\n"
                + api_context
                + "\n\n## 用户补充说明（次要，仅用于理解业务语境，"
                "不得脱离上述接口信息编造字段/路径）\n\n"
                + (requirement_text or "(无用户补充说明)")
            )
        else:
            system_prompt = REQUIREMENT_SPLITTER_SYSTEM
            user_prompt = REQUIREMENT_ANALYSIS_USER.format(content=requirement_text)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.info("开始需求拆分分析...")
        result = await _structured_output_robust(
            self.model, RequirementAnalysisResult, messages
        )

        if result is None:
            raise ValueError("需求拆分失败：模型返回空结果")

        # 兼容 BaseModel 和 dict 返回
        if isinstance(result, RequirementAnalysisResult):
            points = [p.model_dump() for p in result.requirement_points]
        elif isinstance(result, dict):
            points = result.get("requirement_points", [])
        else:
            points = []

        # 标准化优先级
        for p in points:
            p["priority"] = normalize_priority(p.get("priority", "medium"))

        logger.info(f"需求拆分完成，共 {len(points)} 个需求点")
        return points
