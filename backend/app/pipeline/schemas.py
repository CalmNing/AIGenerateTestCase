"""Pipeline-specific Pydantic schemas for structured output."""

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

from utils.helpers import _split_numbered_text_items


def _normalize_preconditions(value) -> List[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    normalized: List[str] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, str):
            normalized.extend(_split_numbered_text_items(item))
        else:
            normalized.append(str(item))
    return normalized


class RequirementPointOut(BaseModel):
    content: str = Field(description="Full requirement point content")
    module: str = Field(default="", description="Module name")
    priority: str = Field(default="medium", description="Priority: high/medium/low")
    order_index: int = Field(default=0, description="Requirement point order index")


class RequirementAnalysisResult(BaseModel):
    requirement_points: List[RequirementPointOut] = Field(default_factory=list)


class RequirementDedupResult(BaseModel):
    """需求点去重结果：返回需保留的新需求点候选编号（0-based）。"""
    keep_indices: List[int] = Field(
        default_factory=list,
        description="需要保留的新需求点候选编号（0-based，对应传入的候选列表下标）。"
        "若全部候选都与已有需求点语义重复则为空列表。",
    )


class TestPointDedupResult(BaseModel):
    """测试点去重结果：返回需保留的测试点 id 列表（DB id）。"""
    keep_ids: List[int] = Field(
        default_factory=list,
        description="需要保留的测试点 id 列表。只保留每个重复簇中最综合的一个；"
        "若候选互不重复则返回全部 id。",
    )


class TestPointOut(BaseModel):
    content: str = Field(description="Test point content")
    test_type: str = Field(default="functional", description="Test type")
    design_method: str = Field(default="", description="Test design method")
    priority: str = Field(default="medium", description="Priority: high/medium/low")


class TestPointGenerationResult(BaseModel):
    test_points: List[TestPointOut] = Field(default_factory=list)


class TestStep(BaseModel):
    step: int = Field(description="Step number")
    action: str = Field(description="Concrete action")
    expected: str = Field(description="Expected result for this step")


class TestCaseOut(BaseModel):
    test_point_id: Optional[int] = Field(default=None, description="Related test point ID")
    title: str = Field(description="Test case title")
    description: str = Field(default="", description="Test case description")
    preconditions: List[str] = Field(
        default_factory=list,
        description="Preconditions. Each array item must contain exactly one precondition.",
    )
    test_steps: List[TestStep] = Field(default_factory=list, description="Test steps list")
    case_level: Optional[int] = Field(default=1, description="Case level: 1-functional 2-boundary 3-exception 4-scenario")

    @field_validator("preconditions", mode="before")
    @classmethod
    def normalize_preconditions(cls, value):
        return _normalize_preconditions(value)


class TestCaseDesignResult(BaseModel):
    test_cases: List[TestCaseOut] = Field(default_factory=list)


class AssertionRuleOut(BaseModel):
    type: str = Field(description="Assertion type")
    value: Optional[Any] = Field(default=None, description="Assertion value")
    min: Optional[int] = Field(default=None, description="Minimum for status_code_range")
    max: Optional[int] = Field(default=None, description="Maximum for status_code_range")
    jsonpath: Optional[str] = Field(default=None, description="JSONPath expression")


class ApiCallKeyValueOut(BaseModel):
    key: str = Field(description="Field name")
    value: str = Field(default="", description="Field value")
    in_field: Optional[str] = Field(default=None, alias="in", description="Parameter location")

    model_config = {"populate_by_name": True}


class PostActionOut(BaseModel):
    type: str = Field(default="extract_jsonpath", description="Action type")
    key: str = Field(description="Extracted variable name")
    jsonpath: str = Field(description="JSONPath expression")


class ApiCallStepOut(BaseModel):
    endpoint_ref: int = Field(description="Referenced endpoint number")
    description: str = Field(default="", description="Step description")
    headers: Optional[List[ApiCallKeyValueOut]] = Field(default=None, description="Request headers")
    parameters: Optional[List[ApiCallKeyValueOut]] = Field(default=None, description="Request parameters")
    body: Optional[str] = Field(default=None, description="Request body JSON string")
    variables: Optional[List[ApiCallKeyValueOut]] = Field(default=None, description="Predefined variables")
    post_actions: Optional[List[PostActionOut]] = Field(default=None, description="Post actions")
    assertions: Optional[List[AssertionRuleOut]] = Field(default=None, description="Assertions")


class ApiTestCaseOut(BaseModel):
    test_point_id: Optional[int] = Field(default=None, description="Related test point ID")
    title: str = Field(description="Test case title")
    description: str = Field(default="", description="Test case description")
    preconditions: List[str] = Field(
        default_factory=list,
        description="Preconditions. Each array item must contain exactly one precondition.",
    )
    test_steps: List[ApiCallStepOut] = Field(default_factory=list, description="API call steps")
    assertions: Optional[List[AssertionRuleOut]] = Field(default=None, description="Case-level assertions")
    api_endpoint_ref: Optional[List[int]] = Field(default=None, description="Referenced API endpoints")
    case_level: Optional[int] = Field(default=1, description="Case level: 1-functional 2-boundary 3-exception 4-scenario")

    @field_validator("preconditions", mode="before")
    @classmethod
    def normalize_preconditions(cls, value):
        return _normalize_preconditions(value)


class ApiTestCaseDesignResult(BaseModel):
    test_cases: List[ApiTestCaseOut] = Field(default_factory=list)


class OptimizedCaseOut(BaseModel):
    id: Optional[int] = Field(default=None, description="Original test case ID")
    title: str = Field(description="Optimized title")
    description: str = Field(default="", description="Optimized description")
    preconditions: List[str] = Field(
        default_factory=list,
        description="Optimized preconditions. Each array item must contain exactly one precondition.",
    )
    test_steps: List[TestStep] = Field(default_factory=list, description="Optimized test steps")
    case_level: Optional[int] = Field(default=1, description="Case level: 1-functional 2-boundary 3-exception 4-scenario")

    @field_validator("preconditions", mode="before")
    @classmethod
    def normalize_preconditions(cls, value):
        return _normalize_preconditions(value)


class ApiOptimizedCaseOut(BaseModel):
    id: Optional[int] = Field(default=None, description="Original test case ID")
    title: str = Field(description="Optimized title")
    description: str = Field(default="", description="Optimized description")
    preconditions: List[str] = Field(
        default_factory=list,
        description="Optimized preconditions. Each array item must contain exactly one precondition.",
    )
    test_steps: List[ApiCallStepOut] = Field(default_factory=list, description="Optimized API call steps")
    assertions: Optional[List[AssertionRuleOut]] = Field(default=None, description="Case-level assertions")
    case_level: Optional[int] = Field(default=1, description="Case level: 1-functional 2-boundary 3-exception 4-scenario")

    @field_validator("preconditions", mode="before")
    @classmethod
    def normalize_preconditions(cls, value):
        return _normalize_preconditions(value)


class TestCaseOptimizationResult(BaseModel):
    optimized_cases: List[OptimizedCaseOut] = Field(default_factory=list)


class ApiTestCaseOptimizationResult(BaseModel):
    optimized_cases: List[ApiOptimizedCaseOut] = Field(default_factory=list)


class TestCaseDedupResult(BaseModel):
    """用例级冗余审查结果：返回验证维度完全被其他用例覆盖、应删除的用例 id 列表。"""
    remove_ids: List[int] = Field(
        default_factory=list,
        description="需要删除的用例 id 列表（其独有断言应已并入保留用例）。"
        "若全部用例互不重复则为空列表。",
    )
