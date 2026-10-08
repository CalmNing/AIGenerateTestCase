"""page_object_codegen 代码导出测试。"""
from app.services.page_object_codegen import render_page_object_code, suggest_file_name

PAGE_OBJECT = {
    "name": "Login",
    "url": "http://a.com/#/login",
    "elements": [
        {
            "key": "username",
            "name": "用户名输入框",
            "locators": [
                {"strategy": "testid", "value": "login-username", "stability": 100},
                {"strategy": "label", "value": "用户名", "stability": 92},
            ],
        },
        {
            "key": "login-btn",
            "name": "登录按钮",
            "locators": [
                {"strategy": "role", "value": 'button[name="登录"]', "stability": 95},
            ],
        },
    ],
}

TREE_PAGE_OBJECT = {
    "name": "List",
    "url": "http://a.com/#/list",
    "elements": [
        {
            "key": "btn_add",
            "name": "新增",
            "type": "button",
            "locators": [{"strategy": "text", "value": "新增", "stability": 90}],
            "probePath": [],
            "children": [
                {
                    "key": "input_name",
                    "name": "名称输入框",
                    "type": "input",
                    "locators": [{"strategy": "placeholder", "value": "请输入名称", "stability": 80}],
                    "probePath": ["新增"],
                    "children": [],
                },
            ],
        },
    ],
}


class TestRenderPageObjectCode:
    def test_module_and_class(self):
        code = render_page_object_code(PAGE_OBJECT)
        assert "from playwright.async_api import Locator, Page" in code
        assert "class LoginPage:" in code

    def test_property_primary_locator(self):
        code = render_page_object_code(PAGE_OBJECT)
        assert "def username(self) -> Locator:" in code
        assert "self.page.get_by_test_id('login-username')" in code

    def test_backup_locators_present(self):
        code = render_page_object_code(PAGE_OBJECT)
        assert "BACKUP_LOCATORS" in code
        assert "self.page.get_by_label('用户名')" in code

    def test_role_expression(self):
        code = render_page_object_code(PAGE_OBJECT)
        assert "self.page.get_by_role('button', name='登录')" in code

    def test_class_name_appends_page(self):
        code = render_page_object_code({"name": "Login", "elements": []})
        assert "class LoginPage:" in code

    def test_empty_elements_still_valid(self):
        code = render_page_object_code({"name": "Blank", "elements": []})
        assert "def __init__(self, page: Page)" in code
        # 无子元素时不生成浮层辅助方法
        assert "_OVERLAY_PATHS" not in code
        assert "open_for" not in code

    def test_nested_child_generates_overlay_helper(self):
        code = render_page_object_code(TREE_PAGE_OBJECT)
        # 子元素也导出属性
        assert "def input_name(self) -> Locator:" in code
        assert "def btn_add(self) -> Locator:" in code
        # 祖先链映射 + 打开浮层方法
        assert "_OVERLAY_PATHS" in code
        assert '"input_name": [\'btn_add\']' in code
        assert "async def open_for(self, prop: str)" in code
        assert "async def _open_overlay(self, prop: str)" in code
        # 子元素定位器本身
        assert "self.page.get_by_placeholder('请输入名称')" in code

    def test_flat_elements_no_overlay_methods(self):
        code = render_page_object_code(PAGE_OBJECT)
        assert "_OVERLAY_PATHS" not in code


class TestSuggestFileName:
    def test_named(self):
        assert suggest_file_name("Login Page") == "login_page.py"

    def test_fallback_for_non_ascii(self):
        assert suggest_file_name("登录页") == "page_object.py"
