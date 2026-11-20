"""chat 路由模型配置回退测试。

覆盖点：设置页全局配置（api_key 为空/有值）与 data/config.json 的回退顺序，
避免只配置 config.json 的部署环境对话接口 500。
不触网、不调真实 LLM。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

from app.routes.chat import _resolve_chat_model_config


def _session_with(shared):
    """构造最小 Session 替身：仅支持 get_shared_system_config 的查询路径。"""
    return SimpleNamespace(_shared=shared)


def test_resolve_prefers_shared_config_with_key(monkeypatch):
    shared = SimpleNamespace(
        api_key="  sk-shared  ",
        api_base_url="https://shared.example.com",
        api_model="shared-model",
    )
    monkeypatch.setattr(
        "app.services.system_config.get_shared_system_config",
        lambda session: shared,
    )
    key, base, model = _resolve_chat_model_config(_session_with(shared))
    assert key == "sk-shared"
    assert base == "https://shared.example.com"
    assert model == "shared-model"


def test_resolve_falls_back_to_config_json_when_shared_key_empty(monkeypatch):
    shared = SimpleNamespace(api_key="   ", api_base_url="", api_model="")
    monkeypatch.setattr(
        "app.services.system_config.get_shared_system_config",
        lambda session: shared,
    )
    monkeypatch.setattr(
        "config.config_manager.get_api_key",
        lambda: "sk-file",
    )
    monkeypatch.setattr(
        "config.config_manager.get_api_base_url",
        lambda: "https://file.example.com",
    )
    monkeypatch.setattr(
        "config.config_manager.get_api_model",
        lambda: "file-model",
    )
    key, base, model = _resolve_chat_model_config(_session_with(shared))
    assert key == "sk-file"
    assert base == "https://file.example.com"
    assert model == "file-model"


def test_resolve_defaults_when_both_missing(monkeypatch):
    monkeypatch.setattr(
        "app.services.system_config.get_shared_system_config",
        lambda session: None,
    )
    monkeypatch.setattr("config.config_manager.get_api_key", lambda: "")
    key, base, model = _resolve_chat_model_config(_session_with(None))
    assert key == ""
    assert base == "https://api.deepseek.com"
    assert model == "deepseek-v4-flash"


def test_resolve_shared_failure_falls_back_to_config_json(monkeypatch):
    def _boom(session):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.services.system_config.get_shared_system_config",
        _boom,
    )
    monkeypatch.setattr("config.config_manager.get_api_key", lambda: "sk-file")
    monkeypatch.setattr(
        "config.config_manager.get_api_base_url",
        lambda: "https://file.example.com",
    )
    monkeypatch.setattr("config.config_manager.get_api_model", lambda: "file-model")
    key, base, model = _resolve_chat_model_config(_session_with(None))
    assert key == "sk-file"
    assert base == "https://file.example.com"