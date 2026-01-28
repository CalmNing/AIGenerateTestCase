import base64
from typing import List, Annotated, Optional

from fastapi import APIRouter, Query, status, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlmodel import select, desc, func

from app.deps import SessionDep
from db.models import TestCase, StatusValue
from utils.base_response import Response

router = APIRouter(prefix="/testcases", tags=["testcases"])


class TestCasePage(BaseModel):
    items: List[TestCase]
    totalNumber: int = Field(int, description="总条数")
    passed: int = Field(int, description="已通过的用例数")
    failed: int = Field(int, description="未通过的用例数")
    not_run: int = Field(int, description="未执行的用例数")
    totalBugs: int = Field(int, description="Bug数")
    model_config = {
        "arbitrary_types_allowed": True,
    }


# 测试用例管理API
@router.get("/{session_id}/testcases", response_model=Response[TestCasePage])
def get_testcases(
        session_id: int,
        session: SessionDep,
        module_id: int = None,
        offset: int = 0,
        limit: Annotated[int, Query(le=100)] = 100,
        case_name: str = None,
        status: str = None,
        bug_id: str = None,
        exist_bug: bool = False
):
    """获取会话的测试用例"""
    query = select(TestCase).where(TestCase.session_id == session_id).order_by(TestCase.case_level,
                                                                               desc(TestCase.created_at))
    if module_id:
        query = query.where(TestCase.module_id == module_id)

    if case_name:
        query = query.where(TestCase.case_name.contains(case_name))
    if status:
        query = query.where(TestCase.status.contains(status))
    if bug_id:
        query = query.where(TestCase.bug_id.contains(bug_id))
    if exist_bug:
        query = query.where(TestCase.bug_id != None)

    testcases_db = session.exec(query.offset(offset).limit(limit)).all()
    # 确保 session_id 被转换为 int 类型
    for testcase in testcases_db:
        testcase.session_id = int(testcase.session_id) if testcase.session_id else None

    # 构建基础查询条件
    count_query_base = [TestCase.session_id == session_id]
    if module_id is not None:
        count_query_base.append(TestCase.module_id == module_id)
        # 统计总数
    totalNumber = session.scalar(select(func.count()).where(*count_query_base))

    # 统计已通过的用例数
    passed_query = [TestCase.session_id == session_id, TestCase.status == StatusValue.PASSED]
    if module_id is not None:
        passed_query.append(TestCase.module_id == module_id)
    passed = session.scalar(select(func.count()).where(*passed_query))

    # 统计未通过的用例数
    failed_query = [TestCase.session_id == session_id, TestCase.status == StatusValue.FAILED]
    if module_id is not None:
        failed_query.append(TestCase.module_id == module_id)
    failed = session.scalar(select(func.count()).where(*failed_query))

    # 统计未执行的用例数
    not_run_query = [TestCase.session_id == session_id, TestCase.status == StatusValue.NOT_RUN]
    if module_id is not None:
        not_run_query.append(TestCase.module_id == module_id)
    not_run = session.scalar(select(func.count()).where(*not_run_query))

    # 统计 Bug 数
    total_bugs_query = [TestCase.session_id == session_id, TestCase.bug_id != None]
    if module_id is not None:
        total_bugs_query.append(TestCase.module_id == module_id)
    totalBugs = session.scalar(select(func.count()).where(*total_bugs_query))


    testcases = TestCasePage(
        items=testcases_db,
        totalNumber=totalNumber,
        passed=passed,
        failed=failed,
        not_run=not_run,
        totalBugs=totalBugs
    )
    return Response(data=testcases)


# 请求模型
class GenerateTestcasesRequest(BaseModel):
    model_type: str = "api"  # "api" 或 "ollama"
    api_key: str = ""  # API 密钥（当 model_type 为 "api" 时使用）
    ollama_url: str = ""  # Ollama URL（当 model_type 为 "ollama" 时使用）
    ollama_model: str = ""  # Ollama 模型名称（当 model_type 为 "ollama" 时使用")
    requirement: Optional[str]


