"""需求管理模块测试：结构化解析、差异计算、影响识别、路由行为。

全部使用 in-memory SQLite 与 monkeypatch，不触网络与真实 LLM。
"""
import asyncio

import pytest
from sqlmodel import select

import app.services.requirement_doc as svc
from db.models import (
    RequirementDoc,
    RequirementRevision,
    RequirementTestcaseLink,
    RequirementPoint,
    Session as DbSession,
    TestCase,
)
from db.models import TestCase as DBTestCase


# ===== 单元：URL 解析 =====

def test_parse_feishu_token_wiki():
    assert svc.parse_feishu_token("https://abc.feishu.cn/wiki/Xy12Z_token") == "Xy12Z_token"


def test_parse_feishu_token_docx():
    assert svc.parse_feishu_token("https://team.feishu.cn/docx/AbC123") == "AbC123"


def test_parse_feishu_token_invalid():
    assert svc.parse_feishu_token("https://example.com/doc") is None
    assert svc.parse_feishu_token("") is None


# ===== 单元：Markdown → 结构化条目 =====

def test_parse_markdown_headings_and_items():
    content = """# 登录功能
## 账号密码登录
用户输入正确的账号与密码后可登录系统。
登录成功后跳转首页。

## 验证码登录
系统向手机号发送 6 位验证码。
"""
    items = svc.parse_markdown_to_items(content)
    sections = {it.section for it in items}
    assert "登录功能 / 账号密码登录" in sections
    assert "登录功能 / 验证码登录" in sections
    contents = [it.content for it in items]
    assert any("账号与密码" in c for c in contents)
    assert any("6 位验证码" in c for c in contents)
    keys = [it.key for it in items]
    assert len(keys) == len(set(keys))


def test_parse_plain_text_numbering_sections():
    content = """一、功能概述
支持批量导入用户数据。
1.1 导入方式
用户可通过 Excel 模板批量导入。
"""
    items = svc.parse_markdown_to_items(content)
    sections = {it.section for it in items}
    assert any(s.startswith("一、功能概述") for s in sections)


def test_parse_table_block_merges_into_one_item():
    content = """| 字段 | 说明 |
| --- | --- |
| name | 用户名 |
后续说明文字。
"""
    items = svc.parse_markdown_to_items(content)
    table_items = [it for it in items if "name" in it.content and "用户名" in it.content]
    assert len(table_items) == 1
    assert "|" in table_items[0].content


def test_item_key_stable_for_same_content():
    a = svc.parse_markdown_to_items("## 登录\n用户输入账号密码登录。")
    b = svc.parse_markdown_to_items("## 其他\n无关内容。\n## 登录\n用户输入账号密码登录。")
    assert a[0].key == b[-1].key


# ===== 单元：稳定 key 合并与差异 =====

def test_merge_structure_keeps_key_when_content_unchanged():
    old = [{"key": "it_old1", "section": "登录", "content": "用户输入账号密码登录。",
            "title": "", "order_index": 1, "status": "active"}]
    new = [{"key": "it_new_x", "section": "登录", "content": "用户输入账号密码登录。",
            "title": "", "order_index": 1, "status": "active"}]
    merged = svc.merge_structure(old, new)
    assert merged[0]["key"] == "it_old1"


def test_compute_diff_added_modified_removed():
    old = [
        {"key": "k1", "section": "登录", "content": "旧内容A", "title": "", "order_index": 1, "status": "active"},
        {"key": "k2", "section": "登录", "content": "将被删除", "title": "", "order_index": 2, "status": "active"},
    ]
    new = [
        {"key": "k1", "section": "登录", "content": "新内容A", "title": "", "order_index": 1, "status": "active"},
        {"key": "k3", "section": "登录", "content": "新增条目", "title": "", "order_index": 2, "status": "active"},
    ]
    diffs = svc.compute_diff(old, new)
    types = {d["type"] for d in diffs}
    assert types == {"added", "modified", "removed"}
    modified = next(d for d in diffs if d["type"] == "modified")
    assert modified["before"] == "旧内容A"
    assert modified["after"] == "新内容A"


