"""定位策略 → Playwright 定位器(运行时) / Python 表达式(代码导出) / JS 表达式(兜底)。

模块本身不导入 playwright,运行时按需延迟导入,便于测试环境 import。
"""
import json as _json
import re

_ROLE_RE = re.compile(r"^\s*([a-zA-Z]+)\s*\[name=[\"'](.*)[\"']\]\s*$")


def parse_role_locator(value: str) -> tuple[str, str]:
    """解析 role 策略值 `textbox[name="用户名"]` → (role, name)。"""
    match = _ROLE_RE.match(value or "")
    if not match:
        return value.strip(), ""
    return match.group(1), match.group(2)


def _js_str(value) -> str:
    """值 → 安全嵌入 JS 表达式的字符串字面量(JSON 字符串即合法 JS 字符串)。"""
    return _json.dumps(str(value), ensure_ascii=False)


def js_match_all(strategy: str, value: str) -> str:
    """JS IIFE:返回该策略匹配的全部元素数组。

    用于 Playwright 选择器引擎不可用的 frame(如低代码平台把微应用 iframe
    嵌在 closed shadow root 里,locator 恒 0 但 evaluate 可用)时的兜底。
    语义与 playwright_locator 尽量对齐,role/label 为近似实现。
    """
    role, name = ("", "")
    if strategy == "role":
        role, name = parse_role_locator(value)
    return f"""(() => {{
  const strategy = {_js_str(strategy)};
  const v = {_js_str(value)};
  const role = {_js_str(role)};
  const name = {_js_str(name)};
  const norm = (t) => (t || "").replace(/\\s+/g, " ").trim();
  let els = [];
  if (strategy === "css") {{
    try {{ els = Array.from(document.querySelectorAll(v)); }} catch (e) {{ els = []; }}
  }} else if (strategy === "id" || strategy === "testid" || strategy === "aria-label" || strategy === "name") {{
    const attr = strategy === "id" ? "id" : strategy === "testid" ? "data-testid" : strategy === "aria-label" ? "aria-label" : "name";
    els = Array.from(document.querySelectorAll("[" + attr + "]")).filter((el) => el.getAttribute(attr) === v);
  }} else if (strategy === "placeholder") {{
    els = Array.from(document.querySelectorAll("[placeholder]")).filter((el) => norm(el.getAttribute("placeholder")).toLowerCase().includes(norm(v).toLowerCase()));
  }} else if (strategy === "xpath") {{
    const snap = document.evaluate(v, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    els = Array.from({{ length: snap.snapshotLength }}, (_, i) => snap.snapshotItem(i));
  }} else if (strategy === "text") {{
    const target = norm(v);
    els = Array.from(document.querySelectorAll("button, a, label, [role='button'], [role='link'], [role='tab']")).filter((el) => norm(el.textContent) === target);
  }} else if (strategy === "role") {{
    const implicit = {{
      button: "button, input[type='button'], input[type='submit']",
      link: "a[href]",
      textbox: "input[type='text'], input:not([type]), textarea",
      checkbox: "input[type='checkbox']",
      radio: "input[type='radio']",
      combobox: "select",
      tab: "[role='tab']",
    }};
    const sel = "[role='" + role + "']" + (implicit[role] ? ", " + implicit[role] : "");
    els = Array.from(document.querySelectorAll(sel)).filter((el) => {{
      if (!name) return true;
      const acc = el.getAttribute("aria-label") || (el.value ? el.value : "") || el.innerText || el.textContent || "";
      return norm(acc) === norm(name);
    }});
  }} else if (strategy === "label") {{
    els = Array.from(document.querySelectorAll("label"))
      .filter((l) => norm(l.textContent) === norm(v))
      .map((l) => (l.htmlFor ? document.getElementById(l.htmlFor) : l.querySelector("input, textarea, select") || l.firstElementChild))
      .filter(Boolean);
  }}
  return els;
}})()"""


def js_count_elements(strategy: str, value: str) -> str:
    """JS 表达式:返回该策略匹配的元素个数。"""
    return f"(() => {{ const els = ({js_match_all(strategy, value)}); return els.length; }})()"


def js_first_element(strategy: str, value: str) -> str:
    """JS 表达式:返回该策略匹配的第一个元素(无匹配返回 null,供 evaluate_handle)。"""
    return f"(() => {{ const els = ({js_match_all(strategy, value)}); return els[0] || null; }})()"


def locator_expression(strategy: str, value: str) -> str:
    """生成 Playwright Python 表达式(用于代码导出),如 `self.page.get_by_label('用户名')`。"""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    if strategy == "testid":
        return f"self.page.get_by_test_id('{escaped}')"
    if strategy == "role":
        role, name = parse_role_locator(value)
        return f"self.page.get_by_role('{role}', name='{name.replace(chr(39), chr(92) + chr(39))}')"
    if strategy == "label":
        return f"self.page.get_by_label('{escaped}')"
    if strategy == "aria-label":
        return f"self.page.locator('[aria-label=\"{escaped}\"]')"
    if strategy == "placeholder":
        return f"self.page.get_by_placeholder('{escaped}')"
    if strategy == "name":
        return f"self.page.locator('[name=\"{escaped}\"]')"
    if strategy == "id":
        return f"self.page.locator('[id=\"{escaped}\"]')"
    if strategy == "text":
        return f"self.page.get_by_text('{escaped}', exact=True)"
    if strategy == "xpath":
        return f"self.page.locator('xpath={escaped}')"
    return f"self.page.locator('{escaped}')"


def playwright_locator(page, strategy: str, value: str):
    """按策略返回 Playwright Locator 对象(需要真实 page)。"""
    if strategy == "testid":
        return page.get_by_test_id(value)
    if strategy == "role":
        role, name = parse_role_locator(value)
        return page.get_by_role(role, name=name) if name else page.get_by_role(role)
    if strategy == "label":
        return page.get_by_label(value)
    if strategy == "aria-label":
        return page.locator(f'[aria-label="{value}"]')
    if strategy == "placeholder":
        return page.get_by_placeholder(value)
    if strategy == "name":
        return page.locator(f'[name="{value}"]')
    if strategy == "id":
        # 裸 id 当 CSS 选择器会把 id 当标签名查(如 rc_select_0)恒 0,必须用属性选择器
        return page.locator(f'[id="{value}"]')
    if strategy == "text":
        return page.get_by_text(value, exact=True)
    if strategy == "xpath":
        return page.locator(f"xpath={value}")
    return page.locator(value)
