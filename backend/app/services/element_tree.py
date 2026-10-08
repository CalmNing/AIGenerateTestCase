"""页面元素树:扁平列表 ↔ 嵌套 children 互转与执行索引。

存储约定:
  - `PageObject.elements` 落库为嵌套列表(节点含 `children`);
  - 抽取/LLM 命名/去重等中间步骤仍操作扁平列表,元素上带 `probePath`
    (list[str],基线为 []) 与 `probeTrigger`(全路径字符串,兼容旧字段);
  - 触发按钮节点本身是基线元素(有 locators),探针采到的弹窗元素挂在它下面;
    按钮未被采到(无定位器)时用虚拟容器节点 `type="trigger_group"` 占位。

全部为纯函数,可直接单测,不依赖 Playwright/DB。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

TRIGGER_GROUP_KEY_PREFIX = "__trigger__::"
TRIGGER_GROUP_TYPE = "trigger_group"


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def is_trigger_group(element: dict) -> bool:
    return isinstance(element, dict) and (
        element.get("type") == TRIGGER_GROUP_TYPE
        or str(element.get("key") or "").startswith(TRIGGER_GROUP_KEY_PREFIX)
    )


def _element_match_segment(element: dict, segment: str) -> bool:
    """元素是否可作为路径段 segment 对应的触发节点。"""
    if not isinstance(element, dict) or is_trigger_group(element):
        return False
    want = _norm_text(segment)
    if not want:
        return False
    name = _norm_text(element.get("name"))
    if name == want:
        return True
    fp = element.get("fingerprint") or {}
    if _norm_text(fp.get("text")) == want:
        return True
    # 名称包含触发词(「新增隐患」按钮 LLM 命名为「新增」)或反之;至少 2 字防误配
    if name and min(len(name), len(want)) >= 2 and (want in name or name in want):
        return True
    return False


def _group_match_segment(element: dict, segment: str, path_prefix: tuple[str, ...]) -> bool:
    if not is_trigger_group(element):
        return False
    expected = list(path_prefix) + [segment]
    if element.get("probePath") == expected:
        return True
    name = _norm_text(element.get("name"))
    want = _norm_text(segment)
    return bool(want) and want in name


def _make_trigger_group(segment: str, path_prefix: tuple[str, ...]) -> dict:
    full = list(path_prefix) + [segment]
    return {
        "key": TRIGGER_GROUP_KEY_PREFIX + "/".join(full),
        "name": f"{segment} 弹窗",
        "type": TRIGGER_GROUP_TYPE,
        "description": "",
        "enabled": True,
        "fingerprint": {},
        "locators": [],
        "frame": "",
        "probeTrigger": "/".join(full),
        "probePath": full,
        "synthetic": True,
        "heal_count": 0,
        "last_healed_at": None,
        "children": [],
    }


def _find_in_level(level: list[dict], segment: str, path_prefix: tuple[str, ...]) -> Optional[dict]:
    for node in level:
        if _element_match_segment(node, segment) or _group_match_segment(node, segment, path_prefix):
            return node
    return None


def _ensure_path_container(roots: list[dict], path: list[str]) -> dict:
    """确保 roots 下存在 path 对应的容器链,返回末端容器节点(沿途可建虚拟组)。"""
    level = roots
    parent: Optional[dict] = None
    prefix: tuple[str, ...] = ()
    for seg in path:
        found = _find_in_level(level, seg, prefix)
        if found is None:
            found = _make_trigger_group(seg, prefix)
            level.append(found)
        parent = found
        prefix = prefix + (seg,)
        level = found.setdefault("children", [])
    if parent is None:
        raise ValueError("build_element_tree: 空 probePath 不应创建容器")
    return parent


def build_element_tree(flat_elements: Iterable[dict]) -> list[dict]:
    """扁平元素列表 → 嵌套树。

    - probePath 为空/缺失 → 根节点;
    - 非空:挂到「与完整 probePath 同名的触发按钮」children 下,按钮缺失则建虚拟组;
    - 按路径深度升序处理,保证父按钮先于深层元素入树。
    """
    roots: list[dict] = []
    pending: list[dict] = []

    for raw in flat_elements or []:
        if not isinstance(raw, dict):
            continue
        node = {k: v for k, v in raw.items() if k != "children"}
        path_raw = node.get("probePath")
        if isinstance(path_raw, (list, tuple)) and any(str(s).strip() for s in path_raw):
            path = [str(s) for s in path_raw if str(s).strip()]
        else:
            trigger = str(node.get("probeTrigger") or "")
            path = [p for p in (s.strip() for s in trigger.split("/")) if p] if trigger else []
        node["probePath"] = path
        node.setdefault("children", [])
        if not path:
            roots.append(node)
        else:
            pending.append(node)

    pending.sort(key=lambda n: (len(n.get("probePath") or []),))

    for node in pending:
        path = list(node.get("probePath") or [])
        # probePath=["新增"] → 父容器是「新增」按钮节点
        container = _ensure_path_container(roots, path)
        container.setdefault("children", []).append(node)

    return roots


def flatten_elements(tree: Iterable[dict]) -> list[dict]:
    """深度优先展开为扁平列表(剥掉 children;不修改原节点)。"""
    out: list[dict] = []

    def walk(nodes: Iterable[dict]) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            out.append({k: v for k, v in node.items() if k != "children"})
            walk(node.get("children") or [])

    walk(tree)
    return out


def count_elements(tree: Iterable[dict]) -> int:
    return len(flatten_elements(tree))


def map_tree(tree: Optional[list], fn: Callable[[dict], Optional[dict]]) -> list:
    """对每个节点应用 fn;返回新 list,children 递归。fn 可就地改节点。"""
    result: list = []
    for node in tree or []:
        if not isinstance(node, dict):
            continue
        replaced = fn(node)
        current = node if replaced is None else replaced
        if not isinstance(current, dict):
            continue
        current["children"] = map_tree(current.get("children") or [], fn)
        result.append(current)
    return result


def filter_tree(tree: Optional[list], pred: Callable[[dict], bool]) -> list:
    """pred 为假的节点连同其整棵子树一起丢弃;为真的保留并递归过滤 children。"""
    result: list = []
    for node in tree or []:
        if not isinstance(node, dict):
            continue
        if not pred(node):
            continue
        clone = dict(node)
        clone["children"] = filter_tree(node.get("children") or [], pred)
        result.append(clone)
    return result


def find_by_key(tree: Optional[list], key: str) -> Optional[dict]:
    for node in tree or []:
        if not isinstance(node, dict):
            continue
        if node.get("key") == key:
            return node
        found = find_by_key(node.get("children") or [], key)
        if found is not None:
            return found
    return None


def element_path_labels(tree: Optional[list]) -> list[tuple[str, str]]:
    """(key, 路径label),label 形如「新增 / 高级设置 / 名称输入框」。"""
    out: list[tuple[str, str]] = []

    def walk(nodes: Optional[list], prefix: str) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or node.get("key") or "")
            label = f"{prefix} / {name}" if prefix else name
            out.append((str(node.get("key") or ""), label))
            walk(node.get("children") or [], label)

    walk(tree, "")
    return out


def build_execution_index(tree: Optional[list]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """执行/校验用索引。

    Returns:
        by_key: key → 扫平元素(无 children);
        path_by_key: key → 祖先触发按钮 key 列表(根→叶,不含自身)。
    """
    by_key: dict[str, dict] = {}
    path_by_key: dict[str, list[str]] = {}

    def walk(nodes: Optional[list], ancestor_keys: list[str]) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            key = str(node.get("key") or "")
            flat = {k: v for k, v in node.items() if k != "children"}
            if key:
                by_key[key] = flat
                path_by_key[key] = list(ancestor_keys)
            children = node.get("children") or []
            if children:
                walk(children, ancestor_keys + ([key] if key else []))

    walk(tree, [])
    return by_key, path_by_key


def ancestors_for_key(
    path_by_key: dict[str, list[str]], key: str, by_key: dict[str, dict]
) -> list[dict]:
    """path_by_key[key] 祖先 key → 元素 dict(跳过缺失)。"""
    return [by_key[k] for k in (path_by_key.get(key) or []) if k in by_key]


def open_state_diff(opened: list[str], desired: list[str]) -> tuple[list[str], list[str]]:
    """浮层状态迁移:返回 (要关闭的 key,栈顶在前, 要打开的 key,底在前)。"""
    i = 0
    while i < len(opened) and i < len(desired) and opened[i] == desired[i]:
        i += 1
    return list(reversed(opened[i:])), list(desired[i:])


def collect_invalid_step_elements(
    steps: Optional[Iterable],
    tree: Optional[Iterable[dict]],
) -> list[dict]:
    """收集步骤引用的 enabled===false 元素(按步骤顺序去重)。

    无效元素不参与删除/过滤,仅在执行前被拦截;缺省/True 视为有效。
    """
    disabled: dict[str, dict] = {}
    for el in flatten_elements(tree or []):
        if el.get("enabled") is False and el.get("key") is not None:
            disabled[str(el["key"])] = el
    if not disabled:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        key = step.get("element")
        if key is None:
            continue
        sk = str(key)
        if sk in disabled and sk not in seen:
            seen.add(sk)
            out.append(disabled[sk])
    return out


def format_invalid_elements_message(elements: list[dict]) -> str:
    """步骤中存在无效的元素: 名称(key)、..."""
    parts: list[str] = []
    for el in elements:
        key = str(el.get("key") or "")
        name = str(el.get("name") or key)
        parts.append(f"{name}({key})" if name and name != key else key)
    return f"步骤中存在无效的元素: {'、'.join(parts)}"
