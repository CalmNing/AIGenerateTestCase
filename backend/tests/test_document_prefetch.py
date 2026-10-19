"""Tests for the shared document-prefetch URL regexes."""
import asyncio
import base64
import io

import pytest

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

# ---------------------------------------------------------------------------
# 飞书文档内嵌图片（多模态描述）
# ---------------------------------------------------------------------------

def _text_block(block_id: str, content: str, children: list | None = None) -> dict:
    block = {
        "block_id": block_id,
        "block_type": 2,
        "text": {"elements": [{"text_run": {"content": content}}]},
    }
    if children:
        block["children"] = children
    return block


def _patch_image_config(monkeypatch, *, enabled: bool = True,
                        max_images: int = 20, max_chars: int = 300):
    monkeypatch.setattr(dp, "_VISION_UNSUPPORTED_CACHE", set())
    monkeypatch.setattr(dp.config_manager, "is_feishu_image_enabled", lambda: enabled)
    monkeypatch.setattr(dp.config_manager, "get_feishu_image_max", lambda: max_images)
    monkeypatch.setattr(dp.config_manager, "get_feishu_image_max_chars", lambda: max_chars)
    monkeypatch.setattr(dp.config_manager, "get_vision_model", lambda: "")
    monkeypatch.setattr(dp.config_manager, "get_vision_api_base_url", lambda: "")
    monkeypatch.setattr(dp.config_manager, "get_vision_api_key", lambda: "")
    monkeypatch.setattr(dp.config_manager, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(dp.config_manager, "get_api_base_url", lambda: "https://example.test")
    monkeypatch.setattr(dp.config_manager, "get_api_model", lambda: "test-model")


def test_extract_image_blocks_in_document_order():
    items = [
        {"block_id": "doc", "block_type": 1, "children": ["t1", "img1", "sec", "img2"]},
        _text_block("t1", "第一段"),
        {"block_id": "img1", "block_type": 27, "image": {"token": "tok1", "width": 100, "height": 50}},
        _text_block("sec", "第二段", children=[]),
        {"block_id": "img2", "block_type": 27, "image": {"token": "tok2", "width": 200, "height": 100}},
    ]
    records = dp._extract_image_blocks(items, "doc")
    assert [r["index"] for r in records] == [1, 2]
    assert [r["token"] for r in records] == ["tok1", "tok2"]
    assert records[0]["context"].endswith("第一段")
    assert records[1]["context"].endswith("第二段")
    assert records[1]["width"] == 200


def test_extract_image_blocks_without_root_uses_list_order():
    items = [
        {"block_id": "img1", "block_type": 27, "image": {"token": "tok1"}},
        _text_block("t1", "后一段"),
        {"block_id": "img2", "block_type": 27, "image": {"token": "tok2"}},
    ]
    records = dp._extract_image_blocks(items, "")
    assert [r["token"] for r in records] == ["tok1", "tok2"]
    assert records[0]["context"] == ""
    assert records[1]["context"].endswith("后一段")


def test_extract_image_blocks_ignores_image_without_token():
    items = [
        {"block_id": "img1", "block_type": 27, "image": {}},
        {"block_id": "img2", "block_type": 27, "image": {"token": "tok2"}},
    ]
    assert [r["token"] for r in dp._extract_image_blocks(items)] == ["tok2"]


def test_format_image_markdown_renders_descriptions():
    records = [
        {"index": 1, "width": 522, "height": 400, "context": "若有修改值，气泡弹窗支持选择",
         "description": "弹窗：按发起值进行发起 / 按当前数据进行发起"},
        {"index": 2, "width": 0, "height": 0, "context": "", "description": "审批流流程图"},
        {"index": 3, "description": ""},
    ]
    md = dp._format_image_markdown(records)
    assert md.startswith("## 文档图片（共 2 张")
    assert "### 图片 1（522×400）" in md
    assert "> 图片前的正文：" in md
    assert "按发起值进行发起" in md
    assert "### 图片 3" not in md
    assert md.count("### 图片") == 2


def test_format_image_markdown_truncates_description():
    records = [{"index": 1, "description": "描" * 50}]
    md = dp._format_image_markdown(records, max_chars=10)
    assert "描" * 10 + "…" in md


def test_format_image_markdown_empty():
    assert dp._format_image_markdown([]) == ""
    assert dp._format_image_markdown([{"index": 1, "description": "  "}]) == ""


def test_encode_image_downscales_with_pillow():
    pytest.importorskip("PIL")
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (3000, 1000), "red").save(buffer, "PNG")
    encoded = dp._encode_image(buffer.getvalue())
    assert encoded is not None
    mime, b64 = encoded
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(base64.b64decode(b64))) as image:
        assert max(image.size) <= dp._VISION_MAX_SIDE


def test_encode_image_returns_none_for_non_image():
    assert dp._encode_image(b"not an image") is None


def test_collect_image_markdown_disabled(monkeypatch):
    _patch_image_config(monkeypatch, enabled=False)
    out = asyncio.run(dp._collect_image_markdown("doc", "app", "sec", {}))
    assert out == ""


