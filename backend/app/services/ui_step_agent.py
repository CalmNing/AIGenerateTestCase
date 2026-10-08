"""L3 步进 Agent:同一 Playwright 页面上的轻量 LLM 解析(非 browser-use 整例)。

用途:
  - hybrid 执行中 level=L3 的操作步:LLM 看 accessibility snapshot → 输出
    {action, element_key, value};就地 _perform
  - 未结构化预期终判(final_judgement=True):LLM 看页面文本 → 简短 PASS/原因

不引入视觉模型;失败返回 None,由 hybrid_runner 记 trace 并可能触发整例 agent 兜底。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class L3StepPlan(BaseModel):
    """L3 单步执行计划。"""

    action: str = Field(description="click|fill|check|uncheck|select_option|press|wait_for|agent")
    element_key: Optional[str] = Field(default=None, description="必须来自给定元素 key 列表,否则 null")
    value: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""


L3_SYSTEM_PROMPT = """你是 UI 步进执行助手。当前在同一页 Playwright 会话中,根据给定的自然语言步骤与可用元素列表,输出下一步应执行的动作。

规则:
1. action 只能是: click / fill / check / uncheck / select_option / press / wait_for / agent
2. element_key 必须严格来自「可用元素」的 key,匹配不到填 null 并 action=agent
3. value:fill/select 需要值;从步骤文本提取字面量,提不出填 null
4. confidence 0~1
5. 只输出 JSON,不要解释"""

L3_USER_PROMPT = """## 可用元素(key | name | type)
{elements}

## 待执行步骤
{step}

请输出执行计划 JSON。"""

JUDGE_SYSTEM_PROMPT = """你是 UI 测试终判助手。根据页面当前文本判断预期是否满足。
输出 JSON: {"pass": true|false, "reason": "一句话"}"""

JUDGE_USER_PROMPT = """## 页面文本(截断)
{content}

## 预期结果
{expected}

请判断是否全部满足。"""


def _build_llm():
    from config import config_manager
    from utils.llm_factory import build_chat_model

    return build_chat_model(
        api_key=config_manager.get_api_key(),
        api_base_url=config_manager.config.get("api_base_url"),
        api_model=config_manager.config.get("api_model"),
        max_tokens=4096,
        temperature=0,
    )


def _element_lines(by_key: dict) -> str:
    lines: list[str] = []
    for key, el in (by_key or {}).items():
        name = str(el.get("name") or "")
        typ = str(el.get("type") or "")
        lines.append(f"{key} | {name} | {typ}")
    return "\n".join(lines) or "(无)"


async def step_agent_llm(page, step, *, by_key: dict, llm=None) -> Optional[dict]:
    """对单条 L3 步:LLM 输出计划 dict;失败返回 None。"""
    from utils.model_utils import _structured_output_robust

    model = llm or _build_llm()
    nl = getattr(step, "nl", "") or str(step)
    user = L3_USER_PROMPT.format(elements=_element_lines(by_key), step=nl)
    messages = [
        {"role": "system", "content": L3_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    raw = await _structured_output_robust(model, L3StepPlan, messages)
    if raw is None:
        return None
    if isinstance(raw, L3StepPlan):
        return raw.model_dump()
    if isinstance(raw, dict):
        try:
            return L3StepPlan.model_validate(raw).model_dump()
        except Exception:  # noqa: BLE001
            return raw
    return None


async def judge_unstructured(page, expected: list[str], *, llm=None) -> str:
    """未结构化预期的 LLM 终判;返回写入 agent_summary 的简短文本。"""
    from utils.model_utils import _structured_output_robust
    from pydantic import BaseModel as _BM

    class _Judge(_BM):
        # pydantic 字段不能叫 pass(关键字),用 alias 映射 JSON 的 "pass"
        passed: bool = Field(default=False, alias="pass")
        reason: str = ""
        model_config = {"populate_by_name": True}

    model = llm or _build_llm()
    try:
        content = await page.content()
    except Exception:  # noqa: BLE001
        content = ""
    content = content[:4000]
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": JUDGE_USER_PROMPT.format(
            content=content,
            expected=json.dumps(expected, ensure_ascii=False),
        )},
    ]
    raw = await _structured_output_robust(model, _Judge, messages)
    if raw is None:
        return ""
    if isinstance(raw, _Judge):
        return f"{'PASS' if raw.passed else 'FAIL'}: {raw.reason}"
    if isinstance(raw, dict):
        ok = bool(raw.get("pass") or raw.get("passed"))
        return f"{'PASS' if ok else 'FAIL'}: {raw.get('reason', '')}"
    return str(raw)
