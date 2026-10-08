"""element_tree 纯函数单测:建树 / 扫平 / 执行索引 / 浮层状态迁移 / 无效步骤元素。"""
from app.services.element_tree import (
    TRIGGER_GROUP_TYPE,
    build_element_tree,
    build_execution_index,
    collect_invalid_step_elements,
    count_elements,
    element_path_labels,
    filter_tree,
    find_by_key,
    flatten_elements,
    format_invalid_elements_message,
    map_tree,
    open_state_diff,
)


def _el(key: str, name: str, **extra) -> dict:
    node = {
        "key": key,
        "name": name,
        "type": "button" if "btn" in key else "input",
        "locators": [{"strategy": "text", "value": name, "stability": 80}],
        "probePath": [],
        "probeTrigger": "",
    }
    node.update(extra)
    return node


class TestBuildElementTree:
    def test_baseline_roots(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el("input_q", "搜索框"),
        ])
        assert [n["key"] for n in tree] == ["btn_add", "input_q"]
        assert all(n.get("children") == [] for n in tree)

    def test_one_level_attach_to_trigger_button(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el("input_name", "名称输入框", probePath=["新增"], probeTrigger="新增"),
            _el("btn_save", "保存", probePath=["新增"], probeTrigger="新增"),
        ])
        btn = next(n for n in tree if n["key"] == "btn_add")
        child_keys = [c["key"] for c in btn["children"]]
        assert child_keys == ["input_name", "btn_save"]
        # 触发按钮自身仍是根,有定位器
        assert btn["locators"]

    def test_two_level_nested(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el("btn_adv", "高级设置", probePath=["新增"], probeTrigger="新增"),
            _el(
                "input_adv",
                "高级字段",
                probePath=["新增", "高级设置"],
                probeTrigger="新增/高级设置",
            ),
        ])
        btn_add = next(n for n in tree if n["key"] == "btn_add")
        btn_adv = next(c for c in btn_add["children"] if c["key"] == "btn_adv")
        deep = [c["key"] for c in btn_adv["children"]]
        assert deep == ["input_adv"]

    def test_missing_trigger_creates_virtual_group(self):
        tree = build_element_tree([
            _el(
                "input_x",
                "孤儿字段",
                probePath=["未采到的按钮"],
                probeTrigger="未采到的按钮",
            ),
        ])
        assert len(tree) == 1
        group = tree[0]
        assert group["type"] == TRIGGER_GROUP_TYPE
        assert group["synthetic"] is True
        assert group["locators"] == []
        assert [c["key"] for c in group["children"]] == ["input_x"]

    def test_nested_missing_inner_trigger(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el(
                "input_deep",
                "深层字段",
                probePath=["新增", "二级按钮"],
                probeTrigger="新增/二级按钮",
            ),
        ])
        btn_add = next(n for n in tree if n["key"] == "btn_add")
        assert len(btn_add["children"]) == 1
        group = btn_add["children"][0]
        assert group["type"] == TRIGGER_GROUP_TYPE
        assert group["probePath"] == ["新增", "二级按钮"]
        assert [c["key"] for c in group["children"]] == ["input_deep"]

    def test_legacy_probe_trigger_string_path(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el("input_name", "名称", probeTrigger="新增"),
        ])
        btn = next(n for n in tree if n["key"] == "btn_add")
        assert [c["key"] for c in btn["children"]] == ["input_name"]

    def test_llm_renamed_trigger_matches_by_containment(self):
        # 按钮原文「新增隐患」,LLM 命名后叫「新增」;弹窗元素 probePath 仍是原文
        tree = build_element_tree([
            _el("btn_add", "新增", fingerprint={"text": "新增隐患"}),
            _el("input_h", "隐患描述", probePath=["新增隐患"], probeTrigger="新增隐患"),
        ])
        btn = next(n for n in tree if n["key"] == "btn_add")
        assert [c["key"] for c in btn["children"]] == ["input_h"]


class TestFlatten:
    def test_roundtrip_keys(self):
        flat = [
            _el("btn_add", "新增"),
            _el("input_name", "名称", probePath=["新增"], probeTrigger="新增"),
        ]
        tree = build_element_tree(flat)
        again = flatten_elements(tree)
        assert [e["key"] for e in again] == ["btn_add", "input_name"]
        assert all("children" not in e for e in again)

    def test_count(self):
        tree = build_element_tree([
            _el("a", "A"),
            _el("b", "B", probePath=["A"], probeTrigger="A"),
        ])
        assert count_elements(tree) == 2


