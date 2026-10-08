"""UI 步骤绑定(NL → PageObject)的 Pydantic schema。

compile 产物结构(存 TestCase.ui_binding / UIExecutionRecord.binding_snapshot):
{
  "page_object_id": 1,
  "steps":   [UIBoundStep, ...],    # 操作步
  "asserts": [UIBoundAssert, ...],  # 由 expected_results 编译出的结构化断言
  "site_origin": "...",             # 编译时输入(前端 dirty 检测)
  "page_path": "...",
  "compile_status": "ok|no_page_object|llm_failed",
}
"""
from typing import List, Optional

from pydantic import BaseModel, Field

# 可被绑定执行的动作白名单(与 page_object_runner._perform 对齐)
BOUND_ACTIONS = frozenset({
    "click", "fill", "check", "uncheck", "select_option", "press",
    "assert_text", "assert_visible", "wait_for",
    "upload_file", "upload", "set_input_files",
    "navigate",  # 仅首步/换页;无 element_key
    "agent",     # L3:交给步进 Agent
})

# 仅用于断言编译的动作
ASSERT_ACTIONS = frozenset({"assert_text", "assert_visible"})

LEVEL_L1 = "L1"
LEVEL_L2 = "L2"
LEVEL_L3 = "L3"

CONFIDENCE_L1 = 0.75
CONFIDENCE_L2 = 0.5


class UIBoundStep(BaseModel):
    """一条已绑定(或标记为 Agent 兜底)的操作步。"""
    step_no: int
    nl: str = Field(default="", description="原始自然语言步骤")
    action: str = Field(default="agent")
    element_key: Optional[str] = None
    value: Optional[str] = None
    confidence: float = 0.0
    level: str = LEVEL_L3
    reason: str = ""


class UIBoundAssert(BaseModel):
    """由 expected_results 编译出的结构化断言(执行在操作步之后)。"""
    index: int = Field(description="expected_results 中的 1-based 序号")
    nl: str = Field(default="", description="原始预期文本")
    action: str = Field(default="assert_text", description="assert_text|assert_visible")
    element_key: Optional[str] = None
    value: Optional[str] = None
    confidence: float = 0.0
    level: str = LEVEL_L3
    reason: str = ""


class ElementOption(BaseModel):
    """前端下拉用的元素精简视图。"""
    key: str
    name: str = ""
    type: str = ""


class UICompileResult(BaseModel):
    page_object_id: Optional[int] = None
    page_object_name: str = ""
    elements: List[ElementOption] = Field(default_factory=list)
    steps: List[UIBoundStep] = Field(default_factory=list)
    asserts: List[UIBoundAssert] = Field(default_factory=list)
    unmatched_elements: List[str] = Field(default_factory=list)
    compile_status: str = Field(default="ok", description="ok|no_page_object|llm_failed")
    site_origin: str = ""
    page_path: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# LLM 结构化输出(仅中间格式,不直接落库)
# ---------------------------------------------------------------------------

class LLMBoundStep(BaseModel):
    step_no: int
    action: str = Field(description="click|fill|check|...|agent;无法绑定时必须用 agent")
    element_key: Optional[str] = Field(default=None, description="必须来自给定元素 key 列表,否则为 null")
    value: Optional[str] = Field(default=None, description="fill/select 等的值;无则 null")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="", description="未绑定/降级原因")


class LLMCompileResult(BaseModel):
    steps: List[LLMBoundStep] = Field(default_factory=list)
    unmatched_elements: List[str] = Field(
        default_factory=list, description="步骤提到但元素表中不存在的元素描述"
    )


class LLMBoundAssert(BaseModel):
    index: int = Field(description="expected_results 的 1-based 序号")
    action: str = Field(default="assert_text", description="assert_text|assert_visible|skip")
    element_key: Optional[str] = None
    value: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="")


class LLMAssertResult(BaseModel):
    asserts: List[LLMBoundAssert] = Field(default_factory=list)


def assign_level(action: str, element_key: Optional[str], confidence: float) -> tuple[str, str]:
    """按动作/元素/置信度划分执行等级;返回 (level, reason)。"""
    action = (action or "").strip().lower()
    if action == "agent" or not element_key:
        return LEVEL_L3, "未绑定元素,交给 Agent 兜底"
    if action not in BOUND_ACTIONS:
        return LEVEL_L3, f"不支持的动作: {action}"
    if confidence >= CONFIDENCE_L1:
        return LEVEL_L1, ""
    if confidence >= CONFIDENCE_L2:
        return LEVEL_L2, "置信度中等,执行期先校验候选定位器"
    return LEVEL_L3, "置信度过低,交给 Agent 兜底"
