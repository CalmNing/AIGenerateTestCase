"""探针浮层关闭策略测试(P1)。

背景:原来关不掉浮层就 `return False` 终止整轮探针。实测 wujie 子应用弹窗对
Escape 无响应,结果只探到前 1~2 个触发按钮 —— 命中从 306 掉到 153。

另外守住一个自己踩过的坑:为了容忍「取 消」这种带空格的渲染,曾把 FIND_POINT_JS
里的文案精确比较删掉,只剩 pattern 匹配 → 每个触发词都点到白名单里的第一个按钮,
后面的弹窗表单全都探不到(命中 275)。文案比较必须保留。
"""
import inspect
import re

from app.services import page_extractor
from app.services.page_extractor import (
    CLOSE_BUTTON_TEXTS,
    FIND_POINT_JS,
    _loose_text_pattern,
    _probe_trigger,
)


class TestLooseTextPattern:
    def test_tolerates_inner_whitespace(self):
        """按钮常被渲染成「取 消」「提 交」,正则要容忍字符间空白。"""
        pattern = _loose_text_pattern("取消")
        assert re.match(pattern, "取消")
        assert re.match(pattern, "取 消")
        assert re.match(pattern, " 取  消 ")
        assert not re.match(pattern, "取消并提交")
        assert not re.match(pattern, "重置")

    def test_blank_text_matches_nothing(self):
        assert _loose_text_pattern("") == r"^$"
        assert not re.match(_loose_text_pattern("  "), "任意文案")


class TestPointMatcher:
    def test_requires_exact_text_not_just_pattern(self):
        """回归守卫:FIND_POINT_JS 必须同时校验文案相等。

        只靠 pattern 会点到白名单里的第一个按钮,导致每个触发词重复点同一个地方,
        后续弹窗表单永远探不到。
        """
        assert "args.text" in FIND_POINT_JS, "FIND_POINT_JS 不再读取目标文案"
        assert "t === want" in FIND_POINT_JS, "文案精确比较被删掉了"

    def test_normalises_whitespace_before_compare(self):
        assert r"replace(/\s+/g, ' ')" in FIND_POINT_JS


class TestCloseStrategy:
    def test_has_multiple_close_candidates(self):
        assert "取消" in CLOSE_BUTTON_TEXTS
        assert any("关闭" in text for text in CLOSE_BUTTON_TEXTS)

    def test_reload_is_the_last_resort(self):
        src = inspect.getsource(page_extractor._close_overlay)
        assert "CLOSE_RELOAD_FALLBACK" in src
        assert "reload" in src

    def test_reload_waits_for_page_to_settle(self):
        """重载后要等落稳,否则后续探针会在半成品页面上找按钮。"""
        src = inspect.getsource(page_extractor._close_overlay)
        assert "_wait_for_settle" in src

    def test_close_verifies_overlay_actually_gone(self):
        """关闭判据是「本轮新增的浮层元素是否全部消失」,不看弹窗容器结构。"""
        src = inspect.getsource(page_extractor._close_overlay)
        assert "_overlay_gone" in src


class TestProbeResolvesFramePerCall:
    def test_probe_trigger_does_not_hold_a_frame_handle(self):
        """回归守卫:探针不能持有 frame 句柄。

        关浮层的兜底重载会重建页面,重载前抓的 frame 句柄全部失效 —— 外层若用
        `list(page.frames)` 快照遍历,后续探针会被静默跳过(实测命中 275 而非 306)。
        所以 `_probe_trigger` 只收 page,自己在当前 frame 列表里重新找按钮。
        """
        params = list(inspect.signature(_probe_trigger).parameters)
        assert "frame" not in params
        assert params[0] == "page"

    def test_extract_collects_triggers_before_probing(self):
        """触发文案先收集成列表再逐个探,而不是按 frame 快照嵌套遍历。"""
        src = inspect.getsource(page_extractor.extract_elements)
        assert "pending" in src
        # 逐个探测逻辑已下沉到 _probe_recursive,extract 侧仍先收集 pending
        assert "_probe_recursive" in src
        recursive_src = inspect.getsource(page_extractor._probe_recursive)
        assert "for text in pending" in recursive_src or "for text in pending" in src
