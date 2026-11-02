"""P2 集成测试：检索接入历史上下文 + 已覆盖清单。

验证：
- 历史上下文在有检索命中时按相关性召回（而非时间序）；
- 检索不可用时回退时间序（行为与改造前一致）；
- 单条历史内容被字符级截断；
- 已覆盖清单能列出本模块已有用例（标题 + 验证要点）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from db.models import HistoryPrompt, Module, Session as DbSession, TestCase
from utils import helpers


@pytest.fixture()
def seeded(db_session):
    """造一个会话 + 模块，并写入若干历史需求与已有用例。"""
    sess = DbSession(name="p2")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    module = Module(name="登录模块", session_id=sess.id)
    db_session.add(module)
    db_session.commit()
    db_session.refresh(module)

    # 历史需求：内容差异明显，便于验证相关性排序
    for i, content in enumerate([
        "用户可以使用账号密码登录系统",
        "订单支持批量导出Excel文件",
        "密码找回需要邮箱验证",
    ]):
        db_session.add(HistoryPrompt(
            content=content, module_id=module.id, session_id=sess.id, user_id="u1",
        ))
        db_session.commit()

    db_session.add(TestCase(
        case_name="登录成功 - 完整参数",
        expected_results=["跳转到首页", "展示用户名"],
        case_level=1,
        session_id=sess.id,
        module_id=module.id,
        user_id="u1",
    ))
    db_session.add(TestCase(
        case_name="密码错误 - 登录失败",
        expected_results=["提示账号或密码错误"],
        case_level=3,
        session_id=sess.id,
        module_id=module.id,
        user_id="u1",
    ))
    db_session.commit()
    return sess, module


def test_coverage_summary_lists_existing_cases(db_session, seeded):
    sess, module = seeded
    out = helpers._build_coverage_summary(db_session, module.id)
    assert "已覆盖清单" in out
    assert "登录成功 - 完整参数" in out
    assert "密码错误 - 登录失败" in out
    # 应带上验证要点
    assert "跳转到首页" in out


def test_coverage_summary_empty_without_cases(db_session):
    sess = DbSession(name="empty")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    module = Module(name="空模块", session_id=sess.id)
    db_session.add(module)
    db_session.commit()
    db_session.refresh(module)
    assert helpers._build_coverage_summary(db_session, module.id) == ""


def test_coverage_summary_requires_module(db_session, seeded):
    assert helpers._build_coverage_summary(db_session, None) == ""
    assert helpers._build_coverage_summary(None, 1) == ""


def test_history_context_falls_back_to_time_order(db_session, seeded):
    """检索无命中（或未启用）时回退时间序，行为与改造前一致。"""
    sess, module = seeded
    out = helpers._build_history_context(db_session, module.id)
    assert "历史需求上下文" in out
    assert "订单支持批量导出Excel文件" in out


def test_history_context_requires_module(db_session, seeded):
    assert helpers._build_history_context(db_session, None) == ""
    assert helpers._build_history_context(None, 1) == ""


def test_history_context_truncates_long_content(db_session):
    sess = DbSession(name="long")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    module = Module(name="长模块", session_id=sess.id)
    db_session.add(module)
    db_session.commit()
    db_session.refresh(module)

    db_session.add(HistoryPrompt(
        content="超长需求" * 2000,   # 远超单条上限
        module_id=module.id, session_id=sess.id, user_id="u1",
    ))
    db_session.commit()

    out = helpers._build_history_context(db_session, module.id)
    assert "…" in out
    # 单条内容被截断到上限附近
    body = out.split("\n", 1)[1]
    assert len(body) <= helpers._HISTORY_CONTENT_MAX_CHARS + 50


def test_history_context_respects_query_when_available(db_session, seeded, monkeypatch):
    """有 query 且检索可用时，优先返回相关性召回结果。"""
    sess, module = seeded

    class _Item:
        def __init__(self, content):
            self.content = content

    calls = {}

    def fake_retrieve(db, *, query, module_id, user_id=None, session_id=None, top_k=8):
        calls["query"] = query
        calls["module_id"] = module_id
        return [_Item("相关性命中的历史需求")]

    import utils.retrieval as retrieval_mod
    monkeypatch.setattr(retrieval_mod, "retrieve_history", fake_retrieve)

    out = helpers._build_history_context(
        db_session, module.id, query="账号密码登录", user_id="u1", session_id=sess.id,
    )
    assert "相关性命中的历史需求" in out
    assert calls["query"] == "账号密码登录"
    assert calls["module_id"] == module.id


def test_history_context_retrieval_failure_falls_back(db_session, seeded, monkeypatch):
    """检索抛异常时必须回退时间序，而不是让生成失败。"""
    sess, module = seeded

    def boom(*a, **k):
        raise RuntimeError("index down")

    import utils.retrieval as retrieval_mod
    monkeypatch.setattr(retrieval_mod, "retrieve_history", boom)

    out = helpers._build_history_context(
        db_session, module.id, query="任意", user_id="u1", session_id=sess.id,
    )
    assert "历史需求上下文" in out
    assert "订单支持批量导出Excel文件" in out


def test_upsert_history_prompt_indexes_new_entry(db_session, seeded, monkeypatch):
    """写入历史需求时应同步进检索索引。"""
    sess, module = seeded

    indexed = {}

    class _FakeIdx:
        def upsert(self, **kwargs):
            indexed.update(kwargs)

    import utils.retrieval as retrieval_mod
    monkeypatch.setattr(retrieval_mod, "get_index", lambda: _FakeIdx())

    helpers._upsert_history_prompt(
        db_session,
        content="新增的历史需求内容",
        module_id=module.id,
        session_id=sess.id,
        user_id="u1",
    )
    assert indexed.get("content") == "新增的历史需求内容"
    assert indexed.get("module_id") == module.id
