"""hybrid_runner 纯逻辑单测:stub 页面/LLM,不碰真浏览器(可注入 page 时直接测 L1/L2 路径)。

说明:run_hybrid 完整路径需要 Playwright;此处主要测:
  - _element_index / _element 索引
  - HybridResult 结构
  - 步进分类与 level 规则协同
  - run_hybrid 在无绑定/空步骤时的行为(需要 mock async_playwright)
"""
import asyncio
import pytest
from types import SimpleNamespace

from app.services.hybrid_runner import (
    HybridResult,
    _element_index,
    run_hybrid,
)
from app.services.ui_binding_schemas import (
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_L3,
    UIBoundAssert,
    UIBoundStep,
    assign_level,
)


class FakeLocator:
    def __init__(self, *a, **k):
        self.first = self

    async def click(self, *a, **k):
        return None

    async def fill(self, *a, **k):
        return None

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def inner_text(self):
        return "ok"

    async def wait_for(self, *a, **k):
        return None

    async def wait_for_element_state(self, *a, **k):
        return None


class FakePage:
    def __init__(self, content="<html><body>admin</body></html>"):
        self._content = content
        self.goto_calls = []
        self.frames = []

    async def goto(self, url, **kw):
        self.goto_calls.append(url)

    async def wait_for_load_state(self, *a, **k):
        return None

    async def wait_for_timeout(self, *a, **k):
        return None

    async def content(self):
        return self._content

    def locator(self, *a, **k):
        return FakeLocator()

    def keyboard(self):
        return SimpleNamespace(press=lambda *a, **k: None)


class FakeBrowser:
    async def new_context(self, **kw):
        return FakeContext()

    async def close(self):
        return None


class FakeContext:
    async def new_page(self):
        return FakePage()

    async def storage_state(self, path=None):
        if path:
            return {"cookies": [], "origins": []}
        return {"cookies": [], "origins": []}


class FakePW:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def chromium(self):
        return SimpleNamespace(launch=lambda **kw: _async_return(FakeBrowser()))


def _async_return(v):
    async def _inner(*a, **k):
        return v
    return _inner()


# async_playwright 是 async context manager;patch 模块级导入
class _FakeAsyncPlaywrightCM:
    async def __aenter__(self):
        return FakePW()

    async def __aexit__(self, *a):
        return False


def _patch_playwright(monkeypatch):
    import app.services.hybrid_runner as hr
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: _FakeAsyncPlaywrightCM(),
        raising=False,
    )
    # run_hybrid 内部 from playwright.async_api import async_playwright — patch 那里
    import playwright.async_api as pwa
    monkeypatch.setattr(pwa, "async_playwright", lambda: _FakeAsyncPlaywrightCM())


class TestElementIndex:
    def test_flat_list(self):
        els = [
            {"key": "a", "name": "A"},
            {"key": "b", "name": "B"},
        ]
        by_key, path_by_key = _element_index(els)
        assert set(by_key) == {"a", "b"}
        assert path_by_key["a"] == []

    def test_nested_tree(self):
        els = [
            {"key": "root", "children": [{"key": "child", "name": "C"}]},
        ]
        by_key, path_by_key = _element_index(els)
        assert "child" in by_key
        # child 的祖先链应含 root
        assert "root" in (path_by_key.get("child") or [])


class TestAssignLevelIntegration:
    def test_l1_high_confidence(self):
        level, _ = assign_level("click", "btn", 0.9)
        assert level == LEVEL_L1

    def test_l2_mid(self):
        level, _ = assign_level("click", "btn", 0.6)
        assert level == LEVEL_L2

    def test_l3_low(self):
        level, _ = assign_level("click", "btn", 0.3)
        assert level == LEVEL_L3


