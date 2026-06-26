"""
Pipeline 专用 Pydantic Schema — 用于 LangChain structured_output
"""
from typing import List, Optional
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


class TestStep(BaseModel):
    """测试步骤"""
    step: int = Field(description="步骤序号")
    action: str = Field(description="具体的操作描述")
    expected: str = Field(description="该步骤的预期结果")


class TestCaseOut(BaseModel):
    """阶段3输出：单个测试用例"""
    test_point_id: Optional[int] = Field(default=None, description="关联的测试点ID")
    title: str = Field(description="测试用例标题")
    description: str = Field(default="", description="测试用例描述")
    preconditions: str = Field(default="无", description="前置条件")
    test_steps: List[TestStep] = Field(default_factory=list, description="测试步骤列表（5-8步）")


class TestCaseDesignResult(BaseModel):
    """阶段3输出：测试用例设计结果"""
    test_cases: List[TestCaseOut] = Field(default_factory=list)


class OptimizedCaseOut(BaseModel):
    """阶段4输出：优化后的测试用例"""
    id: Optional[int] = Field(default=None, description="原测试用例的ID")
    title: str = Field(description="优化后的标题")
    description: str = Field(default="", description="优化后的描述")
    preconditions: str = Field(default="无", description="优化后的前置条件")
    test_steps: List[TestStep] = Field(default_factory=list, description="优化后的测试步骤")


class TestCaseOptimizationResult(BaseModel):
    """阶段4输出：测试用例优化结果"""
    optimized_cases: List[OptimizedCaseOut] = Field(default_factory=list)