class TestMapFilterFind:
    def test_map_tree_updates_nested(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el("input_name", "名称", probePath=["新增"], probeTrigger="新增"),
        ])
        mapped = map_tree(tree, lambda n: n.update({"name": "X"}) or None)
        assert mapped[0]["name"] == "X"
        assert mapped[0]["children"][0]["name"] == "X"

    def test_filter_tree_drops_subtree(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el("input_name", "名称", probePath=["新增"], probeTrigger="新增"),
            _el("btn_edit", "编辑"),
        ])
        out = filter_tree(tree, lambda n: n["key"] != "btn_add")
        keys = [n["key"] for n in out]
        assert keys == ["btn_edit"]

    def test_find_by_key_deep(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el(
                "input_deep",
                "深层",
                probePath=["新增", "高级"],
                probeTrigger="新增/高级",
            ),
        ])
        assert find_by_key(tree, "input_deep") is not None
        assert find_by_key(tree, "missing") is None


class TestExecutionIndex:
    def test_root_empty_path(self):
        tree = build_element_tree([_el("btn_add", "新增"), _el("q", "搜索")])
        by_key, path_by_key = build_execution_index(tree)
        assert path_by_key["btn_add"] == []
        assert path_by_key["q"] == []
        assert by_key["btn_add"]["locators"]

    def test_child_ancestor_chain(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el("btn_adv", "高级设置", probePath=["新增"], probeTrigger="新增"),
            _el(
                "input_deep",
                "深层",
                probePath=["新增", "高级设置"],
                probeTrigger="新增/高级设置",
            ),
            _el("input_name", "名称", probePath=["新增"], probeTrigger="新增"),
        ])
        by_key, path_by_key = build_execution_index(tree)
        assert path_by_key["input_name"] == ["btn_add"]
        assert path_by_key["input_deep"] == ["btn_add", "btn_adv"]
        assert path_by_key["btn_adv"] == ["btn_add"]
        assert "children" not in by_key["input_deep"]

    def test_labels(self):
        tree = build_element_tree([
            _el("btn_add", "新增"),
            _el("input_name", "名称", probePath=["新增"], probeTrigger="新增"),
        ])
        labels = dict(element_path_labels(tree))
        assert labels["input_name"] == "新增 / 名称"


class TestOpenStateDiff:
    def test_cold_start_opens_all(self):
        to_close, to_open = open_state_diff([], ["a", "b"])
        assert to_close == []
        assert to_open == ["a", "b"]

    def test_sibling_switch(self):
        to_close, to_open = open_state_diff(["a"], ["b"])
        assert to_close == ["a"]
        assert to_open == ["b"]

    def test_lca_keeps_prefix(self):
        to_close, to_open = open_state_diff(["a", "b"], ["a", "c"])
        assert to_close == ["b"]
        assert to_open == ["c"]

    def test_deeper_target_only_opens_suffix(self):
        to_close, to_open = open_state_diff(["a"], ["a", "b", "c"])
        assert to_close == []
        assert to_open == ["b", "c"]

    def test_close_to_root(self):
        to_close, to_open = open_state_diff(["a", "b"], [])
        assert to_close == ["b", "a"]
        assert to_open == []


class TestCollectInvalidStepElements:
    def test_collects_disabled_in_step_order(self):
        tree = [
            _el("btn_ok", "可用按钮"),
            _el("input_bad", "无效输入", enabled=False),
            _el("btn_bad", "无效按钮", enabled=False),
        ]
        steps = [
            {"element": "btn_bad", "action": "click"},
            {"element": "btn_ok", "action": "click"},
            {"element": "input_bad", "action": "fill", "value": "x"},
            {"element": "input_bad", "action": "clear"},
        ]
        invalid = collect_invalid_step_elements(steps, tree)
        assert [e["key"] for e in invalid] == ["btn_bad", "input_bad"]

    def test_nested_disabled_and_enabled_treated_as_valid(self):
        tree = [{
            **_el("btn_open", "打开"),
            "children": [
                _el("input_name", "名称", probePath=["打开"], probeTrigger="打开"),
                _el("input_dead", "死字段", probePath=["打开"], probeTrigger="打开", enabled=False),
            ],
        }]
        steps = [
            {"element": "input_name", "action": "fill", "value": "a"},
            {"element": "input_dead", "action": "fill", "value": "b"},
        ]
        invalid = collect_invalid_step_elements(steps, tree)
        assert [e["key"] for e in invalid] == ["input_dead"]

    def test_no_steps_or_no_disabled_returns_empty(self):
        tree = [_el("a", "A", enabled=False)]
        assert collect_invalid_step_elements([], tree) == []
        assert collect_invalid_step_elements([{"element": "a"}], [_el("a", "A")]) == []

    def test_missing_enabled_field_treated_as_valid(self):
        tree = [_el("a", "A")]
        assert collect_invalid_step_elements([{"element": "a"}], tree) == []

    def test_format_message(self):
        msg = format_invalid_elements_message([
            {"key": "username", "name": "用户名输入框"},
            {"key": "submit", "name": "submit"},
        ])
        assert msg == "步骤中存在无效的元素: 用户名输入框(username)、submit"
