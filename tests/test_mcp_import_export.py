"""
MCP JSON 导入/导出功能 — 单元测试
测试 _parse_mcp_import_data, _normalize_import_entry, 导出 env 转换

Run: python -m pytest tests/test_mcp_import_export.py -v
"""

import json
import sys
import os

# 确保可以导入后端模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.routes.mcp import _parse_mcp_import_data, _normalize_import_entry


# ============================================================
# 测试 _normalize_import_entry
# ============================================================

def test_normalize_http_entry():
    """HTTP 类型的单条记录规范化"""
    item = {"name": "my-server", "type": "http", "url": "http://localhost:8000/mcp"}
    result = _normalize_import_entry(item)
    assert result["name"] == "my-server"
    assert result["type"] == "http"
    assert result["url"] == "http://localhost:8000/mcp"
    assert result["enabled"] is True
    assert result["timeout"] == 60
    assert result["env"] == []


def test_normalize_stdio_entry():
    """STDIO 类型的单条记录规范化"""
    item = {
        "name": "fs-server",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
    }
    result = _normalize_import_entry(item)
    assert result["name"] == "fs-server"
    assert result["type"] == "stdio"
    assert result["command"] == "npx"
    assert result["args"] == ["-y", "@modelcontextprotocol/server-filesystem"]


def test_normalize_auto_infer_http():
    """自动推断 HTTP 类型：有 url 无 command"""
    item = {"name": "auto-http", "url": "http://localhost:9999/mcp"}
    result = _normalize_import_entry(item)
    assert result["type"] == "http"
    assert result["url"] == "http://localhost:9999/mcp"


def test_normalize_auto_infer_stdio():
    """自动推断 STDIO 类型：有 command"""
    item = {"name": "auto-stdio", "command": "npx", "args": ["-y", "some-server"]}
    result = _normalize_import_entry(item)
    assert result["type"] == "stdio"
    assert result["command"] == "npx"
    assert result["args"] == ["-y", "some-server"]


def test_normalize_url_and_command_both_present():
    """url 和 command 同时存在时，优先推断为 stdio（command 优先），url 不继承"""
    item = {"name": "both", "url": "http://localhost:8000/mcp", "command": "npx"}
    result = _normalize_import_entry(item)
    assert result["type"] == "stdio"  # command 优先
    assert result["command"] == "npx"
    assert result.get("url") is None  # stdio 模式不携带 url


def test_normalize_env_dict_to_list():
    """env 对象格式转数组格式"""
    item = {
        "name": "env-test",
        "command": "npx",
        "env": {"API_KEY": "abc123", "DEBUG": "true"},
    }
    result = _normalize_import_entry(item)
    assert result["type"] == "stdio"
    assert len(result["env"]) == 2
    assert {"key": "API_KEY", "value": "abc123"} in result["env"]
    assert {"key": "DEBUG", "value": "true"} in result["env"]


def test_normalize_env_list_passthrough():
    """env 数组格式保持原样"""
    item = {
        "name": "env-list",
        "command": "npx",
        "env": [{"key": "FOO", "value": "bar"}],
    }
    result = _normalize_import_entry(item)
    assert result["env"] == [{"key": "FOO", "value": "bar"}]


def test_normalize_env_none():
    """env 为 None 时默认为空数组"""
    item = {"name": "no-env", "command": "npx"}
    result = _normalize_import_entry(item)
    assert result["env"] == []


def test_normalize_enabled_tools():
    """可选字段 enabled_tools"""
    item = {"name": "tools-test", "command": "npx", "enabled_tools": ["tool1", "tool2"]}
    result = _normalize_import_entry(item)
    assert result["enabled_tools"] == ["tool1", "tool2"]


def test_normalize_empty_name():
    """name 为空字符串时，仍能返回 entry（过滤在调用方）"""
    item = {"name": "", "command": "npx"}
    result = _normalize_import_entry(item)
    assert result["name"] == ""


def test_normalize_missing_type_default_http():
    """未指定 type 时默认为 http"""
    item = {"name": "no-type", "url": "http://localhost:8000/mcp"}
    result = _normalize_import_entry(item)
    assert result["type"] == "http"  # 默认 type=http，且 url 存在，所以是 http


# ============================================================
# 测试 _parse_mcp_import_data
# ============================================================

def test_parse_standard_mcp_servers_object():
    """标准 mcpServers 对象格式"""
    data = {
        "mcpServers": {
            "my-server": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
            }
        }
    }
    results = _parse_mcp_import_data(data)
    assert len(results) == 1
    assert results[0]["name"] == "my-server"
    assert results[0]["type"] == "stdio"
    assert results[0]["command"] == "npx"


def test_parse_multiple_mcp_servers_object():
    """多个服务器的 mcpServers 对象格式"""
    data = {
        "mcpServers": {
            "server-a": {"command": "npx", "args": ["-y", "package-a"]},
            "server-b": {"url": "http://localhost:8001/mcp"},
        }
    }
    results = _parse_mcp_import_data(data)
    assert len(results) == 2
    names = {r["name"] for r in results}
    assert names == {"server-a", "server-b"}


