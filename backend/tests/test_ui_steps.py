"""UI steps 归一化与 URL 拼接的纯函数测试(spec §4/§5)。"""
from app.services.ui_steps import normalize_ui_steps, has_api_call_step, build_target_url


class TestNormalizeUiSteps:
    def test_plain_strings_pass_through(self):
        steps, _ = normalize_ui_steps(["打开登录页", "点击登录"])
        assert steps == ["打开登录页", "点击登录"]

    def test_pipeline_dict_takes_action_field(self):
        steps, _ = normalize_ui_steps([
            {"step": 1, "action": "输入有效用户名", "expected": "输入成功"},
            {"step": 2, "action": "点击登录按钮", "expected": "跳转首页"},
        ])
        assert steps == ["输入有效用户名", "点击登录按钮"]

    def test_mixed_formats(self):
        steps, _ = normalize_ui_steps([
            "打开登录页",
            {"step": 1, "action": "输入密码", "expected": "成功"},
        ])
        assert steps == ["打开登录页", "输入密码"]

    def test_api_call_steps_flagged_and_skipped(self):
        steps, has_api = normalize_ui_steps([
            {"type": "api_call", "endpoint_id": 1, "method": "POST"},
            "真实操作",
        ])
        assert has_api is True
        assert steps == ["真实操作"]

    def test_all_api_call_returns_empty(self):
        steps, has_api = normalize_ui_steps([
            {"endpoint_ref": 1, "body": "{}"},
        ])
        assert has_api is True
        assert steps == []

    def test_empty_and_garbage_skipped(self):
        steps, has_api = normalize_ui_steps(["", None, 123, "  "])
        assert steps == []
        assert has_api is False


class TestHasApiCallStep:
    def test_true_for_type_marker(self):
        assert has_api_call_step([{"type": "api_call"}]) is True

    def test_true_for_endpoint_ref(self):
        assert has_api_call_step([{"endpoint_ref": 1}]) is True

    def test_false_for_text(self):
        assert has_api_call_step(["打开页面"]) is False


class TestBuildTargetUrl:
    def test_origin_only(self):
        assert build_target_url("http://192.168.13.178:8182", "") == "http://192.168.13.178:8182"

    def test_hash_path(self):
        assert build_target_url(
            "http://192.168.13.178:8182/",
            "#/home/cmp/contract-web/main/ent/contractRegistration",
        ) == "http://192.168.13.178:8182#/home/cmp/contract-web/main/ent/contractRegistration"

    def test_slash_path(self):
        assert build_target_url("http://a.com", "/login") == "http://a.com/login"

    def test_bare_path_gets_slash(self):
        assert build_target_url("http://a.com", "login") == "http://a.com/login"
