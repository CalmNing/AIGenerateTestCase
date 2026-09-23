"""page_extractor 纯函数测试(不碰浏览器)。"""
from app.services.page_extractor import (
    apply_llm_names,
    build_element_key,
    build_fingerprint,
    build_locators,
    element_type,
    heuristic_name,
    is_dynamic_id,
    normalize_elements,
    parse_llm_names,
    score_locator,
)


class TestIsDynamicId:
    def test_uuid_is_dynamic(self):
        assert is_dynamic_id("550e8400-e29b-41d4-a716-446655440000") is True

    def test_react_use_id_is_dynamic(self):
        assert is_dynamic_id(":r1a:") is True

    def test_long_number_is_dynamic(self):
        assert is_dynamic_id("123456") is True

    def test_long_hex_is_dynamic(self):
        assert is_dynamic_id("a1b2c3d4e5f6a7b8") is True

    def test_semantic_id_is_stable(self):
        assert is_dynamic_id("username") is False
        assert is_dynamic_id("login-btn") is False
        assert is_dynamic_id("") is False


class TestScoreLocator:
    def test_testid_highest(self):
        assert score_locator("testid", "login-username") == 100

    def test_dynamic_id_scored_zero(self):
        assert score_locator("id", "123456") == 0

    def test_stable_id(self):
        assert score_locator("id", "login-btn") == 80

    def test_unknown_strategy_low(self):
        assert score_locator("weird", "x") == 10


class TestBuildLocators:
    def _attrs(self, **overrides):
        attrs = {
            "tag": "input",
            "inputType": "text",
            "testId": "login-username",
            "name": "username",
            "placeholder": "请输入用户名",
            "label": "用户名",
            "id": "123456",
            "cssPath": "form > input",
        }
        attrs.update(overrides)
        return attrs

    def test_sorted_by_stability_desc(self):
        locators = build_locators(self._attrs())
        stabilities = [loc["stability"] for loc in locators]
        assert stabilities == sorted(stabilities, reverse=True)

    def test_testid_primary(self):
        locators = build_locators(self._attrs())
        assert locators[0]["strategy"] == "testid"
        assert locators[0]["value"] == "login-username"

    def test_dynamic_id_skipped(self):
        strategies = {loc["strategy"] for loc in build_locators(self._attrs())}
        assert "id" not in strategies

    def test_role_with_accessible_name(self):
        locators = build_locators({"tag": "button", "label": "登录", "text": "登录"})
        role = next(loc for loc in locators if loc["strategy"] == "role")
        assert role["value"] == 'button[name="登录"]'

    def test_no_locators_for_empty(self):
        assert build_locators({}) == []


class TestElementMeta:
    def test_element_type_for_submit_is_button(self):
        assert element_type({"tag": "input", "inputType": "submit"}) == "button"

    def test_element_type_for_anchor_is_link(self):
        assert element_type({"tag": "a"}) == "link"

    def test_heuristic_name_prefers_label(self):
        assert heuristic_name({"label": "用户名", "placeholder": "x"}) == "用户名"

    def test_heuristic_name_input_type_fallback(self):
        assert heuristic_name({"tag": "input", "inputType": "password"}) == "密码框"

    def test_build_element_key_ascii_and_unique(self):
        used: set = set()
        first = build_element_key({"name": "username"}, 1, used)
        second = build_element_key({"name": "username"}, 2, used)
        assert first == "username"
        assert second == "username_2"


class TestNormalizeElements:
    def _raw(self, index, **overrides):
        attrs = {
            "tag": "input",
            "inputType": "text",
            "name": f"field{index}",
            "placeholder": "请输入",
        }
        attrs.update(overrides)
        return attrs

    def test_drops_elements_without_locators(self):
        elements = normalize_elements([{"tag": "input"}, self._raw(1)])
        assert len(elements) == 1

    def test_dedupes_same_fingerprint(self):
        elements = normalize_elements([self._raw(1), self._raw(1)])
        assert len(elements) == 1

    def test_llm_name_applied(self):
        raw = apply_llm_names(
            [self._raw(1)],
            {"1": {"name": "用户名输入框", "type": "input", "description": "登录账号"}},
        )
        elements = normalize_elements(raw)
        assert elements[0]["name"] == "用户名输入框"
        assert elements[0]["description"] == "登录账号"

    def test_fingerprint_fields(self):
        fp = build_fingerprint({"tag": "input", "inputType": "password", "name": "pwd"})
        assert fp["tag"] == "input"
        assert fp["input_type"] == "password"
        assert fp["name"] == "pwd"


class TestParseLlmNames:
    def test_plain_json(self):
        assert parse_llm_names('{"1": {"name": "用户名"}}') == {"1": {"name": "用户名"}}

    def test_fenced_json(self):
        text = '```json\n{"1": {"name": "用户名"}}\n```'
        assert parse_llm_names(text)["1"]["name"] == "用户名"

    def test_json_with_surrounding_text(self):
        text = '结果如下:{"2": {"name": "登录按钮"}} 完成'
        assert parse_llm_names(text)["2"]["name"] == "登录按钮"

    def test_invalid_returns_empty(self):
        assert parse_llm_names("not json") == {}
        assert parse_llm_names("") == {}


def test_probe_key_ignores_probe_trigger_and_frame():
    """探针增量 diff 键:同属性元素键一致,probeTrigger/frame 不参与。"""
    from app.services.page_extractor import _probe_key

    base = {"tag": "input", "inputType": "text", "id": "name", "cssPath": "div > input"}
    a = dict(base, probeTrigger="新增", frame="http://x/iframe.html")
    b = dict(base)
    assert _probe_key(a) == _probe_key(b)
    assert _probe_key(dict(base, id="other")) != _probe_key(b)


def test_normalize_element_keeps_probe_trigger():
    """探针元素归一化后保留 probeTrigger;基线元素为空串。"""
    from app.services.page_extractor import normalize_element

    used: set = set()
    probed = normalize_element(
        {"tag": "input", "placeholder": "设备编号", "probeTrigger": "新增"}, 1, used
    )
    assert probed["probeTrigger"] == "新增"
    base = normalize_element({"tag": "button", "text": "查询"}, 2, used)
    assert base["probeTrigger"] == ""
