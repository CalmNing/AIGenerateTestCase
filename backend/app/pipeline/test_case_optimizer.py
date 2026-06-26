"""
Agent 4: 测试用例优化专家
优化测试用例质量（扩展简陋、精简冗长、提升模糊描述）
"""
import json
import logging
from typing import List, Dict, Any, Optional

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.pipeline.prompts import TEST_CASE_OPTIMIZER_SYSTEM, TEST_CASE_BATCH_OPTIMIZE_USER
from app.pipeline.schemas import TestCaseOptimizationResult

logger = logging.getLogger(__name__)


class TestCaseOptimizer:
    """测试用例优化 Agent"""

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

    async def optimize(self, test_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        批量优化测试用例

        Args:
            test_cases: 测试用例列表，每个含 id, title, description, preconditions, test_steps

        Returns:
            List[dict]: 优化后的测试用例列表
        """
        from utils.model_utils import _structured_output_robust

        # 简化传给AI的数据
        simplified = []
        for tc in test_cases:
            simplified.append({
                "id": tc.get("id"),
                "title": tc.get("title", ""),
                "description": tc.get("description", ""),
                "preconditions": tc.get("preconditions", ""),
                "test_steps": tc.get("test_steps", []),
            })

        cases_json = json.dumps(simplified, ensure_ascii=False, indent=2)
        user_prompt = TEST_CASE_BATCH_OPTIMIZE_USER.format(test_cases=cases_json)
        messages = [
            {"role": "system", "content": TEST_CASE_OPTIMIZER_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]

        logger.info(f"开始优化测试用例，共 {len(test_cases)} 个...")
        result = await _structured_output_robust(
            self.model, TestCaseOptimizationResult, messages
        )

        if result is None:
            raise ValueError("测试用例优化失败：模型返回空结果")

        if isinstance(result, TestCaseOptimizationResult):
            optimized = [c.model_dump() for c in result.optimized_cases]
        elif isinstance(result, dict):
            optimized = result.get("optimized_cases", [])
        else:
            optimized = []

        # 创建 id 映射，用于匹配原始用例
        original_map = {tc.get("id"): tc for tc in test_cases}

        # 合并优化结果
        final_cases = []
        for opt in optimized:
            opt_id = opt.get("id")
            original = original_map.get(opt_id)
            if original and opt.get("title"):
                # 用优化结果更新原始用例
                merged = {**original}
                merged["title"] = opt.get("title", original.get("title", ""))
                merged["description"] = opt.get("description", original.get("description", ""))
                merged["preconditions"] = opt.get("preconditions", original.get("preconditions", ""))
                if opt.get("test_steps"):
                    merged["test_steps"] = opt["test_steps"]
                final_cases.append(merged)
            elif original:
                # 优化结果无效，保留原始
                final_cases.append(original)

        # 处理未被优化的用例（AI可能遗漏）
        returned_ids = {opt.get("id") for opt in optimized}
        for tc in test_cases:
            if tc.get("id") not in returned_ids:
                final_cases.append(tc)

        logger.info(f"测试用例优化完成，共 {len(final_cases)} 个用例")
        return final_cases