def test_collect_image_markdown_success(monkeypatch):
    _patch_image_config(monkeypatch)

    async def fake_token(*_args, **_kwargs):
        return "tenant-token"

    async def fake_discover(*_args, **_kwargs):
        return [
            {"index": 1, "block_id": "b1", "token": "tok1", "width": 10, "height": 20,
             "context": "第一段"},
            {"index": 2, "block_id": "b2", "token": "tok2", "width": 0, "height": 0,
             "context": ""},
        ]

    async def fake_download(_token, _tenant_token):
        return b"\x89PNG\r\n\x1a\nfake"

    def fake_encode(_data):
        return "image/png", "ZmFrZQ=="

    prompts: list[str] = []

    async def fake_vision(_config, _mime, _b64, prompt):
        prompts.append(prompt)
        return "图片描述"

    monkeypatch.setattr(dp, "_get_feishu_tenant_token", fake_token)
    monkeypatch.setattr(dp, "_discover_image_blocks", fake_discover)
    monkeypatch.setattr(dp, "_download_image_bytes", fake_download)
    monkeypatch.setattr(dp, "_encode_image", fake_encode)
    monkeypatch.setattr(dp, "_post_vision_chat", fake_vision)

    out = asyncio.run(dp._collect_image_markdown(
        "doc", "app", "sec", {"api_key": "k", "api_base_url": "https://example.test",
                              "api_model": "vl"}))
    assert "## 文档图片（共 2 张" in out
    assert out.count("### 图片") == 2
    assert "图片描述" in out
    # 有上下文的图片应把上下文带进视觉 prompt
    assert any("第一段" in p for p in prompts)


def test_collect_image_markdown_respects_max_images(monkeypatch):
    _patch_image_config(monkeypatch, max_images=1)

    async def fake_token(*_args, **_kwargs):
        return "tenant-token"

    async def fake_discover(*_args, **_kwargs):
        return [
            {"index": 1, "token": "tok1", "width": 0, "height": 0, "context": ""},
            {"index": 2, "token": "tok2", "width": 0, "height": 0, "context": ""},
        ]

    async def fake_download(_token, _tenant_token):
        return b"data"

    async def fake_vision(*_args, **_kwargs):
        return "desc"

    monkeypatch.setattr(dp, "_get_feishu_tenant_token", fake_token)
    monkeypatch.setattr(dp, "_discover_image_blocks", fake_discover)
    monkeypatch.setattr(dp, "_download_image_bytes", fake_download)
    monkeypatch.setattr(dp, "_post_vision_chat", fake_vision)
    monkeypatch.setattr(dp, "_encode_image", lambda _data: ("image/png", "ZmFrZQ=="))

    out = asyncio.run(dp._collect_image_markdown("doc", "app", "sec", {}))
    assert out.count("### 图片") == 1


def test_collect_image_markdown_skips_when_vision_unavailable(monkeypatch):
    _patch_image_config(monkeypatch)

    async def fake_token(*_args, **_kwargs):
        return "tenant-token"

    async def fake_discover(*_args, **_kwargs):
        return [{"index": 1, "token": "tok1", "width": 0, "height": 0, "context": ""}]

    async def fake_download(*_args, **_kwargs):
        return b"data"

    async def fake_vision(*_args, **_kwargs):
        raise dp._VisionUnavailable("HTTP 400")

    monkeypatch.setattr(dp, "_get_feishu_tenant_token", fake_token)
    monkeypatch.setattr(dp, "_discover_image_blocks", fake_discover)
    monkeypatch.setattr(dp, "_download_image_bytes", fake_download)
    monkeypatch.setattr(dp, "_post_vision_chat", fake_vision)
    monkeypatch.setattr(dp, "_encode_image", lambda _data: ("image/png", "ZmFrZQ=="))

    assert asyncio.run(dp._collect_image_markdown("doc", "app", "sec", {})) == ""

def test_resolve_vision_config_priority(monkeypatch):
    monkeypatch.setattr(dp.config_manager, "get_vision_model", lambda: "vision-model")
    monkeypatch.setattr(dp.config_manager, "get_vision_api_base_url", lambda: "https://vision.test")
    monkeypatch.setattr(dp.config_manager, "get_vision_api_key", lambda: "vision-key")
    monkeypatch.setattr(dp.config_manager, "get_api_key", lambda: "main-key")
    monkeypatch.setattr(dp.config_manager, "get_api_base_url", lambda: "https://main.test")
    monkeypatch.setattr(dp.config_manager, "get_api_model", lambda: "main-model")

    resolved = dp._resolve_vision_config({"api_key": "req-key", "api_base_url": "https://req.test",
                                         "api_model": "req-model"})
    assert resolved == {
        "api_key": "vision-key",
        "api_base_url": "https://vision.test",
        "api_model": "vision-model",
        "api_proxy_url": "",
    }

    # vision_* 为空时回退到调用方传入的当前模型配置
    monkeypatch.setattr(dp.config_manager, "get_vision_model", lambda: "")
    monkeypatch.setattr(dp.config_manager, "get_vision_api_base_url", lambda: "")
    monkeypatch.setattr(dp.config_manager, "get_vision_api_key", lambda: "")
    resolved = dp._resolve_vision_config({"api_key": "req-key", "api_base_url": "https://req.test",
                                         "api_model": "req-model"})
    assert resolved["api_key"] == "req-key"
    assert resolved["api_model"] == "req-model"

    # 都没有时回退到 config.json 主模型；无 key 则不可用
    resolved = dp._resolve_vision_config(None)
    assert resolved["api_key"] == "main-key"
    assert resolved["api_model"] == "main-model"
    monkeypatch.setattr(dp.config_manager, "get_api_key", lambda: "")
    assert dp._resolve_vision_config(None) is None


