"""
Pipeline 专用 Pydantic Schema — 用于 LangChain structured_output
"""
from typing import List, Optional, Any
from pydantic import BaseModel, Field


class RequirementPointOut(BaseModel):
    """阶段1输出：单个需求点"""
    content: str = Field(description="需求点的完整描述，保持原文准确性")
    module: str = Field(default="", description="所属功能模块名称")
    priority: str = Field(default="medium", description="优先级: high/medium/low")
    order_index: int = Field(default=0, description="需求点的顺序编号，从1开始")


class RequirementAnalysisResult(BaseModel):
    """阶段1输出：需求分析结果"""
    requirement_points: List[RequirementPointOut] = Field(default_factory=list)


class TestPointOut(BaseModel):
    """阶段2输出：单个测试点"""
    content: str = Field(description="测试点内容（清晰描述测试目的和验证要点）")
    test_type: str = Field(default="functional", description="测试类别: functional/performance/security/compatibility")
    design_method: str = Field(default="", description="测试设计方法: equivalence_partitioning/boundary_value/decision_table/state_transition")
    priority: str = Field(default="medium", description="优先级: high/medium/low")


class TestPointGenerationResult(BaseModel):
    """阶段2输出：测试点生成结果"""
    test_points: List[TestPointOut] = Field(default_factory=list)


# ============================================================
# 功能测试用例 schema（纯文本步骤）
# ============================================================

class TestStep(BaseModel):
    """测试步骤"""
    step: int = Field(description="步骤序号")
    action: str = Field(description="具体的操作描述")
    expected: str = Field(description="该步骤的预期结果")


class TestCaseOut(BaseModel):
    """阶段3输出：单个测试用例（功能测试）"""
    test_point_id: Optional[int] = Field(default=None, description="关联的测试点ID")
    title: str = Field(description="测试用例标题")
    description: str = Field(default="", description="测试用例描述")
    preconditions: str = Field(default="无", description="前置条件")
    test_steps: List[TestStep] = Field(default_factory=list, description="测试步骤列表（5-8步）")
    case_level: Optional[int] = Field(default=1, description="用例级别：1-功能 2-边界 3-异常 4-场景")


class TestCaseDesignResult(BaseModel):
    """阶段3输出：测试用例设计结果"""
    test_cases: List[TestCaseOut] = Field(default_factory=list)


# ============================================================
# 接口测试用例 schema（ApiCallStep + AssertionRule）
# ============================================================

class AssertionRuleOut(BaseModel):
    """断言规则"""
    type: str = Field(description="断言类型: status_code | status_code_range | response_time_lt | jsonpath_exists | jsonpath_equals | jsonpath_not_equals")
    value: Optional[Any] = Field(default=None, description="断言值")
    min: Optional[int] = Field(default=None, description="范围最小值（用于 status_code_range）")
    max: Optional[int] = Field(default=None, description="范围最大值（用于 status_code_range）")
    jsonpath: Optional[str] = Field(default=None, description="JSONPath 表达式")


class ApiCallKeyValueOut(BaseModel):
    """键值对（headers/parameters/variables）"""
    key: str = Field(description="字段名")
    value: str = Field(default="", description="字段值，可用 {{变量名}} 引用环境变量")
    in_field: Optional[str] = Field(default=None, alias="in", description="参数位置: query/path/header（仅 parameters 使用）")
    model_config = {"populate_by_name": True}


class PostActionOut(BaseModel):
    """后置动作，用于从响应中提取变量"""
    type: str = Field(default="extract_jsonpath", description="动作类型: extract_jsonpath")
    key: str = Field(description="提取的变量名，后续步骤用 {{变量名}} 引用")
    jsonpath: str = Field(description="JSONPath 表达式，如 $.data.id")


class ApiCallStepOut(BaseModel):
    """结构化的 API 调用步骤"""
    endpoint_ref: int = Field(description="关联的接口编号（在 API 信息中的序号，从1开始）")
    description: str = Field(default="", description="该步骤的测试描述")
    headers: Optional[List[ApiCallKeyValueOut]] = Field(default=None, description="请求头，覆盖接口默认值")
    parameters: Optional[List[ApiCallKeyValueOut]] = Field(default=None, description="请求参数（含 query/path），覆盖接口默认值")
    body: Optional[str] = Field(default=None, description="请求体 JSON 字符串，覆盖接口默认值。使用 {{变量名}} 引用环境变量")
    variables: Optional[List[ApiCallKeyValueOut]] = Field(default=None, description="预定义变量（固定值），不能从响应中提取")
    post_actions: Optional[List[PostActionOut]] = Field(default=None, description="后置动作，用于从响应中提取变量供后续步骤使用")
    assertions: Optional[List[AssertionRuleOut]] = Field(default=None, description="该步骤的断言规则")


class ApiTestCaseOut(BaseModel):
    """阶段3输出：单个接口测试用例"""
    test_point_id: Optional[int] = Field(default=None, description="关联的测试点ID")
    title: str = Field(description="测试用例标题")
    description: str = Field(default="", description="测试用例描述")
    preconditions: str = Field(default="无", description="前置条件")
    test_steps: List[ApiCallStepOut] = Field(default_factory=list, description="API 调用步骤列表")
    assertions: Optional[List[AssertionRuleOut]] = Field(default=None, description="用例级断言规则")
    api_endpoint_ref: Optional[List[int]] = Field(default=None, description="关联的 API 接口编号列表")
    case_level: Optional[int] = Field(default=1, description="用例级别：1-功能 2-边界 3-异常 4-场景")


class ApiTestCaseDesignResult(BaseModel):
    """阶段3输出：接口测试用例设计结果"""
    test_cases: List[ApiTestCaseOut] = Field(default_factory=list)


# ============================================================
# 阶段4：用例优化（功能测试和接口测试通用）
# ============================================================

class OptimizedCaseOut(BaseModel):
    """阶段4输出：优化后的测试用例"""
    id: Optional[int] = Field(default=None, description="原测试用例的ID")
    title: str = Field(description="优化后的标题")
    description: str = Field(default="", description="优化后的描述")
    preconditions: str = Field(default="无", description="优化后的前置条件")
    test_steps: List[TestStep] = Field(default_factory=list, description="优化后的测试步骤")
    case_level: Optional[int] = Field(default=1, description="用例级别：1-功能 2-边界 3-异常 4-场景")


class ApiOptimizedCaseOut(BaseModel):
    """阶段4输出：优化后的接口测试用例"""
    id: Optional[int] = Field(default=None, description="原测试用例的ID")
    title: str = Field(description="优化后的标题")
    description: str = Field(default="", description="优化后的描述")
    preconditions: str = Field(default="无", description="优化后的前置条件")
    test_steps: List[ApiCallStepOut] = Field(default_factory=list, description="优化后的 API 调用步骤")
    assertions: Optional[List[AssertionRuleOut]] = Field(default=None, description="用例级断言规则")
    case_level: Optional[int] = Field(default=1, description="用例级别：1-功能 2-边界 3-异常 4-场景")


class TestCaseOptimizationResult(BaseModel):
    """阶段4输出：功能测试用例优化结果"""
    optimized_cases: List[OptimizedCaseOut] = Field(default_factory=list)


class ApiTestCaseOptimizationResult(BaseModel):
    """阶段4输出：接口测试用例优化结果"""
    optimized_cases: List[ApiOptimizedCaseOut] = Field(default_factory=list)
