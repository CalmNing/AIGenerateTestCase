"""AI 对话会话持久化测试（chat_history）。

覆盖：保存/列表/详情/更新/删除全链路 + 消息 JSON 容错。
全部用例基于 in-memory SQLite，不触网。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pytest
from fastapi import HTTPException

from app.routes import chat_history as ch
from db.models import ChatConversation


SAMPLE_MESSAGES = [
    {"role": "user", "content": "帮我分析登录需求"},
    {"role": "assistant", "content": "好的，先从输入字段开始", "reasoning": "思考内容"},
]


def _save(db, messages=None, **kwargs):
    req = ch.ConversationSaveRequest(
        messages=messages if messages is not None else SAMPLE_MESSAGES,
        **kwargs,
    )
    return ch.save_conversation(req, db, None)


def test_save_and_payload_shape(db_session):
    res = _save(db_session, title="自定义标题", mode="requirement_clarify", scope_key="req-clarify-1", thread_id="chat-x-1")
    assert res.code == 200
    data = res.data
    assert data["title"] == "自定义标题"
    assert data["mode"] == "requirement_clarify"
    assert data["scope_key"] == "req-clarify-1"
    assert data["thread_id"] == "chat-x-1"
    assert data["message_count"] == 2
    assert "好的，先从输入字段开始" in data["last_message_preview"]


def test_save_title_derived_from_first_user_message(db_session):
    res = _save(db_session, title="")
    assert res.data["title"].startswith("帮我分析登录需求")


def test_list_filters_by_mode_and_scope(db_session):
    _save(db_session, mode="requirement_clarify", scope_key="req-clarify-1", title="A")
    _save(db_session, mode="testcase_optimize", scope_key="case-opt-3", title="B")
    all_rows = ch.list_conversations(db_session, None).data
    assert len(all_rows) == 2
    only_req = ch.list_conversations(db_session, None, mode="requirement_clarify").data
    assert len(only_req) == 1 and only_req[0]["title"] == "A"
    only_scope = ch.list_conversations(db_session, None, scope_key="case-opt-3").data
    assert len(only_scope) == 1 and only_scope[0]["title"] == "B"


def test_get_includes_messages(db_session):
    saved = _save(db_session).data
    detail = ch.get_conversation(saved["id"], db_session, None).data
    assert detail["messages"] == SAMPLE_MESSAGES


def test_update_messages_and_title(db_session):
    saved = _save(db_session).data
    new_messages = SAMPLE_MESSAGES + [{"role": "user", "content": "继续第二问"}]
    res = ch.update_conversation(saved["id"], ch.ConversationUpdateRequest(messages=new_messages), db_session, None)
    assert res.data["message_count"] == 3
    assert "继续第二问" in res.data["last_message_preview"]
    res2 = ch.update_conversation(saved["id"], ch.ConversationUpdateRequest(title="新名字"), db_session, None)
    assert res2.data["title"] == "新名字"


def test_delete_conversation(db_session):
    saved = _save(db_session).data
    res = ch.delete_conversation(saved["id"], db_session, None)
    assert res.data == {"deleted": saved["id"]}
    with pytest.raises(HTTPException):
        ch.get_conversation(saved["id"], db_session, None)


def test_missing_conversation_404(db_session):
    with pytest.raises(HTTPException):
        ch.get_conversation(999999, db_session, None)
    with pytest.raises(HTTPException):
        ch.delete_conversation(999999, db_session, None)


def test_corrupt_messages_json_tolerated(db_session):
    conv = ChatConversation(title="坏数据", messages_json="{not-json")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    detail = ch.get_conversation(conv.id, db_session, None).data
    assert detail["messages"] == []
    assert detail["message_count"] == 0


def test_serialize_roundtrip_keeps_reasoning_and_tools(db_session):
    messages = [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "答案", "reasoning": "想了想", "toolEvents": ["wiki_v2"]},
    ]
    res = _save(db_session, messages=messages).data
    detail = ch.get_conversation(res["id"], db_session, None).data
    assert detail["messages"][1]["reasoning"] == "想了想"
    assert detail["messages"][1]["toolEvents"] == ["wiki_v2"]