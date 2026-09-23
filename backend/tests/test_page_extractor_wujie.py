"""wujie 微前端作用域选择测试(静态断言,不碰浏览器)。

背景:页面上可能并存多个 <wujie-app>,每个承载一个子应用。穿透 shadow root 时
必须只进「当前路由对应的那个」,否则会把预加载/已切走的子应用元素混进来。

实测该站点同页挂着 `/sg-security/`(1172 节点/79 控件)与 `mmt-ai`(38 节点/0 控件)
两个 wujie-app,且**两个都 visible 且铺满视口**,靠可见性区分不了。

采集起根(2026-09):微前端页面只从选中子应用的 shadowRoot 起根 —— 主框架的
header/侧栏/面包屑/二级菜单在 light DOM 里,从 document 起根会整片采进基线;
非微前端页面回落 document,行为不变。计数与探针触发定位同一根。
"""
from app.services.page_extractor import (
    EXTRACT_JS,
    FIND_POINT_JS,
    FIND_TRIGGERS_JS,
    PAGE_COUNT_JS,
    WUJIE_SCOPE_JS,
)


class TestWujieScopeSnippet:
    def test_recognises_wujie_app_hosts(self):
        assert "data-wujie-id" in WUJIE_SCOPE_JS
        assert "WUJIE-APP" in WUJIE_SCOPE_JS

    def test_matches_current_url_before_falling_back(self):
        """data-wujie-id 规范化后出现在当前地址里时优先选中(用户的用法:
        data-wujie-id="/basic-data-web" 对应地址里的 basic-data-web 段)。"""
        assert "location.href" in WUJIE_SCOPE_JS
        assert "href.indexOf(id) >= 0" in WUJIE_SCOPE_JS

    def test_falls_back_to_largest_shadow(self):
        """URL 匹配不上时兜底取 shadow 节点最多的那个 —— 当前真正在渲染的子应用。

        实测该站点地址里没有 "sg-security",只能靠这条兜底选中正确的子应用。
        """
        assert "shadowRoot.querySelectorAll('*').length" in WUJIE_SCOPE_JS
        assert "bestCount" in WUJIE_SCOPE_JS

    def test_zero_or_one_host_is_used_directly(self):
        """0/1 个 wujie-app 时不做任何筛选,普通页面完全不受影响。"""
        assert "apps.length <= 1" in WUJIE_SCOPE_JS


class TestScopeWiring:
    def test_both_scripts_share_one_scope_decision(self):
        """采集脚本与落稳计数脚本必须用同一套作用域判定,否则计数和采集对不上。"""
        for js in (EXTRACT_JS, PAGE_COUNT_JS):
            assert "data-wujie-id" in js
            assert "pickWujieScope" in js
            assert "isOtherWujie" in js

    def test_shadow_traversal_skips_sibling_wujie_apps(self):
        """穿透 shadow 的循环里必须排除旁支 wujie 子应用。

        实测:想旁支子应用的 shadow 注入一个可见按钮,旧逻辑会把它采进来,
        新逻辑排除 —— 这条守住别再退回去。
        """
        for js in (EXTRACT_JS, PAGE_COUNT_JS):
            assert "isOtherWujie(el)" in js

    def test_extract_reports_scope_for_observability(self):
        """采集结果回传实际使用的 wujie 作用域,日志里可确认采的是哪个子应用。"""
        assert "wujieScope:" in EXTRACT_JS
        assert "WUJIE_ID_ATTR" in EXTRACT_JS


class TestShadowRootAsQueryRoot:
    """微前端页面起根只允许是选中子应用的 shadowRoot,不允许从 document 起根采壳。"""

    def test_extract_roots_at_selected_shadow(self):
        assert "extractRoots" in EXTRACT_JS
        assert "wujieScope.shadowRoot" in EXTRACT_JS
        # 非微前端/取不到 shadow 时必须回落 document,普通页面不能被误伤
        assert "wujieScope.shadowRoot ? [wujieScope.shadowRoot] : [document]" in EXTRACT_JS

    def test_page_count_uses_same_root(self):
        """落稳计数与采集同一根,否则计数含壳、采集不含,落稳判据对不上。"""
        assert "wujieScope.shadowRoot ? wujieScope.shadowRoot : document" in PAGE_COUNT_JS

    def test_probe_trigger_search_roots_at_shadow(self):
        """探针触发词只在子框架 shadow 内找:document 查会落空(壳上无触发按钮),
        同时避免误点主框架壳上的同名词按钮。"""
        for js in (FIND_TRIGGERS_JS, FIND_POINT_JS):
            assert "wujieScope" in js
            assert "wujieScope.shadowRoot" in js
            assert "searchRoot" in js
