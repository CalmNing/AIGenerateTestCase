import zoneinfo
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import field_validator, model_validator
from sqlmodel import Field, SQLModel, Relationship
from pydantic.config import ConfigDict
from sqlalchemy.dialects.sqlite import JSON

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
            for key in ['created_at', 'updated_at', 'last_run_at']:
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
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")
    api_config: Optional[dict] = Field(default=None, sa_type=JSON, description="会话级API调用配置（headers、environment_id等）")
    # 定义与测试用例的一对多关系
    test_cases: List["TestCase"] = Relationship(back_populates="session")
    # 定义与模块的一对多关系
    modules: List["Module"] = Relationship(back_populates="session")


class TestCase(BaseModel, table=True):
    """测试用例数据模型"""
    case_name: str = Field(default="")
    case_level: Optional[int] = Field(default=4, ge=1, le=4)  # 使用整数类型，添加范围约束
    preset_conditions: List = Field(default_factory=list, sa_type=JSON)
    steps: List = Field(default_factory=list, sa_type=JSON)
    expected_results: List = Field(default_factory=list, sa_type=JSON)
    session_id: Optional[int] = Field(default=None, foreign_key="session.id")
    status: str = Field(default="NOT_RUN")  # 使用字符串类型，直接设置默认值created_at
    bug_id: Optional[int] = Field(default=None)
    module_id: Optional[int] = Field(default=None)
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")
    api_endpoint_id: Optional[str] = Field(default=None, description="关联的 API Endpoint ID（逗号分隔）")
    api_project_id: Optional[int] = Field(default=None, description="关联的 API Project ID")
    assertions: Optional[List[dict]] = Field(default=None, sa_type=JSON, description="用例级断言规则，执行时覆盖接口默认断言")
    scenario_id: Optional[int] = Field(default=None, foreign_key="apiscenario.id", description="关联的接口场景ID")

    # 定义与会话的多对一关系
    session: Optional[Session] = Relationship(back_populates="test_cases")

class Module(BaseModel, table=True):
    """模块数据模型"""
    module_name: str = Field(default="")
    session_id: Optional[int] = Field(default=None, foreign_key="session.id")
    parent_id: Optional[int] = Field(default=None, foreign_key="module.id")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")

    # 添加与会话的一对多关系
    session: Optional[Session] = Relationship(back_populates="modules")
    # 子模块关系
    children: List["Module"] = Relationship(
        back_populates="parent",
        sa_relationship_kwargs={"foreign_keys": "[Module.parent_id]"}
    )
    # 父模块关系
    parent: Optional["Module"] = Relationship(
        back_populates="children",
        sa_relationship_kwargs={"foreign_keys": "[Module.parent_id]", "remote_side": "Module.id"}
    )


class SavedRequest(BaseModel, table=True):
    """保存的请求配置数据模型"""
    name: str = Field(default="")
    method: str = Field(default="GET")
    url: str = Field(default="")
    headers: List[dict] = Field(default_factory=list, sa_type=JSON)
    parameters: List[dict] = Field(default_factory=list, sa_type=JSON)
    body: Optional[str] = Field(default=None)
    post_extractions: List[dict] = Field(default_factory=list, sa_type=JSON)  # 后置提取规则
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")  # 激活用户关联

    # 定义与会话的多对一关系（可选）
    # session_id: Optional[int] = Field(default=None, foreign_key="session.id")
    # session: Optional[Session] = Relationship(back_populates="saved_requests")


class GlobalParameter(BaseModel, table=True):
    """全局参数配置数据模型"""
    name: str = Field(default="")  # 环境名称
    parameters: List[dict] = Field(default_factory=list, sa_type=JSON)  # 参数列表
    is_default: bool = Field(default=False)  # 是否为默认环境
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")  # 激活用户关联


