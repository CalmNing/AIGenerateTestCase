"""Tests for Mock 接口平台 <-> Mock 流量代理 打通。

覆盖：接口地址绑定匹配、服务前缀剥离、主机定向优先级、尾斜杠规则语义、
代理命中只写一条日志、解析异常回退透传、覆盖率与预览接口。
纯单元测试，内存 SQLite，不触网。
"""
import asyncio
import json

import pytest
from sqlmodel import Session, select

import app.routes.mock_proxy as mock_proxy_routes
import app.routes.mock_server as mock_server_mod
import app.services.mock_proxy as proxy_mod
from app.routes.mock_proxy import PreviewRequest, get_platform_mocks, preview_mock
from app.routes.mock_server import resolve_mock_response
from app.services.mock_matcher import (
    compile_path_pattern,
    match_address,
    match_config_path,
    match_host,
    normalize_mock_path,
    path_candidates,
    path_matches,
    parse_address,
    rule_fixed_prefix,
    strip_service_prefix,
)
from db.models import MockConfig, MockLog, MockProxyRule


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _FakeWriter:
    """收集代理写回的响应字节。"""

    def __init__(self):
        self.data = bytearray()

    def write(self, chunk):
        self.data.extend(chunk)

    async def drain(self):
        return None

    def close(self):
        return None

    def get_extra_info(self, key):
        return ("127.0.0.1", 12345)

    def status_line(self):
        return bytes(self.data).split(b"\r\n", 1)[0].decode("latin-1")


def _req(method, host, path, query=None, body=None):
    return proxy_mod.ParsedRequest(method, "http", host, 80, path, query or {}, {}, body)


@pytest.fixture()
def patched(monkeypatch, db_engine):
    """把代理与 mock_server 都指向内存库。"""
    monkeypatch.setattr(proxy_mod, "engine", db_engine)
    monkeypatch.setattr(mock_server_mod, "engine", db_engine)
    monkeypatch.setattr(mock_proxy_routes, "SessionDep", object)
    return db_engine


# ---------------------------------------------------------------------------
# 接口地址解析与匹配
# ---------------------------------------------------------------------------

class TestParseAddress:
    def test_full_url(self):
        parsed = parse_address("http://192.168.13.178:18896/contract/update")
        assert parsed.host == "192.168.13.178"
        assert parsed.path == "/contract/update"

    def test_path_only(self):
        parsed = parse_address("/contract/update")
        assert parsed.host is None
        assert parsed.path == "/contract/update"

    def test_host_with_path(self):
        parsed = parse_address("192.168.13.178:18896/contract/update")
        assert parsed.host == "192.168.13.178"
        assert parsed.path == "/contract/update"

    def test_host_only_matches_all_paths(self):
        parsed = parse_address("api.example.com")
        assert parsed.host == "api.example.com"
        assert parsed.path == "/"

    def test_empty_returns_none(self):
        assert parse_address("") is None


class TestMatchAddress:
    def test_full_url_binds_real_request(self):
        ok, _ = match_address(
            "http://192.168.13.178:18896/contract/update",
            "192.168.13.178", "/contract/update",
        )
        assert ok is True

    def test_host_mismatch_rejected(self):
        ok, _ = match_address(
            "http://192.168.13.178:18896/contract/update",
            "10.0.0.1", "/contract/update",
        )
        assert ok is False

    def test_wildcard_host(self):
        ok, _ = match_address("*.example.com/users/{id}", "api.example.com", "/users/9")
        assert ok is True and _["id"] == "9"

    def test_path_param_bound(self):
        ok, params = match_address("/users/{id}", "api.example.com", "/users/42")
        assert ok is True
        assert params == {"id": "42"}

    def test_prefix_alignment_across_gateways(self):
        # 配置内网地址 /contract/token/get，真实流量走网关 /service/contract/token/get
        ok, _ = match_address(
            "/contract/token/get", "a.sg.luban.cn",
            "/service/contract/token/get", "/service/contract",
        )
        assert ok is True

    def test_single_segment_not_overmatched(self):
        # /get 这类单段地址不能被 /a/b/get 过度匹配
        ok, _ = match_address("/get", "x.com", "/a/b/get")
        assert ok is False

    def test_other_path_not_matched(self):
        ok, _ = match_address("/v1/add", "x.com", "/v2/add")
        assert ok is False


# ---------------------------------------------------------------------------
# 服务前缀与规则语义
# ---------------------------------------------------------------------------

