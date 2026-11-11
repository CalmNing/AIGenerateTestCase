"""P2 集成测试：检索接入历史上下文 + 已覆盖清单。

实体合一后：历史需求的事实来源是 requirement_doc（需求实体），
生成输入日志通过 upsert_generation_entry 写入（source_type='generation'）。

验证：
- 历史上下文在有检索命中时按相关性召回（而非时间序）；
- 检索不可用时回退时间序（行为与改造前一致）；
- 单条历史内容被字符级截断；
- 已覆盖清单能列出本模块已有用例（标题 + 验证要点）；
- 生成输入写入需求实体并进入检索索引；
- 启动迁移把 historyprompt 行迁入 requirement_doc 且幂等。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlmodel import select

from db.models import (
    HistoryPrompt,
    Module,
    RequirementDoc,
    Session as DbSession,
    TestCase,
)
from utils import helpers


def _seed_requirement(db, module, sess, content: str, source_type: str = "generation") -> RequirementDoc:
    doc = RequirementDoc(
        title=content.split("\n", 1)[0][:30],
        url="",
        feishu_token="",
        content_snapshot=content,
        structured=[],
        session_id=sess.id,
        version=0,
        module_id=module.id if module is not None else None,
        source_type=source_type,
        user_id="u1",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


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
    for content in [
        "用户可以使用账号密码登录系统",
        "订单支持批量导出Excel文件",
        "密码找回需要邮箱验证",
    ]:
        _seed_requirement(db_session, module, sess, content)

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

    _seed_requirement(db_session, module, sess, "超长需求" * 2000)  # 远超单条上限

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


def test_upsert_generation_entry_indexes_new_doc(db_session, seeded, monkeypatch):
    """生成输入写入需求实体时应同步进检索索引（rowid = requirement_doc.id）。"""
    sess, module = seeded

    indexed = {}

    class _FakeIdx:
        def upsert(self, **kwargs):
            indexed.update(kwargs)

    import utils.retrieval as retrieval_mod
    monkeypatch.setattr(retrieval_mod, "get_index", lambda: _FakeIdx())

    doc = helpers._upsert_history_prompt(
        db_session,
        content="新增的历史需求内容",
        module_id=module.id,
        session_id=sess.id,
        user_id="u1",
    )
    assert doc is not None
    assert doc.source_type == "generation"
    assert indexed.get("content") == "新增的历史需求内容"
    assert indexed.get("module_id") == module.id
    assert indexed.get("row_id") == doc.id


def test_upsert_generation_entry_is_idempotent(db_session, seeded):
    """同 (session, module, content) 重复生成不产生重复需求实体。"""
    sess, module = seeded
    first = helpers._upsert_history_prompt(
        db_session, content="相同内容的需求", module_id=module.id,
        session_id=sess.id, user_id="u1",
    )
    second = helpers._upsert_history_prompt(
        db_session, content="相同内容的需求", module_id=module.id,
        session_id=sess.id, user_id="u1",
    )
    assert first is not None and second is not None
    assert first.id == second.id

    docs = db_session.exec(
        select(RequirementDoc).where(RequirementDoc.content_snapshot == "相同内容的需求")
    ).all()
    assert len(docs) == 1


def test_upsert_generation_entry_empty_content_returns_none(db_session, seeded):
    sess, module = seeded
    assert helpers._upsert_history_prompt(
        db_session, content="", module_id=module.id, session_id=sess.id, user_id="u1",
    ) is None


def test_migrate_history_prompt_to_requirement_doc(db_session):
    """启动迁移：historyprompt 行迁入 requirement_doc(source_type='generation')，幂等。"""
    from db.db import _migrate_history_prompt_to_requirement_doc

    sess = DbSession(name="mig")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    module = Module(name="迁移模块", session_id=sess.id)
    db_session.add(module)
    db_session.commit()
    db_session.refresh(module)

    db_session.add(HistoryPrompt(
        content="迁移的历史需求一\n详细描述内容",
        module_id=module.id,
        session_id=sess.id,
        user_id="u1",
    ))
    db_session.commit()

    # 内存库需要人工建 requirement_doc 表（迁移函数假定表已由 create_all 建出）
    _migrate_history_prompt_to_requirement_doc(db_session.get_bind())

    docs = db_session.exec(
        select(RequirementDoc).where(RequirementDoc.source_type == "generation")
    ).all()
    assert len(docs) == 1
    assert "迁移的历史需求一" in docs[0].content_snapshot
    assert docs[0].module_id == module.id
    assert docs[0].user_id == "u1"

    # 幂等：已存在 generation 来源时不再迁移
    db_session.add(HistoryPrompt(
        content="迁移之后新增的第二条",
        module_id=module.id,
        session_id=sess.id,
        user_id="u1",
    ))
    db_session.commit()
    _migrate_history_prompt_to_requirement_doc(db_session.get_bind())
    docs2 = db_session.exec(
        select(RequirementDoc).where(RequirementDoc.source_type == "generation")
    ).all()
    assert len(docs2) == 1  # 未新增


def test_text_requirement_endpoint_creates_manual_doc(db_session, _rd_user):
    from app.routes import requirement_doc as rd

    resp = rd.create_text_requirement(
        rd.TextRequirementRequest(
            title="手动需求",
            content="一、登录\n用户输入账号密码登录系统。",
            session_id=None,
            module_id=None,
        ),
        db_session,
        _rd_user,
    )
    assert resp.code == 200
    assert resp.data["source_type"] == "manual"
    assert resp.data["version"] == 1
    assert len(resp.data["structured"]) >= 1

    doc = db_session.get(RequirementDoc, resp.data["id"])
    assert doc is not None
    assert doc.source_type == "manual"
    assert doc.content_snapshot.startswith("一、登录")


@pytest.fixture()
def _rd_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester", roles=["admin"])


def test_list_requirement_docs_filters_by_source(db_session, _rd_user):
    from app.routes import requirement_doc as rd

    sess = DbSession(name="s", user_id="u1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    _seed_requirement(db_session, None, sess, "生成日志需求", source_type="generation")
    _seed_requirement(db_session, None, sess, "登记文档需求", source_type="feishu")

    all_docs = rd.list_requirement_docs(db_session, _rd_user)
    assert len(all_docs.data) == 2

    only_gen = rd.list_requirement_docs(db_session, _rd_user, source_type="generation")
    assert len(only_gen.data) == 1
    assert only_gen.data[0]["source_type"] == "generation"

    only_feishu = rd.list_requirement_docs(db_session, _rd_user, source_type="feishu")
    assert len(only_feishu.data) == 1
    assert only_feishu.data[0]["source_type"] == "feishu"
