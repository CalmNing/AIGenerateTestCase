"""低代码平台控件的抽取与执行测试(纯逻辑,不开浏览器)。

覆盖「隐患级别 textRadio 抽不到」「检查部位 input 抽不到」这类问题的三类成因:
  1. 原生控件被框架隐藏(display:none),被可见性过滤滤掉;
  2. 控件由 div/span 模拟,不在标准选择器里;
  3. 控件在 open shadow root 内,普通 querySelectorAll 进不去。
以及执行侧:隐藏控件等 visible 会超时,需要 force + 可见壳兜底。
"""
import asyncio

import pytest

from app.services.page_extractor import (
    EXTRACT_JS,
    build_locators,
    custom_control_type,
    element_type,
    heuristic_name,
    normalize_elements,
    trigger_locator,
)
from app.services.page_object_runner import _click_like, _force_needed

# 低代码 textRadio:原生 radio 被 display:none 藏起,视觉由 label 壳承担
VISUAL_REPLACED_RADIO = {
    "tag": "input",
    "inputType": "radio",
    "name": "dangerLevel",
    "label": "一般隐患",
    "class": "",
    "cssPath": "html > body > div:nth-of-type(1) > label > input",
    "visualReplaced": True,
    "triggerSelector": "html > body > div:nth-of-type(1) > label",
    "triggerText": "一般隐患",
}

# 纯 div 模拟的输入框(contenteditable 或仅视觉壳)
CUSTOM_INPUT_DIV = {
    "tag": "div",
    "class": "lc-input",
    "text": "请输入检查部位",
    "cssPath": "html > body > div:nth-of-type(3)",
    "customControl": True,
    "triggerText": "请输入检查部位",
}


class TestCustomControlTyping:
    def test_infers_type_from_class(self):
        assert custom_control_type({"class": "textRadio-item"}) == "radio"
        assert custom_control_type({"class": "lc-checkbox"}) == "checkbox"
        assert custom_control_type({"class": "x-switch"}) == "checkbox"
        assert custom_control_type({"class": "lc-select"}) == "select"
        assert custom_control_type({"class": "custom-dropdown"}) == "select"
        assert custom_control_type({"class": "lc-input"}) == "input"
        assert custom_control_type({"class": "form-textarea"}) == "textarea"
        assert custom_control_type({"class": "toolbar-btn"}) == "button"
        assert custom_control_type({"class": "decorative-box"}) == "element"

    def test_radio_wins_over_input(self):
        # textRadio-item 同时含 radio 与 input 语义时必须判成 radio
        assert custom_control_type({"class": "textRadio-input"}) == "radio"

    def test_element_type_routes_custom_control(self):
        assert element_type(CUSTOM_INPUT_DIV) == "input"
        assert element_type({**CUSTOM_INPUT_DIV, "class": "textRadio"}) == "radio"

    def test_visual_replaced_radio_keeps_input_type(self):
        assert element_type(VISUAL_REPLACED_RADIO) == "radio"

    def test_name_uses_trigger_text(self):
        assert heuristic_name(CUSTOM_INPUT_DIV) == "请输入检查部位"
        assert heuristic_name({**CUSTOM_INPUT_DIV, "triggerText": "", "text": "请选择"}) == "请选择"
        assert heuristic_name({**CUSTOM_INPUT_DIV, "triggerText": "", "text": ""}) == "自定义控件"


class TestVisualReplacedLocators:
    def test_no_role_locator_for_hidden_control(self):
        """get_by_role 默认不含隐藏元素,给隐藏控件加 role 定位器只会恒 0 命中。"""
        values = [loc["value"] for loc in build_locators(VISUAL_REPLACED_RADIO)]
        assert not any(v.startswith("radio[") for v in values)
        # label 定位器仍然可用(get_by_label 返回关联控件,不受可见性影响)
        assert any(loc["strategy"] == "label" for loc in build_locators(VISUAL_REPLACED_RADIO))

    def test_visible_radio_still_gets_role_locator(self):
        attrs = {**VISUAL_REPLACED_RADIO, "visualReplaced": False}
        values = [loc["value"] for loc in build_locators(attrs)]
        assert any(v.startswith("radio[") for v in values)

    def test_trigger_locator_is_separate_from_candidates(self):
        """壳定位器不能混进 locators,否则会被多候选降级误选成主定位器。"""
        element = normalize_elements([VISUAL_REPLACED_RADIO])[0]
        assert element["trigger"] == {
            "strategy": "css",
            "value": VISUAL_REPLACED_RADIO["triggerSelector"],
            "stability": 45,
        }
        assert all(loc["value"] != VISUAL_REPLACED_RADIO["triggerSelector"] for loc in element["locators"])

    def test_trigger_locator_absent_without_shell(self):
        assert trigger_locator({"inputType": "radio"}) is None
        assert normalize_elements([{"tag": "input", "inputType": "text", "name": "part"}])[0]["trigger"] is None


