"""深度递归探针测试:probePath / max_depth / 预算 / 嵌套关窗禁用重载。

不启动真浏览器 —— 用源码断言与纯函数单测守住递归语义。
"""
import inspect

from app.services import page_extractor
from app.services.page_extractor import (
    DEFAULT_TRIGGER_PATTERN,
    _ProbeBudget,
    _probe_recursive,
    _probe_trigger,
    _triggers_from_raw,
    extract_elements,
)


class TestTriggersFromRaw:
    def test_only_buttons_matching_pattern(self):
        items = [
            {"tag": "button", "text": "新增高级"},
            {"tag": "button", "text": "确定"},
            {"tag": "input", "text": "新增"},
            {"tag": "button", "text": "新增隐患"},
            {"tag": "a", "text": "编辑"},
        ]
        out = _triggers_from_raw(items, DEFAULT_TRIGGER_PATTERN, limit=8)
        assert "新增高级" in out
        assert "新增隐患" in out
        assert "编辑" in out
        assert "确定" not in out
        # input 标签不作为按钮触发词
        assert not any(
            item["tag"] == "input" and item.get("text") in out for item in items
        )

    def test_respects_limit(self):
        items = [{"tag": "button", "text": f"新增项目{i}"} for i in range(20)]
        out = _triggers_from_raw(items, DEFAULT_TRIGGER_PATTERN, limit=3)
        assert len(out) == 3

    def test_skips_non_dict(self):
        raw: list = [None, "x", {"tag": "button", "text": "新增"}]
        out = _triggers_from_raw([i for i in raw if isinstance(i, dict)], DEFAULT_TRIGGER_PATTERN, 8)
        assert out == ["新增"]


class TestProbeBudget:
    def test_consumes_until_max(self):
        b = _ProbeBudget(3)
        assert b.consume()
        assert b.consume()
        assert b.consume()
        assert not b.consume()
        assert b.used == 3

    def test_zero_budget(self):
        b = _ProbeBudget(0)
        assert not b.consume()


class TestNormalizePassesProbePath:
    def test_probe_path_on_normalized_element(self):
        element = page_extractor.normalize_element(
            {
                "tag": "input",
                "placeholder": "名称",
                "cssPath": "#name",
                "probePath": ["新增", "高级设置"],
                "probeTrigger": "新增/高级设置",
            },
            1,
            set(),
        )
        assert element["probePath"] == ["新增", "高级设置"]
        assert element["probeTrigger"] == "新增/高级设置"

    def test_baseline_empty_probe_path(self):
        element = page_extractor.normalize_element(
            {"tag": "button", "text": "搜索", "cssPath": ".search"},
            1,
            set(),
        )
        assert element["probePath"] == []


class TestRecursiveSourceGuards:
    def test_extract_elements_uses_recursive_probe(self):
        src = inspect.getsource(extract_elements)
        assert "_probe_recursive" in src
        assert "max_depth" in src
        assert "pending" in src

    def test_extract_elements_respects_max_depth_zero(self):
        """max_depth=0 完全跳过探针。"""
        src = inspect.getsource(extract_elements)
        assert "if max_depth > 0" in src.replace("if max_depth>0", "if max_depth > 0")

    def test_probe_recursive_signature(self):
        params = list(inspect.signature(_probe_recursive).parameters)
        assert params[0] == "page"
        assert "frame" not in params
        for name in ("path", "depth", "max_depth", "budget", "visited", "ancestor_keys"):
            assert name in params, f"缺少参数 {name}"

    def test_nested_close_disables_reload(self):
        """嵌套层(depth>0)关窗禁用 reload;仅 depth==0 允许。"""
        src = inspect.getsource(_probe_recursive)
        assert "allow_reload = depth == 0" in src or "allow_reload=depth == 0" in src

    def test_close_overlay_honors_allow_reload(self):
        src = inspect.getsource(page_extractor._close_overlay)
        assert "allow_reload" in src

    def test_probe_trigger_still_page_first_param(self):
        params = list(inspect.signature(_probe_trigger).parameters)
        assert params[0] == "page"
        assert "frame" not in params

    def test_recursion_guards_exist(self):
        src = inspect.getsource(_probe_recursive)
        assert "visited" in src
        assert "budget.consume" in src or "budget.consume()" in src
        assert "child_depth < max_depth" in src or "child_depth < max_depth" in src

    def test_probe_path_written_on_add(self):
        src = inspect.getsource(_probe_recursive)
        assert 'item["probePath"] = item_path' in src
        assert 'item["probeTrigger"]' in src


class TestDedupeIncludesProbePath:
    def test_baseline_and_dialog_not_merged(self):
        """与基线同指纹但带 probePath 的元素不得并入基线(否则子树残缺)。"""
        baseline = {
            "tag": "input",
            "inputType": "text",
            "name": "title",
            "placeholder": "请输入",
            "cssPath": "div > input",
        }
        dialog = dict(baseline, probePath=["新增隐患"], probeTrigger="新增隐患")
        elements = page_extractor.normalize_elements([baseline, dialog])
        assert len(elements) == 2
        by_path = {tuple(e.get("probePath") or []): e for e in elements}
        assert by_path[("新增隐患",)]["probeTrigger"] == "新增隐患"
        assert by_path[()]["probeTrigger"] == ""

    def test_same_dialog_still_merges_rows(self):
        """同一浮层内同结构表格行仍归并计次。"""
        rows = [
            {
                "tag": "button",
                "role": "button",
                "text": "编辑",
                "label": "编辑",
                "cssPath": f"tbody > tr:nth-of-type({i}) > td > button",
                "probePath": ["列表"],
            }
            for i in (1, 2, 3)
        ]
        elements = page_extractor.normalize_elements(rows)
        assert len(elements) == 1
        assert elements[0]["occurrences"] == 3
        assert elements[0]["probePath"] == ["列表"]

    def test_different_dialogs_not_merged(self):
        a = {
            "tag": "input",
            "inputType": "text",
            "name": "title",
            "placeholder": "请输入",
            "cssPath": "div > input",
            "probePath": ["新增隐患"],
        }
        b = dict(a, probePath=["编辑隐患"])
        elements = page_extractor.normalize_elements([a, b])
        assert len(elements) == 2
