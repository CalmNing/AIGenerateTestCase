"""
Agent 4: 测试用例优化专家
优化测试用例质量（扩展简陋、精简冗长、提升模糊描述）
支持功能测试和接口测试两种模式
"""
import json
import logging
from typing import List, Dict, Any, Optional

from app.pipeline.prompts import TEST_CASE_OPTIMIZER_SYSTEM, TEST_CASE_BATCH_OPTIMIZE_USER
from app.pipeline.schemas import TestCaseOptimizationResult, ApiTestCaseOptimizationResult
from utils.llm_factory import build_chat_model

logger = logging.getLogger(__name__)


class TestCaseOptimizer:
    """测试用例优化 Agent（支持功能测试和接口测试）"""

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

    async def optimize(
        self,
        test_cases: List[Dict[str, Any]],
        *,
        is_api_test: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        批量优化测试用例

        Args:
            test_cases: 测试用例列表，每个含 id, title, description, preconditions, test_steps
            is_api_test: 是否为接口测试模式

        Returns:
            List[dict]: 优化后的测试用例列表
        """
        from utils.model_utils import _structured_output_robust

        # 简化传给AI的数据
        simplified = []
        for tc in test_cases:
            item = {
                "id": tc.get("id"),
                "title": tc.get("title", ""),
                "description": tc.get("description", ""),
                "preconditions": tc.get("preconditions", ""),
                "test_steps": tc.get("test_steps", []),
            }
            if is_api_test:
                item["assertions"] = tc.get("assertions", [])
            simplified.append(item)

        cases_json = json.dumps(simplified, ensure_ascii=False, indent=2)
        user_prompt = TEST_CASE_BATCH_OPTIMIZE_USER.format(test_cases=cases_json)
        messages = [
            {"role": "system", "content": TEST_CASE_OPTIMIZER_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]

        schema = ApiTestCaseOptimizationResult if is_api_test else TestCaseOptimizationResult
        mode_label = "接口测试" if is_api_test else "功能测试"

        logger.info(f"开始优化{mode_label}用例，共 {len(test_cases)} 个...")
        result = await _structured_output_robust(
            self.model, schema, messages
        )

        if result is None:
            raise ValueError(f"{mode_label}用例优化失败：模型返回空结果")

        if isinstance(result, (TestCaseOptimizationResult, ApiTestCaseOptimizationResult)):
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
                if is_api_test and opt.get("assertions"):
                    merged["assertions"] = opt["assertions"]
                final_cases.append(merged)
            elif original:
                # 优化结果无效，保留原始
                final_cases.append(original)

        # 处理未被优化的用例（AI可能遗漏）
        returned_ids = {opt.get("id") for opt in optimized}
        for tc in test_cases:
            if tc.get("id") not in returned_ids:
                final_cases.append(tc)

        logger.info(f"{mode_label}用例优化完成，共 {len(final_cases)} 个用例")
        return final_cases
