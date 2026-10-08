"""ui_step_binder 纯函数单测:不碰真 LLM/真浏览器。"""
import pytest

from app.services.ui_step_binder import (
    _all_l3,
    _to_bound_steps,
    build_asserts_from_expected,
    match_page_object,
    normalize_incoming_binding,
)
from app.services.ui_binding_schemas import (
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_L3,
    LLMBoundStep,
    assign_level,
)
from db.models import PageObject


def _po(id_=1, origin="http://a.com", path="#/login", **kw):
    return PageObject(
        id=id_,
        name=kw.get("name", "LoginPage"),
        site_origin=origin,
        page_path=path,
        url=f"{origin}/{path.lstrip('#') if path.startswith('#') else path}",
        elements=kw.get("elements", [
            {
                "key": "username",
                "name": "用户名输入框",
                "type": "input",
                "locators": [{"strategy": "placeholder", "value": "请输入用户名", "stability": 0.9}],
            },
            {
                "key": "login_btn",
                "name": "登录按钮",
                "type": "button",
                "locators": [{"strategy": "role", "value": 'button[name="登录"]', "stability": 0.95}],
            },
            {
                "key": "disabled_x",
                "name": "禁用",
                "type": "button",
                "enabled": False,
                "locators": [],
            },
        ]),
    )


class TestMatchPageObject:
    def test_exact_origin_and_path(self):
        pos = [_po(1, path="#/login"), _po(2, path="#/home")]
        got = match_page_object(pos, "http://a.com#/login")
        assert got is not None and got.id == 1

    def test_origin_mismatch(self):
        assert match_page_object([_po(1)], "http://b.com#/login") is None

    def test_longest_path_wins(self):
        pos = [
            _po(1, origin="http://a.com", path="/"),
            _po(2, origin="http://a.com", path="#/login"),
        ]
        got = match_page_object(pos, "http://a.com#/login")
        assert got is not None and got.id == 2

    def test_empty_target(self):
        assert match_page_object([_po(1)], "") is None

    def test_no_candidates(self):
        assert match_page_object([], "http://a.com#/login") is None


class TestAssignLevel:
    def test_l1(self):
        level, _ = assign_level("click", "login_btn", 0.9)
        assert level == LEVEL_L1

    def test_l2(self):
        level, _ = assign_level("click", "login_btn", 0.6)
        assert level == LEVEL_L2

    def test_l3_low_confidence(self):
        level, _ = assign_level("click", "login_btn", 0.3)
        assert level == LEVEL_L3

    def test_l3_agent_action(self):
        level, _ = assign_level("agent", None, 0.9)
        assert level == LEVEL_L3

    def test_l3_missing_key(self):
        level, _ = assign_level("click", None, 0.9)
        assert level == LEVEL_L3


class TestToBoundSteps:
    def test_happy_path(self):
        nl = ["打开登录页", "输入用户名 admin", "点击登录"]
        llm = [
            LLMBoundStep(step_no=1, action="navigate", element_key=None, confidence=1.0),
            LLMBoundStep(step_no=2, action="fill", element_key="username", value="admin", confidence=0.9),
            LLMBoundStep(step_no=3, action="click", element_key="login_btn", confidence=0.95),
        ]
        bound, unmatched = _to_bound_steps(nl, llm, {"username", "login_btn"})
        assert unmatched == []
        assert [b.level for b in bound] == [LEVEL_L1, LEVEL_L1, LEVEL_L1]
        assert bound[1].value == "admin"

    def test_invalid_key_downgrades_to_agent(self):
        nl = ["点击登录"]
        llm = [LLMBoundStep(step_no=1, action="click", element_key="nope", confidence=0.9)]
        bound, unmatched = _to_bound_steps(nl, llm, {"username"})
        assert unmatched == ["nope"]
        assert bound[0].level == LEVEL_L3
        assert bound[0].action == "agent"

    def test_count_mismatch_returns_empty(self):
        nl = ["步骤1", "步骤2"]
        llm = [LLMBoundStep(step_no=1, action="click", element_key="username", confidence=0.9)]
        bound, errors = _to_bound_steps(nl, llm, {"username"})
        assert bound == []
        assert errors

    def test_all_l3_helper(self):
        steps = _all_l3(["a", "b"], "no po")
        assert len(steps) == 2
        assert all(s.level == LEVEL_L3 and s.action == "agent" for s in steps)


class TestAsserts:
    def test_skip_when_no_llm(self):
        out = build_asserts_from_expected(["跳转首页", "显示欢迎"], {"x"}, None)
        assert len(out) == 2
        assert all(a.action == "skip" and a.level == LEVEL_L3 for a in out)

    def test_valid_assert_text_with_key(self):
        from app.services.ui_binding_schemas import LLMBoundAssert
        llm = [LLMBoundAssert(index=1, action="assert_text", element_key="username", value="admin", confidence=0.9)]
        out = build_asserts_from_expected(["用户名回显 admin"], {"username"}, llm)
        assert out[0].action == "assert_text"
        assert out[0].level == LEVEL_L1

    def test_missing_key_becomes_skip(self):
        from app.services.ui_binding_schemas import LLMBoundAssert
        llm = [LLMBoundAssert(index=1, action="assert_visible", element_key="ghost", confidence=0.9)]
        out = build_asserts_from_expected(["可见"], {"username"}, llm)
        assert out[0].action == "skip"
        assert out[0].level == LEVEL_L3


class TestNormalizeIncomingBinding:
    def test_normalize_roundtrip(self):
        raw = {
            "page_object_id": 3,
            "steps": [{"step_no": 1, "nl": "点击", "action": "click", "element_key": "b", "confidence": 0.9}],
            "asserts": [{"index": 1, "nl": "可见", "action": "assert_visible", "element_key": "b"}],
            "compile_status": "ok",
        }
        out = normalize_incoming_binding(raw)
        assert out["page_object_id"] == 3
        assert out["steps"][0]["level"] in (LEVEL_L1, LEVEL_L2, LEVEL_L3)
        assert out["asserts"][0]["index"] == 1

    def test_non_dict(self):
        assert normalize_incoming_binding(None) == {}
