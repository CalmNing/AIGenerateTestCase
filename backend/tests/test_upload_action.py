"""上传动作与上传元素抽取的纯逻辑测试(不开浏览器、不依赖 Playwright)。

覆盖两类回归:
  1. 抽取侧——框架上传组件的 file input 天生隐藏,必须仍被抽成 upload 元素并带可用定位器;
  2. 执行侧——upload_file 走 set_input_files 而非 fill/click,且不能等 visible(隐藏元素必然超时)。
"""
import pytest

from app.services.page_extractor import (
    build_locators,
    element_type,
    heuristic_name,
    normalize_elements,
)
from app.services.page_object_runner import _perform, _upload_paths

# ---------------------------------------------------------------------------
# 抽取侧
# ---------------------------------------------------------------------------

# Element UI 图片卡片上传:input 隐藏且无 name,靠框架固定 class 定名
EL_UPLOAD_ATTRS = {
    "tag": "input",
    "inputType": "file",
    "class": "el-upload__input",
    "accept": "image/*",
    "cssPath": "html > body > div > input",
    "isFileInput": True,
}

# antd 按钮式上传:input 隐藏,可见触发体是外层 span,文案通过 triggerText 传下来
ANTD_UPLOAD_ATTRS = {
    "tag": "input",
    "inputType": "file",
    "class": "ant-upload-input",
    "accept": "image/*",
    "cssPath": "html > body > span > input",
    "triggerText": "上传图片",
    "isFileInput": True,
}

# 自定义 div 触发器:text 为空、只有 + 图标
CUSTOM_TRIGGER_ATTRS = {
    "tag": "div",
    "class": "upload-box",
    "text": "",
    "cssPath": "html > body > div:nth-of-type(3)",
    "uploadTrigger": True,
    "triggerText": "",
}


class TestUploadElementExtraction:
    def test_file_input_is_typed_as_upload(self):
        assert element_type(EL_UPLOAD_ATTRS) == "upload"
        assert element_type({"tag": "input", "inputType": "text"}) == "input"

    def test_name_falls_back_to_trigger_text(self):
        assert heuristic_name(ANTD_UPLOAD_ATTRS) == "上传图片"

    def test_name_falls_back_to_file_upload_when_trigger_has_no_text(self):
        assert heuristic_name(EL_UPLOAD_ATTRS) == "文件上传"

    def test_custom_trigger_without_text_gets_generic_name(self):
        assert heuristic_name(CUSTOM_TRIGGER_ATTRS) == "上传控件"

    def test_locator_prefers_framework_class_over_css_path(self):
        locators = build_locators(EL_UPLOAD_ATTRS)
        values = [loc["value"] for loc in locators]
        assert 'input[type="file"].el-upload__input' in values
        # 语义 class 必须排在易漂移的 cssPath 之前
        assert values.index('input[type="file"].el-upload__input') < values.index(
            "html > body > div > input"
        )

    def test_accept_locator_is_last_resort(self):
        # accept 可能被同页多个上传框命中,只能当兜底,不能盖过 cssPath
        locators = build_locators({**ANTD_UPLOAD_ATTRS, "class": ""})
        values = [loc["value"] for loc in locators]
        assert values.index("html > body > span > input") < values.index(
            'input[type="file"][accept="image/*"]'
        )

    def test_hidden_file_input_survives_normalization(self):
        elements = normalize_elements([EL_UPLOAD_ATTRS, ANTD_UPLOAD_ATTRS])
        assert len(elements) == 2
        assert {el["type"] for el in elements} == {"upload"}
        assert all(el["locators"] for el in elements)

    def test_extract_js_collects_file_inputs_without_visibility_gate(self):
        from app.services.page_extractor import EXTRACT_JS

        assert "FILE_SELECTOR" in EXTRACT_JS
        assert "isFileInput: true" in EXTRACT_JS
        # 文件输入走了独立扫描,不能被主循环的可见性过滤拦住
        assert "queryAllDeep(FILE_SELECTOR)" in EXTRACT_JS


# ---------------------------------------------------------------------------
# 执行侧
# ---------------------------------------------------------------------------


class FakeLocator:
    """记录调用序列的假定位器;is_file 决定走 set_input_files 还是 file chooser。"""

    def __init__(self, *, is_file=True, calls=None):
        self._is_file = is_file
        self.calls = calls if calls is not None else []

    async def evaluate(self, _expr):
        return self._is_file

    async def set_input_files(self, paths):
        self.calls.append(("set_input_files", tuple(paths)))

    async def click(self):
        self.calls.append(("click",))

    async def wait_for(self, state=None, timeout=None):
        self.calls.append(("wait_for", state))


class FakeLocatorRoot:
    def __init__(self, first):
        self.first = first


