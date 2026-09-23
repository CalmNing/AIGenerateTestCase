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


def render_page_object_code(page_object: dict) -> str:
    """把 PageObject dict 渲染为 Python Playwright 类源码。

    每个元素导出主定位器属性;全部候选定位器写入 BACKUP_LOCATORS 供自愈/排查。
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
    elements = [e for e in (page_object.get("elements") or []) if isinstance(e, dict)]
    prop_map = []
    backup_rows = []
    for element in elements:
        prop = _property_name(element.get("key") or element.get("name") or "", used_props)
        locators = element.get("locators") or []
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

    for prop, primary in prop_map:
        lines.append("    @property")
        lines.append(f"    def {prop}(self) -> Locator:")
        lines.append(f"        return {locator_expression(primary.get('strategy'), primary.get('value'))}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def suggest_file_name(name: Optional[str]) -> str:
    """PageObject 名称 → 建议的 Python 文件名(snake_case)。"""
    base = re.sub(r"[^0-9a-zA-Z]+", "_", name or "").strip("_").lower() or "page_object"
    return f"{base}.py"
