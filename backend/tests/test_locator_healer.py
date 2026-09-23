"""locator_healer 相似度/解析/自愈测试(使用 fake page,不碰真浏览器)。"""
import asyncio
import json as _json

from app.services.locator_healer import (
    apply_heal,
    best_match,
    count_via_js,
    heal_from_candidates,
    merge_elements,
    merge_locators,
    promote_locator,
    resolve_locator,
    resolve_target,
    similarity,
    validate_element,
)
from app.services.locator_strategies import (
    js_match_all,
    locator_expression,
    playwright_locator,
)
from app.services.page_extractor import build_fingerprint


class FakeLocator:
    def __init__(self, count):
        self._count = count

    async def count(self):
        return self._count


class FakePage:
    """value → count 的简易映射;未命中返回 0。"""

    def __init__(self, counts=None):
        self.counts = counts or {}

    def _make(self, value):
        return FakeLocator(self.counts.get(value, 0))

    def get_by_test_id(self, value):
        return self._make(value)

    def get_by_role(self, role, name=None):
        return self._make(f"{role}[name=\"{name}\"]" if name else role)

    def get_by_label(self, value):
        return self._make(value)

    def get_by_placeholder(self, value):
        return self._make(value)

    def get_by_text(self, value, exact=False):
        return self._make(value)

    def locator(self, value):
        return self._make(value)


def _element(locators, key="username"):
    return {"key": key, "name": "用户名", "locators": locators}


class TestSimilarity:
    def test_identical_is_one(self):
        fp = build_fingerprint({"tag": "input", "inputType": "text", "name": "username"})
        assert similarity(fp, fp) == 1.0

    def test_no_known_fields_is_zero(self):
        assert similarity({}, {"tag": "input"}) == 0.0

    def test_partial_match(self):
        source = build_fingerprint({"tag": "input", "name": "username", "inputType": "text"})
        candidate = build_fingerprint({"tag": "input", "name": "username", "inputType": "password"})
        assert 0 < similarity(source, candidate) < 1

    def test_mismatch_is_zero(self):
        source = build_fingerprint({"tag": "input", "name": "username"})
        candidate = build_fingerprint({"tag": "button", "name": "submit"})
        assert similarity(source, candidate) == 0.0


class TestBestMatch:
    def test_picks_above_threshold(self):
        source = build_fingerprint({"tag": "input", "name": "username", "inputType": "text"})
        candidates = [
            {"tag": "button", "name": "login"},
            {"tag": "input", "inputType": "text", "name": "username"},
        ]
        index, score, _second = best_match(source, candidates)
        assert index == 1
        assert score == 1.0

    def test_returns_none_below_threshold(self):
        source = build_fingerprint({"tag": "input", "name": "username"})
        index, score, _second = best_match(source, [{"tag": "button", "name": "login"}])
        assert index is None
        assert score == 0.0

    def test_empty_candidates(self):
        assert best_match({}, []) == (None, 0.0, 0.0)


class TestMergeLocators:
    def test_new_locators_promoted(self):
        existing = [{"strategy": "css", "value": "form > input", "stability": 50}]
        merged = merge_locators(existing, {"testId": "login-username", "tag": "input"})
        assert merged[0]["strategy"] == "testid"

    def test_dedupes(self):
        existing = [{"strategy": "name", "value": "username", "stability": 82}]
        merged = merge_locators(existing, {"tag": "input", "name": "username"})
        assert len([m for m in merged if m["value"] == "username"]) == 1

    def test_capped(self):
        existing = [{"strategy": "css", "value": f".c{i}", "stability": 50 - i} for i in range(10)]
        merged = merge_locators(existing, {"tag": "input", "name": "x"})
        assert len(merged) <= 6


class TestApplyHeal:
    def test_writeback_increments_count(self):
        element = _element([{"strategy": "css", "value": "old", "stability": 50}])
        event = apply_heal(element, {"tag": "input", "testId": "login-username"}, 0.9)
        assert element["heal_count"] == 1
        assert element["locators"][0]["strategy"] == "testid"
        assert event["from"] == "old"
        assert event["to"] == "login-username"
        assert event["similarity"] == 0.9


