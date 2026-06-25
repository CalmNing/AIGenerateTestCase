"""AI 测试用例生成的数据模型定义。"""

from typing import List, Optional, Literal, Any, Union

from pydantic import BaseModel, Field, model_validator


# 定义字面量类型：仅允许 1/2/3/4
AllowedValue = Literal[1, 2, 3, 4]


# 定义断言规则
class AssertionRule(BaseModel):
    """单个断言规则，匹配 api_test_tool._run_assertions 的期望格式"""
    type: str = Field(..., description="断言类型: status_code | status_code_range | response_time_lt | jsonpath_exists | jsonpath_equals")
    value: Optional[Any] = Field(default=None, description="断言值")
    min: Optional[int] = Field(default=None, description="范围最小值（用于 status_code_range）")
    max: Optional[int] = Field(default=None, description="范围最大值（用于 status_code_range）")
    jsonpath: Optional[str] = Field(default=None, description="JSONPath 表达式")

    @model_validator(mode='after')
    def validate_assertion_type(self) -> 'AssertionRule':
        """按断言类型验证必需字段。"""
        t = self.type
        if t == 'status_code' and self.value is None:
            raise ValueError(f'status_code 断言必须提供 value 参数')
        if t == 'status_code_range' and (self.min is None or self.max is None):
            raise ValueError(f'status_code_range 断言必须同时提供 min 和 max 参数')
        if t == 'jsonpath_exists' and not self.jsonpath:
            raise ValueError(f'jsonpath_exists 断言必须提供 jsonpath 参数')
        if t == 'jsonpath_equals' and (not self.jsonpath or self.value is None):
            raise ValueError(f'jsonpath_equals 断言必须同时提供 jsonpath 和 value 参数')
        return self


class ApiCallKeyValue(BaseModel):
    """API 调用步骤中的键值对（headers/parameters/variables）"""
    key: str = Field(..., description="字段名")
    value: str = Field(default="", description="字段值，可用 {{变量名}} 引用环境变量")
    in_field: Optional[str] = Field(default=None, alias="in", description="参数位置: query/path/header（仅 parameters 使用）")
    model_config = {"populate_by_name": True}


class ApiCallStep(BaseModel):
    """结构化的 API 调用步骤，对应执行时的 api_call 步骤"""
    endpoint_ref: int = Field(..., description="关联的接口编号（在 API 信息中的序号，从1开始）")
    description: str = Field(default="", description="该步骤的测试描述")
    headers: Optional[List[ApiCallKeyValue]] = Field(default=None, description="请求头，覆盖接口默认值")
    parameters: Optional[List[ApiCallKeyValue]] = Field(default=None, description="请求参数（含 query/path），覆盖接口默认值")
    body: Optional[str] = Field(default=None, description="请求体 JSON 字符串，覆盖接口默认值。使用 {{变量名}} 引用环境变量")
    variables: Optional[List[ApiCallKeyValue]] = Field(default=None, description="环境变量（可在 body/parameters/headers 中用 {{key}} 引用）")
    assertions: Optional[List[AssertionRule]] = Field(default=None, description="该步骤的断言规则，覆盖用例级和接口级断言")


# 定义测试用例数据类
class TestCase(BaseModel):
    """自定义 TestCase schema."""
    case_name: str = Field(..., description="用例名称")
    steps: List[Union[str, ApiCallStep]] = Field(..., description="用例步骤，支持纯文本步骤和结构化 api_call 步骤")
    case_level: Optional[AllowedValue] = Field(default=4, description="用例级别: 1-功能 2-边界 3-异常 4-场景")
    preset_conditions: List[Union[str, ApiCallStep]] = Field(default_factory=list, description="前置条件，支持纯文本和 api_call 步骤")
    expected_results: List[str] = Field(default_factory=list, description="预期结果列表")
    api_endpoint_ref: Optional[List[int]] = Field(default=None, description="关联的接口编号列表")


class ResponseFormat(BaseModel):
    """定义结构化输出格式"""
    testcases: List[TestCase] = Field(..., description="测试用例列表")


class TestCaseDesignMethod(BaseModel):
    """自定义 TestCaseDesignMethod schema."""
    method: str
    description: str | None = None
