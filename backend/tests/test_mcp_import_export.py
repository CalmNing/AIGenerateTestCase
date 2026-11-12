"""Tests for MCP JSON import/export functionality.

Covers:
  - _parse_mcp_import_data — pure parse logic (object format, array format, edge cases)
  - _normalize_import_entry — single-entry normalisation
  - import_mcp_servers — full import via DB (overwrite on name conflict, create new)
  - export_mcp_servers — full export verifying mcpServers structure and env format
  - Route registration — confirms new routes are present on the router
"""
import json
from unittest.mock import MagicMock

import pytest

from app.routes.mcp import (
    _parse_mcp_import_data,
    _normalize_import_entry,
    import_mcp_servers,
    export_mcp_servers,
    router as mcp_router,
)
from db.models import McpServer


# =============================================================================
# _parse_mcp_import_data — pure function tests
# =============================================================================

class TestParseMcpImportData:
    """Unit tests for the pure data-parsing helper."""

    def test_object_format_with_mcpServers_key(self):
        """Standard object format: {"mcpServers": {"name": {...}}}"""
        data = {
            "mcpServers": {
                "test-server": {
                    "command": "npx",
                    "args": ["-y", "test"],
                    "env": {"KEY": "VAL"},
                }
            }
        }
        entries = _parse_mcp_import_data(data)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["name"] == "test-server"
        assert entry["type"] == "stdio"  # has command → stdio
        assert entry["command"] == "npx"
        assert entry["args"] == ["-y", "test"]
        # env dict → list of {key, value}
        assert entry["env"] == [{"key": "KEY", "value": "VAL"}]

    def test_object_format_http_auto_type(self):
        """Object format with url and no command → type=http"""
        data = {
            "mcpServers": {
                "http-server": {
                    "url": "http://localhost:9999/mcp",
                }
            }
        }
        entries = _parse_mcp_import_data(data)
        assert len(entries) == 1
        assert entries[0]["type"] == "http"
        assert entries[0]["url"] == "http://localhost:9999/mcp"

    def test_array_format(self):
        """Array format: [{name, type, url, ...}]"""
        data = [
            {"name": "server-a", "type": "http", "url": "http://localhost:8000/mcp"},
            {"name": "server-b", "type": "stdio", "command": "npx", "args": ["run"]},
        ]
        entries = _parse_mcp_import_data(data)
        assert len(entries) == 2
        assert entries[0]["name"] == "server-a"
        assert entries[0]["url"] == "http://localhost:8000/mcp"
        assert entries[1]["name"] == "server-b"
        assert entries[1]["command"] == "npx"

    def test_empty_data_returns_empty_list(self):
        """Empty/missing data → empty list."""
        assert _parse_mcp_import_data({}) == []
        assert _parse_mcp_import_data({"mcpServers": {}}) == []

    def test_non_dict_item_skipped(self):
        """Non-dict items in array should be skipped."""
        data = [{"name": "valid"}, "invalid_string", 42, None]
        entries = _parse_mcp_import_data(data)
        assert len(entries) == 1
        assert entries[0]["name"] == "valid"

    def test_env_dict_and_list_both_supported(self):
        """env as dict → converted to list; env as list → kept as-is."""
        # Dict env
        entries = _parse_mcp_import_data({
            "mcpServers": {"s1": {"command": "echo", "env": {"A": "1", "B": "2"}}}
        })
        assert entries[0]["env"] == [{"key": "A", "value": "1"}, {"key": "B", "value": "2"}]

        # List env
        entries = _parse_mcp_import_data([{
            "name": "s2", "command": "echo",
            "env": [{"key": "X", "value": "Y"}]
        }])
        assert entries[0]["env"] == [{"key": "X", "value": "Y"}]

    def test_enabled_tools_preserved(self):
        """enabled_tools field should be preserved when present."""
        data = {
            "mcpServers": {
                "tool-server": {
                    "command": "npx",
                    "enabled_tools": ["tool_a", "tool_b"],
                }
            }
        }
        entries = _parse_mcp_import_data(data)
        assert entries[0]["enabled_tools"] == ["tool_a", "tool_b"]

    def test_timeout_field_parsed(self):
        """timeout field should be parsed as int."""
        data = {
            "mcpServers": {
                "srv": {"command": "echo", "timeout": 120}
            }
        }
        entries = _parse_mcp_import_data(data)
        assert entries[0]["timeout"] == 120

    def test_enabled_field_parsed(self):
        """enabled field should be parsed as bool."""
        data = {
            "mcpServers": {
                "srv": {"command": "echo", "enabled": False}
            }
        }
        entries = _parse_mcp_import_data(data)
        assert entries[0]["enabled"] is False

    def test_nested_mcpServers_as_list(self):
        """When mcpServers value is a list, treat as array format."""
        data = {
            "mcpServers": [
                {"name": "a", "command": "echo"},
                {"name": "b", "command": "cat"},
            ]
        }
        entries = _parse_mcp_import_data(data)
        assert len(entries) == 2