class TestServicePrefix:
    def test_strip_matching_prefix(self):
        assert strip_service_prefix("/contract/update", "/contract") == "/update"

    def test_strip_exact_prefix_returns_root(self):
        assert strip_service_prefix("/contract", "/contract") == "/"

    def test_strip_unrelated_returns_none(self):
        assert strip_service_prefix("/other/x", "/contract") is None

    def test_empty_prefix_returns_none(self):
        assert strip_service_prefix("/contract/x", "") is None

    def test_candidates_order(self):
        assert path_candidates("/contract/update", "/contract") == ["/contract/update", "/update"]

    def test_candidates_without_prefix(self):
        assert path_candidates("/other/x", "/contract") == ["/other/x"]

    def test_rule_fixed_prefix(self):
        assert rule_fixed_prefix("/contract/") == "/contract"
        assert rule_fixed_prefix("/service/contract/*") == "/service/contract"
        assert rule_fixed_prefix("/users/{id}") == "/users"
        assert rule_fixed_prefix("*") == ""

    def test_trailing_slash_is_directory_prefix(self):
        # 曾被编译成 /contract/ 精确串，导致该规则永远不命中
        pattern = compile_path_pattern("/contract/")
        assert path_matches(pattern, "/contract/update") is True
        assert path_matches(pattern, "/contractual/update") is False

    def test_existing_pattern_semantics_unchanged(self):
        assert path_matches(compile_path_pattern(""), "/any/path") is True
        assert path_matches(compile_path_pattern("*"), "/any") is True
        assert path_matches(compile_path_pattern("/api/user/*"), "/api/user/1") is True
        assert path_matches(compile_path_pattern("/users/{id}"), "/users/1") is True
        assert path_matches(compile_path_pattern("/a.b/c"), "/axb/c") is False


class TestMatchConfigPath:
    def test_api_mock_prefix_both_sides(self):
        assert match_config_path("/users/{id}", "/api/mock/users/7") == (True, {"id": "7"})
        assert match_config_path("/api/mock/users/{id}", "/users/7") == (True, {"id": "7"})

    def test_service_prefix_aware(self):
        ok, params = match_config_path("/detail/{id}", "/contract/detail/9", "/contract")
        assert ok is True and params == {"id": "9"}

    def test_normalize(self):
        assert normalize_mock_path("/api/mock/x?y=1") == "/x"
        assert normalize_mock_path("") == "/"


# ---------------------------------------------------------------------------
# 平台解析：地址绑定 / 主机定向 / 项目前缀
# ---------------------------------------------------------------------------

RESPONSE_SCHEMA = {"type": "object", "properties": {"id": {"type": "integer"}}}


class TestResolveWithAddressBinding:
    def test_proxy_url_hit_returns_mock(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(
                name="合同-新增", method="POST", url_path="/update",
                response_body='{"ok": true}', enabled=True,
                proxy_urls=["http://192.168.13.178:18896/contract/update"],
            ))
            session.commit()

        result = _run(resolve_mock_response(
            "/contract/update", "POST", {}, "{}", {},
            host="192.168.13.178",
            path_candidates=["/contract/update"],
            log_label="[代理→Mock:合同]", save_log=False,
        ))
        assert result is not None
        assert result.status_code == 200
        assert result.config_name.startswith("[代理→Mock:合同]")
        assert result.matched_url == "http://192.168.13.178:18896/contract/update"

    def test_proxy_url_not_hit_returns_none(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(
                name="合同-新增", method="POST", url_path="/update",
                response_body="{}", enabled=True,
                proxy_urls=["/contract/update"],
            ))
            session.commit()

        result = _run(resolve_mock_response(
            "/other/update", "POST", {}, None, {},
            host="192.168.13.178", path_candidates=["/other/update"],
        ))
        assert result is None

    def test_host_bound_beats_generic(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(name="GENERIC", method="POST", url_path="/detail/{id}",
                                  response_body='{"v": 1}', enabled=True))
            session.add(MockConfig(name="HOSTBOUND", method="POST", url_path="/detail/{id}",
                                  response_body='{"v": 2}', enabled=True,
                                  hosts=["*.sg.luban.cn"]))
            session.commit()

        hit = _run(resolve_mock_response(
            "/service/contract/detail/8", "POST", {}, None, {},
            host="a.sg.luban.cn",
            path_candidates=["/service/contract/detail/8", "/detail/8"],
        ))
        assert hit is not None and hit.config_name == "HOSTBOUND"

        other = _run(resolve_mock_response(
            "/contract/detail/8", "POST", {}, None, {},
            host="192.168.13.178",
            path_candidates=["/contract/detail/8", "/detail/8"],
        ))
        assert other is not None and other.config_name == "GENERIC"

    def test_candidates_enable_prefix_stripped_match(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(name="getToken", method="POST", url_path="/token/get",
                                  response_body='{"t": 1}', enabled=True))
            session.commit()

        hit = _run(resolve_mock_response(
            "/service/contract/token/get", "POST", {}, None, {},
            host="a.sg.luban.cn",
            path_candidates=path_candidates("/service/contract/token/get", "/service/contract"),
        ))
        assert hit is not None and hit.config_name == "getToken"

    def test_save_log_false_skips_log(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(name="m", method="GET", url_path="/x",
                                  response_body="{}", enabled=True))
            session.commit()

        _run(resolve_mock_response("/x", "GET", {}, None, {}, host="h.com", save_log=False))
        with Session(patched) as session:
            assert session.exec(select(MockLog)).all() == []


