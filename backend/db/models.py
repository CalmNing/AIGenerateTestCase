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
    # 定义与需求点的一对多关系（4阶段pipeline）
    requirement_points: List["RequirementPoint"] = Relationship(back_populates="session")
    # 定义与测试点的一对多关系（4阶段pipeline）
    test_points: List["TestPoint"] = Relationship(back_populates="session")


class TestCase(BaseModel, table=True):
    """测试用例数据模型"""
    case_name: str = Field(default="")
    case_level: Optional[int] = Field(default=1, ge=1, le=4)  # 使用整数类型，添加范围约束，默认为功能测试
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
    # 4阶段pipeline关联字段
    test_point_id: Optional[int] = Field(default=None, foreign_key="testpoint.id", index=True, description="关联的测试点ID（4阶段pipeline）")
    test_type: Optional[str] = Field(default=None, description="测试类别（继承自测试点，如 functional/performance/security）")
    design_method: Optional[str] = Field(default=None, description="测试设计方法（继承自测试点，如 equivalence_partitioning/boundary_value）")

    # ===== 准确性保证字段 =====
    consistency_report: Optional[str] = Field(default=None, description="一致性检查报告(JSON字符串)")
    confidence_score: Optional[float] = Field(default=None, description="置信度评分 0~1")
    confidence_level: Optional[str] = Field(default=None, description="置信度等级: high/medium/low")
    validation_status: Optional[str] = Field(default=None, description="验证状态: pending/passed/failed/error")
    validation_detail: Optional[str] = Field(default=None, description="验证结果详情(JSON字符串)")
    validation_at: Optional[datetime] = Field(default=None, description="验证执行时间(UTC+8)")

    # UI 自动化绑定计划(NL 步骤 → PageObject 元素),由 /ui-execution/compile 生成、前端可修正
    ui_binding: Optional[dict] = Field(default=None, sa_type=JSON, description="UI绑定计划{page_object_id,steps,asserts,...}")

    # 定义与会话的多对一关系
    session: Optional[Session] = Relationship(back_populates="test_cases")

class UIExecutionRecord(BaseModel, table=True):
    """UI 自动化执行记录(browser-use Agent 驱动无头浏览器执行功能用例)"""
    testcase_id: int = Field(foreign_key="testcase.id", index=True, description="关联用例ID")
    user_id: Optional[str] = Field(default=None, index=True, description="发起人ID（Keycloak sub）")
    target_url: str = Field(default="", description="拼接后的目标页面完整地址")
    status: str = Field(default="RUNNING", description="执行状态: RUNNING/PASSED/FAILED/ERROR/STOPPED")
    steps_trace: List = Field(default_factory=list, sa_type=JSON, description="Agent每步动作轨迹[{step_no,action,url,result}]")
    agent_summary: str = Field(default="", description="Agent最终判定说明(逐条预期核对)")
    error: Optional[str] = Field(default=None, description="异常信息(仅ERROR时)")
    finished_at: Optional[datetime] = Field(default=None, description="执行完成时间(UTC+8)")
    execution_mode: str = Field(default="agent", description="执行模式: agent(纯浏览器Agent) / hybrid(绑定+Agent兜底)")
    page_object_id: Optional[int] = Field(default=None, foreign_key="pageobject.id", index=True, description="本次使用的页面对象ID")
    binding_snapshot: List = Field(default_factory=list, sa_type=JSON, description="执行时的绑定计划快照(防用例后续被改)")

