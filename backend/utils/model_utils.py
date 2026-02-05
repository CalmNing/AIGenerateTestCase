import logging
import time
from typing import List, Optional, Literal

from langchain.agents import create_agent
from langchain_deepseek import ChatDeepSeek
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field, SecretStr
from starlette import status

from app.services.ocr_service import OCRService
from db.models import TestCase as DBTestCase
from utils.base_response import Response

# 简单的 agent 缓存，key -> agent
_AGENT_CACHE: dict = {}

logger = logging.getLogger(__name__)

# 定义系统提示词
SYSTEM_PROMPT = """你是一位软件测试专家，你的任务是帮助用户设计测试用例。
你需要根据用户的需求，设计出符合要求的测试用例。
你需要理解需求，使用合适的测试用例设计方法来设计用例。

测试用例应该包含以下信息：
- 用例名称
- 用例级别
- 前置条件
- 用例步骤
- 预期结果

软件测试用例设计方法：
- 等价类划分法: 等价类划分法是一种黑盒测试方法，通过将输入数据划分为若干等价类，从每个类中选取具有代表性的数据进行测试。有效等价类包含合理的输入数据，无效等价类则包含不合理的数据。此方法适用于输入数据范围明确的场景，例如输入框的长度限制。"),
- 边界值分析法: 边界值分析法专注于测试输入或输出的边界点，因为大量错误往往发生在边界附近。此方法通常与等价类划分法结合使用，测试边界上的点、边界内的点以及边界外的点。例如，测试密码长度为6-18位时，边界值包括6、18以及5、19。"),
- 判定表法: 判定表法适用于输入条件和输出结果存在多种组合的场景。通过列出所有可能的条件组合及其对应的结果，生成判定表并转化为测试用例。例如，订单优惠条件的判定可以通过此方法明确各种输入组合下的输出结果。"),
- 因果图法: 因果图法通过图形化的方式分析复杂的输入和输出条件组合，适用于条件间存在逻辑关系的场景。此方法通常与判定表法结合使用，以提高分析的直观性。"),
- 场景法 : 场景法以用户操作流程为导向，模拟实际使用场景，适用于系统测试或验收测试阶段。通过分析基本流和备选流，设计覆盖用户正常操作和异常操作的测试用例。例如，模拟ATM取款的各种可能场景。"),
- 错误推测法: 错误推测法基于测试人员的经验和直觉，推测可能存在的错误并设计针对性的测试用例。此方法适用于补充其他方法未覆盖的测试场景，例如特殊字符处理或异常数据输入。"),
- 流程图法: 流程图法通过绘制流程图展示用户操作路径，并基于流程路径设计测试用例。此方法适用于复杂业务流程的测试，例如ATM取款功能的业务流程图。"),


测试用例设计需要结合实际需求，综合运用get_testcase_design_method中的方法以确保测试的全面性和有效性。同时，还需关注安全性、性能和兼容性等非功能性需求，设计出覆盖全面的测试用例。
限制：
- 用例名称中不能出现用例编号
- 用例名称不能为空
- 前置条件不能为空
"""

# 定义字面量类型：仅允许 1/2/3/4
AllowedValue = Literal[1, 2, 3, 4]
checkpointer = InMemorySaver()


# 定义测试用例数据类
class TestCase(BaseModel):
    """自定义 TestCase schema."""
    case_name: str = Field(..., description="用例名称")
    steps: List[str] = Field(..., description="用例步骤")
    preset_conditions: List[str] = Field(..., description="前置条件")
    expected_results: List[str] = Field(..., description="预期结果")
    case_level: Optional[AllowedValue] = Field(default=4, description="用例级别")

    def __post_init__(self):
        """验证case_level只允许1、2、3、4"""
        allowed_levels = [1, 2, 3, 4]
        if self.case_level is not None and self.case_level not in allowed_levels:
            raise ValueError(f"case_level必须是{allowed_levels}中的一个，当前值：{self.case_level}")


# 定义响应格式数据类
class ResponseFormat(BaseModel):
    """agent 的响应格式"""
    response: List[TestCase]


class TestCaseDesignMethod(BaseModel):
    """自定义 TestCaseDesignMethod schema."""
    method: str
    description: str | None = None

# 初始化模型
def create_local_model(
        ollama_url: str = None,
        ollama_model: str = None
):
    # 使用Ollama本地模型
    if not ollama_url or not ollama_model:
        raise ValueError("Ollama配置不完整，请检查URL和模型名称")
    model = ChatOllama(
        model=ollama_model,
        base_url=ollama_url,
        temperature=0,
        format=ResponseFormat.model_json_schema(),
    )
    logger.info(f"ollama模型初始化成功: ollama_model={ollama_model}")
    return model