# ---------------------------------------------------------------------------
# 代理端到端
# ---------------------------------------------------------------------------

class TestProxyPipeline:
    def test_rule_hit_writes_exactly_one_log(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(name="getToken", method="POST", url_path="/token/get",
                                  status_code=201, response_body='{"t": 1}', enabled=True))
            session.add(MockProxyRule(name="网关", host_pattern="a.sg.luban.cn",
                                     path_pattern="", service_prefix="/service/contract"))
            session.commit()

        writer = _FakeWriter()
        _run(proxy_mod.handle_plain_request(
            _req("POST", "a.sg.luban.cn", "/service/contract/token/get"), writer
        ))
        assert writer.status_line().startswith("HTTP/1.1 201")

        with Session(patched) as session:
            logs = session.exec(select(MockLog)).all()
        assert len(logs) == 1
        assert logs[0].config_id is not None
        assert logs[0].config_name.startswith("[代理→Mock:")

    def test_address_binding_intercepts_without_rule(self, patched):
        # 用户只配了「接口地址 + 响应」，没建转发规则，也应被拦住
        with Session(patched) as session:
            session.add(MockConfig(
                name="合同-新增", method="POST", url_path="/update",
                response_body='{"ok": true}', enabled=True,
                proxy_urls=["192.168.13.178/contract/update"],
            ))
            session.commit()

        writer = _FakeWriter()
        _run(proxy_mod.handle_plain_request(
            _req("POST", "192.168.13.178", "/contract/update"), writer
        ))
        assert writer.status_line().startswith("HTTP/1.1 200")
        with Session(patched) as session:
            logs = session.exec(select(MockLog)).all()
        assert len(logs) == 1 and logs[0].config_id is not None

    def test_resolution_error_falls_back_to_upstream(self, patched, monkeypatch):
        with Session(patched) as session:
            session.add(MockConfig(name="boom", method="GET", url_path="/boom",
                                  response_body="{}", enabled=True))
            session.add(MockProxyRule(name="r", host_pattern="api.example.com",
                                     path_pattern="", service_prefix=""))
            session.commit()

        async def _raise(*args, **kwargs):
            raise RuntimeError("matcher exploded")

        async def _fake_upstream(*args, **kwargs):
            return 200, {"Content-Type": "application/json"}, b'{"real": true}'

        monkeypatch.setattr(mock_server_mod, "resolve_mock_response", _raise)
        monkeypatch.setattr(proxy_mod, "forward_upstream", _fake_upstream)

        writer = _FakeWriter()
        _run(proxy_mod.handle_plain_request(
            _req("GET", "api.example.com", "/boom"), writer
        ))
        # 不应直接 502，而是透传上游
        assert writer.status_line().startswith("HTTP/1.1 200")
        assert b'"real": true' in bytes(writer.data)


# ---------------------------------------------------------------------------
# 覆盖率与预览
# ---------------------------------------------------------------------------