class PageObject(BaseModel, table=True):
    """UI 自动化页面对象(抽取页面元素 + 多候选定位器,支持执行期自愈)。

    登录态也挂在页面对象上:把一个页面对象的 `is_login_page` 打开(通常是登录页),
    它「执行(自愈)」里配置的步骤就承担登录动作,执行产生的登录态被保存到该对象的
    专属文件;其他页面对象只需 `login_page_object_id` 关联它,即可直接复用登录态
    (有就直接用,没有就先执行它的步骤完成登录并保持),无需重复配置账号密码。
    """
    name: str = Field(default="", description="页面对象名称,如 LoginPage/登录页")
    site_origin: str = Field(default="", description="站点 origin,如 http://192.168.13.178:8182")
    page_path: str = Field(default="", description="页面路径(hash 路由或普通路径)")
    url: str = Field(default="", description="拼接后的完整页面地址")
    description: str = Field(default="", description="页面说明")
    elements: List = Field(default_factory=list, sa_type=JSON, description="抽取的元素列表[{name,type,description,fingerprint,locators,heal_count,...}]")
    steps: List = Field(default_factory=list, sa_type=JSON, description="执行(自愈)步骤(持久化):[{element,action,value}],登录页对象据此完成登录")
    is_login_page: bool = Field(default=False, description="是否作为登录页对象:其他页面对象可关联它复用登录态,并调用其步骤完成登录")
    login_page_object_id: Optional[int] = Field(default=None, index=True, description="关联的登录页对象ID:抽取/验证/执行前确保其登录态可用(没有则先执行它的步骤登录)")
    session_path: str = Field(default="", description="该页面对象的登录态(storage_state)文件路径,按页面对象隔离")
    session_status: str = Field(default="NONE", description="登录态状态: NONE(未建立)/READY(可用)/EXPIRED(已过期)/FAILED(登录失败)")
    session_updated_at: Optional[datetime] = Field(default=None, description="登录态最近写入时间(UTC+8)")
    session_cookie_count: int = Field(default=0, description="登录态内 cookie 条数(状态展示用)")
    session_origin_count: int = Field(default=0, description="登录态内 localStorage origin 数(状态展示用)")
    last_login_at: Optional[datetime] = Field(default=None, description="最近一次成功建立登录态的时间(UTC+8)")
    last_login_error: Optional[str] = Field(default=None, description="最近一次登录失败原因")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")


class UIAutomationRun(BaseModel, table=True):
    """UI 自动化运行记录(元素抽取 / 定位器验证 / PageObject 执行)"""
    page_object_id: Optional[int] = Field(default=None, foreign_key="pageobject.id", index=True, description="关联页面对象ID(抽取时可为空)")
    task_id: Optional[str] = Field(default=None, index=True, description="后台任务ID(用于服务重启后按任务ID回查)")
    user_id: Optional[str] = Field(default=None, index=True, description="发起人ID（Keycloak sub）")
    run_type: str = Field(default="extract", description="运行类型: extract/validate/execute/login")
    target_url: str = Field(default="", description="本次运行的目标页面地址")
    status: str = Field(default="RUNNING", description="状态: RUNNING/PASSED/FAILED/ERROR/STOPPED")
    steps: List = Field(default_factory=list, sa_type=JSON, description="执行步骤输入快照")
    steps_trace: List = Field(default_factory=list, sa_type=JSON, description="每步执行轨迹")
    heal_events: List = Field(default_factory=list, sa_type=JSON, description="定位器自愈事件[{step,element,type,from,to,similarity}]")
    result: dict = Field(default_factory=dict, sa_type=JSON, description="结构化结果(抽取的元素/验证结果等)")
    summary: str = Field(default="", description="结果摘要")
    error: Optional[str] = Field(default=None, description="异常信息(仅 ERROR 时)")
    finished_at: Optional[datetime] = Field(default=None, description="完成时间(UTC+8)")


class RequirementPoint(BaseModel, table=True):
    """需求点数据模型（4阶段pipeline阶段1产物）"""
    session_id: Optional[int] = Field(default=None, foreign_key="session.id", index=True)
    content: str = Field(default="", description="需求点描述")
    module_id: Optional[int] = Field(default=None, foreign_key="module.id", description="关联模块ID（所属功能模块）")
    priority: str = Field(default="medium", description="优先级: high/medium/low")
    order_index: int = Field(default=0, description="排序序号")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")
    api_project_id: Optional[int] = Field(default=None, description="关联的 API 项目ID（接口测试用，从需求点级联到测试点/用例）")
    api_endpoint_ids: Optional[str] = Field(default=None, description="关联的 API Endpoint ID 列表（逗号分隔，接口测试用）")

    # 关系
    session: Optional[Session] = Relationship(back_populates="requirement_points")
    test_points: List["TestPoint"] = Relationship(back_populates="requirement_point")


