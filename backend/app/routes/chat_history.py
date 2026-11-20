"""AI 对话会话持久化 API。

存储/列出/删除 AI 对话会话，支持"选择上次的对话继续"。会话消息整体以 JSON
快照存于 ChatConversation.messages_json；读写时解析/序列化，失败按空处理。
挂载于 /chat-history（backend/app/main.py），鉴权与 /chat 同源。
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select, desc

from app.deps import SessionDep, CurrentUser
from db.models import ChatConversation
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat-history", tags=["chat-history"])


class ConversationSaveRequest(BaseModel):
    title: str = Field(default="", max_length=128, description="会话标题，留空自动取首条用户消息")
    mode: str = Field(default="general", max_length=32, description="对话模式")
    scope_key: str = Field(default="", max_length=64, description="业务上下文标识")
    thread_id: str = Field(default="", max_length=128, description="服务端记忆 thread id")
    messages: list = Field(default_factory=list, description="[{role, content, reasoning?, toolEvents?}]")


class ConversationUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=128, description="新标题")
    messages: Optional[list] = Field(default=None, description="覆盖消息列表")
    thread_id: Optional[str] = Field(default=None, max_length=128, description="更新 thread id")


def _serialize_messages(messages: list) -> str:
    """消息列表 -> JSON 文本；序列化失败按空列表。"""
    try:
        return json.dumps(messages, ensure_ascii=False)
    except Exception as e:
        logger.warning("消息序列化失败，按空处理: %s", e)
        return "[]"


def _parse_messages(raw: str) -> list:
    """JSON 文本 -> 消息列表；解析失败按空列表。"""
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("消息解析失败，按空处理: %s", e)
        return []


def _derive_title(messages: list, fallback: str) -> str:
    """标题：显式传入的优先；未传时取首条用户消息截断。"""
    if (fallback or "").strip():
        return fallback.strip()[:60]
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = str(msg.get("content") or "").strip().replace("\n", " ")
            if text:
                return text[:60]
    return "AI 对话"


def _preview(messages: list) -> str:
    """最后一条消息摘要。"""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("content"):
            text = str(msg["content"]).strip().replace("\n", " ")
            if text:
                return text[:100]
    return ""


def _payload(conv: ChatConversation, include_messages: bool = False) -> dict:
    data = {
        "id": conv.id,
        "title": conv.title,
        "mode": conv.mode,
        "scope_key": conv.scope_key,
        "thread_id": conv.thread_id,
        "last_message_preview": conv.last_message_preview,
        "message_count": len(_parse_messages(conv.messages_json)),
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }
    if include_messages:
        data["messages"] = _parse_messages(conv.messages_json)
    return data


@router.get("", response_model=Response)
def list_conversations(
    db: SessionDep,
    current_user: CurrentUser = None,
    mode: Optional[str] = None,
    scope_key: Optional[str] = None,
    limit: int = 50,
):
    """会话列表（按更新时间倒序），可按 mode / scope_key 过滤。"""
    stmt = select(ChatConversation)
    if mode:
        stmt = stmt.where(ChatConversation.mode == mode)
    if scope_key:
        stmt = stmt.where(ChatConversation.scope_key == scope_key)
    rows = db.exec(stmt.order_by(desc(ChatConversation.updated_at)).limit(max(1, min(limit, 200)))).all()
    return Response(data=[_payload(c) for c in rows])


@router.post("", response_model=Response)
def save_conversation(req: ConversationSaveRequest, db: SessionDep, current_user: CurrentUser = None):
    """保存（创建）一个对话会话。"""
    conv = ChatConversation(
        title=_derive_title(req.messages, req.title),
        mode=req.mode or "general",
        scope_key=req.scope_key or "",
        thread_id=req.thread_id or "",
        last_message_preview=_preview(req.messages),
        messages_json=_serialize_messages(req.messages),
        user_id=getattr(current_user, "user_id", None),
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return Response(data=_payload(conv))


@router.put("/{conversation_id}", response_model=Response)
def update_conversation(
    conversation_id: int,
    req: ConversationUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser = None,
):
    """更新会话：标题 / 覆盖消息列表（续聊时前端整体回传）/ thread id。"""
    conv = db.get(ChatConversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    if req.title is not None and req.title.strip():
        conv.title = req.title.strip()[:128]
    if req.messages is not None:
        conv.messages_json = _serialize_messages(req.messages)
        conv.last_message_preview = _preview(req.messages)
    if req.thread_id is not None:
        conv.thread_id = req.thread_id
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return Response(data=_payload(conv))


@router.get("/{conversation_id}", response_model=Response)
def get_conversation(conversation_id: int, db: SessionDep, current_user: CurrentUser = None):
    """会话详情（含完整消息列表）。"""
    conv = db.get(ChatConversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return Response(data=_payload(conv, include_messages=True))


@router.delete("/{conversation_id}", response_model=Response)
def delete_conversation(conversation_id: int, db: SessionDep, current_user: CurrentUser = None):
    """删除会话。"""
    conv = db.get(ChatConversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.delete(conv)
    db.commit()
    return Response(data={"deleted": conversation_id})