# ===== 单元：关键词与影响识别 =====

def test_extract_keywords_chinese_bigram():
    words = svc.extract_keywords("用户输入验证码登录")
    assert "验证" in words or "证码" in words
    assert "验证码" not in words  # 中文按 2-gram


def test_find_affected_by_keyword_overlap(db_session):
    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    case = TestCase(
        case_name="验证码登录成功",
        case_level=1,
        preset_conditions=["手机号已注册"],
        steps=["输入手机号", "输入验证码", "点击登录"],
        expected_results=["登录成功跳转首页"],
        session_id=sess.id,
    )
    other = TestCase(
        case_name="文件导出",
        case_level=1,
        preset_conditions=[],
        steps=["点击导出按钮"],
        expected_results=["导出文件成功"],
        session_id=sess.id,
    )
    db_session.add(case)
    db_session.add(other)
    db_session.commit()

    old_items = [{"key": "k1", "section": "登录", "content": "用户通过验证码登录系统。",
                  "title": "", "order_index": 1, "status": "active"}]
    new_items = [{"key": "k1", "section": "登录", "content": "用户通过短信验证码登录系统，验证码 5 分钟有效。",
                  "title": "", "order_index": 1, "status": "active"}]

    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id="u1")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    affected = svc.find_affected_testcases(db_session, doc.id, sess.id, old_items, new_items)
    ids = [a["testcase_id"] for a in affected]
    assert case.id in ids
    assert other.id not in ids
    assert affected[0]["suggestion"]


def test_find_affected_by_explicit_link(db_session):
    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    case = TestCase(case_name="完全无关用例", case_level=1, preset_conditions=[],
                    steps=["做某事"], expected_results=["完成"], session_id=sess.id)
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)

    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id="u1")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    db_session.add(RequirementTestcaseLink(doc_id=doc.id, item_key="k1", testcase_id=case.id,
                                           session_id=sess.id, user_id="u1"))
    db_session.commit()

    old_items = [{"key": "k1", "section": "s", "content": "旧", "title": "", "order_index": 1, "status": "active"}]
    new_items = [{"key": "k1", "section": "s", "content": "新", "title": "", "order_index": 1, "status": "active"}]
    affected = svc.find_affected_testcases(db_session, doc.id, sess.id, old_items, new_items)
    assert [a["testcase_id"] for a in affected] == [case.id]


def test_find_affected_no_change_returns_empty(db_session):
    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id="u1")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    items = [{"key": "k1", "section": "s", "content": "同", "title": "", "order_index": 1, "status": "active"}]
    assert svc.find_affected_testcases(db_session, doc.id, None, items, items) == []


def test_build_case_update_blocks_contains_sections():
    items = [{"section": "登录 / 验证码", "content": "验证码 6 位数字。"}]
    case = TestCase(case_name="验证码登录", case_level=1,
                    preset_conditions=["手机号可用"], steps=["输入验证码"],
                    expected_results=["登录成功"])
    block = svc.build_case_update_blocks(items, case)
    assert "【登录 / 验证码】" in block
    assert "6 位数字" in block
    assert "验证码登录" in block
    assert "输入验证码" in block


# ===== 路由：创建 → 同步（mock 飞书）→ 内容更新 → 历史 → 联动 =====

@pytest.fixture()
def _user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester", roles=["admin"])


def test_create_doc_invalid_url_rejected(db_session, _user):
    from app.routes import requirement_doc as rd
    with pytest.raises(Exception):
        asyncio.run(rd.create_requirement_doc(
            rd.DocCreateRequest(url="https://example.com/x"), db_session, _user))


