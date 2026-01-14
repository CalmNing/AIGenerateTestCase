from typing import List, Annotated, Optional

import base64

from fastapi import APIRouter, Query, HTTPException, status, File, Form, Depends, UploadFile
from pydantic import BaseModel, Field
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
    model_config = {
        "arbitrary_types_allowed": True,
    }


# 测试用例管理API
@router.get("/{session_id}/testcases", response_model=Response[TestCasePage])
def get_testcases(
        session_id: int,
        session: SessionDep,
        offset: int = 0,
        limit: Annotated[int, Query(le=100)] = 100,
        case_name: str = None,
        status: str = None
):
    """获取会话的测试用例"""
    query = select(TestCase).where(TestCase.session_id == session_id).order_by(TestCase.case_level,
                                                                               desc(TestCase.created_at))

    if case_name:
        query = query.where(TestCase.case_name.contains(case_name))

    if status:
        query = query.where(TestCase.status.contains(status))

    testcases_db = session.exec(query.offset(offset).limit(limit)).all()
    # 确保 session_id 被转换为 int 类型
    for testcase in testcases_db:
        testcase.session_id = int(testcase.session_id) if testcase.session_id else None

    totalNumber = session.scalar(select(func.count()).where(TestCase.session_id == session_id))
    passed = session.scalar(select(func.count()).where(
        TestCase.session_id == session_id,
        TestCase.status == StatusValue.PASSED
    ))

    failed = session.scalar(select(func.count()).where(
        TestCase.session_id == session_id,
        TestCase.status == StatusValue.FAILED
    ))
    not_run = session.scalar(select(func.count()).where(
        TestCase.session_id == session_id,
        TestCase.status == StatusValue.NOT_RUN
    ))
    testcases = TestCasePage(
        items=testcases_db,
        totalNumber=totalNumber,
        passed=passed,
        failed=failed,
        not_run=not_run
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
):
    """生成测试用例"""
    from utils.model_utils import generate_testcases
    
    # 添加详细的调试日志
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"接收到生成测试用例请求")
    logger.info(f"  session_id: {session_id}")
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
        image_data=image_data,
        is_base64=True,
        model_type=model_type,
        api_key=api_key,
        ollama_url=ollama_url,
        ollama_model=ollama_model,

    )
    session.add_all(testcases)
    session.commit()
    return Response(data="生成测试用例成功")


@router.put("/{session_id}/testcases/{testcase_id}", response_model=Response[str])
def update_testcase(
        session: SessionDep,
        session_id: int,
        testcase_id: int,
        testcase: TestCase
):
    """更新测试用例"""

    testcase_db = session.get(TestCase, testcase_id)
    if not testcase_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="测试用例不存在")

    testcase_data = testcase.model_dump(exclude_unset=True)
    testcase_data.pop("created_at")
    testcase_data.pop("updated_at")
    testcase_db.sqlmodel_update(testcase_data)
    session.add(testcase_db)
    session.commit()
    session.refresh(testcase_db)
    return Response(data="更新测试用例成功")


@router.delete("/{session_id}/testcases/{testcase_id}", response_model=Response[str])
def delete_testcase(
        session: SessionDep,
        session_id: int,
        testcase_id: int
):
    testcase_status = session.exec(
        select(TestCase.status)
        .where(
            TestCase.id == testcase_id,
            TestCase.status != StatusValue.NOT_RUN
        )
    ).first()
    if testcase_status:
        return Response(code=status.HTTP_400_BAD_REQUEST, data="用例已执行，删除失败！")
    """删除测试用例"""
    testcase = session.exec(
        select(TestCase)
        .where(
            TestCase.id == testcase_id,
            TestCase.session_id == session_id
        )
    ).first()
    if not testcase:
        return Response(code=status.HTTP_404_NOT_FOUND, data="测试用例不存在")
    session.delete(testcase)
    session.commit()
    return Response(data="删除测试用例成功")
