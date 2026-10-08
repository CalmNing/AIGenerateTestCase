"""Tests for the shared document-prefetch URL regexes."""
import asyncio

import utils.document_prefetch as dp
from utils.document_prefetch import (
    FEISHU_URL_RE,
    LANHU_URL_RE,
    FEISHU_SHEET_TOKEN_RE,
    _split_sheet_token,
    _sheet_rows_to_markdown,
    extract_document_section,
)


def test_feishu_content_route_returns_standard_response(monkeypatch):
    import app.deps
    import app.services.mcp_loader as mcp_loader
    from app.routes.document import FeishuContentRequest, fetch_feishu_content

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_get_db():
        yield DummySession()

    async def fake_fetch(_url, _configs):
        return "# Feature\nDetails"

    monkeypatch.setattr(app.deps, "get_db", fake_get_db)
    monkeypatch.setattr(mcp_loader, "load_user_mcp_configs", lambda _session, _user: [])
    monkeypatch.setattr(dp, "fetch_feishu_document", fake_fetch)

    result = asyncio.run(fetch_feishu_content(
        FeishuContentRequest(url="https://team.feishu.cn/docx/AbC123", include_boards=False),
        current_user=None,
    ))

    assert result.code == 200
    assert result.data.text == "# Feature\nDetails"
    assert result.data.document_id == "AbC123"


def test_feishu_wiki_url_matches_and_captures_token():
    m = FEISHU_URL_RE.search("see https://abc.feishu.cn/wiki/Xy12Z_token here")
    assert m is not None
    assert m.group(1) == "Xy12Z_token"


def test_feishu_docx_url_matches():
    m = FEISHU_URL_RE.search("https://team.feishu.cn/docx/AbC123")
    assert m is not None
    assert m.group(1) == "AbC123"


def test_lanhu_url_matches():
    m = LANHU_URL_RE.search("https://lanhuapp.com/web/#/item/project/detail?pid=1")
    assert m is not None


def test_no_match_plain_text():
    assert FEISHU_URL_RE.search("no url here") is None
    assert LANHU_URL_RE.search("no url here") is None


def test_extract_document_section_handles_feishu_plain_text_numbering():
    content = """方案3-安全AI相关
一、用户故事与范围限定
1.1 问题背景
问题陈述
使用量级与频率预估
1.3 价值点分析
二、功能模块拆解
2.1 模块列表
三、功能模块详细设计
3.1 视频AI同步"""

    section = extract_document_section(content, "用户故事与范围限定")

    assert section is not None
    assert "1.1 问题背景" in section
    assert "1.3 价值点分析" in section
    assert "二、功能模块拆解" not in section

    numbered_section = extract_document_section(content, "1.1 问题背景")
    assert numbered_section is not None
    assert "问题陈述" in numbered_section
    assert "1.3 价值点分析" not in numbered_section


def test_extract_document_section_includes_nested_headings_only():
    content = """# Overview
Intro
## API
API intro
### Auth
Token details
## UI
Screen details
# Appendix
Notes"""

    section = extract_document_section(content, "API")

    assert section == "## API\nAPI intro\n### Auth\nToken details"
    assert "Screen details" not in section
    assert extract_document_section(content, "Missing") is None


def test_extract_document_section_matches_case_insensitively():
    assert extract_document_section("# Feature Design\nDetails", "feature design") == "# Feature Design\nDetails"


def test_split_sheet_token_with_sheet_id():
    spread, sheet_id = _split_sheet_token("NLWUsvjrfhWUgVtIXD9cpm8jnQh_5iKWZs")
    assert spread == "NLWUsvjrfhWUgVtIXD9cpm8jnQh"
    assert sheet_id == "5iKWZs"


def test_split_sheet_token_plain():
    spread, sheet_id = _split_sheet_token("shtcnABCDEFG")
    assert spread == "shtcnABCDEFG"
    assert sheet_id is None


def test_sheet_token_regex_finds_embedded_block():
    text = "foo bar NLWUsvjrfhWUgVtIXD9cpm8jnQh_5iKWZs baz"
    found = FEISHU_SHEET_TOKEN_RE.findall(text)
    assert "NLWUsvjrfhWUgVtIXD9cpm8jnQh_5iKWZs" in found


def test_sheet_token_regex_ignores_plain_words():
    found = FEISHU_SHEET_TOKEN_RE.findall("普通中文段落没有任何token")
    assert found == []


def test_sheet_rows_to_markdown_header_and_row():
    rows = [
        ["模块", "优先级", "说明"],
        ["安全检查", "P0", "视频AI自动同步"],
        [None, None, None],
        ["隐患整改", "P1", "人工复核"],
    ]
    md = _sheet_rows_to_markdown(rows)
    assert md.startswith("| 模块 | 优先级 | 说明 |")
    assert "| --- | --- | --- |" in md
    assert "| 安全检查 | P0 | 视频AI自动同步 |" in md
    # 尾部全空行被去除
    assert "隐患整改" in md
    assert md.count("\n") == 3  # 表头 + 分隔 + 2 数据行，无空行


def test_sheet_rows_to_markdown_empty():
    assert _sheet_rows_to_markdown([]) == ""
    assert _sheet_rows_to_markdown([[None, None]]) == ""


def test_collect_sheet_markdown_merges_and_dedups(monkeypatch):
    async def fake_fetch(spread, sheet_id, app_id, app_secret):
        return f"| {spread} | {sheet_id} |"

    monkeypatch.setattr(dp, "fetch_feishu_sheet", fake_fetch)
    out = asyncio.run(dp._collect_sheet_markdown(
        ["NLWUsvjrfhWUgVtIXD9cpm8jnQh_5iKWZs",
         "NLWUsvjrfhWUgVtIXD9cpm8jnQh_5iKWZs",  # 重复，应去重
         "shtcnPlain"], "app_id", "app_sec"))
    # 2 个不同 token -> 2 个表格块；且输出中不得包含 token 标题行
    assert "### 嵌入表格" not in out
    assert out.count("|") >= 4  # 2 个表格各自有表头与分隔行
    assert "| NLWUsvjrfhWUgVtIXD9cpm8jnQh | 5iKWZs |" in out
    assert "| shtcnPlain | None |" in out


def test_discover_embedded_sheet_tokens_from_blocks(monkeypatch):
    async def fake_token(*args, **kwargs):
        return "fake-tenant-token"

    monkeypatch.setattr(dp, "_get_feishu_tenant_token", fake_token)

    payload = {"code": 0, "data": {"items": [
        {"block_type": 30, "sheet": {"token": "NLWUsvjrfhWUgVtIXD9cpm8jnQh_5iKWZs"}},
        {"block_type": 14, "bitable": {"token": "appX", "table_id": "tblY"}},
        {"block_type": 1},  # 普通段落块，应忽略
    ]}}

    class _FakeResp:
        def __init__(self, p):
            self._p = p

        def json(self):
            return self._p

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _FakeResp(payload)

    monkeypatch.setattr(dp.httpx, "AsyncClient", lambda *a, **k: _FakeClient())
    tokens = asyncio.run(dp._discover_embedded_sheet_tokens("docX", "app_id", "app_sec"))
    assert tokens == ["NLWUsvjrfhWUgVtIXD9cpm8jnQh_5iKWZs", "appX_tblY"]

