from typing import List, Optional

from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlmodel import select, desc

from app.deps import SessionDep, CurrentUser
from db.models import HistoryPrompt
from utils.base_response import Response
from utils.history_prompt_cleaner import clean_history_prompt_content

router = APIRouter(prefix="/history_prompt", tags=["history_prompt"])


# 请求模型
class CreateHistoryPromptRequest(BaseModel):
    content: str
    module_id: Optional[int] = None
    session_id: Optional[int] = None


# 获取模块下的历史提示词列表
@router.get("/{module_id}", response_model=Response[List[HistoryPrompt]])
def get_history_prompts(
    session: SessionDep,
    user: CurrentUser,
    module_id: int
):
    """获取指定模块下的历史提示词列表"""
    prompts = session.exec(
        select(HistoryPrompt)
        .where(HistoryPrompt.module_id == module_id)
        .order_by(desc(HistoryPrompt.created_at))
    ).all()
    for prompt in prompts:
        prompt.content = clean_history_prompt_content(prompt.content)
    return Response(data=prompts)


# 获取会话下的所有历史提示词（不分模块）
@router.get("/session/{session_id}", response_model=Response[List[HistoryPrompt]])
def get_session_history_prompts(
    session: SessionDep,
    user: CurrentUser,
    session_id: int
):
    """获取指定会话下的所有历史提示词"""
    prompts = session.exec(
        select(HistoryPrompt)
        .where(HistoryPrompt.session_id == session_id)
        .order_by(desc(HistoryPrompt.created_at))
    ).all()
    for prompt in prompts:
        prompt.content = clean_history_prompt_content(prompt.content)
    return Response(data=prompts)


# 创建历史提示词
@router.post("", response_model=Response[HistoryPrompt])
def create_history_prompt(
    session: SessionDep,
    user: CurrentUser,
    request: CreateHistoryPromptRequest
):
    """创建新的历史提示词，已存在完全一致的提示词时跳过"""
    cleaned_content = clean_history_prompt_content(request.content)
    if not cleaned_content:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="历史需求描述内容为空")

    filters = [
        HistoryPrompt.content == cleaned_content,
        HistoryPrompt.user_id == user.user_id,
    ]
    if request.module_id is None:
        filters.append(HistoryPrompt.module_id.is_(None))
    else:
        filters.append(HistoryPrompt.module_id == request.module_id)
    if request.session_id is None:
        filters.append(HistoryPrompt.session_id.is_(None))
    else:
        filters.append(HistoryPrompt.session_id == request.session_id)

    existing = session.exec(select(HistoryPrompt).where(*filters)).first()
    if existing:
        return Response(data=existing, message="历史提示词已存在，跳过创建")

    prompt = HistoryPrompt(
        content=cleaned_content,
        module_id=request.module_id,
        session_id=request.session_id,
        user_id=user.user_id
    )
    session.add(prompt)
    session.commit()
    session.refresh(prompt)
    return Response(data=prompt, message="历史提示词创建成功")


# 删除历史提示词
@router.delete("/{prompt_id}", response_model=Response)
def delete_history_prompt(
    session: SessionDep,
    user: CurrentUser,
    prompt_id: int
):
    """删除历史提示词"""
    prompt = session.get(HistoryPrompt, prompt_id)
    if not prompt:
        return Response(code=status.HTTP_404_NOT_FOUND, message="历史提示词不存在")

    session.delete(prompt)
    session.commit()
    return Response(message="历史提示词删除成功")
