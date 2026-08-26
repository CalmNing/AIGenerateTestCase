"""Tests for Mock 正向代理：主机/路径匹配引擎、规则查找、共享 Mock 解析函数、证书模块。

纯单元测试，使用内存 SQLite（conftest.db_engine），不发起真实网络请求。
"""
import asyncio
import json

import pytest
from sqlmodel import Session

import app.routes.mock_server as mock_server_mod
import app.services.mock_proxy as proxy_mod
from app.routes.mock_server import resolve_mock_response
from app.services.mock_proxy import (
    compile_path_pattern,
    find_matching_rule,
    match_host,
    path_matches,
)
from db.models import ApiEndpoint, ApiProject, MockConfig, MockProxyRule


# ---------------------------------------------------------------------------
# 主机匹配
# ---------------------------------------------------------------------------

class TestMatchHost:
    def test_exact_domain(self):
        assert match_host("api.example.com", "api.example.com") is True

    def test_exact_domain_case_insensitive(self):
        assert match_host("API.Example.COM", "api.example.com") is True

    def test_ip_exact(self):
        assert match_host("127.0.0.1", "127.0.0.1") is True
        assert match_host("127.0.0.1", "127.0.0.2") is False

    def test_wildcard_matches_subdomains(self):
        assert match_host("*.example.com", "a.example.com") is True
        assert match_host("*.example.com", "a.b.example.com") is True

    def test_wildcard_does_not_match_root_domain(self):
        assert match_host("*.example.com", "example.com") is False

    def test_wildcard_does_not_match_other_suffix(self):
        assert match_host("*.example.com", "a.example.org") is False

    def test_empty_patterns_never_match(self):
        assert match_host("", "api.example.com") is False
        assert match_host("*.example.com", "") is False

    def test_port_in_request_host_is_stripped(self):
        assert match_host("api.example.com", "api.example.com:8443") is True


# ---------------------------------------------------------------------------
# 路径模式编译与匹配
# ---------------------------------------------------------------------------

class TestCompilePathPattern:
    def test_empty_matches_everything(self):
        pattern = compile_path_pattern("")
        assert path_matches(pattern, "/any/path")
        assert path_matches(pattern, "/")

    def test_star_matches_recursive(self):
        pattern = compile_path_pattern("/api/user/*")
        assert path_matches(pattern, "/api/user/123")
        assert path_matches(pattern, "/api/user/123/orders")
        assert not path_matches(pattern, "/api/user")
        assert not path_matches(pattern, "/other/api/user/1")

    def test_param_is_single_segment(self):
        pattern = compile_path_pattern("/users/{id}")
        assert path_matches(pattern, "/users/123")
        assert path_matches(pattern, "/users/abc-9")
        assert not path_matches(pattern, "/users/123/orders")
        assert not path_matches(pattern, "/users")

    def test_literal_chars_are_escaped(self):
        pattern = compile_path_pattern("/a.b/c")
        assert path_matches(pattern, "/a.b/c")
        assert not path_matches(pattern, "/axb/c")

    def test_unclosed_param_is_literal(self):
        # {name 不闭合 → 按字面转义，不抛异常
        pattern = compile_path_pattern("/{unclosed")
        assert path_matches(pattern, "/{unclosed")
        assert not path_matches(pattern, "/anything")


# ---------------------------------------------------------------------------
# URI 解析（query 提取，透传时参数不能丢）
# ---------------------------------------------------------------------------

class TestParseAbsoluteUri:
    def test_relative_path_extracts_query(self):
        # MITM 解密后的 origin-form 请求行，query 必须解析出来否则透传丢参数
        scheme, host, port, path, query = proxy_mod.parse_absolute_uri("/service/acl/biz/modules?type=web")
        assert path == "/service/acl/biz/modules"
        assert query == {"type": "web"}

    def test_relative_path_no_query(self):
        scheme, host, port, path, query = proxy_mod.parse_absolute_uri("/users/1")
        assert path == "/users/1"
        assert query == {}

    def test_relative_path_keeps_blank_values(self):
        scheme, host, port, path, query = proxy_mod.parse_absolute_uri("/x?a=1&b=")
        assert query == {"a": "1", "b": ""}

    def test_absolute_uri_extracts_query(self):
        scheme, host, port, path, query = proxy_mod.parse_absolute_uri("https://api.example.com/users?page=2&size=10")
        assert scheme == "https"
        assert host == "api.example.com"
        assert port == 443
        assert path == "/users"
        assert query == {"page": "2", "size": "10"}


# ---------------------------------------------------------------------------
# 规则查找（内存 SQLite）
# ---------------------------------------------------------------------------

class TestFindMatchingRule:
    def _patch_engine(self, monkeypatch, db_engine):
        import app.routes.mock_server as ms
        monkeypatch.setattr(proxy_mod, "engine", db_engine)
        monkeypatch.setattr(ms, "engine", db_engine)

    def test_longest_host_pattern_wins(self, monkeypatch, db_engine):
        self._patch_engine(monkeypatch, db_engine)
        with Session(db_engine) as session:
            session.add(MockProxyRule(name="wild", host_pattern="*.example.com", path_pattern=""))
            session.add(MockProxyRule(name="exact", host_pattern="api.example.com", path_pattern=""))
            session.commit()

        rule = find_matching_rule("api.example.com", "/x")
        assert rule is not None and rule.name == "exact"

    def test_disabled_rules_skipped(self, monkeypatch, db_engine):
        self._patch_engine(monkeypatch, db_engine)
        with Session(db_engine) as session:
            session.add(MockProxyRule(name="off", host_pattern="*.example.com", path_pattern="", enabled=False))
            session.add(MockProxyRule(name="on", host_pattern="other.com", path_pattern=""))
            session.commit()

        assert find_matching_rule("a.example.com", "/x") is None
        rule = find_matching_rule("other.com", "/y")
        assert rule is not None and rule.name == "on"

    def test_path_must_also_match(self, monkeypatch, db_engine):
        self._patch_engine(monkeypatch, db_engine)
        with Session(db_engine) as session:
            session.add(MockProxyRule(name="r", host_pattern="api.example.com", path_pattern="/v1/*"))
            session.commit()

        assert find_matching_rule("api.example.com", "/v1/users") is not None
        assert find_matching_rule("api.example.com", "/v2/users") is None