class HistoryPrompt(BaseModel, table=True):
    """历史需求描述（提示词）数据模型"""
    content: str = Field(default="", description="需求描述内容")
    module_id: Optional[int] = Field(default=None, foreign_key="module.id", description="关联模块ID")
    session_id: Optional[int] = Field(default=None, foreign_key="session.id", description="关联会话ID")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")

    # 定义与模块的多对一关系
    module: Optional["Module"] = Relationship(sa_relationship_kwargs={"foreign_keys": "[HistoryPrompt.module_id]"})
    # 定义与会话的多对一关系
    session: Optional["Session"] = Relationship(sa_relationship_kwargs={"foreign_keys": "[HistoryPrompt.session_id]"})


class ScheduledTask(BaseModel, table=True):
    """定时任务数据模型"""
    name: str = Field(default="", description="任务名称")
    schedule_type: str = Field(default="interval", description="调度类型: interval | cron")
    interval_seconds: int = Field(default=60, description="间隔秒数（interval类型）")
    cron_expression: Optional[str] = Field(default=None, description="Cron 表达式（cron类型）")
    request_ids: List[int] = Field(default_factory=list, sa_type=JSON, description="关联的保存请求ID列表")
    environment_id: Optional[int] = Field(default=None, description="执行时使用的环境ID")
    parameters: List[dict] = Field(default_factory=list, sa_type=JSON, description="任务级参数（优先级高于环境参数）")
    enabled: bool = Field(default=True, description="是否启用")
    last_run_at: Optional[datetime] = Field(default=None, description="上次执行时间")
    last_run_result: Optional[str] = Field(default=None, description="上次执行结果（JSON字符串）")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")


class MockConfig(BaseModel, table=True):
    """Mock 接口配置数据模型"""
    name: str = Field(default="", description="Mock名称")
    method: str = Field(default="GET", description="HTTP方法")
    url_path: str = Field(default="", description="匹配的URL路径")
    status_code: int = Field(default=200, description="响应状态码")
    response_headers: List[dict] = Field(default_factory=list, sa_type=JSON, description="响应头列表")
    response_body: Optional[str] = Field(default=None, description="响应体（JSON字符串）")
    enabled: bool = Field(default=True, description="是否启用")
    environment_id: Optional[int] = Field(default=None, description="参数化使用的环境ID")
    response_count: int = Field(default=1, ge=1, description="返回数据条目数量（分页用）")
    page_size: Optional[int] = Field(default=None, ge=1, description="分页大小（可选，默认为1）")
    json_path: Optional[str] = Field(default=None, description="JSON路径，指定响应体中返回批量数据的字段，如 $.data.items")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")

class McpServer(BaseModel, table=True):
    """MCP 服务器配置数据模型（服务端持久化）"""
    name: str = Field(default="", description="服务器名称")
    type: str = Field(default="http", description="传输类型: http | stdio")
    enabled: bool = Field(default=True, description="是否启用")
    url: Optional[str] = Field(default=None, description="HTTP 模式的 URL")
    command: Optional[str] = Field(default=None, description="STDIO 模式的命令")
    args: List[str] = Field(default_factory=list, sa_type=JSON, description="命令行参数")
    timeout: int = Field(default=60, description="超时时间（秒）")
    env: List[dict] = Field(default_factory=list, sa_type=JSON, description="环境变量")
    enabled_tools: Optional[List[str]] = Field(default=None, sa_type=JSON, description="启用的工具名称列表，null表示全部启用")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")


class ApiProject(BaseModel, table=True):
    """导入的接口测试项目"""
    name: str = Field(default="", description="项目名称")
    base_url: str = Field(default="", description="默认 Base URL")
    headers: List[dict] = Field(default_factory=list, sa_type=JSON, description="项目级请求头")
    environment_id: Optional[int] = Field(default=None, description="项目默认执行环境ID")
    source_type: str = Field(default="manual", description="来源: upload | url | manual")
    source_url: Optional[str] = Field(default=None, description="OpenAPI/Swagger 来源 URL")
    raw_spec: Optional[str] = Field(default=None, description="原始 OpenAPI/Swagger 文档")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")