def test_create_doc_syncs_and_records_history(db_session, _user, monkeypatch):
    from app.routes import requirement_doc as rd

    async def fake_fetch(url, user_id):
        return "一、登录功能\n用户输入账号密码登录系统。"
    monkeypatch.setattr(rd, "_fetch_feishu_content", fake_fetch)

    resp = asyncio.run(rd.create_requirement_doc(
        rd.DocCreateRequest(url="https://abc.feishu.cn/wiki/Tok123"), db_session, _user))
    assert resp.code == 200
    doc = db_session.exec(select(RequirementDoc)).first()
    assert doc.feishu_token == "Tok123"
    assert doc.version == 1
    assert len(doc.structured) >= 1

    revs = db_session.exec(select(RequirementRevision)).all()
    assert len(revs) == 1
    assert revs[0].source == "sync"


def test_update_content_records_diff_and_bumps_version(db_session, _user):
    from app.routes import requirement_doc as rd

    doc = RequirementDoc(title="d", url="https://abc.feishu.cn/wiki/T1",
                         feishu_token="T1", user_id="u1", version=0)
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    resp = rd.update_content(doc.id, rd.DocContentUpdateRequest(
        content="一、登录功能\n用户输入账号密码登录系统。"), db_session, _user)
    assert resp.code == 200
    assert resp.data["version"] == 1
    assert any(d["type"] == "added" for d in resp.data["differences"])

    resp2 = rd.update_content(doc.id, rd.DocContentUpdateRequest(
        content="一、登录功能\n用户输入账号密码登录系统，登录后跳转首页。"), db_session, _user)
    assert resp2.data["version"] == 2
    types = {d["type"] for d in resp2.data["differences"]}
    assert types == {"modified", "added"} or types == {"modified"}

    revs = db_session.exec(select(RequirementRevision)
                           .where(RequirementRevision.doc_id == doc.id)).all()
    assert len(revs) == 2


def test_sync_route_uses_snapshot_when_fetch_false(db_session, _user):
    from app.routes import requirement_doc as rd
    doc = RequirementDoc(title="d", url="https://abc.feishu.cn/wiki/T2",
                         feishu_token="T2", user_id="u1",
                         content_snapshot="一、功能\n已有内容。")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    resp = asyncio.run(rd.sync_requirement_doc(doc.id, rd.DocSyncRequest(fetch_feishu=False),
                                               db_session, _user))
    assert resp.code == 200
    assert resp.data["version"] == 1


def test_sync_route_failure_raises(db_session, _user, monkeypatch):
    from app.routes import requirement_doc as rd

    async def fake_fail(url, user_id):
        return None
    monkeypatch.setattr(rd, "_fetch_feishu_content", fake_fail)

    doc = RequirementDoc(title="d", url="https://abc.feishu.cn/wiki/T3",
                         feishu_token="T3", user_id="u1")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    with pytest.raises(Exception):
        asyncio.run(rd.sync_requirement_doc(doc.id, rd.DocSyncRequest(fetch_feishu=True),
                                            db_session, _user))


def test_links_upsert_and_list(db_session, _user):
    from app.routes import requirement_doc as rd
    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id="u1")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    resp = rd.upsert_link(doc.id, rd.LinkUpsertRequest(item_key="k1", testcase_id=5,
                                                       session_id=1), db_session, _user)
    assert resp.data["linked"] is True
    links = rd.list_links(doc.id, db_session, _user)
    assert len(links.data) == 1
    assert links.data[0]["item_key"] == "k1"

    resp2 = rd.upsert_link(doc.id, rd.LinkUpsertRequest(item_key="k1", testcase_id=5,
                                                        session_id=1), db_session, _user)
    assert resp2.data["linked"] is True
    links2 = rd.list_links(doc.id, db_session, _user)
    assert len(links2.data) == 1

    rd.upsert_link(doc.id, rd.LinkUpsertRequest(item_key="k1", testcase_id=0), db_session, _user)
    links3 = rd.list_links(doc.id, db_session, _user)
    assert len(links3.data) == 0