class TestPromoteLocator:
    def test_already_primary_returns_none(self):
        element = _element([
            {"strategy": "testid", "value": "login-username", "stability": 100},
            {"strategy": "css", "value": "form > input", "stability": 50},
        ])
        assert promote_locator(element, "testid", "login-username") is None

    def test_fallback_promoted_and_counted(self):
        element = _element([
            {"strategy": "testid", "value": "gone", "stability": 100},
            {"strategy": "css", "value": "form > input", "stability": 50},
        ])
        event = promote_locator(element, "css", "form > input")
        assert element["locators"][0]["value"] == "form > input"
        assert element["heal_count"] == 1
        assert event["reason"] == "fallback"
        assert event["from"] == "gone"
        assert event["to"] == "form > input"

    def test_unknown_locator_returns_none(self):
        element = _element([{"strategy": "css", "value": "old", "stability": 50}])
        assert promote_locator(element, "testid", "missing") is None


class TestHealFromCandidates:
    def test_rescan_match_writeback(self):
        element = _element([{"strategy": "css", "value": "old", "stability": 50}])
        element["fingerprint"] = build_fingerprint({"tag": "input", "name": "username", "inputType": "text"})
        candidates = [{"tag": "input", "name": "username", "inputType": "text", "testId": "login-username"}]
        event = heal_from_candidates(element, candidates)
        assert event is not None
        assert event["reason"] == "rescan"
        assert element["locators"][0]["strategy"] == "testid"
        assert element["heal_count"] == 1

    def test_no_similar_candidate_returns_none(self):
        element = _element([{"strategy": "css", "value": "old", "stability": 50}])
        element["fingerprint"] = build_fingerprint({"tag": "input", "name": "username"})
        assert heal_from_candidates(element, [{"tag": "button", "name": "login"}]) is None


class TestMergeElements:
    def _old(self, key, name, value, heal=2):
        return {
            "key": key,
            "name": name,
            "type": "input",
            "description": "用户编辑的描述",
            "enabled": False,
            "fingerprint": build_fingerprint({"tag": "input", "name": key}),
            "locators": [{"strategy": "css", "value": value, "stability": 50}],
            "heal_count": heal,
        }

    def test_key_match_preserves_user_edits(self):
        old = self._old("username", "我改过的名字", "old-css")
        new = {
            "key": "username",
            "name": "规则命名",
            "type": "input",
            "fingerprint": build_fingerprint({"tag": "input", "name": "username"}),
            "locators": [{"strategy": "testid", "value": "login-username", "stability": 100}],
        }
        merged = merge_elements([old], [new])
        assert len(merged) == 1
        assert merged[0]["name"] == "我改过的名字"
        assert merged[0]["description"] == "用户编辑的描述"
        assert merged[0]["enabled"] is False
        assert merged[0]["heal_count"] == 2
        assert merged[0]["locators"][0]["strategy"] == "testid"

    def test_primary_locator_match_when_key_changed(self):
        old = self._old("old_key", "旧名", "css-x", heal=0)
        new = {
            "key": "new_key",
            "name": "新名",
            "type": "input",
            "fingerprint": build_fingerprint({"tag": "input", "name": "username"}),
            "locators": [{"strategy": "css", "value": "css-x", "stability": 50}],
        }
        merged = merge_elements([old], [new])
        assert merged[0]["key"] == "old_key"
        assert merged[0]["name"] == "旧名"

    def test_similarity_match(self):
        old = self._old("username", "用户名", "gone-css", heal=1)
        new = {
            "key": "username_field",
            "name": "用户名输入框",
            "type": "input",
            "fingerprint": build_fingerprint({"tag": "input", "name": "username", "inputType": "text"}),
            "locators": [{"strategy": "testid", "value": "login-username", "stability": 100}],
        }
        merged = merge_elements([old], [new])
        assert merged[0]["name"] == "用户名"
        assert merged[0]["heal_count"] == 1

    def test_unmatched_dropped_and_added(self):
        old = self._old("removed", "移除的元素", "removed-css")
        new = {
            "key": "added",
            "name": "新元素",
            "type": "button",
            "fingerprint": build_fingerprint({"tag": "button", "name": "submit"}),
            "locators": [{"strategy": "css", "value": "button", "stability": 50}],
        }
        merged = merge_elements([old], [new])
        assert [e["key"] for e in merged] == ["added"]

    def test_empty_old_returns_new(self):
        new = [{"key": "a", "name": "A", "locators": [], "fingerprint": {}}]
        assert merge_elements([], new) == new


class TestResolveLocator:
    def test_primary_hit_index_zero(self):
        element = _element([
            {"strategy": "testid", "value": "login-username", "stability": 100},
            {"strategy": "css", "value": "form > input", "stability": 50},
        ])
        page = FakePage({"login-username": 1, "form > input": 1})
        result = asyncio.run(resolve_locator(page, element))
        assert result["index"] == 0
        assert result["strategy"] == "testid"

    def test_fallback_hit_higher_index(self):
        element = _element([
            {"strategy": "testid", "value": "gone", "stability": 100},
            {"strategy": "css", "value": "form > input", "stability": 50},
        ])
        page = FakePage({"form > input": 1})
        result = asyncio.run(resolve_locator(page, element))
        assert result["index"] == 1
        assert result["strategy"] == "css"

    def test_all_miss_returns_none(self):
        element = _element([{"strategy": "testid", "value": "gone", "stability": 100}])
        assert asyncio.run(resolve_locator(FakePage(), element)) is None


