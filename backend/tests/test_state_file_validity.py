"""登录态文件有效性判定测试:空壳(无 cookie 且无有效 localStorage)不得当作已登录。

背景(2026-09-22 实测):鲁班是 localStorage 型站点(cookies 恒为 0,token 在
localStorage)。旧判定「cookies 或 origins 任一非空即有效」把两类空壳当成了已登录:
  1. {"cookies":[],"origins":[{origin,localStorage:[]}]} —— 复用后直接被重定向回登录页;
  2. {"cookies":[],"origins":[{origin,localStorage:[{name:'LBuilderTitle',value:'10字符'}]}]}
     —— 只剩展示键,没有 token。
空壳还会被保存路径覆盖好文件,造成 po514 永远停在未登录。
"""
import json

import pytest

import app.services.browser_agent as ba

TOKEN_LS = [{"name": "LBuilderToken", "value": "x" * 200}]
TITLE_LS = [{"name": "LBuilderTitle", "value": "鲁班数字工程"}]


def _write(path, cookies=(), origins=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cookies": list(cookies), "origins": list(origins)}, ensure_ascii=False),
        encoding="utf-8",
    )


class TestStateMeaningful:
    def test_cookie_state_is_meaningful(self):
        assert ba.state_meaningful({"cookies": [{"name": "sid", "value": "x"}], "origins": []})

    def test_localstorage_token_is_meaningful(self):
        state = {"cookies": [], "origins": [{"origin": "http://a.com", "localStorage": TOKEN_LS}]}
        assert ba.state_meaningful(state)

    def test_origin_without_localstorage_is_shell(self):
        state = {"cookies": [], "origins": [{"origin": "http://a.com", "localStorage": []}]}
        assert not ba.state_meaningful(state)

    def test_title_only_localstorage_is_shell(self):
        """只剩短展示键(如 LBuilderTitle)不算有凭据。"""
        state = {"cookies": [], "origins": [{"origin": "http://a.com", "localStorage": TITLE_LS}]}
        assert not ba.state_meaningful(state)

    def test_empty_and_garbage_are_shell(self):
        assert not ba.state_meaningful({})
        assert not ba.state_meaningful({"cookies": [], "origins": []})
        assert not ba.state_meaningful("not-a-dict")


class TestHasValidStateFile:
    def test_rejects_empty_shell_file(self, tmp_path):
        path = tmp_path / "po1.json"
        _write(path)
        assert not ba.has_valid_state_file(path)

    def test_rejects_title_only_shell(self, tmp_path):
        path = tmp_path / "po1.json"
        _write(path, origins=[{"origin": "http://a.com", "localStorage": TITLE_LS}])
        assert not ba.has_valid_state_file(path)

    def test_accepts_token_state(self, tmp_path):
        path = tmp_path / "po1.json"
        _write(path, origins=[{"origin": "http://a.com", "localStorage": TOKEN_LS}])
        assert ba.has_valid_state_file(path)

    def test_accepts_cookie_state(self, tmp_path):
        path = tmp_path / "po1.json"
        _write(path, cookies=[{"name": "sid", "value": "x"}])
        assert ba.has_valid_state_file(path)

    def test_missing_file_invalid(self, tmp_path):
        assert not ba.has_valid_state_file(tmp_path / "nope.json")


class TestHasValidSession:
    @pytest.fixture()
    def sessions_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_sessions_dir", tmp_path)
        return tmp_path

    def test_shell_cache_not_valid(self, sessions_dir):
        cache = ba._cache_path("http://a.com:8182")
        _write(cache, origins=[{"origin": "http://a.com:8182", "localStorage": []}])
        assert not ba.has_valid_session("http://a.com:8182")

    def test_token_cache_valid(self, sessions_dir):
        cache = ba._cache_path("http://a.com:8182")
        _write(cache, origins=[{"origin": "http://a.com:8182", "localStorage": TOKEN_LS}])
        assert ba.has_valid_session("http://a.com:8182")