@router.post("/{session_id}/testcases")
async def generate_testcases(
        session: SessionDep,
        session_id: int,
        # 调整参数顺序，与前端发送的FormData顺序一致
        requirement: Optional[str] = Form(None),
        model_type: str = Form("api"),
        api_key: str = Form(""),
        ollama_url: str = Form(""),
        ollama_model: str = Form(""),
        file: Optional[UploadFile] = File(None),
        module_id: Optional[int] = Form(""),

):
    """生成测试用例"""
    from utils.model_utils import generate_testcases

    # 添加详细的调试日志
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"接收到生成测试用例请求")
    logger.info(f"  session_id: {session_id}")
    logger.info(f"  module_id: {module_id}")
    logger.info(f"  requirement: {'有值' if requirement else 'None'} (长度: {len(requirement) if requirement else 0})")
    logger.info(f"  model_type: {model_type}")
    logger.info(f"  api_key: {'有值' if api_key else 'None'}")
    logger.info(f"  file: {'有文件' if file else 'None'}")
    logger.info(f"  file.filename: {file.filename if file else 'None'}")
    logger.info(f"  file.content_type: {file.content_type if file else 'None'}")

    # 处理图像数据
    image_data = None
    if file:
        logger.info(f"  开始读取文件内容")
        # 验证文件类型
        if not file.content_type.startswith('image/'):
            return Response(code=status.HTTP_400_BAD_REQUEST, data="请上传图片文件")
        # 读取上传的图像数据 - 使用 await 处理协程
        image_bytes = await file.read()
        logger.info(f"  文件读取完成，大小: {len(image_bytes)} 字节")
        image_data = base64.b64encode(image_bytes).decode('utf-8')

    # 基本输入校验 - 修复
    # 检查是否至少有一个有效输入
    has_valid_input = False

    # 检查是否有文件 - 修复：检查file是否为None
    if file is not None:
        has_valid_input = True
        logger.info(f"  文件上传成功: {file.filename}")

    # 检查是否有有效的requirement
    if requirement and requirement.strip():
        has_valid_input = True
        logger.info(f"  有有效的requirement")

    logger.info(f"  验证结果: has_valid_input={has_valid_input}")

    # 如果没有有效输入，返回错误
    if not has_valid_input:
        logger.error(f"  验证失败: 请上传图片文件或输入requirement")
        return Response(code=status.HTTP_400_BAD_REQUEST, data="请上传图片文件或输入requirement")

    # 模型参数校验
    if model_type == "api" and not api_key:
        return Response(code=status.HTTP_400_BAD_REQUEST, data="api_key 未提供!")
    if model_type == "ollama" and (not ollama_url or not ollama_model):
        return Response(code=status.HTTP_400_BAD_REQUEST,
                        data="Ollama 配置不完整（ollama_url/ollama_model）")

    testcases = generate_testcases(
        requirement=requirement,
        session_id=session_id,
        module_id=module_id,
        image_data=image_data,
        is_base64=True,
        model_type=model_type,
        api_key=api_key,
        ollama_url=ollama_url,
        ollama_model=ollama_model,

    )
    session.add_all(testcases)
    session.commit()
    return Response(message="生成测试用例成功")


@router.put("/{session_id}/testcases/{testcase_id}", response_model=Response)
def update_testcase(
        session: SessionDep,
        session_id: int,
        testcase_id: int,
        testcase: TestCase
):
    """更新测试用例"""

    testcase_db = session.get(TestCase, testcase_id)
    if not testcase_db:
        return Response(code=status.HTTP_404_NOT_FOUND, message="测试用例不存在")

    testcase_data = testcase.model_dump(exclude_unset=True)
    testcase_data.pop("created_at")
    testcase_data.pop("updated_at")
    testcase_db.sqlmodel_update(testcase_data)
    session.add(testcase_db)
    session.commit()
    session.refresh(testcase_db)
    return Response(message="更新测试用例成功")


@router.delete("/{session_id}/testcases", response_model=Response[str])
def delete_testcase(
        session: SessionDep,
        session_id: int,
        testcases: List[int]
):
    testcase_status = session.exec(
        select(TestCase.status)
        .where(
            TestCase.id.in_(testcases),
            TestCase.status != StatusValue.NOT_RUN
        )
    ).first()
    if testcase_status:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="存在已执行的用例，删除失败！")
    """删除测试用例"""

    # 批量删除测试用例
    statement = delete(TestCase).where(
        TestCase.id.in_(testcases),
        TestCase.session_id == session_id
    )
    session.exec(statement)
    session.commit()
    return Response(message="删除测试用例成功")