# =============================================================================
# _normalize_import_entry — unit tests
# =============================================================================

class TestNormalizeImportEntry:
    def test_basic_http_entry(self):
        entry = _normalize_import_entry({
            "name": "test", "type": "http", "url": "http://localhost/mcp"
        })
        assert entry["name"] == "test"
        assert entry["type"] == "http"
        assert entry["url"] == "http://localhost/mcp"

    def test_stdio_entry_with_command(self):
        entry = _normalize_import_entry({
            "name": "test", "command": "npx", "args": ["-y", "test"]
        })
        assert entry["type"] == "stdio"
        assert entry["command"] == "npx"
        assert entry["args"] == ["-y", "test"]

    def test_defaults_for_minimal_entry(self):
        entry = _normalize_import_entry({"name": "minimal"})
        assert entry["type"] == "http"
        assert entry["enabled"] is True
        assert entry["timeout"] == 60
        assert entry["env"] == []


# =============================================================================
# import_mcp_servers — integration tests (in-memory DB)
# =============================================================================

class TestImportMcpServers:
    """Tests import via DB — uses in-memory SQLite via db_session fixture."""

    def test_import_object_format_creates_servers(self, db_session):
        """Import object format creates McpServer rows."""
        data = {
            "mcpServers": {
                "srv-a": {"command": "npx", "args": ["-y", "test"]},
                "srv-b": {"url": "http://localhost:9000/mcp"},
            }
        }
        mock_user = MagicMock()
        mock_user.user_id = "test-user-1"

        response = import_mcp_servers(session=db_session, user=mock_user, data=data)
        assert response.code == 200
        assert len(response.data) == 2

        # Verify in DB
        from sqlmodel import select
        servers = db_session.exec(select(McpServer).where(McpServer.user_id == "test-user-1")).all()
        assert len(servers) == 2
        names = {s.name for s in servers}
        assert names == {"srv-a", "srv-b"}

    def test_import_array_format(self, db_session):
        """Import array format creates McpServer rows."""
        data = [
            {"name": "arr-srv", "type": "http", "url": "http://localhost:7777/mcp"},
        ]
        mock_user = MagicMock()
        mock_user.user_id = "test-user-2"
        response = import_mcp_servers(session=db_session, user=mock_user, data=data)
        assert response.code == 200
        assert len(response.data) == 1
        assert response.data[0].name == "arr-srv"

    def test_import_overwrites_existing_by_name(self, db_session):
        """Importing with same name + user_id should overwrite existing."""
        # Pre-create a server
        existing = McpServer(name="dup-srv", type="http", url="http://old/url",
                             command=None, args=[], timeout=30, env=[],
                             enabled=True, user_id="test-user-3")
        db_session.add(existing)
        db_session.commit()

        # Import with same name but different config
        data = {
            "mcpServers": {
                "dup-srv": {"command": "npx", "args": ["new"], "timeout": 120}
            }
        }
        mock_user = MagicMock()
        mock_user.user_id = "test-user-3"
        response = import_mcp_servers(session=db_session, user=mock_user, data=data)
        assert response.code == 200
        assert len(response.data) == 1

        # Verify it's updated (not a new row)
        db_session.refresh(existing)
        assert existing.type == "stdio"  # changed from http to stdio (has command)
        assert existing.command == "npx"
        assert existing.args == ["new"]
        assert existing.timeout == 120

    def test_import_empty_data_returns_400(self, db_session):
        """Empty data should return code 400."""
        mock_user = MagicMock()
        mock_user.user_id = "test-user-4"
        response = import_mcp_servers(session=db_session, user=mock_user, data={})
        assert response.code == 400

        response = import_mcp_servers(session=db_session, user=mock_user, data=[])
        assert response.code == 400

    def test_import_skips_entry_without_name(self, db_session):
        """Entries without name should be skipped."""
        data = [
            {"name": "", "command": "npx"},
            {"name": "valid-name", "command": "echo"},
        ]
        mock_user = MagicMock()
        mock_user.user_id = "test-user-5"
        response = import_mcp_servers(session=db_session, user=mock_user, data=data)
        # Only one valid entry
        assert response.code == 200
        assert len(response.data) == 1
        assert response.data[0].name == "valid-name"

    def test_import_with_env_dict(self, db_session):
        """Import with env dict → converted to list format in DB."""
        data = {
            "mcpServers": {
                "env-server": {
                    "command": "echo",
                    "env": {"TOKEN": "abc123", "HOST": "localhost"},
                }
            }
        }
        mock_user = MagicMock()
        mock_user.user_id = "test-user-6"
        response = import_mcp_servers(session=db_session, user=mock_user, data=data)
        assert response.code == 200
        stored = response.data[0]
        # env should be stored as list of dicts
        assert isinstance(stored.env, list)
        env_keys = {e["key"] for e in stored.env}
        assert env_keys == {"TOKEN", "HOST"}

    def test_import_with_enabled_tools(self, db_session):
        """Import preserves enabled_tools."""
        data = {
            "mcpServers": {
                "tool-srv": {
                    "command": "npx",
                    "enabled_tools": ["tool1", "tool2"],
                }
            }
        }
        mock_user = MagicMock()
        mock_user.user_id = "test-user-7"
        response = import_mcp_servers(session=db_session, user=mock_user, data=data)
        assert response.code == 200
        assert sorted(response.data[0].enabled_tools) == ["tool1", "tool2"]


