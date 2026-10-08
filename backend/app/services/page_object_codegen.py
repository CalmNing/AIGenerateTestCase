"""PageObject → Python Playwright 代码导出。"""
import re
from typing import Optional

from app.services.locator_strategies import locator_expression


def _class_name(name: str) -> str:
    base = re.sub(r"[^0-9a-zA-Z]+", " ", name or "").strip()
    parts = [part for part in base.split(" ") if part]
    if not parts:
        return "PageObject"
    pascal = "".join(part[:1].upper() + part[1:] for part in parts)
    if not re.search(r"page", pascal, re.IGNORECASE):
        pascal += "Page"
    if pascal[0].isdigit():
        pascal = "P" + pascal
    return pascal


def _property_name(key: str, used: set) -> str:
    base = re.sub(r"[^0-9a-zA-Z_]+", "_", key or "element").strip("_").lower() or "element"
    if base[0].isdigit():
        base = f"e_{base}"
    name = base
    suffix = 2
    while name in used:
        name = f"{base}_{suffix}"
        suffix += 1
    used.add(name)
    return name


def _walk_elements(nodes, prefix_keys: list[str]):
    """深度优先展开 (element, 祖先 key 链)。"""
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        key = str(node.get("key") or "")
        yield node, list(prefix_keys)
        children = node.get("children") or []
        if children:
            yield from _walk_elements(children, prefix_keys + ([key] if key else []))


def render_page_object_code(page_object: dict) -> str:
    """把 PageObject dict 渲染为 Python Playwright 类源码。

    每个元素导出主定位器属性;全部候选定位器写入 BACKUP_LOCATORS 供自愈/排查。
    子元素(弹窗内)额外生成 `open_ancestor_<prop>` 辅助方法,执行前先按祖先链开浮层。
    """
    class_name = _class_name(page_object.get("name") or "")
    url = page_object.get("url") or ""
    lines = [
        '"""自动生成的 PageObject(UI 自动化工具)。"""',
        "from playwright.async_api import Locator, Page",
        "",
        "",
        f"class {class_name}:",
        f'    """{page_object.get("name") or class_name}"{f" — {url}" if url else ""}"""',
        "",
    ]

    used_props: set = set()
    elements = page_object.get("elements") or []
    prop_map = []
    backup_rows = []
    ancestor_props: dict[str, list[str]] = {}  # key → 祖先 property 名列表
    prop_by_key: dict[str, str] = {}
    open_methods: list[tuple[str, list[str]]] = []  # (self prop, 祖先 property 名)

    # 先为所有节点分配 property 名(保证祖先的 property 在子元素生成前已知)
    flat_list = list(_walk_elements(elements, []))
    for element, _anc_keys in flat_list:
        prop = _property_name(element.get("key") or element.get("name") or "", used_props)
        prop_by_key[str(element.get("key") or "")] = prop

    for element, anc_keys in flat_list:
        key = str(element.get("key") or "")
        prop = prop_by_key.get(key) or _property_name(key or element.get("name") or "", used_props)
        locators = element.get("locators") or []
        # 仅保留有定位器的祖先(虚拟容器/无定位器节点生成不了 property)
        anc_props = [
            prop_by_key[k]
            for k in anc_keys
            if k in prop_by_key and any(
                n.get("locators") for n, _ in flat_list if str(n.get("key") or "") == k
            )
        ]
        ancestor_props[key] = anc_props
        if not locators:
            continue
        primary = locators[0]
        prop_map.append((prop, primary))
        backups = [
            locator_expression(loc.get("strategy"), loc.get("value"))
            for loc in locators[1:]
        ]
        if backups:
            backup_rows.append((prop, backups))
        if anc_props:
            open_methods.append((prop, anc_props))

    lines.append("    BACKUP_LOCATORS = {")
    for prop, backups in backup_rows:
        lines.append(f'        "{prop}": [')
        for expr in backups:
            lines.append(f'            "{expr}",')
        lines.append("        ],")
    lines.append("    }")
    lines.append("")

    lines.append("    def __init__(self, page: Page) -> None:")
    lines.append("        self.page = page")
    lines.append("")

    if open_methods:
        lines.append("    async def _open_overlay(self, prop: str) -> None:")
        lines.append('        """按祖先链打开浮层:执行子元素定位前调用。"""')
        lines.append("        opens = getattr(self, '_OVERLAY_PATHS', {})")
        lines.append("        for trigger_prop in opens.get(prop, []):")
        lines.append("            await getattr(self, trigger_prop).click()")
        lines.append("            await self.page.wait_for_timeout(300)")
        lines.append("")
        lines.append("    _OVERLAY_PATHS = {")
        for prop, anc in open_methods:
            lines.append(f'        "{prop}": [{", ".join(repr(a) for a in anc)}],')
        lines.append("    }")
        lines.append("")

    for prop, primary in prop_map:
        lines.append("    @property")
        lines.append(f"    def {prop}(self) -> Locator:")
        lines.append(f"        return {locator_expression(primary.get('strategy'), primary.get('value'))}")
        lines.append("")

    if open_methods:
        lines.append("    async def open_for(self, prop: str) -> None:")
        lines.append('        """确保浮层已打开后再访问子元素属性。"""')
        lines.append("        await self._open_overlay(prop)")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def suggest_file_name(name: Optional[str]) -> str:
    """PageObject 名称 → 建议的 Python 文件名(snake_case)。"""
    base = re.sub(r"[^0-9a-zA-Z]+", "_", name or "").strip("_").lower() or "page_object"
    return f"{base}.py"
