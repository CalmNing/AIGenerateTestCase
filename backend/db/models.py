import zoneinfo
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import field_validator, model_validator
from sqlmodel import Field, SQLModel, Relationship
from pydantic.config import ConfigDict

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
    FAILED = "FAILED"  # "未通过")


class BaseModel(SQLModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None
        }
    )
    
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=cn_tz))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=cn_tz),
                                 sa_column_kwargs={"onupdate": lambda: datetime.now(tz=cn_tz)})

    @model_validator(mode='before')
    @classmethod
    def convert_datetime_strings(cls, values):
        if isinstance(values, dict):
            for key in ['created_at', 'updated_at']:
                if key in values and isinstance(values[key], str):
                    # 处理ISO格式的日期时间字符串
                    dt_str = values[key].replace('Z', '+00:00')
                    try:
                        # 尝试解析带时区的日期时间字符串
                        values[key] = datetime.fromisoformat(dt_str)
                    except ValueError:
                        # 如果fromisoformat失败，尝试手动替换并解析
                        dt_str = dt_str.replace('T', ' ')
                        if len(dt_str) > 19 and dt_str[19] == '.':  # 包含毫秒
                            dt_str = dt_str[:26]  # 截取合理的长度
                        values[key] = datetime.fromisoformat(dt_str)
        return values


class Session(BaseModel, table=True):
    """会话数据模型"""
    name: str
    # 定义与测试用例的一对多关系
    test_cases: List["TestCase"] = Relationship(back_populates="session")
    # 定义与模块的一对多关系
    modules: List["Module"] = Relationship(back_populates="session")


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
    module_id: Optional[int] = Field(default=None)

    # 定义与会话的多对一关系
    session: Optional[Session] = Relationship(back_populates="test_cases")

class Module(BaseModel, table=True):
    """模块数据模型"""
    module_name: str = Field(default="")
    session_id: Optional[int] = Field(default=None, foreign_key="session.id")

    # 添加与会话的一对多关系
    session: Optional[Session] = Relationship(back_populates="modules")


class SavedRequest(BaseModel, table=True):
    """保存的请求配置数据模型"""
    name: str = Field(default="")
    method: str = Field(default="GET")
    url: str = Field(default="")
    headers: List[dict] = Field(default_factory=list, sa_type=JSON)
    parameters: List[dict] = Field(default_factory=list, sa_type=JSON)
    body: Optional[str] = Field(default=None)
    user_id: Optional[int] = Field(default=None)  # 可以根据需要添加用户关联

    # 定义与会话的多对一关系（可选）
    # session_id: Optional[int] = Field(default=None, foreign_key="session.id")
    # session: Optional[Session] = Relationship(back_populates="saved_requests")