# =============================================================================
# export_mcp_servers — integration tests
# =============================================================================

class TestExportMcpServers:
    """Tests export via DB."""

    def test_export_returns_mcpServers_structure(self, db_session):
        """Export should return {"mcpServers": {...}}."""
        # Pre-populate with one server
        srv = McpServer(name="exp-srv", type="http", url="http://export.test/mcp",
                        command=None, args=[], timeout=60, env=[{"key": "K", "value": "V"}],
                        enabled=True, user_id="export-user")
        db_session.add(srv)
        db_session.commit()

        mock_user = MagicMock()
        mock_user.user_id = "export-user"
        result = export_mcp_servers(session=db_session, user=mock_user)

        # Top-level key
        assert "mcpServers" in result
        servers = result["mcpServers"]
        assert "exp-srv" in servers

        entry = servers["exp-srv"]
        assert entry["type"] == "http"
        assert entry["url"] == "http://export.test/mcp"
        assert entry["enabled"] is True
        assert entry["timeout"] == 60

    def test_export_env_as_object_format(self, db_session):
        """Export should convert env list → dict/object format."""
        srv = McpServer(name="env-srv", type="stdio", command="echo",
                        args=[], timeout=60,
                        env=[{"key": "FOO", "value": "bar"}, {"key": "BAZ", "value": "qux"}],
                        enabled=True, user_id="env-user")
        db_session.add(srv)
        db_session.commit()

        mock_user = MagicMock()
        mock_user.user_id = "env-user"
        result = export_mcp_servers(session=db_session, user=mock_user)
        entry = result["mcpServers"]["env-srv"]
        assert entry["env"] == {"FOO": "bar", "BAZ": "qux"}

    def test_export_includes_all_users_data(self, db_session):
        """数据不按用户隔离：导出返回所有用户的 MCP 服务器配置。"""
        srv_a = McpServer(name="user-a-srv", type="http", url="http://a/mcp",
                          command=None, args=[], timeout=60, env=[],
                          enabled=True, user_id="user-a")
        srv_b = McpServer(name="user-b-srv", type="http", url="http://b/mcp",
                          command=None, args=[], timeout=60, env=[],
                          enabled=True, user_id="user-b")
        db_session.add(srv_a)
        db_session.add(srv_b)
        db_session.commit()

        mock_user = MagicMock()
        mock_user.user_id = "user-a"
        result = export_mcp_servers(session=db_session, user=mock_user)
        assert sorted(result["mcpServers"].keys()) == ["user-a-srv", "user-b-srv"]

    def test_export_empty_returns_empty_mcpServers(self, db_session):
        """Export with no servers returns empty mcpServers dict."""
        mock_user = MagicMock()
        mock_user.user_id = "no-servers"
        result = export_mcp_servers(session=db_session, user=mock_user)
        assert result == {"mcpServers": {}}

    def test_export_stdio_command_and_args(self, db_session):
        """Export stdio server should include command and args."""
        srv = McpServer(name="stdio-srv", type="stdio", command="npx",
                        args=["-y", "test-pkg"], timeout=60, env=[],
                        enabled=True, user_id="stdio-user")
        db_session.add(srv)
        db_session.commit()

        mock_user = MagicMock()
        mock_user.user_id = "stdio-user"
        result = export_mcp_servers(session=db_session, user=mock_user)
        entry = result["mcpServers"]["stdio-srv"]
        assert entry["command"] == "npx"
        assert entry["args"] == ["-y", "test-pkg"]

    def test_export_enabled_tools(self, db_session):
        """Export should include enabled_tools if present."""
        srv = McpServer(name="tool-srv-export", type="stdio", command="echo",
                        args=[], timeout=60, env=[],
                        enabled_tools=["read", "write"],
                        enabled=True, user_id="tool-user")
        db_session.add(srv)
        db_session.commit()

        mock_user = MagicMock()
        mock_user.user_id = "tool-user"
        result = export_mcp_servers(session=db_session, user=mock_user)
        entry = result["mcpServers"]["tool-srv-export"]
        assert sorted(entry["enabled_tools"]) == ["read", "write"]