class TestPoint(BaseModel, table=True):
    """测试点数据模型（4阶段pipeline阶段2产物）"""
    requirement_point_id: Optional[int] = Field(default=None, foreign_key="requirementpoint.id", index=True)
    session_id: Optional[int] = Field(default=None, foreign_key="session.id", index=True)
    content: str = Field(default="", description="测试点描述")
    test_type: str = Field(default="functional", description="测试类别: functional/performance/security/compatibility")
    design_method: str = Field(default="", description="测试设计方法: equivalence_partitioning/boundary_value/decision_table/state_transition")
    priority: str = Field(default="medium", description="优先级: high/medium/low")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")
    api_project_id: Optional[int] = Field(default=None, description="关联的 API 项目ID（接口测试用例重生成时重建上下文用）")
    api_endpoint_ids: Optional[str] = Field(default=None, description="关联的 API Endpoint ID 列表（逗号分隔，接口测试用）")

    # 关系
    requirement_point: Optional[RequirementPoint] = Relationship(back_populates="test_points")
    session: Optional[Session] = Relationship(back_populates="test_points")


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


class SavedRequestFolder(BaseModel, table=True):
    """保存的请求文件夹数据模型"""
    name: str = Field(default="", description="文件夹名称")
    parent_id: Optional[int] = Field(default=None, foreign_key="savedrequestfolder.id", description="父文件夹ID")
    sort_order: Optional[int] = Field(default=None, description="同级排序序号（拖拽排序，越小越靠前）")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")

    children: List["SavedRequestFolder"] = Relationship(
        back_populates="parent",
        sa_relationship_kwargs={"foreign_keys": "[SavedRequestFolder.parent_id]"}
    )
    parent: Optional["SavedRequestFolder"] = Relationship(
        back_populates="children",
        sa_relationship_kwargs={"foreign_keys": "[SavedRequestFolder.parent_id]", "remote_side": "SavedRequestFolder.id"}
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
    folder_id: Optional[int] = Field(default=None, foreign_key="savedrequestfolder.id", index=True, description="所属文件夹ID")
    sort_order: Optional[int] = Field(default=None, description="所属文件夹内的排序序号（拖拽排序，越小越靠前）")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")  # 激活用户关联
    protocol: str = Field(default="http", description="推送协议: http | mqtt | websocket")
    mqtt_config: Optional[dict] = Field(default=None, sa_type=JSON, description="MQTT 配置 {broker,topic,qos,retain,payload,client_id,username,password,tls}")
    ws_config: Optional[dict] = Field(default=None, sa_type=JSON, description="WebSocket 配置 {url,headers,maxMessages,timeoutSeconds,protocols,sendMessage}")


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
    mock_enabled: bool = Field(default=False, description="是否开启项目级 Mock 服务（按 response schema 实时生成响应）")
    mock_prefix: str = Field(default="", description="Mock 地址项目前缀，如 /contract；实际地址为 /api/mock{mock_prefix}{path}")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")


class ApiEndpoint(BaseModel, table=True):
    """接口测试工具中的单个接口定义"""
    project_id: int = Field(foreign_key="apiproject.id", description="接口项目ID")
    name: str = Field(default="", description="接口名称")
    description: Optional[str] = Field(default=None, description="接口业务描述")
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


class PushLog(BaseModel, table=True):
    """IoT 推送请求日志：记录每次 /api/proxy/forward 的原始请求与变量替换后的解析结果"""
    request_method: str = Field(default="", index=True, description="HTTP请求方法")
    source_url: str = Field(default="", description="原始请求URL（替换变量前）")
    final_url: str = Field(default="", description="变量替换后的实际请求URL")
    request_headers: dict = Field(default_factory=dict, sa_type=JSON, description="原始请求头")
    final_headers: dict = Field(default_factory=dict, sa_type=JSON, description="变量替换后的请求头")
    request_params: dict = Field(default_factory=dict, sa_type=JSON, description="原始查询参数")
    final_params: dict = Field(default_factory=dict, sa_type=JSON, description="变量替换后的查询参数")
    request_body: Optional[str] = Field(default=None, description="原始请求体")
    final_body: Optional[str] = Field(default=None, description="变量替换后的请求体")
    response_status: int = Field(default=0, description="响应状态码（0=未收到响应/转发失败）")
    response_headers: dict = Field(default_factory=dict, sa_type=JSON, description="响应头")
    response_body: Optional[str] = Field(default=None, description="响应体")
    mocked: bool = Field(default=False, description="是否命中Mock配置")
    error: Optional[str] = Field(default=None, description="转发失败时的错误信息")
    duration_ms: int = Field(default=0, description="请求耗时（毫秒）")
    environment_id: Optional[int] = Field(default=None, description="使用的环境参数ID")
    user_id: Optional[str] = Field(default=None, index=True, description="触发请求的用户ID（Keycloak sub）")
    # MQTT 发布专用字段（protocol=mqtt 时填充）
    protocol: str = Field(default="http", index=True, description="推送协议: http | mqtt")
    mqtt_broker: Optional[str] = Field(default=None, description="MQTT Broker 地址（变量替换后）")
    mqtt_topic: Optional[str] = Field(default=None, description="MQTT 主题（变量替换后）")
    mqtt_qos: Optional[int] = Field(default=None, description="MQTT QoS 等级")
    mqtt_retain: Optional[bool] = Field(default=None, description="是否保留消息（retain）")
    mqtt_client_id: Optional[str] = Field(default=None, description="MQTT 客户端ID")


class MockProxyRule(BaseModel, table=True):
    """Mock 代理转发规则：命中 host_pattern 且 path_pattern 匹配的流量走 Mock"""
    name: str = Field(default="", description="规则名称")
    host_pattern: str = Field(default="", description="主机匹配模式，如 api.example.com 或 *.example.com")
    path_pattern: str = Field(default="", description="路径匹配模式，如 /api/user/* 或 /users/{id}；空=全部路径")
    enabled: bool = Field(default=True, description="是否启用")
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub）")


class SystemConfig(BaseModel, table=True):
    """系统配置数据模型（全局配置，按 user_id 隔离）"""
    user_id: Optional[str] = Field(default=None, index=True, description="所属用户ID（Keycloak sub），null表示全局配置")
    model_type: str = Field(default="api", description="模型类型: api | ollama")
    api_key: Optional[str] = Field(default=None, description="API Key")
    api_base_url: str = Field(default="https://api.deepseek.com", description="API Base URL")
    api_model: str = Field(default="deepseek-v4-flash", description="API模型名称")
    api_proxy_url: Optional[str] = Field(default=None, description="API代理URL")
    ollama_url: str = Field(default="http://localhost:11434", description="Ollama URL")
    ollama_model: str = Field(default="llama3", description="Ollama模型名称")
    lanhu_cookie: Optional[str] = Field(default=None, description="蓝湖Cookie")
    bug_link_template: Optional[str] = Field(default=None, description="Bug链接模板")


# ===== 资源导航（接口文档 / 快捷工具 等共享书签） =====
class NavCategory(BaseModel, table=True):
    """导航分类（如：接口文档、快捷工具、其他），全局共享，仅配置管理员可增删改。"""
    __tablename__ = "nav_category"
    name: str = Field(default="", max_length=64, description="分类名称")
    icon: Optional[str] = Field(default=None, max_length=32, description="分类图标（emoji 或图标名）")
    sort_order: int = Field(default=0, description="排序权重，越小越靠前")
    links: List["NavLink"] = Relationship(
        back_populates="category",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "NavLink.sort_order",
        },
    )


class NavLink(BaseModel, table=True):
    """导航链接条目，归属某个分类。"""
    __tablename__ = "nav_link"
    category_id: int = Field(default=0, foreign_key="nav_category.id", index=True, description="所属分类ID")
    title: str = Field(default="", max_length=128, description="链接标题")
    url: str = Field(default="", max_length=1024, description="链接地址")
    description: Optional[str] = Field(default=None, max_length=512, description="链接描述")
    icon: Optional[str] = Field(default=None, max_length=32, description="链接图标（emoji 或图标名）")
    open_type: str = Field(default="blank", max_length=16, description="打开方式：blank=新标签页，self=当前页")
    sort_order: int = Field(default=0, description="排序权重，越小越靠前")
    category: Optional["NavCategory"] = Relationship(back_populates="links")
