"""
Agent 1: 需求拆分专家
将需求文档拆分为原子化需求点
"""
import json
import logging
from typing import List, Dict, Any, Optional

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.pipeline.prompts import REQUIREMENT_SPLITTER_SYSTEM, REQUIREMENT_ANALYSIS_USER
from app.pipeline.schemas import RequirementAnalysisResult

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

    async def analyze(self, requirement_text: str) -> List[Dict[str, Any]]:
        """
        分析需求文档，提取需求点列表

        Returns:
            List[dict]: 每个 dict 包含 content, module, priority, order_index
        """
        from utils.model_utils import _structured_output_robust

        user_prompt = REQUIREMENT_ANALYSIS_USER.format(content=requirement_text)
        messages = [
            {"role": "system", "content": REQUIREMENT_SPLITTER_SYSTEM},
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
            p["priority"] = self._normalize_priority(p.get("priority", "medium"))

        logger.info(f"需求拆分完成，共 {len(points)} 个需求点")
        return points

    @staticmethod
    def _normalize_priority(priority: str) -> str:
        if not priority:
            return "medium"
        mapping = {
            "P0": "high", "HIGH": "high", "H": "high",
            "P1": "medium", "MEDIUM": "medium", "M": "medium",
            "P2": "low", "LOW": "low", "L": "low",
        }
        return mapping.get(priority.upper(), "medium")