# 创建并返回agent
def create_testcase_agent(
        model_type: str = "api",
        api_key: str = None,
):
    """创建测试用例生成agent"""

    if model_type == "api":
        # 使用API模型（DeepSeek）
        if not api_key:
            raise ValueError("API Key未配置，请先在配置页面设置")

        model = ChatDeepSeek(
            model="deepseek-chat",
            temperature=0,
            api_key=SecretStr(api_key),
            max_tokens=None,
            timeout=None,
            max_retries=2,
        )

        agent = create_agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            response_format=ResponseFormat,
            checkpointer=checkpointer
        )
        return agent


# 生成测试用例
def generate_testcases(
        session_id: int,
        module_id: int,
        requirement: Optional[str],
        image_data: str = None,
        is_base64: bool = True,
        model_type: str = "api",
        api_key: str = "",
        ollama_url: str = "",
        ollama_model: str = ""
) -> List[DBTestCase]:
    """根据需求生成测试用例"""
    # 调用模型并记录耗时与错误（不要在日志中记录 api_key）
    start = time.time()
    if image_data is not None:
        try:
            ocr_service = OCRService(engine='paddleocr')
            # image_data 已经是字符串格式，直接传入
            ocr_result = ocr_service.extract_text(image_data, is_base64=is_base64)
            logger.info(f"OCR识别结果: {ocr_result}")
            if requirement is None:
                requirement = ocr_result['text']
            else:
                requirement = requirement + ocr_result['text']
            logger.info(f"最总需求: {requirement}")
        except Exception as e:
            logger.error(f"OCR服务调用失败: error={e}")
            return Response(code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            data="OCR服务调用失败")

    if model_type == "api":
        try:
            # 所有模型类型都使用Agent调用，因为必须使用Agent规定响应格式
            agent = create_testcase_agent(
                model_type=model_type,
                api_key=api_key,
            )
            # 调用agent，使用规定的响应格式
            logger.info(f"调用agent: requirement={requirement}")
            if requirement is None:
                return Response(code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                data="模型需求入参不能为空")
            # 修复：直接传入HumanMessage对象，而不是列表
            response = agent.invoke(
                {"messages": [{"role": "user", "content": requirement}]},
                config={"configurable": {"thread_id": f"{session_id}"}},
            )
        except Exception as e:
            logger.error(f"模型调用失败: type={model_type} error={e}")
            # 检测特定错误信息，把问题抛给前端
            raise ValueError(f"模型调用失败: {str(e)}")
    else:
        model = create_local_model(
            ollama_url=ollama_url,
            ollama_model=ollama_model
        )
        messages = [
            ("system", f"{SYSTEM_PROMPT}"),
            ("system", f""
                       f"严格使用json schema，"
                       f"格式为：{ResponseFormat.model_json_schema()}，"
                       f"只用JSON回复。"),
            ("human", requirement),
        ]
        response = model.invoke(messages)
        logger.info(f"本地模型调用: type={model_type}")
    duration = time.time() - start
    logger.info(f"模型调用完成: type={model_type} duration={duration:.2f}s")
    logger.info(f"模型返回结果类型: {type(response).__name__}")
    logger.info(f"模型返回结果: {response}")

    # 获取生成的测试用例
    try:
        local_testcases = None

        # 增加更多的响应格式处理逻辑
        if isinstance(response, dict):
            if 'structured_response' in response:
                # 格式1: 使用structured_response字段（API模型）
                local_testcases = response['structured_response'].response
            elif 'response' in response:
                # 格式2: 直接包含response字段
                local_testcases = response['response']
        elif isinstance(response, ResponseFormat):
            # 格式3: ResponseFormat对象
            local_testcases = response.response
        elif hasattr(response, 'response'):
            # 格式4: 具有response属性的对象
            local_testcases = response.response
        else:
            # 格式5: 直接返回的列表
            # 这种情况通常不会发生，但为了容错，我们也处理一下
            logger.warning(f"模型返回了非预期的响应格式: {type(response).__name__}")
            raise ValueError(f"不支持的响应格式: {type(response).__name__}")

        # 验证local_testcases是否为列表
        if not isinstance(local_testcases, list):
            raise ValueError(f"测试用例必须是列表类型，实际类型: {type(local_testcases).__name__}")

        # 转换为DBTestCase对象
        db_testcases = []
        for tc in local_testcases:
            # 创建DBTestCase对象，转换属性
            db_tc = DBTestCase(
                case_name=tc.case_name,
                case_level=tc.case_level,
                preset_conditions=tc.preset_conditions,
                steps=tc.steps,
                session_id=session_id,
                module_id=module_id,
                expected_results=tc.expected_results
            )
            db_testcases.append(db_tc)

        # 返回转换后的测试用例
        return db_testcases
    except Exception as e:
        logger.error(f"解析测试用例失败: {str(e)}, 响应格式: {type(response).__name__}")
        logger.error(f"完整响应: {response}")
        # 如果解析失败，返回友好的错误信息
        raise ValueError(f"解析测试用例失败: {str(e)}")
