"""Tests for the shared document-prefetch URL regexes."""
from utils.document_prefetch import FEISHU_URL_RE, LANHU_URL_RE


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