# ---------------------------------------------------------------------------
# resolve_mock_response 重构回归
# ---------------------------------------------------------------------------

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
    },
}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class TestResolveMockResponse:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch, db_engine):
        monkeypatch.setattr(mock_server_mod, "engine", db_engine)
        yield

    def test_handwritten_config_hit(self):
        with Session(mock_server_mod.engine) as session:
            session.add(MockConfig(
                name="获取用户", method="GET", url_path="/api/mock/users/{id}",
                status_code=200, response_body='{"id": 1, "name": "张三"}',
                enabled=True,
            ))
            session.commit()

        result = _run(resolve_mock_response(
            "/api/mock/users/42", "GET", {}, None, {}
        ))
        assert result is not None
        assert result.status_code == 200
        body = json.loads(result.body)
        assert body == {"id": 1, "name": "张三"}
        assert result.config_id is not None
        assert result.config_name == "获取用户"

    def test_project_schema_mock_hit(self):
        with Session(mock_server_mod.engine) as session:
            project = ApiProject(name="合同服务", mock_enabled=True, mock_prefix="/contract")
            session.add(project)
            session.commit()
            session.refresh(project)
            session.add(ApiEndpoint(
                project_id=project.id, name="查询用户", method="GET",
                path="/users/{id}", response_schema=RESPONSE_SCHEMA,
            ))
            session.commit()

        result = _run(resolve_mock_response(
            "/api/mock/contract/users/7", "GET", {"id": "7"}, None, {}
        ))
        assert result is not None
        assert result.status_code == 200
        payload = json.loads(result.body)
        assert payload["code"] == 200
        assert "data" in payload
        assert "[项目Mock]" in result.config_name

    def test_validation_failure_returns_400(self):
        request_schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        with Session(mock_server_mod.engine) as session:
            project = ApiProject(name="校验项目", mock_enabled=True, mock_prefix="/val")
            session.add(project)
            session.commit()
            session.refresh(project)
            session.add(ApiEndpoint(
                project_id=project.id, method="POST", path="/create",
                request_schema=request_schema, response_schema=RESPONSE_SCHEMA,
            ))
            session.commit()

        result = _run(resolve_mock_response(
            "/api/mock/val/create", "POST", {}, '{"other": 1}', {}
        ))
        assert result is not None
        assert result.status_code == 400
        assert json.loads(result.body)["success"] is False

    def test_no_match_returns_none(self):
        result = _run(resolve_mock_response(
            "/api/mock/nothing/here", "GET", {}, None, {}
        ))
        assert result is None


# ---------------------------------------------------------------------------
# 证书模块
# ---------------------------------------------------------------------------

class TestProxyCert:
    def test_ensure_ca_generates_and_loads(self, tmp_path, monkeypatch):
        from cryptography import x509

        from app.services import proxy_cert

        certs_dir = str(tmp_path / "certs")
        monkeypatch.setattr(proxy_cert, "CERTS_DIR", certs_dir)
        monkeypatch.setattr(proxy_cert, "CA_KEY_FILE", f"{certs_dir}/ca.key")
        monkeypatch.setattr(proxy_cert, "CA_CERT_FILE", f"{certs_dir}/ca.pem")
        monkeypatch.setattr(proxy_cert, "_leaf_cache", {})
        monkeypatch.setattr(proxy_cert, "_ca_key", None)
        monkeypatch.setattr(proxy_cert, "_ca_cert", None)

        key, cert = proxy_cert.ensure_ca()
        assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True

        pem = proxy_cert.load_ca_cert_pem()
        loaded = x509.load_pem_x509_certificate(pem)
        assert loaded.subject == cert.subject

    def test_get_cert_for_host_san_and_cache(self, tmp_path, monkeypatch):
        from cryptography import x509

        from app.services import proxy_cert

        certs_dir = str(tmp_path / "certs")
        monkeypatch.setattr(proxy_cert, "CERTS_DIR", certs_dir)
        monkeypatch.setattr(proxy_cert, "CA_KEY_FILE", f"{certs_dir}/ca.key")
        monkeypatch.setattr(proxy_cert, "CA_CERT_FILE", f"{certs_dir}/ca.pem")
        monkeypatch.setattr(proxy_cert, "_leaf_cache", {})
        monkeypatch.setattr(proxy_cert, "_ca_key", None)
        monkeypatch.setattr(proxy_cert, "_ca_cert", None)

        pem1 = proxy_cert.get_cert_for_host("svc.example.com")
        cert = x509.load_pem_x509_certificate(
            pem1.split("-----END CERTIFICATE-----")[0].encode() + b"-----END CERTIFICATE-----\n"
        )
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert "svc.example.com" in sans.get_values_for_type(x509.DNSName)

        # 第二次走缓存（磁盘或内存），内容一致
        pem2 = proxy_cert.get_cert_for_host("svc.example.com")
        assert pem2 == pem1