def test_get_feishu_image_max_capped(monkeypatch):
    monkeypatch.setitem(dp.config_manager.config, "feishu_image_max", 9999)
    assert dp.config_manager.get_feishu_image_max() == 50
    monkeypatch.setitem(dp.config_manager.config, "feishu_image_max", 0)
    assert dp.config_manager.get_feishu_image_max() == 1


def test_collect_image_markdown_probes_only_first_image_when_vision_unsupported(monkeypatch):
    _patch_image_config(monkeypatch)
    downloads: list[str] = []
    vision_calls = 0

    async def fake_token(*_args, **_kwargs):
        return "tenant-token"

    async def fake_discover(*_args, **_kwargs):
        return [
            {"index": 1, "block_id": "b1", "token": "tok1", "width": 0, "height": 0, "context": ""},
            {"index": 2, "block_id": "b2", "token": "tok2", "width": 0, "height": 0, "context": ""},
            {"index": 3, "block_id": "b3", "token": "tok3", "width": 0, "height": 0, "context": ""},
        ]

    async def fake_download(token, _tenant_token):
        downloads.append(token)
        return b"data"

    async def fake_vision(*_args, **_kwargs):
        nonlocal vision_calls
        vision_calls += 1
        raise dp._VisionUnavailable("HTTP 400")

    monkeypatch.setattr(dp, "_get_feishu_tenant_token", fake_token)
    monkeypatch.setattr(dp, "_discover_image_blocks", fake_discover)
    monkeypatch.setattr(dp, "_download_image_bytes", fake_download)
    monkeypatch.setattr(dp, "_post_vision_chat", fake_vision)
    monkeypatch.setattr(dp, "_encode_image", lambda _data: ("image/png", "ZmFrZQ=="))

    assert asyncio.run(dp._collect_image_markdown("doc", "app", "sec", {})) == ""
    # 文本模型场景：只下载/调用第一张图，且负缓存命中后不再重复尝试
    assert downloads == ["tok1"]
    assert vision_calls == 1
    assert asyncio.run(dp._collect_image_markdown("doc", "app", "sec", {})) == ""
    assert downloads == ["tok1"]
    assert vision_calls == 1


def test_collect_image_markdown_survives_single_image_failure(monkeypatch):
    _patch_image_config(monkeypatch)

    async def fake_token(*_args, **_kwargs):
        return "tenant-token"

    async def fake_discover(*_args, **_kwargs):
        return [
            {"index": 1, "block_id": "b1", "token": "tok1", "width": 0, "height": 0, "context": ""},
            {"index": 2, "block_id": "b2", "token": "tok2", "width": 0, "height": 0, "context": ""},
        ]

    async def fake_download(*_args, **_kwargs):
        return b"data"

    calls = {"n": 0}

    async def fake_vision(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "第一张描述"
        raise RuntimeError("network hiccup")

    monkeypatch.setattr(dp, "_get_feishu_tenant_token", fake_token)
    monkeypatch.setattr(dp, "_discover_image_blocks", fake_discover)
    monkeypatch.setattr(dp, "_download_image_bytes", fake_download)
    monkeypatch.setattr(dp, "_post_vision_chat", fake_vision)
    monkeypatch.setattr(dp, "_encode_image", lambda _data: ("image/png", "ZmFrZQ=="))

    out = asyncio.run(dp._collect_image_markdown("doc", "app", "sec", {}))
    assert "## 文档图片（共 1 张" in out
    assert "第一张描述" in out

def test_is_deepseek_endpoint():
    assert dp._is_deepseek_endpoint("https://api.deepseek.com")
    assert dp._is_deepseek_endpoint("https://api.deepseek.com/v1")
    assert not dp._is_deepseek_endpoint("https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert not dp._is_deepseek_endpoint("")


def test_vision_payload_disables_thinking_for_deepseek():
    payload = dp._build_vision_payload(
        {"api_model": "deepseek-v4-flash", "api_base_url": "https://api.deepseek.com"},
        "image/jpeg", "ZmFrZQ==", "描述这张图",
    )
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == dp._VISION_MAX_TOKENS
    content = payload["messages"][0]["content"]
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,ZmFrZQ=="


def test_vision_payload_keeps_thinking_for_other_providers():
    payload = dp._build_vision_payload(
        {"api_model": "qwen-vl-max",
         "api_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        "image/png", "ZmFrZQ==", "描述这张图",
    )
    assert "thinking" not in payload




