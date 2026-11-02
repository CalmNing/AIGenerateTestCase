"""P2 检索层（FTS5 + 中文二元分词）测试。

全部用例都不触网、不调真实 LLM，只用临时 SQLite 文件。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from utils.retrieval import (
    DEFAULT_TOP_K,
    FtsIndex,
    build_match_query,
    format_retrieved_context,
    tokenize_cjk,
)


@pytest.fixture()
def idx(tmp_path):
    """每个用例一个独立的临时索引文件。"""
    index = FtsIndex(str(tmp_path / "idx.db"))
    assert index.ensure_index() is True
    yield index
    index.close()


def _seed(index: FtsIndex):
    rows = [
        (1, "用户可以使用账号密码登录系统", 10),
        (2, "订单支持批量导出Excel文件", 10),
        (3, "密码找回需要邮箱验证", 10),
        (4, "用户头像上传支持jpg格式", 11),
        (5, "订单详情页展示收货地址", 11),
    ]
    for rid, content, mid in rows:
        index.upsert(
            row_id=rid,
            content=content,
            module_id=mid,
            user_id="u1",
            session_id=1,
            created_at="2026-01-01",
        )


# --------------------------------------------------------------- 分词

def test_tokenize_cjk_bigrams():
    assert tokenize_cjk("用户登录") == ["用户", "户登", "登录"]


def test_tokenize_ascii_words():
    assert "excel" in tokenize_cjk("导出 Excel 文件")


def test_tokenize_single_cjk_char_kept():
    assert "a" not in tokenize_cjk("")
    assert tokenize_cjk("好") == ["好"]


def test_tokenize_empty():
    assert tokenize_cjk("") == []
    assert tokenize_cjk(None) == []


def test_build_match_query_quotes_tokens():
    q = build_match_query("密码找回")
    assert q.startswith('"') and " OR " in q
    # FTS5 语法字符不能破坏查询（需先能切出 token）
    assert '"' in build_match_query("a-b (c)") or build_match_query("a-b (c)") == ""


def test_build_match_query_empty():
    assert build_match_query("") == ""
    assert build_match_query("   ") == ""


# --------------------------------------------------------------- 索引

def test_upsert_and_search_chinese(idx):
    _seed(idx)
    hits = idx.search("账号密码", module_id=10)
    assert hits
    assert hits[0].row_id == 1


def test_search_filters_by_module(idx):
    _seed(idx)
    # 种子数据：row2(订单导出) 在 module=10，row5(订单详情) 在 module=11
    hits = idx.search("订单", module_id=11)
    assert {h.row_id for h in hits} == {5}
    hits10 = idx.search("订单", module_id=10)
    assert {h.row_id for h in hits10} == {2}


def test_search_across_modules_when_not_specified(idx):
    _seed(idx)
    hits = idx.search("订单", module_id=None)
    assert {h.row_id for h in hits} >= {2, 5}


def test_search_user_filter_excludes_other_users(idx):
    idx.upsert(row_id=1, content="仅用户u2的需求", module_id=10,
               user_id="u2", session_id=1, created_at=None)
    hits = idx.search("需求", module_id=10, user_id="u1")
    assert hits == []


def test_search_exclude_ids(idx):
    _seed(idx)
    hits = idx.search("订单", module_id=10, exclude_ids={2})
    assert 2 not in {h.row_id for h in hits}


def test_top_k_limits_results(idx):
    _seed(idx)
    hits = idx.search("用户", module_id=None, top_k=1)
    assert len(hits) <= 1


def test_delete_removes_from_index(idx):
    _seed(idx)
    idx.delete(1)
    hits = idx.search("账号密码", module_id=10)
    assert 1 not in {h.row_id for h in hits}


def test_sync_from_source_rebuilds(idx):
    idx.upsert(row_id=99, content="临时条目", module_id=10,
               user_id="u1", session_id=1, created_at=None)
    count = idx.sync_from_source([
        {"id": 5, "content": "同步后的内容", "module_id": 10,
         "user_id": "u1", "session_id": 1, "created_at": "2026-01-01"},
    ])
    assert count == 1
    hits = idx.search("同步", module_id=10)
    assert [h.row_id for h in hits] == [5]
    assert 99 not in {h.row_id for h in hits}


def test_sync_skips_empty_content(idx):
    count = idx.sync_from_source([
        {"id": 1, "content": "   ", "module_id": 10},
        {"id": 2, "content": "有内容", "module_id": 10},
    ])
    assert count == 1


def test_upsert_truncates_long_content(idx):
    long_text = "测试" * 5000
    idx.upsert(row_id=1, content=long_text, module_id=10,
               user_id="u1", session_id=1, created_at=None)
    hits = idx.search("测试", module_id=10)
    assert hits
    assert len(hits[0].content) <= 4000


def test_upsert_ignores_empty_content(idx):
    idx.upsert(row_id=1, content="   ", module_id=10,
               user_id="u1", session_id=1, created_at=None)
    assert idx.search("任意", module_id=10) == []


# --------------------------------------------------------------- 同义词

def test_synonym_expansion_finds_related(idx):
    _seed(idx)
    idx.set_terms({"登录": ["登入", "账号登录"]})
    hits = idx.search("登入", module_id=None)
    assert 1 in {h.row_id for h in hits}


def test_get_terms_roundtrip(idx):
    idx.set_terms({"导出": ["输出"]})
    terms = idx.get_terms()
    assert "导出" in terms
    assert "输出" in terms["导出"]


def test_set_terms_with_empty_synonyms(idx):
    idx.set_terms({"空词": []})
    assert idx.get_terms()["空词"] == []


# --------------------------------------------------------------- 格式化

def test_format_retrieved_context_empty():
    assert format_retrieved_context([]) == ""


def test_format_retrieved_context_truncates():
    from utils.retrieval import RetrievedItem

    items = [RetrievedItem(row_id=1, content="很长的内容" * 500, score=-1.0)]
    out = format_retrieved_context(items, max_chars=100)
    assert out.startswith("## 相关历史需求")
    assert "…" in out


def test_format_retrieved_context_lists_items():
    from utils.retrieval import RetrievedItem

    items = [
        RetrievedItem(row_id=1, content="需求A", score=-1.0),
        RetrievedItem(row_id=2, content="需求B", score=-2.0),
    ]
    out = format_retrieved_context(items)
    assert "1. 需求A" in out and "2. 需求B" in out


# --------------------------------------------------------------- 降级

def test_search_on_missing_db_degrades_gracefully(tmp_path):
    """索引文件所在目录不可用时不能抛异常。"""
    index = FtsIndex(str(tmp_path / "sub" / "dir" / "idx.db"))
    # ensure_index 会自动建目录的情况不保证，这里只要求不抛异常
    try:
        ok = index.ensure_index()
        assert ok in (True, False)
    finally:
        index.close()


def test_default_top_k_value():
    assert DEFAULT_TOP_K > 0
