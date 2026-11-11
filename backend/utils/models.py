"""Pydantic models for AI-generated test cases."""

from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from utils.helpers import _normalize_testcase_dict, _split_numbered_text_items


AllowedValue = Literal[1, 2, 3, 4]


class AssertionRule(BaseModel):
    """Assertion rule used by API test execution."""

    type: str = Field(
        ...,
        description="Assertion type: status_code | status_code_range | response_time_lt | jsonpath_exists | jsonpath_equals | jsonpath_not_equals",
    )
    value: Optional[Any] = Field(default=None, description="Assertion value")
    min: Optional[int] = Field(default=None, description="Minimum value for status_code_range")
    max: Optional[int] = Field(default=None, description="Maximum value for status_code_range")
    jsonpath: Optional[str] = Field(default=None, description="JSONPath expression")

    @model_validator(mode="after")
    def validate_assertion_type(self) -> "AssertionRule":
        t = self.type
        if t == "status_code" and self.value is None:
            raise ValueError("status_code assertion requires value")
        if t == "status_code_range" and (self.min is None or self.max is None):
            raise ValueError("status_code_range assertion requires min and max")
        if t == "jsonpath_exists" and not self.jsonpath:
            raise ValueError("jsonpath_exists assertion requires jsonpath")
        if t == "jsonpath_equals" and (not self.jsonpath or self.value is None):
            raise ValueError("jsonpath_equals assertion requires jsonpath and value")
        if t == "jsonpath_not_equals" and (not self.jsonpath or self.value is None):
            raise ValueError("jsonpath_not_equals assertion requires jsonpath and value")
        return self


class ApiCallKeyValue(BaseModel):
    """Key/value item in API call config."""

    key: str = Field(..., description="Field name")
    value: str = Field(default="", description="Field value, supports {{variable}} references")
    in_field: Optional[str] = Field(default=None, alias="in", description="Parameter location: query/path/header")

    model_config = {"populate_by_name": True}


class PostAction(BaseModel):
    """Post action for extracting variables from API responses."""

    type: str = Field(default="extract_jsonpath", description="Action type")
    key: str = Field(..., description="Extracted variable name")
    jsonpath: str = Field(..., description="JSONPath expression, e.g. $.data.id")


class ApiCallStep(BaseModel):
    """Structured API call step."""

    endpoint_ref: int = Field(..., description="Referenced endpoint number, starting from 1")
    description: str = Field(default="", description="Step description")
    headers: Optional[List[ApiCallKeyValue]] = Field(default=None, description="Request headers")
    parameters: Optional[List[ApiCallKeyValue]] = Field(default=None, description="Request parameters including query/path")
    body: Optional[str] = Field(default=None, description="Request body JSON string")
    variables: Optional[List[ApiCallKeyValue]] = Field(default=None, description="Predefined fixed variables")
    post_actions: Optional[List[PostAction]] = Field(default=None, description="Post actions for variable extraction")
    assertions: Optional[List[AssertionRule]] = Field(default=None, description="Step-level assertions")


def _normalize_preset_conditions_value(value):
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    normalized = []
    for item in items:
        if isinstance(item, str):
            normalized.extend(_split_numbered_text_items(item))
        else:
            normalized.append(item)
    return normalized


class TestCase(BaseModel):
    """General test case schema."""

    case_name: str = Field(..., description="Test case name")
    steps: List[Union[str, ApiCallStep]] = Field(..., description="Test steps, supports plain text and api_call steps")
    preset_conditions: List[Union[str, ApiCallStep]] = Field(
        default_factory=list,
        description="Preconditions. Each list item must contain exactly one precondition; do not combine multiple numbered preconditions into one string item.",
    )
    expected_results: List[str] = Field(..., description="Expected results")
    case_level: Optional[AllowedValue] = Field(default=1, description="Case level: 1-functional 2-boundary 3-exception 4-scenario")
    api_endpoint_ref: Optional[List[int]] = Field(default=None, description="Referenced API endpoint numbers")
    assertions: Optional[List[AssertionRule]] = Field(default=None, description="Case-level assertions")

    @field_validator("preset_conditions", mode="before")
    @classmethod
    def normalize_preset_conditions(cls, value):
        return _normalize_preset_conditions_value(value)


class FunctionalTestCase(BaseModel):
    """Functional test case schema with plain-text steps only."""

    case_name: str = Field(..., description="Test case name in the form 'action + scenario + expected result'")
    steps: List[str] = Field(..., description="Plain-text test steps")
    preset_conditions: List[str] = Field(
        default_factory=list,
        description="Plain-text preconditions. Each list item must contain exactly one precondition; do not combine multiple numbered preconditions into one string item.",
    )
    expected_results: List[str] = Field(..., description="Expected results")
    case_level: Optional[AllowedValue] = Field(default=1, description="Case level: 1-functional 2-boundary 3-exception 4-scenario")

    @field_validator("preset_conditions", mode="before")
    @classmethod
    def normalize_preset_conditions(cls, value):
        return _normalize_preset_conditions_value(value)

    def __post_init__(self):
        allowed_levels = [1, 2, 3, 4]
        if self.case_level is not None and self.case_level not in allowed_levels:
            raise ValueError(f"case_level must be one of {allowed_levels}, got {self.case_level}")


class ResponseFormat(BaseModel):
    """Structured response format for general test cases."""

    response: List[TestCase] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_response_key(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "test_cases" in data and "response" not in data:
                data = dict(data)
                data["response"] = data.pop("test_cases")
            if "response" in data and isinstance(data["response"], list):
                data["response"] = [
                    _normalize_testcase_dict(tc) if isinstance(tc, dict) else tc
                    for tc in data["response"]
                ]
        return data


class FunctionalResponseFormat(BaseModel):
    """Structured response format for functional test cases."""

    response: List[FunctionalTestCase] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_response_key(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "test_cases" in data and "response" not in data:
                data = dict(data)
                data["response"] = data.pop("test_cases")
            if "response" in data and isinstance(data["response"], list):
                data["response"] = [
                    _normalize_testcase_dict(tc) if isinstance(tc, dict) else tc
                    for tc in data["response"]
                ]
        return data


class TestCaseDesignMethod(BaseModel):
    """Test case design method metadata."""

    method: str
    description: str | None = None


class CaseImpactSuggestion(BaseModel):
    """需求变更影响分析：单条用例修改建议。"""

    testcase_id: int = Field(..., description="受影响的测试用例ID")
    suggestion: str = Field(default="", description="基于需求差异的修改建议")


class CaseImpactSuggestionList(BaseModel):
    """需求变更影响分析：批量用例修改建议。"""

    suggestions: List[CaseImpactSuggestion] = Field(default_factory=list)