def test_impact_analysis_route(db_session, _user):
    from app.routes import requirement_doc as rd
    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    case = TestCase(case_name="账号密码登录成功", case_level=1, preset_conditions=[],
                    steps=["输入账号密码", "点击登录"], expected_results=["登录成功"],
                    session_id=sess.id)
    db_session.add(case)
    db_session.commit()

    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id="u1",
                         session_id=sess.id, version=0)
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    rd.update_content(doc.id, rd.DocContentUpdateRequest(
        content="一、登录\n用户输入账号密码登录系统。"), db_session, _user)
    rd.update_content(doc.id, rd.DocContentUpdateRequest(
        content="一、登录\n用户输入账号密码登录系统，连续失败 5 次锁定账号。"), db_session, _user)

    resp = asyncio.run(rd.impact_analysis(doc.id, db_session, _user))
    assert resp.data["has_diff"] is True
    ids = [a["testcase_id"] for a in resp.data["affected"]]
    assert case.id in ids

    rev = db_session.exec(select(RequirementRevision)
                          .where(RequirementRevision.doc_id == doc.id)
                          .order_by(RequirementRevision.version.desc())).first()
    assert case.id in (rev.affected_case_ids or [])


def test_apply_cases_suggestion_mode(db_session, _user, monkeypatch):
    from app.routes import requirement_doc as rd
    import utils.model_utils as mu
    from utils.models import FunctionalTestCase, FunctionalResponseFormat

    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    case = TestCase(case_name="账号密码登录", case_level=1, preset_conditions=[],
                    steps=["输入账号密码", "点击登录"], expected_results=["登录成功"],
                    session_id=sess.id)
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)

    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id="u1", version=1,
                         structured=[{"key": "k1", "section": "登录",
                                      "content": "连续失败 5 次锁定账号。", "title": "",
                                      "order_index": 1, "status": "active"}],
                         content_snapshot="一、登录\n连续失败 5 次锁定账号。")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    import app.routes.requirement_doc as rd_mod
    monkeypatch.setattr(rd_mod, "_APPLY_CONCURRENCY", 1)

    async def fake_structured(model, schema, messages, **kwargs):
        return FunctionalResponseFormat(response=[FunctionalTestCase(
            case_name="账号密码登录-失败锁定",
            case_level=1,
            steps=["输入账号密码", "连续失败5次", "验证账号锁定"],
            preset_conditions=["账号已注册"],
            expected_results=["账号被锁定并提示"],
        )])
    monkeypatch.setattr(mu, "_structured_output_robust", fake_structured)
    monkeypatch.setattr("utils.llm_factory.build_chat_model", lambda *a, **k: object())

    from config import config_manager
    monkeypatch.setattr(config_manager, "get_model_type", lambda: "api", raising=False)
    monkeypatch.setattr(config_manager, "get_api_key", lambda: "sk-test", raising=False)
    monkeypatch.setattr(config_manager, "get_api_base_url", lambda: "https://api.deepseek.com", raising=False)
    monkeypatch.setattr(config_manager, "get_api_model", lambda: "deepseek-test", raising=False)

    resp = asyncio.run(rd.apply_cases(
        doc.id, rd.ApplyCasesRequest(case_ids=[case.id], apply=False),
        db_session, _user))
    assert resp.code == 200
    r0 = resp.data["results"][0]
    assert r0["status"] == "suggested"
    assert "失败" in r0["updated"]["case_name"]

    db_session.refresh(case)
    assert case.case_name == "账号密码登录"