class TestValidateElement:
    def test_reports_counts(self):
        element = _element([
            {"strategy": "testid", "value": "login-username", "stability": 100},
            {"strategy": "css", "value": "gone", "stability": 50},
        ])
        page = FakePage({"login-username": 1})
        result = asyncio.run(validate_element(page, element))
        assert result["matched"] is True
        assert result["locators"][0]["count"] == 1
        assert result["locators"][1]["matched"] is False


class FakeElement:
    pass


class FakeJSHandle:
    def __init__(self, element=None):
        self._element = element

    def as_element(self):
        return self._element


class FakeShadowFrame:
    """模拟 closed shadow root 内嵌 iframe:locator 引擎不可用,evaluate 可用。

    按 (strategy, value) 配置命中;命中时 evaluate 返回 3、evaluate_handle 返回元素。
    """

    def __init__(self, matches=None):
        self.matches = matches or set()

    def _hit(self, expr: str) -> bool:
        for strategy, value in self.matches:
            if f'"{strategy}"' in expr and _json.dumps(value, ensure_ascii=False) in expr:
                return True
        return False

    async def evaluate(self, expr):
        return 3 if self._hit(expr) else 0

    async def evaluate_handle(self, expr):
        return FakeJSHandle(FakeElement() if self._hit(expr) else None)


class TestJsFallback:
    def test_count_via_js_on_shadow_frame(self):
        frame = FakeShadowFrame({("placeholder", "全部")})
        assert asyncio.run(count_via_js(frame, "placeholder", "全部")) == 3
        assert asyncio.run(count_via_js(frame, "placeholder", "其他")) == 0

    def test_count_via_js_skips_plain_page(self):
        # FakePage 无 evaluate/evaluate_handle,不应触发兜底
        assert asyncio.run(count_via_js(FakePage(), "css", "x")) is None

    def test_resolve_target_uses_handle_when_locator_dead(self):
        element = _element([
            {"strategy": "role", "value": 'button[name="新增隐患"]', "stability": 95},
            {"strategy": "css", "value": "div > button", "stability": 50},
        ])
        frame = FakeShadowFrame({("role", 'button[name="新增隐患"]')})
        result = asyncio.run(resolve_target(frame, element))
        assert result["kind"] == "handle"
        assert result["strategy"] == "role"
        assert isinstance(result["handle"], FakeElement)

    def test_resolve_target_locator_wins_when_available(self):
        page = FakePage({"form > input": 1})
        element = _element([
            {"strategy": "testid", "value": "gone", "stability": 100},
            {"strategy": "css", "value": "form > input", "stability": 50},
        ])
        result = asyncio.run(resolve_target(page, element))
        assert result["kind"] == "locator"
        assert result["strategy"] == "css"

    def test_resolve_target_all_miss_returns_none(self):
        element = _element([{"strategy": "css", "value": "nope", "stability": 50}])
        assert asyncio.run(resolve_target(FakeShadowFrame(), element)) is None

    def test_validate_element_js_fallback_marks_matched(self):
        element = _element([{"strategy": "placeholder", "value": "全部", "stability": 85}])
        frame = FakeShadowFrame({("placeholder", "全部")})
        result = asyncio.run(validate_element(frame, element))
        assert result["matched"] is True
        assert result["locators"][0]["via_js"] is True
        assert result["locators"][0]["count"] == 3

    def test_validate_element_no_js_on_plain_page(self):
        element = _element([{"strategy": "css", "value": "gone", "stability": 50}])
        result = asyncio.run(validate_element(FakePage(), element))
        assert result["matched"] is False

    def test_id_strategy_uses_attribute_selector(self):
        # 裸 id 会被当标签名查询恒 0,必须映射为 [id="..."]
        page = FakePage({'[id="login-btn"]': 1})
        assert asyncio.run(playwright_locator(page, "id", "login-btn").count()) == 1
        assert '[id="login-btn"]' in locator_expression("id", "login-btn")

    def test_js_expr_embeds_value_safely(self):
        expr = js_match_all("text", 'he said "hi" <b>')
        assert _json.dumps('he said "hi" <b>', ensure_ascii=False) in expr