class FakeFileChooser:
    def __init__(self, calls):
        self.calls = calls

    async def set_files(self, paths):
        self.calls.append(("chooser.set_files", tuple(paths)))


class _ChooserInfo:
    def __init__(self, chooser):
        self._chooser = chooser

    @property
    def value(self):
        async def _resolve():
            return self._chooser

        return _resolve()


class FakePage:
    def __init__(self, calls):
        self.calls = calls

    def expect_file_chooser(self, timeout=None):
        self.calls.append(("expect_file_chooser", timeout))
        page = self

        class _Ctx:
            async def __aenter__(self):
                return _ChooserInfo(FakeFileChooser(page.calls))

            async def __aexit__(self, *_exc):
                return False

        return _Ctx()


class TestUploadPaths:
    def test_single_path(self, tmp_path):
        target = tmp_path / "a.png"
        target.write_bytes(b"x")
        assert _upload_paths(str(target)) == [str(target)]

    def test_multiple_paths_split_by_separator(self, tmp_path):
        first = tmp_path / "a.png"
        second = tmp_path / "b.png"
        first.write_bytes(b"a")
        second.write_bytes(b"b")
        raw = f"{first}, {second}\n{first}"
        assert _upload_paths(raw) == [str(first), str(second), str(first)]

    def test_quoted_path_is_unwrapped(self, tmp_path):
        target = tmp_path / "a b.png"
        target.write_bytes(b"x")
        assert _upload_paths(f'"{target}"') == [str(target)]

    def test_empty_value_raises(self):
        with pytest.raises(ValueError, match="缺少文件路径"):
            _upload_paths("   ")

    def test_missing_file_raises_with_path(self, tmp_path):
        missing = tmp_path / "nope.png"
        with pytest.raises(ValueError, match="待上传文件不存在"):
            _upload_paths(str(missing))

    def test_directory_raises(self, tmp_path):
        with pytest.raises(ValueError, match="目录"):
            _upload_paths(str(tmp_path))


class TestPerformUpload:
    def test_upload_waits_attached_not_visible(self, tmp_path):
        """隐藏的 file input 等 visible 必然超时,必须用 attached。"""
        target = tmp_path / "a.png"
        target.write_bytes(b"x")
        locator = FakeLocator(is_file=True)
        resolved = {"kind": "locator", "locator": FakeLocatorRoot(locator)}

        import asyncio

        asyncio.run(
            _perform(resolved, {"action": "upload_file", "value": str(target)}, None)
        )

        assert ("wait_for", "attached") in locator.calls
        assert ("wait_for", "visible") not in locator.calls
        assert ("set_input_files", (str(target),)) in locator.calls

    def test_upload_action_aliases(self, tmp_path):
        target = tmp_path / "a.png"
        target.write_bytes(b"x")

        import asyncio

        for action in ("upload_file", "upload", "set_input_files", "UPLOAD_FILE"):
            locator = FakeLocator(is_file=True)
            resolved = {"kind": "locator", "locator": FakeLocatorRoot(locator)}
            asyncio.run(_perform(resolved, {"action": action, "value": str(target)}, None))
            assert ("set_input_files", (str(target),)) in locator.calls, action

    def test_non_input_target_falls_back_to_file_chooser(self, tmp_path):
        """用户选中的若是可见触发器(自定义 div),退回拦截文件选择器 + 点击。"""
        target = tmp_path / "a.png"
        target.write_bytes(b"x")
        calls: list = []
        locator = FakeLocator(is_file=False, calls=calls)
        resolved = {"kind": "locator", "locator": FakeLocatorRoot(locator)}
        page = FakePage(calls)

        import asyncio

        asyncio.run(
            _perform(resolved, {"action": "upload_file", "value": str(target)}, page)
        )

        assert ("click",) in calls
        assert ("chooser.set_files", (str(target),)) in calls
        assert not any(call[0] == "set_input_files" for call in calls)

    def test_non_input_target_without_page_reports_actionable_error(self, tmp_path):
        target = tmp_path / "a.png"
        target.write_bytes(b"x")
        locator = FakeLocator(is_file=False)
        resolved = {"kind": "locator", "locator": FakeLocatorRoot(locator)}

        import asyncio

        with pytest.raises(ValueError, match="file input"):
            asyncio.run(
                _perform(resolved, {"action": "upload_file", "value": str(target)}, None)
            )

    def test_missing_file_never_reports_success(self):
        """文件不存在必须抛错,否则用例会假通过。"""
        locator = FakeLocator(is_file=True)
        resolved = {"kind": "locator", "locator": FakeLocatorRoot(locator)}

        import asyncio

        with pytest.raises(ValueError, match="不存在"):
            asyncio.run(
                _perform(
                    resolved,
                    {"action": "upload_file", "value": r"F:\nope\missing.png"},
                    None,
                )
            )
        assert not any(call[0] == "set_input_files" for call in locator.calls)
