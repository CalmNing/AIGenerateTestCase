"""swagger-config 导入：识别、多文档合并、URL 加载与项目名推断。"""
import asyncio
import json

import httpx
import pytest

from app.services.api_test_tool import (
    endpoints_from_spec,
    load_openapi_from_url,
    merge_specs,
    parse_spec_text,
    suggest_project_name,
    swagger_config_docs,
)


def _spec(paths: dict, tag: str, server: str) -> dict:
    return {
        "openapi": "3.0.1",
        "info": {"title": tag, "version": "1.0"},
        "servers": [{"url": server}],
        "tags": [{"name": tag}],
        "paths": paths,
        "components": {"schemas": {f"{tag}Dto": {"type": "object"}}},
    }


def _path_get(summary: str) -> dict:
    return {
        "get": {
            "summary": summary,
            "operationId": summary,
            "responses": {"200": {"description": "ok"}},
        }
    }


def test_swagger_config_docs_detects_urls():
    text = json.dumps({"configUrl": "/x", "urls": [
        {"url": "/v3/api-docs/组一", "name": "组一"},
        {"url": "/v3/api-docs/group2", "name": "group2"},
    ]}, ensure_ascii=False)
    docs = swagger_config_docs(text)
    assert [d["name"] for d in docs] == ["组一", "group2"]


def test_swagger_config_docs_ignores_normal_spec():
    assert swagger_config_docs(json.dumps(_spec({"/a": _path_get("a")}, "t", "http://x"))) == []
    assert swagger_config_docs("not json") == []
    assert swagger_config_docs(json.dumps({"urls": [{"no_url": 1}]})) == []


def test_merge_specs_unions_paths_tags_components_servers():
    first = _spec({"/a": _path_get("A")}, "t1", "http://x")
    second = _spec({"/b": _path_get("B")}, "t2", "http://y")
    merged = merge_specs([first, second])

    assert set(merged["paths"]) == {"/a", "/b"}
    assert [t["name"] for t in merged["tags"]] == ["t1", "t2"]
    assert merged["components"]["schemas"] == {"t1Dto": {"type": "object"}, "t2Dto": {"type": "object"}}
    assert [s["url"] for s in merged["servers"]] == ["http://x", "http://y"]
    assert len(endpoints_from_spec(merged, project_id=1, user_id=None)) == 2


def test_merge_specs_keeps_first_on_duplicate_path_method():
    first = _spec({"/a": {"get": _path_get("A")["get"], "post": _path_get("A-post")["get"]}}, "t1", "http://x")
    second = _spec({"/a": _path_get("B")}, "t2", "http://x")
    merged = merge_specs([first, second])
    assert merged["paths"]["/a"]["get"]["summary"] == "A"
    assert merged["paths"]["/a"]["post"]["summary"] == "A-post"


def test_merge_specs_requires_non_empty():
    with pytest.raises(ValueError):
        merge_specs([])


def test_load_openapi_from_url_follows_config():
    config = {"urls": [
        {"url": "/contract/v3/api-docs/接口文档", "name": "接口文档"},
        {"url": "/contract/v3/api-docs/流水号接口文档", "name": "流水号接口文档"},
    ]}
    base = "http://host:18896"
    specs = {
        f"{base}/contract/v3/api-docs/接口文档": _spec({"/a": _path_get("A")}, "t1", f"{base}/contract"),
        f"{base}/contract/v3/api-docs/流水号接口文档": _spec({"/b": _path_get("B")}, "t2", f"{base}/contract"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote
        url = unquote(str(request.url).split("?")[0])
        if url.endswith("/swagger-config"):
            return httpx.Response(200, json=config)
        return httpx.Response(200, json=specs[url])

    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await load_openapi_from_url(client, f"{base}/contract/v3/api-docs/swagger-config")

    raw, docs = asyncio.run(run())
    assert [d["name"] for d in docs] == ["接口文档", "流水号接口文档"]
    spec = parse_spec_text(raw)
    assert set(spec["paths"]) == {"/a", "/b"}


def test_load_openapi_from_url_passthrough_plain_spec():
    spec = _spec({"/a": _path_get("A")}, "t1", "http://x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=spec)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await load_openapi_from_url(client, "http://x/v3/api-docs")

    raw, docs = asyncio.run(run())
    assert docs == []
    assert parse_spec_text(raw)["paths"] == {"/a": spec["paths"]["/a"]}


def test_suggest_project_name_strips_swagger_suffixes():
    assert suggest_project_name("http://192.168.13.178:18896/contract/v3/api-docs/swagger-config", True) == "contract"
    assert suggest_project_name("http://192.168.13.178:18896/contract/v3/api-docs/", True) == "contract"
    assert suggest_project_name("http://192.168.13.178:18896/swagger-config", True) == "192.168.13.178"
    from urllib.parse import quote
    encoded = quote("接口文档")
    assert suggest_project_name(f"http://h/api-docs/{encoded}", False) == "接口文档"