# =============================================================================
# Route registration verification
# =============================================================================

class TestMcpRouteRegistration:
    """Verify that new import/export routes are registered on the router."""

    def _get_route_paths(self) -> set[str]:
        """Extract all registered route path+method combinations."""
        routes = set()
        for route in mcp_router.routes:
            if hasattr(route, "methods") and hasattr(route, "path"):
                for method in route.methods:
                    if method in {"GET", "POST", "PUT", "DELETE"}:
                        routes.add(f"{method} {route.path}")
        return routes

    def test_import_route_registered(self):
        routes = self._get_route_paths()
        assert "POST /mcp/servers/import" in routes, (
            "POST /mcp/servers/import should be registered"
        )

    def test_import_file_route_registered(self):
        routes = self._get_route_paths()
        assert "POST /mcp/servers/import/file" in routes, (
            "POST /mcp/servers/import/file should be registered"
        )

    def test_export_route_registered(self):
        routes = self._get_route_paths()
        assert "GET /mcp/servers/export" in routes, (
            "GET /mcp/servers/export should be registered"
        )

    def test_crud_routes_still_registered(self):
        """Existing CRUD routes should still be present."""
        routes = self._get_route_paths()
        assert "GET /mcp/servers" in routes
        assert "POST /mcp/servers" in routes
        assert "PUT /mcp/servers/{server_id}" in routes
        assert "DELETE /mcp/servers/{server_id}" in routes
