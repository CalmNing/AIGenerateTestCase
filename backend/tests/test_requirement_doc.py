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


def test_strict_heading_levels_only_marked_headings():
    """带 # 标题标记的正文：严格按标题等级分类，正文行不再被抬为章节。

    模拟飞书 blocks 重建快照：首行为正文、标题带 #/##/###/##### 标记，
    章节路径只由标记标题按等级嵌套构成；短行/编号行是正文，不是章节。
    """
    content = """【低成本】、【制度】
# 一、背景与价值
对我司而言
填补基础能力缺失：如果没有，就会显得平台的AI能力不强。
对客户而言
什么样的客户：有能力也有意愿为此付费的。
### 2.1 平台知识库
知识范围
在统一后台维护，包含平台使用手册。
1. 产品定义4个行业标签，作为功能配置在统一后台维护。
### 2.2 企业知识库
客户的制度等企业知识
##### （1） 列表页
分类标签
保留一个【全部】，默认着陆在这里。
"""
    items = svc.parse_markdown_to_items(content)
    sections = {it.section for it in items}
    # 只有带标记的标题进入章节路径，且严格按标题等级嵌套
    assert "一、背景与价值" in sections
    assert "一、背景与价值 / 2.1 平台知识库" in sections
    assert "一、背景与价值 / 2.2 企业知识库" in sections
    assert "一、背景与价值 / 2.2 企业知识库 / （1） 列表页" in sections
    # 正文行（短行/编号行）不得成为章节，也不得混进章节路径
    for bad in ("对我司而言", "知识范围", "分类标签",
                "一、背景与价值 / 对我司而言", "2.1 平台知识库 / 知识范围"):
        assert not any(bad in s for s in sections), sections
    # 正文行保留在条目内容里，不丢失
    all_content = "\n".join(it.content for it in items)
    assert "对我司而言" in all_content
    assert "知识范围" in all_content
    assert "保留一个【全部】" in all_content


def test_has_markdown_headings_detection():
    """严格模式检测：任一行为 # 标题即视为带标记；纯文本返回 False。"""
    assert svc.has_markdown_headings("正文行\n# 一、背景") is True
    assert svc.has_markdown_headings("### 2.1 子章节\n正文") is True
    assert svc.has_markdown_headings("#FF0000 是颜色值\n正文") is False
    assert svc.has_markdown_headings("一、背景\n1.1 子项\n正文") is False
    assert svc.has_markdown_headings("") is False


def test_plain_text_without_markers_keeps_heuristics():
    """纯文本（无 # 标记）保持原启发式行为，编号章节仍可识别。"""
    items = svc.parse_markdown_to_items("背景\n这是背景说明。\n一、功能概述\n支持批量导入。")
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

    resp = asyncio.run(rd.update_content(doc.id, rd.DocContentUpdateRequest(
        content="一、登录功能\n用户输入账号密码登录系统。"), db_session, _user))
    assert resp.code == 200
    assert resp.data["version"] == 1
    assert any(d["type"] == "added" for d in resp.data["differences"])

    resp2 = asyncio.run(rd.update_content(doc.id, rd.DocContentUpdateRequest(
        content="一、登录功能\n用户输入账号密码登录系统，登录后跳转首页。"), db_session, _user))
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


def test_session_access_shared_across_users(db_session, monkeypatch):
    """数据不按用户隔离：任何人（含 admin/未登录上下文）都能访问既有会话，列表返回全部会话。"""
    from app.routes.pipeline import _check_session_owner
    from app.routes import session as session_routes
    from app.auth import UserInfo

    sess = DbSession(name="工作台", user_id="owner-user")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    owner = UserInfo(user_id="owner-user", username="owner", roles=["user"])
    stranger = UserInfo(user_id="someone-else", username="stranger", roles=["admin"])

    # 本人放行
    _check_session_owner(db_session, sess.id, owner)
    # 非本人也放行（数据全局共享，不再按 user_id 校验归属）
    _check_session_owner(db_session, sess.id, stranger)
    # 未登录上下文也放行（仅校验会话存在）
    _check_session_owner(db_session, sess.id, None)

    # 会话列表全局共享：能看到所有用户的会话
    db_session.add(DbSession(name="别人的", user_id="someone-else"))
    db_session.commit()
    resp = session_routes.get_sessions(db_session, stranger)
    assert {s.name for s in resp.data} == {"工作台", "别人的"}
    # 不存在的会话仍然 404
    with pytest.raises(Exception) as exc:
        _check_session_owner(db_session, 999999, stranger)
    assert "会话不存在" in str(exc.value)
