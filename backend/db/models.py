import zoneinfo
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import field_validator
from sqlmodel import Field, SQLModel, Relationship

cn_tz = zoneinfo.ZoneInfo("Asia/Shanghai")


# 定义枚举类型：仅允许 1/2/3/4
class AllowedValue(int, Enum):
    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4


# 定义状态枚举类型
class StatusValue(str, Enum):
    NOT_RUN = "NOT_RUN"  # "未执行"
    PASSED = "PASSED"  # "通过"
    FAILED = "FAILED"  # "未通过"


class BaseModel(SQLModel):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=cn_tz))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=cn_tz),
                                 sa_column_kwargs={"onupdate": lambda: datetime.now(tz=cn_tz)})


class Session(BaseModel, table=True):
    """会话数据模型"""
    name: str
    # 定义与测试用例的一对多关系
    test_cases: List["TestCase"] = Relationship(back_populates="session")


from sqlalchemy.dialects.sqlite import JSON


class TestCase(BaseModel, table=True):
    """测试用例数据模型"""
    case_name: str = Field(default="")
    case_level: Optional[int] = Field(default=4, ge=1, le=4)  # 使用整数类型，添加范围约束
    preset_conditions: List[str] = Field(default_factory=list, sa_type=JSON)
    steps: List[str] = Field(default_factory=list, sa_type=JSON)
    expected_results: List[str] = Field(default_factory=list, sa_type=JSON)
    session_id: Optional[int] = Field(default=None, foreign_key="session.id")
    status: str = Field(default="NOT_RUN")  # 使用字符串类型，直接设置默认值created_at
    bug_id: Optional[int] = Field(default=None)

    # 定义与会话的多对一关系
    session: Optional[Session] = Relationship(back_populates="test_cases")