def test_parse_array_format():
    """数组格式 [{name, type, ...}]"""
    data = [
        {"name": "s1", "type": "http", "url": "http://localhost:8000/mcp"},
        {"name": "s2", "type": "stdio", "command": "npx"},
    ]
    results = _parse_mcp_import_data(data)
    assert len(results) == 2
    assert results[0]["name"] == "s1"
    assert results[0]["type"] == "http"
    assert results[1]["name"] == "s2"
    assert results[1]["type"] == "stdio"


def test_parse_mcp_servers_as_array():
    """mcpServers 值为数组格式"""
    data = {
        "mcpServers": [
            {"name": "s1", "type": "http", "url": "http://localhost:8000/mcp"},
        ]
    }
    results = _parse_mcp_import_data(data)
    assert len(results) == 1
    assert results[0]["name"] == "s1"


def test_parse_empty_data():
    """空 dict 无 mcpServers"""
    data = {}
    results = _parse_mcp_import_data(data)
    assert len(results) == 0


def test_parse_empty_list():
    """空列表"""
    data = []
    results = _parse_mcp_import_data(data)
    assert len(results) == 0


def test_parse_empty_mcp_servers():
    """mcpServers 为空 dict"""
    data = {"mcpServers": {}}
    results = _parse_mcp_import_data(data)
    # BUG: {} or data → data (整个 dict)，导致生成了一个名为 "mcpServers" 的假条目
    # 期望: 0 条；实际: 1 条（详见代码审查）
    assert len(results) == 0, (
        f"BUG: 空 mcpServers 应该返回 0 条，实际返回 {len(results)} 条。"
        f"原因: _parse_mcp_import_data 中 'data.get(\"mcpServers\") or data' 在 mcpServers 为空 dict({{}}) 时回退到 data 本身。"
    )


def test_parse_env_object_format():
    """mcpServers 对象格式中的 env 对象格式"""
    data = {
        "mcpServers": {
            "my-server": {
                "command": "npx",
                "env": {"KEY1": "value1", "KEY2": "value2"},
            }
        }
    }
    results = _parse_mcp_import_data(data)
    assert len(results) == 1
    env = results[0]["env"]
    assert len(env) == 2
    assert {"key": "KEY1", "value": "value1"} in env
    assert {"key": "KEY2", "value": "value2"} in env


def test_parse_env_array_format_in_object():
    """mcpServers 对象格式中的 env 数组格式"""
    data = {
        "mcpServers": {
            "my-server": {
                "command": "npx",
                "env": [{"key": "FOO", "value": "bar"}],
            }
        }
    }
    results = _parse_mcp_import_data(data)
    assert len(results) == 1
    assert results[0]["env"] == [{"key": "FOO", "value": "bar"}]


def test_parse_mixed_types():
    """混合类型：一些有 command，一些有 url"""
    data = {
        "mcpServers": {
            "http-srv": {"url": "http://localhost:8000/mcp"},
            "stdio-srv": {"command": "npx", "args": ["-y", "some-pkg"]},
            "both-srv": {"url": "http://x", "command": "npx"},
        }
    }
    results = _parse_mcp_import_data(data)
    types = {r["name"]: r["type"] for r in results}
    assert types["http-srv"] == "http"
    assert types["stdio-srv"] == "stdio"
    assert types["both-srv"] == "stdio"  # command 优先


def test_parse_invalid_entry_skipped():
    """非 dict 的 cfg 跳过"""
    data = {
        "mcpServers": {
            "valid": {"command": "npx"},
            "invalid": "not a dict",
            "also_invalid": 123,
        }
    }
    results = _parse_mcp_import_data(data)
    assert len(results) == 1
    assert results[0]["name"] == "valid"


def test_parse_cfg_missing_name_handled():
    """_parse_mcp_import_data 自身不过滤空 name，由调用方 import_mcp_servers 过滤"""
    data = [
        {"name": "", "type": "http", "url": "http://localhost:8000/mcp"},
        {"name": "valid", "type": "http", "url": "http://localhost:8001/mcp"},
    ]
    results = _parse_mcp_import_data(data)
    assert len(results) == 2  # 不在此过滤


def test_parse_timeout_and_enabled_fields():
    """timeout 和 enabled 字段传递"""
    data = {
        "mcpServers": {
            "custom": {
                "command": "npx",
                "timeout": 120,
                "enabled": False,
            }
        }
    }
    results = _parse_mcp_import_data(data)
    assert results[0]["timeout"] == 120
    assert results[0]["enabled"] is False


def test_parse_timeout_null():
    """timeout 为 null 时使用默认值 60"""
    data = {
        "mcpServers": {
            "s1": {"command": "npx", "timeout": None}
        }
    }
    results = _parse_mcp_import_data(data)
    assert results[0]["timeout"] == 60