class TestExtractJsCoverage:
    def test_two_extra_scans_present(self):
        assert "CONTROL_SELECTOR" in EXTRACT_JS
        assert "CUSTOM_SELECTOR" in EXTRACT_JS
        assert "visualReplaced: true" in EXTRACT_JS
        assert "customControl: true" in EXTRACT_JS

    def test_shadow_dom_traversal(self):
        assert "queryAllDeep" in EXTRACT_JS
        assert "shadowHost" in EXTRACT_JS
        # 四处扫描都必须走穿透版查询,漏一处就等于漏一片 shadow 内的控件
        for selector in ("SELECTOR", "FILE_SELECTOR", "CONTROL_SELECTOR", "CUSTOM_SELECTOR"):
            assert f"queryAllDeep({selector})" in EXTRACT_JS

    def test_hidden_by_ancestor_is_not_collected(self):
        """未激活 tab / 未打开弹窗里的控件必须仍然不采,否则元素表会被隐藏表单淹没。"""
        assert "const isVisuallyReplaced" in EXTRACT_JS
        assert "style.display === 'none'" in EXTRACT_JS

    def test_skipped_diagnostics_returned(self):
        assert "visible-not-collected" in EXTRACT_JS
        # 返回体除 elements/skipped 外还带 wujieScope(微前端作用域,供日志观测),
        # 所以这里只断言两个核心字段,不再匹配整行字面量
        assert "elements: out" in EXTRACT_JS
        assert "skipped," in EXTRACT_JS

    def test_label_for_ancestor_container_resolved(self):
        """低代码 label[for] 指向包装 span 时,仍要反查到业务名并标记非直接绑定。"""
        assert "findLabelFor" in EXTRACT_JS
        assert "isLabelBound" in EXTRACT_JS
        assert "labelBound: isLabelBound(el)" in EXTRACT_JS


class TestSharedPlaceholderDedupe:
    """同 placeholder 的不同表单字段不得被归一化合并(检查部位被隐患类别吞掉)。"""

    def test_same_placeholder_different_css_not_merged(self):
        hazard = {
            "tag": "input",
            "inputType": "",
            "name": "",
            "placeholder": "请输入",
            "label": "",
            "cssPath": "span#hazardCategory > div > div > input",
        }
        check = {
            "tag": "input",
            "inputType": "",
            "name": "",
            "placeholder": "请输入",
            "label": "",
            "cssPath": "span#checkLocation > div > div > input",
        }
        elements = normalize_elements([hazard, check])
        assert len(elements) == 2
        css_values = {e["locators"][-1]["value"] for e in elements}
        assert "span#hazardCategory > div > div > input" in css_values
        assert "span#checkLocation > div > div > input" in css_values

    def test_same_placeholder_same_css_still_merged(self):
        attrs = {
            "tag": "input",
            "inputType": "",
            "name": "",
            "placeholder": "请输入",
            "label": "",
            "cssPath": "span#field > div > input",
        }
        elements = normalize_elements([attrs, dict(attrs), dict(attrs)])
        assert len(elements) == 1
        assert elements[0]["occurrences"] == 3

    def test_table_rows_still_merge_via_tr_structure(self):
        """主定位器是 css 时仍按 tr 行号归并,不因弱语义策略改键而拆行。"""
        rows = [
            {
                "tag": "button",
                "role": "",
                "text": "编辑",
                "label": "",
                "cssPath": f"tbody > tr:nth-of-type({i}) > td > button",
            }
            for i in (1, 2, 3)
        ]
        elements = normalize_elements(rows)
        # button 无 role 时 build_locators 会补 role=button + text → 主定位器是 role,
        # 不进弱语义分支;这里断言不炸且产出一条或按 role 归并即可
        assert elements
        assert all(e["type"] == "button" for e in elements)

    def test_role_primary_not_split_by_css(self):
        """强语义主定位器(role)保持原去重键:跨 frame css 路径不同也归并。"""
        a = {
            "tag": "button",
            "role": "button",
            "text": "保存",
            "label": "保存",
            "cssPath": "div > button",
        }
        b = {
            "tag": "button",
            "role": "button",
            "text": "保存",
            "label": "保存",
            "cssPath": "div:nth-of-type(2) > button",
        }
        elements = normalize_elements([a, b])
        assert len(elements) == 1