class ApiEndpoint(BaseModel, table=True):
    """接口测试工具中的单个接口定义"""
    project_id: int = Field(foreign_key="apiproject.id", description="接口项目ID")
    name: str = Field(default="", description="接口名称")
    method: str = Field(default="GET", description="HTTP 方法")
    path: str = Field(default="", description="接口路径")
    url: Optional[str] = Field(default=None, description="覆盖完整 URL")
    tags: List[str] = Field(default_factory=list, sa_type=JSON)
    headers: List[dict] = Field(default_factory=list, sa_type=JSON)
    parameters: List[dict] = Field(default_factory=list, sa_type=JSON)
    body: Optional[str] = Field(default=None, description="请求体")
    request_schema: dict = Field(default_factory=dict, sa_type=JSON, description="OpenAPI 请求体 Schema")
    response_schema: dict = Field(default_factory=dict, sa_type=JSON, description="OpenAPI 主要响应 Schema")
    environment_id: Optional[int] = Field(default=None, description="默认执行环境ID")
    pre_actions: List[dict] = Field(default_factory=list, sa_type=JSON)
    post_actions: List[dict] = Field(default_factory=list, sa_type=JSON)
    assertions: List[dict] = Field(default_factory=list, sa_type=JSON)
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")


class ApiScenario(BaseModel, table=True):
    """接口场景"""
    project_id: int = Field(foreign_key="apiproject.id", description="接口项目ID")
    name: str = Field(default="", description="场景名称")
    description: str = Field(default="", description="场景描述")
    base_url: Optional[str] = Field(default=None, description="场景级 Base URL 覆盖")
    environment_id: Optional[int] = Field(default=None, description="执行环境ID")
    variables: List[dict] = Field(default_factory=list, sa_type=JSON)
    steps: List[dict] = Field(default_factory=list, sa_type=JSON)
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")


class ApiScenarioResult(BaseModel, table=True):
    """接口场景执行结果"""
    scenario_id: int = Field(foreign_key="apiscenario.id", index=True, description="接口场景ID")
    project_id: int = Field(foreign_key="apiproject.id", index=True, description="接口项目ID")
    scenario_name: str = Field(default="", description="执行时的场景名称快照")
    passed: bool = Field(default=False, description="是否执行通过")
    result: dict = Field(default_factory=dict, sa_type=JSON, description="执行结果详情")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")


class TestCaseExecutionLog(BaseModel, table=True):
    """测试用例 API 执行日志"""
    testcase_id: int = Field(foreign_key="testcase.id", index=True, description="测试用例ID")
    session_id: int = Field(foreign_key="session.id", index=True, description="会话ID")
    case_name: str = Field(default="", description="执行时的用例名称快照")
    passed: bool = Field(default=False, description="是否执行通过")
    status: str = Field(default="FAILED", description="执行后的用例状态")
    result: dict = Field(default_factory=dict, sa_type=JSON, description="执行日志详情")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID")


class MockLog(BaseModel, table=True):
    """Mock 日志数据模型，记录每次Mock请求/响应的完整信息"""
    config_id: Optional[int] = Field(default=None, foreign_key="mockconfig.id", description="关联的Mock配置ID")
    config_name: str = Field(default="", description="Mock配置名称（快照，即使配置被删除也可追溯）")
    request_method: str = Field(default="", description="HTTP请求方法")
    request_path: str = Field(default="", description="请求URL路径")
    request_headers: List[dict] = Field(default_factory=list, sa_type=JSON, description="请求头列表")
    request_query_params: Optional[str] = Field(default=None, description="请求查询参数（JSON字符串）")
    request_body: Optional[str] = Field(default=None, description="请求体内容")
    response_status_code: int = Field(default=200, description="响应状态码")
    response_headers: List[dict] = Field(default_factory=list, sa_type=JSON, description="响应头列表")
    response_body: Optional[str] = Field(default=None, description="响应体内容")
    matched: bool = Field(default=True, description="是否匹配到Mock配置")
    user_id: Optional[str] = Field(default=None, index=True, description="触发请求的用户ID（Keycloak sub）")
