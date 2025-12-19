from dataclasses import dataclass
from typing import List, Optional, Literal
from datetime import datetime

# 定义字面量类型：仅允许 1/2/3/4
AllowedValue = Literal[1, 2, 3, 4]

@dataclass
class Session:
    """会话数据模型"""
    id: str
    name: str
    created_at: datetime
    updated_at: datetime

@dataclass
class TestCase:
    """测试用例数据模型"""
    id: Optional[int] = None
    case_name: str = ""
    case_level: Optional[AllowedValue] = 4
    preset_conditions: List[str] = None
    steps: List[str] = None
    expected_results: List[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    session_id: Optional[str] = None
    status: Optional[str] = "pending"  # 测试用例状态：pending（待完成）、completed（已完成）
    
    def __post_init__(self):
        """初始化默认值"""
        if self.preset_conditions is None:
            self.preset_conditions = []
        if self.steps is None:
            self.steps = []
        if self.expected_results is None:
            self.expected_results = []
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.status is None:
            self.status = "pending"
        
        """验证case_level只允许1、2、3、4"""
        allowed_levels = [1, 2, 3, 4]
        if self.case_level is not None and self.case_level not in allowed_levels:
            raise ValueError(f"case_level必须是{allowed_levels}中的一个，当前值：{self.case_level}")
        
        """验证status只允许pending或completed"""
        allowed_status = ["pending", "completed"]
        if self.status not in allowed_status:
            raise ValueError(f"status必须是{allowed_status}中的一个，当前值：{self.status}")