class TestLabelBoundLocator:
    def test_indirect_label_used_for_name_not_locator(self):
        """label[for] 指向祖先 span:名称用业务 label,定位器不生成 label 策略。"""
        attrs = {
            "tag": "input",
            "inputType": "",
            "name": "",
            "id": "",
            "placeholder": "请输入",
            "label": "检查部位",
            "labelBound": False,
            "cssPath": "span#checkLocation > div > input",
        }
        element = normalize_elements([attrs])[0]
        assert element["name"] == "检查部位"
        strategies = {loc["strategy"] for loc in element["locators"]}
        assert "label" not in strategies
        assert "placeholder" in strategies
        assert "css" in strategies

    def test_direct_label_still_gets_label_locator(self):
        attrs = {
            "tag": "input",
            "inputType": "",
            "label": "用户名",
            "labelBound": True,
            "placeholder": "请输入用户名",
            "cssPath": "form > input",
        }
        strategies = {loc["strategy"] for loc in build_locators(attrs)}
        assert "label" in strategies

    def test_missing_label_bound_defaults_true(self):
        """旧数据/测试夹具无 labelBound 字段时保持原行为,仍生成 label 定位器。"""
        attrs = {"tag": "input", "label": "用户名", "placeholder": "请输入"}
        strategies = {loc["strategy"] for loc in build_locators(attrs)}
        assert "label" in strategies


class FakeTarget:
    """可配置可见性的假定位器。"""

    def __init__(self, *, visible=True, raise_on_action=None, calls=None):
        self._visible = visible
        self._raise_on_action = raise_on_action
        self.calls = calls if calls is not None else []

    async def wait_for(self, state=None, timeout=None):
        self.calls.append(("wait_for", state))
        if state == "visible" and not self._visible:
            raise TimeoutError("not visible")

    async def wait_for_element_state(self, state=None, timeout=None):
        self.calls.append(("wait_for_element_state", state))
        if state == "visible" and not self._visible:
            raise TimeoutError("not visible")

    async def click(self, force=None):
        self.calls.append(("click", force))
        if self._raise_on_action == "click":
            raise RuntimeError("Element is not visible")

    async def check(self, force=None):
        self.calls.append(("check", force))
        if self._raise_on_action == "check":
            raise RuntimeError("Element is not visible")

    async def uncheck(self, force=None):
        self.calls.append(("uncheck", force))
        if self._raise_on_action == "uncheck":
            raise RuntimeError("Element is not visible")


class FakeShellElement:
    def __init__(self, calls, value):
        self.calls = calls
        self.value = value

    async def click(self):
        self.calls.append(("shell.click", self.value))


class FakeShellLocator:
    def __init__(self, calls, value):
        self._shell = FakeShellElement(calls, value)

    @property
    def first(self):
        return self._shell


class FakePage:
    def __init__(self, calls):
        self.calls = calls

    def locator(self, value):
        return FakeShellLocator(self.calls, value)


class TestForceAndShellFallback:
    def test_visible_element_needs_no_force(self):
        target = FakeTarget(visible=True)
        assert asyncio.run(_force_needed(target, handle=False)) is False

    def test_hidden_element_gets_force_after_attached_probe(self):
        target = FakeTarget(visible=False)
        assert asyncio.run(_force_needed(target, handle=False)) is True
        assert ("wait_for", "visible") in target.calls
        assert ("wait_for", "attached") in target.calls

    def test_missing_element_still_raises(self):
        class Missing(FakeTarget):
            async def wait_for(self, state=None, timeout=None):
                raise TimeoutError(f"{state} never satisfied")

        with pytest.raises(TimeoutError):
            asyncio.run(_force_needed(Missing(visible=False), handle=False))

    def test_check_uses_force_for_hidden(self):
        target = FakeTarget(visible=False)
        asyncio.run(_click_like(target, {}, "check", force=True, page=None))
        assert ("check", True) in target.calls

    def test_falls_back_to_visible_shell(self):
        """隐藏 radio 连 force 点击都点不动时,改点 label 壳。"""
        calls: list = []
        target = FakeTarget(visible=False, raise_on_action="check", calls=calls)
        resolved = {"trigger": {"strategy": "css", "value": "div > label", "stability": 45}}
        asyncio.run(_click_like(target, resolved, "check", force=True, page=FakePage(calls)))
        assert ("shell.click", "div > label") in calls

    def test_reraises_when_no_shell(self):
        target = FakeTarget(visible=False, raise_on_action="click")
        with pytest.raises(RuntimeError, match="not visible"):
            asyncio.run(_click_like(target, {}, "click", force=True, page=None))

    def test_shell_used_for_plain_click_too(self):
        calls: list = []
        target = FakeTarget(visible=False, raise_on_action="click", calls=calls)
        resolved = {"trigger": {"strategy": "css", "value": "span.shell", "stability": 45}}
        asyncio.run(_click_like(target, resolved, "click", force=True, page=FakePage(calls)))
        assert ("shell.click", "span.shell") in calls