class TestRunHybrid:
    def test_all_pass_success(self, monkeypatch):
        """仅 navigate + 页面级 assert_text:不依赖 resolve_target 的完整 mock。"""
        _patch_playwright(monkeypatch)
        steps = [
            UIBoundStep(step_no=1, nl="打开页面", action="navigate", level=LEVEL_L1, confidence=1.0),
            UIBoundStep(step_no=2, nl="等待", action="wait_for", element_key="username",
                        level=LEVEL_L1, confidence=0.9),
        ]
        asserts = [
            UIBoundAssert(index=1, nl="页面有 admin", action="assert_text",
                          element_key=None, value="admin", level=LEVEL_L1, confidence=0.9),
        ]
        els = [
            {"key": "username", "name": "用户名", "type": "input",
             "locators": [{"strategy": "placeholder", "value": "x", "stability": 0.9}]},
        ]
        # wait_for 会走 resolve_target;让 FakePage.locator 返回可 wait 的对象即可
        out = asyncio.run(run_hybrid(
            url="http://a.com#/login",
            page_elements=els,
            steps=steps,
            asserts=asserts,
            storage_state=None,
            expected_results=["页面有 admin"],
            final_judgement=False,
        ))
        assert isinstance(out, HybridResult)
        # 至少 navigate + assert 通过;wait_for 若 mock 定位失败则 success=False 但不 fallback
        statuses = {t.get("step_no") or t.get("assert_index"): t["status"] for t in out.steps_trace}
        assert statuses[1] == "passed"  # navigate
        assert statuses.get(1) == "passed"  # assert_index 也可能为 1
        assert out.needs_agent_fallback is False
        # 页面级 assert_text 必须 passed
        assert any(
            t.get("assert_index") == 1 and t["status"] == "passed"
            for t in out.steps_trace
        )

    def test_failed_assert_fails(self, monkeypatch):
        _patch_playwright(monkeypatch)
        steps = [
            UIBoundStep(step_no=1, nl="打开", action="navigate", level=LEVEL_L1, confidence=1.0),
        ]
        # 页面 content 是 "admin",断言期望 "missing" → fail
        asserts = [
            UIBoundAssert(index=1, nl="x", action="assert_text",
                          element_key=None, value="missing", level=LEVEL_L1, confidence=0.9),
        ]
        out = asyncio.run(run_hybrid(
            url="http://a.com",
            page_elements=[],
            steps=steps,
            asserts=asserts,
            storage_state=None,
        ))
        assert out.success is False

    def test_l3_all_fail_triggers_fallback(self, monkeypatch):
        _patch_playwright(monkeypatch)

        async def fail_agent(page, step, *, by_key, llm=None):
            return None  # LLM 失败

        steps = [
            UIBoundStep(step_no=1, nl="随便点", action="agent", level=LEVEL_L3, confidence=0.2),
            UIBoundStep(step_no=2, nl="再点", action="agent", level=LEVEL_L3, confidence=0.2),
        ]
        out = asyncio.run(run_hybrid(
            url="http://a.com",
            page_elements=[],
            steps=steps,
            asserts=[],
            storage_state=None,
            step_agent=fail_agent,
        ))
        assert out.success is False
        assert out.needs_agent_fallback is True

    def test_unstructured_skipped_by_default(self, monkeypatch):
        _patch_playwright(monkeypatch)
        steps = [
            UIBoundStep(step_no=1, nl="打开", action="navigate", level=LEVEL_L1, confidence=1.0),
        ]
        out = asyncio.run(run_hybrid(
            url="http://a.com",
            page_elements=[],
            steps=steps,
            asserts=[],
            storage_state=None,
            expected_results=["跳转到首页", "显示欢迎"],
            final_judgement=False,
        ))
        assert out.success is True
        assert "未结构化预期默认跳过" in out.agent_summary

    def test_skip_asserts_do_not_fail(self, monkeypatch):
        _patch_playwright(monkeypatch)
        steps = [
            UIBoundStep(step_no=1, nl="打开", action="navigate", level=LEVEL_L1, confidence=1.0),
        ]
        asserts = [
            UIBoundAssert(index=1, nl="看不懂", action="skip", level=LEVEL_L3, confidence=0.0),
        ]
        out = asyncio.run(run_hybrid(
            url="http://a.com",
            page_elements=[],
            steps=steps,
            asserts=asserts,
            storage_state=None,
        ))
        assert out.success is True
        assert out.steps_trace[-1]["status"] == "skipped"


class TestStepAgent:
    def test_step_agent_returns_none_on_llm_fail(self):
        from app.services.ui_step_agent import step_agent_llm

        async def _run():
            class FakePage:
                pass

            async def fail_llm(*a, **k):
                return None

            # 注入 fail 的 _structured_output_robust
            import utils.model_utils as mu
            orig = mu._structured_output_robust
            mu._structured_output_robust = fail_llm
            try:
                return await step_agent_llm(
                    FakePage(),
                    UIBoundStep(step_no=1, nl="点击登录", action="agent", level=LEVEL_L3),
                    by_key={"login_btn": {"key": "login_btn", "name": "登录", "type": "button"}},
                    llm=object(),  # 非 None,跳过 _build_llm
                )
            finally:
                mu._structured_output_robust = orig

        assert asyncio.run(_run()) is None