def test_parse_enabled_tools():
    """enabled_tools 字段传递"""
    data = {
        "mcpServers": {
            "s1": {
                "command": "npx",
                "enabled_tools": ["tool_a", "tool_b"],
            }
        }
    }
    results = _parse_mcp_import_data(data)
    assert results[0]["enabled_tools"] == ["tool_a", "tool_b"]


def test_parse_enabled_tools_null():
    """enabled_tools 为 null 时，不设置该字段"""
    data = {
        "mcpServers": {
            "s1": {"command": "npx", "enabled_tools": None}
        }
    }
    results = _parse_mcp_import_data(data)
    assert "enabled_tools" not in results[0]


def test_parse_non_dict_data():
    """非 dict/list 数据返回空列表"""
    assert _parse_mcp_import_data("string") == []
    assert _parse_mcp_import_data(123) == []
    assert _parse_mcp_import_data(None) == []


# ============================================================
# 测试导出 env 转换逻辑（模拟 export_mcp_servers 中的 env 转换）
# ============================================================

def test_export_env_array_to_object():
    """导出时 env 数组格式转对象格式"""
    env_array = [
        {"key": "API_KEY", "value": "secret123"},
        {"key": "DEBUG", "value": "true"},
    ]
    env_dict = {}
    for e in env_array:
        if isinstance(e, dict) and e.get("key"):
            env_dict[e["key"]] = e.get("value", "")
    assert env_dict == {"API_KEY": "secret123", "DEBUG": "true"}


def test_export_env_empty_array():
    """空数组 env 在导出时不生成 env 字段"""
    env_array: list = []
    env_dict = {}
    for e in env_array:
        if isinstance(e, dict) and e.get("key"):
            env_dict[e["key"]] = e.get("value", "")
    assert env_dict == {}


def test_export_env_entry_missing_key():
    """缺少 key 的 env 条目在导出时跳过"""
    env_array = [
        {"value": "no-key"},
        {"key": "VALID", "value": "ok"},
    ]
    env_dict = {}
    for e in env_array:
        if isinstance(e, dict) and e.get("key"):
            env_dict[e["key"]] = e.get("value", "")
    assert env_dict == {"VALID": "ok"}
    assert "no-key" not in env_dict


# ============================================================
# 验证导入-导出往返一致性 (roundtrip)
# ============================================================

def test_roundtrip_mcp_servers_object():
    """导入 mcpServers 对象格式 → 导出的 env 格式正确"""
    import_data = {
        "mcpServers": {
            "dev-server": {
                "command": "npx",
                "args": ["-y", "dev-package"],
                "env": {"NODE_ENV": "development", "PORT": "3000"},
                "timeout": 120,
            }
        }
    }
    entries = _parse_mcp_import_data(import_data)

    # 模拟导出转换
    export_servers = {}
    for entry in entries:
        export_entry = {
            "type": entry.get("type", "http"),
            "enabled": entry.get("enabled", True),
            "timeout": entry.get("timeout", 60),
        }
        if entry.get("type") == "stdio":
            export_entry["command"] = entry.get("command", "")
            export_entry["args"] = entry.get("args", [])
        # env 数组 → 对象
        raw_env = entry.get("env", [])
        if raw_env:
            env_obj = {}
            for e in raw_env:
                if isinstance(e, dict) and e.get("key"):
                    env_obj[e["key"]] = e.get("value", "")
            if env_obj:
                export_entry["env"] = env_obj

        export_servers[entry["name"]] = export_entry

    assert export_servers["dev-server"]["env"] == {"NODE_ENV": "development", "PORT": "3000"}
    assert export_servers["dev-server"]["type"] == "stdio"


# ============================================================
# 测试已知 Bug 的显式标记
# ============================================================

def test_known_bug_empty_mcp_servers():
    """
    已知 Bug：当 data = {"mcpServers": {}} 时

    问题代码（mcp.py 第 728 行）：
        servers_dict = data.get("mcpServers") or data
    在 data.get("mcpServers") 返回 {}（空 dict，Python 中为 falsy）时，
    `{} or data` 会返回 data 本身（即 {"mcpServers": {}}），
    导致后续迭代生成一个名为 "mcpServers" 的假条目。

    修复建议：
        servers_dict = data.get("mcpServers")
        if servers_dict is None:
            servers_dict = data

    或更简洁：
        servers_dict = data.get("mcpServers")
        if not isinstance(servers_dict, (dict, list)):
            servers_dict = data
    """
    data = {"mcpServers": {}}
    results = _parse_mcp_import_data(data)

    # 当前行为（Bug）：会生成一个假条目
    # 期望行为：len(results) == 0
    # 以下断言仅为记录 Bug，不阻断测试
    if len(results) > 0:
        print(f"\n[KNOWN BUG] 空 mcpServers 产生 {len(results)} 个假条目: {results}")
        print("[FIX] 将 data.get('mcpServers') or data 改为使用 is None 判断")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