def test_apply_cases_applies_updates(db_session, _user, monkeypatch):
    from app.routes import requirement_doc as rd
    import utils.model_utils as mu
    from utils.models import FunctionalTestCase, FunctionalResponseFormat

    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    case = TestCase(case_name="旧用例名", case_level=1, preset_conditions=[],
                    steps=["旧步骤"], expected_results=["旧预期"], session_id=sess.id)
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)

    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id="u1", version=2,
                         structured=[{"key": "k1", "section": "登录",
                                      "content": "新需求内容。", "title": "",
                                      "order_index": 1, "status": "active"}],
                         content_snapshot="x")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    async def fake_structured(model, schema, messages, **kwargs):
        return FunctionalResponseFormat(response=[FunctionalTestCase(
            case_name="新用例名", case_level=2,
            steps=["新步骤1", "新步骤2"],
            preset_conditions=["新预置"],
            expected_results=["新预期"],
        )])
    monkeypatch.setattr(mu, "_structured_output_robust", fake_structured)
    monkeypatch.setattr("utils.llm_factory.build_chat_model", lambda *a, **k: object())

    from config import config_manager
    monkeypatch.setattr(config_manager, "get_model_type", lambda: "api", raising=False)
    monkeypatch.setattr(config_manager, "get_api_key", lambda: "sk-test", raising=False)
    monkeypatch.setattr(config_manager, "get_api_base_url", lambda: "https://api.deepseek.com", raising=False)
    monkeypatch.setattr(config_manager, "get_api_model", lambda: "deepseek-test", raising=False)

    resp = asyncio.run(rd.apply_cases(
        doc.id, rd.ApplyCasesRequest(case_ids=[case.id], apply=True),
        db_session, _user))
    assert resp.data["applied"] is True
    db_session.refresh(case)
    assert case.case_name == "新用例名"
    assert case.steps == ["新步骤1", "新步骤2"]
    assert case.case_level == 2
    assert case.human_edited is True

    revs = db_session.exec(select(RequirementRevision)
                           .where(RequirementRevision.doc_id == doc.id)
                           .order_by(RequirementRevision.version.desc())).all()
    assert revs[0].source == "apply_cases"
    assert case.id in (revs[0].affected_case_ids or [])


def test_apply_cases_requires_model_config(db_session, _user, monkeypatch):
    from app.routes import requirement_doc as rd
    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id="u1", version=1,
                         structured=[{"key": "k1", "section": "s", "content": "c",
                                      "title": "", "order_index": 1, "status": "active"}],
                         content_snapshot="c")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    from config import config_manager
    monkeypatch.setattr(config_manager, "get_model_type", lambda: "api", raising=False)
    monkeypatch.setattr(config_manager, "get_api_key", lambda: "", raising=False)

    with pytest.raises(Exception):
        asyncio.run(rd.apply_cases(doc.id, rd.ApplyCasesRequest(case_ids=[1], apply=False),
                                   db_session, _user))


def test_delete_doc_removes_related(db_session, _user):
    from app.routes import requirement_doc as rd
    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id="u1")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(RequirementRevision(doc_id=doc.id, version=1, source="sync",
                                       summary="", differences=[], content_snapshot="",
                                       affected_case_ids=[], user_id="u1"))
    db_session.add(RequirementTestcaseLink(doc_id=doc.id, item_key="k", testcase_id=1,
                                           session_id=None, user_id="u1"))
    db_session.commit()

    rd.delete_requirement_doc(doc.id, db_session, _user)
    assert db_session.exec(select(RequirementDoc)).first() is None
    assert db_session.exec(select(RequirementRevision)).first() is None
    assert db_session.exec(select(RequirementTestcaseLink)).first() is None


# ===== P0:条目圈选 → 需求点 / 覆盖视图 / 条目直生用例 =====

def _make_doc_with_items(db_session, items: list[dict], user_id: str = "u1") -> RequirementDoc:
    doc = RequirementDoc(title="d", url="u", feishu_token="t", user_id=user_id,
                         version=1, structured=items, content_snapshot="x")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


