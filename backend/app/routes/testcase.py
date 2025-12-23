from typing import List, Annotated

from fastapi import APIRouter, Query, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select

from app.deps import SessionDep
from db.models import TestCase
from utils.base_response import Response

router = APIRouter(prefix="/testcases", tags=["testcases"])


class TestCasePage(BaseModel):
    items: List[TestCase]
    totalNumber: int = Field(int, description="总条数")
    completed: int = Field(int, description="已完成的用例数")
    pending: int = Field(int, description="待完成的用例数")
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
    query = select(TestCase).where(TestCase.session_id == session_id)

    if case_name:
        query = query.where(TestCase.case_name.contains(case_name))

    if status:
        query = query.where(TestCase.status.contains(status))

    testcases_db = session.exec(query.offset(offset).limit(limit)).all()

    totalNumber = session.scalar(select(func.count()).where(TestCase.session_id == session_id))
    completed = session.scalar(select(func.count()).where(
        TestCase.session_id == session_id,
        TestCase.status == "completed"
    ))

    pending = session.scalar(select(func.count()).where(
        TestCase.session_id == session_id,
        TestCase.status == "pending"
    ))
    testcases = TestCasePage(
        items=testcases_db,
        totalNumber=totalNumber,
        completed=completed,
        pending=pending
    )
    return Response(data=testcases)


# 请求模型
class GenerateTestcasesRequest(BaseModel):
    requirement: str
    model_type: str = "api"  # "api" 或 "ollama"
    api_key: str = ""  # API 密钥（当 model_type 为 "api" 时使用）
    ollama_url: str = ""  # Ollama URL（当 model_type 为 "ollama" 时使用）
    ollama_model: str = ""  # Ollama 模型名称（当 model_type 为 "ollama" 时使用）


@router.post("/{session_id}/testcases", response_model=Response[str])
def generate_testcases(
        session: SessionDep,
        session_id: int,
        request: GenerateTestcasesRequest
):
    """生成测试用例"""
    from utils.model_utils import generate_testcases

    # 基本输入校验
    if not request.requirement or not request.requirement.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="requirement 不能为空")

    # 回退配置：如果请求未提供 api_key/ollama 配置，则从 config_manager 读取
    api_key = request.api_key
    ollama_url = request.ollama_url
    ollama_model = request.ollama_model

    # 模型参数校验
    if request.model_type == "api" and not api_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="api_key 未提供!")
    if request.model_type == "ollama" and (not ollama_url or not ollama_model):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Ollama 配置不完整（ollama_url/ollama_model）")

    testcases = generate_testcases(
        request.requirement,
        session_id=session_id,
        model_type=request.model_type,
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
    """删除测试用例"""
    testcase = session.exec(
        select(TestCase)
        .where(
            TestCase.id == testcase_id,
            TestCase.session_id == session_id
        )
    ).first()
    if not testcase:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="测试用例不存在")
    session.delete(testcase)
    session.commit()
    return Response(data="删除测试用例成功")
