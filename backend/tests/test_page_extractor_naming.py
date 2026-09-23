"""元素命名回退测试(P2)。

背景:原兜底是 `f"{tag}元素"` —— 页面上三个无文案图标按钮全叫「button元素」,
用户无从区分;innerText 的换行也没处理,「数字孪生平台\\n在建」这种整段文本被
直接当成元素名;表单 label 的尾部冒号(「隐患类别:」)原样带进名字。
"""
from app.services.page_extractor import (
    EXTRACT_JS,
    _clean_name,
    _disambiguate_names,
    heuristic_name,
    normalize_element,
)


class TestCleanName:
    def test_collapses_newlines(self):
        assert _clean_name("数字孪生平台\n在建") == "数字孪生平台 在建"

    def test_collapses_runs_of_whitespace(self):
        assert _clean_name("  查看 \n\t 编辑  ") == "查看 编辑"

    def test_strips_trailing_colon(self):
        assert _clean_name("隐患类别:") == "隐患类别"
        assert _clean_name("隐患类别：") == "隐患类别"

    def test_none_and_empty(self):
        assert _clean_name(None) == ""
        assert _clean_name("   ") == ""


class TestHeuristicName:
    def test_label_wins_over_text(self):
        assert heuristic_name({"label": "用户名", "text": "请输入"}) == "用户名"

    def test_uses_title_for_icon_buttons(self):
        """图标按钮没有文案,语义常在 title 上 —— 不采只剩「按钮」。"""
        assert heuristic_name({"tag": "button", "title": "刷新"}) == "刷新"

    def test_aria_label_wins_over_title(self):
        assert heuristic_name({"ariaLabel": "关闭", "title": "close"}) == "关闭"

    def test_text_is_flattened(self):
        assert heuristic_name({"tag": "span", "text": "数字孪生平台\n在建"}) == "数字孪生平台 在建"

    def test_falls_back_to_input_type_label(self):
        assert heuristic_name({"tag": "input", "inputType": "number"}) == "数字框"


class TestTypeLabelFallback:
    def test_unnamed_button_gets_chinese_label(self):
        """回归:原兜底是「button元素」,零信息量。"""
        assert normalize_element({"tag": "button"}, 1, set())["name"] == "按钮"

    def test_unnamed_input_gets_chinese_label(self):
        assert normalize_element({"tag": "input"}, 1, set())["name"] == "输入框"

    def test_unnamed_unknown_tag_falls_back_to_element(self):
        assert normalize_element({"tag": "marquee"}, 1, set())["name"] == "元素"

    def test_llm_name_still_wins(self):
        element = normalize_element({"tag": "button", "llmName": "提交表单"}, 1, set())
        assert element["name"] == "提交表单"


class TestDisambiguateNames:
    def test_duplicates_get_suffix(self):
        elements = [{"name": "按钮"}, {"name": "按钮"}, {"name": "按钮"}, {"name": "新增隐患"}]
        _disambiguate_names(elements)
        assert [e["name"] for e in elements] == ["按钮1", "按钮2", "按钮3", "新增隐患"]

    def test_unique_names_untouched(self):
        elements = [{"name": "查看"}, {"name": "编辑"}, {"name": "删除"}]
        _disambiguate_names(elements)
        assert [e["name"] for e in elements] == ["查看", "编辑", "删除"]

    def test_empty_names_are_counted_too(self):
        elements = [{"name": ""}, {"name": ""}]
        _disambiguate_names(elements)
        assert [e["name"] for e in elements] == ["1", "2"]


class TestCollectGaps:
    def test_collects_title_attribute(self):
        assert "title: flat(el.getAttribute('title'), 60)" in EXTRACT_JS

    def test_flattens_text_fields(self):
        assert "const flat = (value, limit)" in EXTRACT_JS
        assert "flat(el.innerText || el.textContent, 60)" in EXTRACT_JS