ITEMS = [
    {"key": "k1", "section": "登录", "content": "用户输入账号密码登录系统。", "title": "账号密码", "order_index": 1, "status": "active"},
    {"key": "k2", "section": "登录", "content": "验证码 6 位数字，5 分钟有效。", "title": "验证码", "order_index": 2, "status": "active"},
]


def test_to_requirement_points_creates_and_is_idempotent(db_session, _user):
    from app.routes import requirement_doc as rd
    sess = DbSession(name="s1", user_id="u1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    doc = _make_doc_with_items(db_session, ITEMS)

    resp = rd.to_requirement_points(doc.id, rd.ToRequirementPointsRequest(
        session_id=sess.id, item_keys=["k1", "k2"]), db_session, _user)
    assert resp.data["created"] == 2
    assert resp.data["skipped"] == 0

    resp2 = rd.to_requirement_points(doc.id, rd.ToRequirementPointsRequest(
        session_id=sess.id, item_keys=["k1", "k2"]), db_session, _user)
    assert resp2.data["created"] == 0
    assert resp2.data["skipped"] == 2

    rps = db_session.exec(select(RequirementPoint)).all()
    assert len(rps) == 2
    assert all(rp.doc_id == doc.id for rp in rps)
    assert all(rp.session_id == sess.id for rp in rps)
    assert {rp.item_key for rp in rps} == {"k1", "k2"}


def test_to_requirement_points_rejects_missing_session(db_session, _user):
    from app.routes import requirement_doc as rd
    doc = _make_doc_with_items(db_session, ITEMS)
    with pytest.raises(Exception):
        rd.to_requirement_points(doc.id, rd.ToRequirementPointsRequest(
            session_id=999, item_keys=["k1"]), db_session, _user)


def test_to_requirement_points_rejects_stale_keys(db_session, _user):
    from app.routes import requirement_doc as rd
    sess = DbSession(name="s1", user_id="u1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    doc = _make_doc_with_items(db_session, ITEMS)
    with pytest.raises(Exception) as exc_info:
        rd.to_requirement_points(doc.id, rd.ToRequirementPointsRequest(
            session_id=sess.id, item_keys=["k_gone"]), db_session, _user)
    assert "已失效" in str(exc_info.value)


def test_coverage_three_states(db_session, _user):
    from app.routes import requirement_doc as rd
    sess = DbSession(name="s1", user_id="u1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    doc = _make_doc_with_items(db_session, ITEMS)

    resp = rd.coverage(doc.id, db_session, _user)
    assert resp.data["uncovered"] == 2
    assert resp.data["covered"] == 0

    rd.to_requirement_points(doc.id, rd.ToRequirementPointsRequest(
        session_id=sess.id, item_keys=["k1"]), db_session, _user)
    resp = rd.coverage(doc.id, db_session, _user)
    by_key = {x["item_key"]: x for x in resp.data["items"]}
    assert by_key["k1"]["coverage"] == "pending"
    assert by_key["k2"]["coverage"] == "uncovered"

    case = TestCase(case_name="验证码登录", case_level=1, preset_conditions=[],
                    steps=["输入验证码"], expected_results=["登录成功"],
                    status="PASSED", session_id=sess.id)
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    rd.upsert_link(doc.id, rd.LinkUpsertRequest(item_key="k2", testcase_id=case.id,
                                                session_id=sess.id), db_session, _user)
    resp = rd.coverage(doc.id, db_session, _user)
    by_key = {x["item_key"]: x for x in resp.data["items"]}
    assert by_key["k2"]["coverage"] == "covered"
    assert by_key["k2"]["case_status"]["passed"] == 1
    assert by_key["k2"]["case_status"]["total"] == 1
    assert resp.data["covered"] == 1
    # 用例明细:名称/状态/会话随覆盖数据返回
    details = by_key["k2"]["case_details"]
    assert len(details) == 1
    assert details[0]["case_name"] == "验证码登录"
    assert details[0]["status"] == "PASSED"
    assert details[0]["session_id"] == sess.id


def test_coverage_counts_cases_via_requirement_point_chain(db_session, _user):
    from app.routes import requirement_doc as rd
    from db.models import TestPoint as TPModel
    sess = DbSession(name="s1", user_id="u1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    doc = _make_doc_with_items(db_session, ITEMS)

    rd.to_requirement_points(doc.id, rd.ToRequirementPointsRequest(
        session_id=sess.id, item_keys=["k1"]), db_session, _user)
    rp = db_session.exec(select(RequirementPoint)).first()

    tp = TPModel(requirement_point_id=rp.id, session_id=sess.id,
                 content="tp", test_type="功能", design_method="等价类", priority="high")
    db_session.add(tp)
    db_session.commit()
    db_session.refresh(tp)

    case = TestCase(case_name="账号密码登录", case_level=1, preset_conditions=[],
                    steps=["输入"], expected_results=["成功"], status="NOT_RUN",
                    session_id=sess.id, test_point_id=tp.id)
    db_session.add(case)
    db_session.commit()

    resp = rd.coverage(doc.id, db_session, _user)
    by_key = {x["item_key"]: x for x in resp.data["items"]}
    assert by_key["k1"]["coverage"] == "covered"
    assert by_key["k1"]["case_status"]["not_run"] == 1
    assert resp.data["covered"] == 1


def test_coverage_empty_doc(db_session, _user):
    from app.routes import requirement_doc as rd
    doc = _make_doc_with_items(db_session, [])
    resp = rd.coverage(doc.id, db_session, _user)
    assert resp.data["total"] == 0
    assert resp.data["items"] == []


def test_generate_from_items_requires_model_config(db_session, _user, monkeypatch):
    from app.routes import requirement_doc as rd
    from config import config_manager
    sess = DbSession(name="s1", user_id="u1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    doc = _make_doc_with_items(db_session, ITEMS)

    monkeypatch.setattr(config_manager, "get_model_type", lambda: "api", raising=False)
    monkeypatch.setattr(config_manager, "get_api_key", lambda: "", raising=False)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(rd.generate_from_items(doc.id, rd.GenerateFromItemsRequest(
            session_id=sess.id, item_keys=["k1"]), db_session, _user))
    assert "未配置 API 模型" in str(exc_info.value)


def test_generate_from_items_creates_cases_and_links(db_session, _user, monkeypatch):
    from app.routes import requirement_doc as rd
    from config import config_manager
    import utils.model_utils as mu

    sess = DbSession(name="s1", user_id="u1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    doc = _make_doc_with_items(db_session, ITEMS)

    monkeypatch.setattr(config_manager, "get_model_type", lambda: "api", raising=False)
    monkeypatch.setattr(config_manager, "get_api_key", lambda: "sk-test", raising=False)
    monkeypatch.setattr(config_manager, "get_api_base_url", lambda: "https://api.deepseek.com", raising=False)
    monkeypatch.setattr(config_manager, "get_api_model", lambda: "deepseek-test", raising=False)

    async def fake_generate(**kwargs):
        tc = DBTestCase(
            case_name="账号密码登录成功",
            case_level=1,
            preset_conditions=["用户已注册"],
            steps=["打开登录页", "输入账号密码", "点击登录"],
            expected_results=["跳转到首页"],
            session_id=kwargs["session_id"],
        )
        return [tc], kwargs["requirement"]

    monkeypatch.setattr(mu, "generate_testcases", fake_generate)

    resp = asyncio.run(rd.generate_from_items(doc.id, rd.GenerateFromItemsRequest(
        session_id=sess.id, item_keys=["k1"]), db_session, _user))
    assert resp.data["generated"] == 1
    assert resp.data["linked"] == 1

    links = rd.list_links(doc.id, db_session, _user)
    assert len(links.data) == 1
    assert links.data[0]["item_key"] == "k1"