class TestCoverageAndPreview:
    def test_platform_mocks_reports_coverage(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(name="covered", method="POST", url_path="/update",
                                  response_body="{}", enabled=True))
            session.add(MockConfig(name="bound", method="POST", url_path="/add",
                                  response_body="{}", enabled=True,
                                  proxy_urls=["/contract/add"]))
            session.add(MockConfig(name="uncovered", method="POST", url_path="/nowhere",
                                  response_body="{}", enabled=True))
            session.add(MockProxyRule(name="内网", host_pattern="192.168.13.178",
                                     path_pattern="/contract/", service_prefix="/contract"))
            session.commit()

        with Session(patched) as session:
            res = get_platform_mocks(session=session, user=None)
        items = {item["name"]: item for item in res.data["items"]}
        assert items["covered"]["covered"] is True
        assert items["covered"]["bound"] is False
        assert items["bound"]["bound"] is True
        # 聚合规则 /contract/ 理论上也能路由到 /nowhere（请求 /contract/nowhere），
        # 因此「规则可达」为 True，但并没有显式地址绑定。
        assert items["uncovered"]["covered"] is True
        assert items["uncovered"]["bound"] is False
        assert res.data["covered_configs"] == 3
        assert res.data["enabled_configs"] == 3

    def test_platform_mocks_uncovered_without_rule(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(name="lonely", method="POST", url_path="/update",
                                  response_body="{}", enabled=True))
            session.commit()
        with Session(patched) as session:
            res = get_platform_mocks(session=session, user=None)
        assert res.data["items"][0]["covered"] is False
        assert res.data["covered_configs"] == 0

    def test_preview_hit_does_not_write_log(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(name="合同-新增", method="POST", url_path="/update",
                                  response_body='{"ok": true}', enabled=True))
            session.add(MockProxyRule(name="内网", host_pattern="192.168.13.178",
                                     path_pattern="/contract/", service_prefix="/contract"))
            session.commit()

        with Session(patched) as session:
            res = _run(preview_mock(
                session=session, user=None,
                body=PreviewRequest(host="192.168.13.178", method="POST", path="/contract/update"),
            ))
        assert res.data["intercepted"] is True
        assert res.data["matched"] is True
        assert res.data["config_name"].endswith("合同-新增")
        assert res.data["source"] == "config"

        with Session(patched) as session:
            assert session.exec(select(MockLog)).all() == []

    def test_preview_miss_reports_intercepted_but_unmatched(self, patched):
        with Session(patched) as session:
            session.add(MockProxyRule(name="内网", host_pattern="192.168.13.178",
                                     path_pattern="", service_prefix="/contract"))
            session.commit()

        with Session(patched) as session:
            res = _run(preview_mock(
                session=session, user=None,
                body=PreviewRequest(host="192.168.13.178", method="POST", path="/contract/nothing"),
            ))
        assert res.data["intercepted"] is True
        assert res.data["matched"] is False


class TestServicePrefixNormalization:
    def test_normalize(self):
        assert mock_proxy_routes._normalize_service_prefix("/contract/") == "/contract"
        assert mock_proxy_routes._normalize_service_prefix("contract") == "/contract"
        assert mock_proxy_routes._normalize_service_prefix("") == ""

    def test_reject_wildcards(self):
        assert mock_proxy_routes._normalize_service_prefix("/{id}") is None
        assert mock_proxy_routes._normalize_service_prefix("/a/*") is None


# ---------------------------------------------------------------------------
# HTTPS 解密范围（地址绑定主机也要 MITM）
# ---------------------------------------------------------------------------

class TestMitmScope:
    def test_rule_host_enables_mitm(self, patched):
        with Session(patched) as session:
            session.add(MockProxyRule(name="r", host_pattern="api.example.com",
                                     path_pattern="", service_prefix=""))
            session.commit()
        assert proxy_mod.host_has_any_rule("api.example.com") is True
        assert proxy_mod.host_has_any_rule("other.com") is False

    def test_address_bound_host_enables_mitm_without_rule(self, patched):
        # 用户只配了接口地址、没建规则：该主机的 HTTPS 也必须解密，否则拦不到
        with Session(patched) as session:
            session.add(MockConfig(
                name="m", method="POST", url_path="/update", response_body="{}",
                enabled=True, proxy_urls=["https://192.168.13.178:18896/contract/update"],
            ))
            session.commit()
        assert proxy_mod.host_has_any_rule("192.168.13.178") is True
        assert proxy_mod.host_has_any_rule("10.0.0.9") is False

    def test_disabled_config_does_not_enable_mitm(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(
                name="m", method="POST", url_path="/update", response_body="{}",
                enabled=False, proxy_urls=["https://192.168.13.178/contract/update"],
            ))
            session.commit()
        assert proxy_mod.host_has_any_rule("192.168.13.178") is False

    def test_wildcard_bound_host_enables_mitm(self, patched):
        with Session(patched) as session:
            session.add(MockConfig(
                name="m", method="GET", url_path="/x", response_body="{}",
                enabled=True, proxy_urls=["*.sg.luban.cn/x"],
            ))
            session.commit()
        assert proxy_mod.host_has_any_rule("a.sg.luban.cn") is True
        assert proxy_mod.host_has_any_rule("sg.luban.cn") is False
