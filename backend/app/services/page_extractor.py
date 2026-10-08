"""页面元素抽取(UI 自动化 PageObject)。

分两层:
  - 纯函数层:定位器稳定性打分、候选定位器构建、元素归一化/去重/命名。
    不依赖浏览器,可直接单测。
  - 浏览器层:Playwright 打开页面 + 注入 `EXTRACT_JS` 采集交互元素;延迟导入
    playwright,测试环境无需安装浏览器即可 import 本模块。
"""
import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 微前端(wujie)作用域选择:页面上可能并存多个 <wujie-app>,每个承载一个子应用
# (实测同一页同时挂着 /sg-security/ 与 mmt-ai 两个)。穿透 shadow root 时必须
# 只进「当前路由对应的那个」,否则会把预加载/已切走的子应用元素混进来。
# 两个都可能是 visible 且铺满视口,靠可见性区分不了,所以按下面顺序判定:
#   ① data-wujie-id 规范化后出现在当前地址里(如 data-wujie-id="/basic-data-web"
#      对应地址里的 basic-data-web 段);
#   ② 兜底取 shadow 内节点最多的那个 —— 当前真正在渲染的子应用。
# 只有 1 个(或 0 个)wujie-app 时直接用它,普通页面完全不受影响。
# 被 EXTRACT_JS(采集)与 PAGE_COUNT_JS(落稳计数)共用。
WUJIE_SCOPE_JS = r"""
  const WUJIE_ID_ATTR = 'data-wujie-id';
  const isWujieApp = (el) => !!el && el.tagName === 'WUJIE-APP' && el.hasAttribute(WUJIE_ID_ATTR);
  const pickWujieScope = () => {
    const apps = [];
    document.querySelectorAll('*').forEach((el) => {
      if (isWujieApp(el)) apps.push(el);
    });
    if (apps.length <= 1) return apps[0] || null;
    const norm = (v) => String(v == null ? '' : v).replace(/^\/+|\/+$/g, '');
    const href = location.href;
    for (const app of apps) {
      const id = norm(app.getAttribute(WUJIE_ID_ATTR));
      if (id && href.indexOf(id) >= 0) return app;
    }
    let best = null;
    let bestCount = -1;
    for (const app of apps) {
      const n = app.shadowRoot ? app.shadowRoot.querySelectorAll('*').length : -1;
      if (n > bestCount) { best = app; bestCount = n; }
    }
    return best;
  };
  const wujieScope = pickWujieScope();
  // 页面上其余 wujie 子应用的 shadow root:整片跳过,不采也不计数。
  const isOtherWujie = (el) => isWujieApp(el) && el !== wujieScope;
"""

# 交互元素选择器(与 EXTRACT_JS 保持一致)
# 注意:上传组件(Element UI / antd)的 input[type=file] 天生隐藏,不在本选择器的
# 可见性语义内;EXTRACT_JS 对它单独扫描并豁免可见性判定。
INTERACTIVE_SELECTOR = (
    "input, textarea, select, button, a[href], "
    "[role='button'], [role='link'], [role='textbox'], [role='checkbox'], "
    "[role='radio'], [role='combobox'], [role='tab'], "
    "[contenteditable='true'], [data-testid], [data-test], [data-cy]"
)

# 各定位策略的基础稳定性分(0-100,越大越稳)
_STABILITY = {
    "testid": 100,
    "role": 95,
    "label": 92,
    "aria-label": 90,
    "placeholder": 85,
    "name": 82,
    "id": 80,
    "text": 70,
    "css": 50,
    "xpath": 35,
}

# 按可信度从高到低尝试
LOCATOR_PRIORITY = [
    "testid",
    "role",
    "label",
    "aria-label",
    "placeholder",
    "name",
    "id",
    "text",
    "css",
    "xpath",
]

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(r"^\d{5,}$")
_HEX_RE = re.compile(r"^[0-9a-f]+$", re.IGNORECASE)

_INPUT_TYPE_NAMES = {
    "text": "文本框",
    "password": "密码框",
    "email": "邮箱框",
    "tel": "电话框",
    "search": "搜索框",
    "number": "数字框",
    "checkbox": "复选框",
    "radio": "单选框",
    "submit": "提交按钮",
    "button": "按钮",
    "file": "文件上传",
    "date": "日期框",
}

# 元素类型的中文名:名称完全取不到时的兜底展示。
# 原来兜底是 f"{tag}元素"(如「button元素」),对用户零信息量。
_TYPE_LABELS = {
    "button": "按钮",
    "input": "输入框",
    "link": "链接",
    "select": "下拉框",
    "textarea": "文本域",
    "checkbox": "复选框",
    "radio": "单选框",
    "upload": "上传控件",
    "element": "元素",
}


def _clean_name(value: Optional[str]) -> str:
    """名称取值统一规整空白与尾部标点。

    innerText 会把块级子元素的换行带进来(实测「数字孪生平台\\n在建」这种整段文本
    被直接当成元素名);表单 label 常带尾部冒号(实测「隐患类别:」)。两者一并规整。
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r"[\s:：]+$", "", text)


_CLASS_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,60}$")
_CLASS_DYNAMIC_RE = re.compile(r"(?:^|[-_])(?:[0-9a-f]{6,}|[0-9]{4,})(?:$|[-_])", re.IGNORECASE)


def upload_class_name(raw: str) -> str:
    """从 class 串里挑出一个语义化的上传类名(用作 css 定位器)。

    Element UI 的 `el-upload__input`、antd 的 `ant-upload` 等是框架固定类名,
    比按兄弟节点层级拼出来的 cssPath 稳得多。
    """
    for name in (raw or "").split():
        if not _CLASS_NAME_RE.match(name) or _CLASS_DYNAMIC_RE.search(name):
            continue
        if re.search(r"upload|file", name, re.IGNORECASE):
            return name
    return ""


def is_dynamic_id(value: Optional[str]) -> bool:
    """判定 id 是否为框架自动生成/易变的动态值(动态 id 不生成定位器)。"""
    text = (value or "").strip()
    if not text:
        return False
    if _UUID_RE.match(text):
        return True
    # React useId: ":r0:", ":r1a:"
    if re.search(r":r[0-9a-z]+:", text, re.IGNORECASE):
        return True
    core = text.replace("-", "").replace("_", "")
    if _NUMERIC_RE.match(core):
        return True
    if len(core) >= 16 and _HEX_RE.match(core):
        return True
    return False


def score_locator(strategy: str, value: str) -> int:
    """定位策略的稳定性评分;id 动态时降为不可用(0)。"""
    if not value:
        return 0
    if strategy == "id" and is_dynamic_id(value):
        return 0
    return _STABILITY.get(strategy, 10)


def _add(locators: list[dict], strategy: str, value: Any) -> None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return
    score = score_locator(strategy, text)
    if score <= 0:
        return
    if any(loc["strategy"] == strategy and loc["value"] == text for loc in locators):
        return
    locators.append({"strategy": strategy, "value": text, "stability": score})


def build_locators(attrs: dict) -> list[dict]:
    """从元素属性构建候选定位器,按稳定性降序返回。"""
    locators: list[dict] = []
    tag = (attrs.get("tag") or "").lower()
    test_id = attrs.get("testId")
    role = attrs.get("role")
    aria_label = attrs.get("ariaLabel")
    label = attrs.get("label")
    placeholder = attrs.get("placeholder")
    name = attrs.get("name")
    element_id = attrs.get("id")
    text = attrs.get("text")
    css_path = attrs.get("cssPath")
    input_type = (attrs.get("inputType") or "").lower()

    if tag == "a" and not role:
        role = "link"
    elif tag == "button" and not role:
        role = "button"
    elif tag == "input" and input_type in ("checkbox", "radio"):
        role = "checkbox" if input_type == "checkbox" else "radio"
    elif tag == "select":
        role = "combobox"

    accessible_name = aria_label or label or placeholder or text
    # 被框架视觉替换的隐藏控件不能给 role 定位器兜底:
    # Playwright 的 get_by_role 默认 include_hidden=False,对隐藏元素恒 0 命中。
    if role and accessible_name and not attrs.get("visualReplaced"):
        _add(locators, "role", f"{role}[name=\"{accessible_name}\"]")
    _add(locators, "testid", test_id)
    # label 关联不在本元素上(低代码把 label[for] 指到包装 span)时只供命名,
    # 不生成 label 定位器 —— get_by_label 会解析到 span 而非 input,主定位器恒偏。
    if attrs.get("labelBound", True):
        _add(locators, "label", label)
    _add(locators, "aria-label", aria_label)
    _add(locators, "placeholder", placeholder)
    _add(locators, "name", name)
    _add(locators, "id", element_id)
    if tag in ("button", "a") and text:
        _add(locators, "text", text)
    if input_type == "file":
        # 隐藏的 file input 靠自身属性定位,补语义 class 档(框架固定类名,
        # 比按兄弟节点层级拼出来的 cssPath 稳)。
        upload_cls = upload_class_name(attrs.get("class") or "")
        if upload_cls:
            _add(locators, "css", f'input[type="file"].{upload_cls}')
    _add(locators, "css", css_path)
    if input_type == "file":
        # accept 只是兜底:同页多个图片上传框会同时命中,排在 cssPath 之后。
        accept = (attrs.get("accept") or "").strip()
        if accept and '"' not in accept:
            _add(locators, "css", f'input[type="file"][accept="{accept}"]')
    if element_id and not is_dynamic_id(element_id):
        _add(locators, "xpath", f"//*[@id=\"{element_id}\"]")

    locators.sort(key=lambda item: item["stability"], reverse=True)
    return locators


def custom_control_type(attrs: dict) -> str:
    """div/span 模拟的自定义控件按 class 语义推断类型。

    低代码平台的 textRadio / lc-input 等不会渲染原生控件,类型只能从类名读。
    """
    hint = f'{attrs.get("class") or ""} {attrs.get("role") or ""}'.lower()
    for keyword, kind in (
        ("radio", "radio"),
        ("checkbox", "checkbox"),
        ("switch", "checkbox"),
        ("toggle", "checkbox"),
        ("select", "select"),
        ("dropdown", "select"),
        ("combobox", "select"),
        ("upload", "upload"),
        ("file", "upload"),
        ("textarea", "textarea"),
        ("textbox", "input"),
        ("input", "input"),
        ("btn", "button"),
        ("button", "button"),
    ):
        if keyword in hint:
            return kind
    return "element"


def element_type(attrs: dict) -> str:
    """归一化元素类型(input/button/link/select/textarea/upload/...)。"""
    if attrs.get("customControl"):
        return custom_control_type(attrs)
    tag = (attrs.get("tag") or "").lower()
    if tag == "input":
        input_type = (attrs.get("inputType") or "text").lower()
        if input_type == "file":
            # 上传控件单独成类:执行侧要按 upload 语义走 set_input_files,
            # 而不是 fill/click,也豁免 visible 等待。
            return "upload"
        if input_type in ("submit", "button"):
            return "button"
        if input_type in ("checkbox", "radio"):
            return input_type
        return "input"
    if tag == "a":
        return "link"
    if tag in ("button", "select", "textarea"):
        return tag
    role = (attrs.get("role") or "").lower()
    if role == "button":
        return "button"
    if role == "link":
        return "link"
    return tag or "element"


def heuristic_name(attrs: dict) -> str:
    """无 LLM 时的元素名称回退:上传触发器 → label → aria → title → placeholder → text → name → id。

    上传控件优先用其可见触发体的文案(「上传图片」),因为 file input 自身无文字,
    直接回退到「文件上传」时用户在元素列表里对不上界面。
    `title` 紧跟 aria 之后:图标按钮(无文案)常把语义放在 title 上,不采只剩「按钮」。
    """
    input_type = (attrs.get("inputType") or "").lower()
    if input_type == "file":
        trigger = _clean_name(attrs.get("triggerText"))
        return trigger[:40] if trigger else "文件上传"
    if attrs.get("customControl"):
        for key in ("triggerText", "text", "ariaLabel", "title", "placeholder"):
            value = _clean_name(attrs.get(key))
            if value:
                return value[:40]
        return "自定义控件"
    if attrs.get("uploadTrigger"):
        for key in ("triggerText", "text", "ariaLabel", "title"):
            value = _clean_name(attrs.get(key))
            if value:
                return value[:40]
        return "上传控件"
    for key in ("label", "ariaLabel", "title", "placeholder", "text"):
        value = _clean_name(attrs.get(key))
        if value:
            return value[:40]
    if (attrs.get("tag") or "").lower() == "input" and input_type in _INPUT_TYPE_NAMES:
        return _INPUT_TYPE_NAMES[input_type]
    name = _clean_name(attrs.get("name"))
    if name:
        return name
    element_id = _clean_name(attrs.get("id"))
    if element_id and not is_dynamic_id(element_id):
        return element_id
    test_id = _clean_name(attrs.get("testId"))
    if test_id:
        return test_id
    return ""


def slugify(value: Optional[str], fallback: str = "element") -> str:
    """名称 → 合法 Python 标识符(ASCII,小写下划线)。"""
    text = re.sub(r"[^0-9a-zA-Z]+", "_", value or "").strip("_").lower()
    if not text:
        return fallback
    if text[0].isdigit():
        text = f"e_{text}"
    return text


def build_element_key(attrs: dict, index: int, used: set) -> str:
    """为元素生成稳定的 ASCII key(代码导出/步骤引用用),冲突时追加序号。"""
    candidates = [
        attrs.get("testId"),
        attrs.get("name"),
        attrs.get("id") if not is_dynamic_id(attrs.get("id")) else None,
        attrs.get("placeholder"),
        attrs.get("label"),
        attrs.get("ariaLabel"),
        f"{attrs.get('tag') or 'element'}_{index}",
    ]
    base = "element"
    for candidate in candidates:
        slug = slugify(candidate, fallback="")
        if slug:
            base = slug
            break
    key = base
    suffix = 2
    while key in used:
        key = f"{base}_{suffix}"
        suffix += 1
    used.add(key)
    return key


def build_fingerprint(attrs: dict) -> dict:
    """元素指纹:自愈重匹配时的相似度依据。"""
    return {
        "tag": (attrs.get("tag") or "").lower(),
        "input_type": (attrs.get("inputType") or "").lower(),
        "role": (attrs.get("role") or "").lower(),
        "name": attrs.get("name") or "",
        "id": attrs.get("id") or "",
        "aria_label": attrs.get("ariaLabel") or "",
        "label": attrs.get("label") or "",
        "placeholder": attrs.get("placeholder") or "",
        "text": attrs.get("text") or "",
        "context": attrs.get("context") or "",
    }


def trigger_locator(attrs: dict) -> Optional[dict]:
    """隐藏控件承载视觉的可见壳定位器(低代码 textRadio 的 label 等)。

    原生节点被 display:none 藏起来后,连 force 点击都可能失败;真实交互落在壳上。
    它单独存放而不混进 `locators`,避免被多候选降级逻辑误选成主定位器。
    """
    selector = (attrs.get("triggerSelector") or "").strip()
    if not selector:
        return None
    return {"strategy": "css", "value": selector, "stability": 45}


def normalize_element(attrs: dict, index: int, used_keys: set) -> dict:
    """原始属性 → PageObject 元素结构(优先采用 LLM 语义命名)。"""
    locators = build_locators(attrs)
    element_kind = (attrs.get("llmType") or "").strip() or element_type(attrs)
    name = (
        _clean_name(attrs.get("llmName"))
        or heuristic_name(attrs)
        # 兜底用元素类型的中文名(原先是 f"{tag}元素" → 「button元素」,零信息量)
        or _TYPE_LABELS.get(element_kind, "元素")
    )
    return {
        "key": build_element_key(attrs, index, used_keys),
        "name": name,
        "type": element_kind,
        "description": (attrs.get("llmDescription") or attrs.get("context") or ""),
        "enabled": True,
        "fingerprint": build_fingerprint(attrs),
        "locators": locators,
        "frame": str(attrs.get("frame") or ""),
        # 探针来源触发词(新增/编辑/删除等),前端据此分组展示;基线元素为空
        "probeTrigger": str(attrs.get("probeTrigger") or ""),
        # 触发路径(如 ["新增","高级设置"]),建树用;基线为 []
        "probePath": list(attrs.get("probePath") or []),
        "trigger": trigger_locator(attrs),
        "heal_count": 0,
        "last_healed_at": None,
    }


# 表格行索引:同一类元素在各行的定位器只差这一段
_TR_ROW_INDEX_RE = re.compile(r"\btr:nth-of-type\(\d+\)")


def structural_key(primary: str) -> str:
    """主定位器的结构签名:去掉表格行索引,把「第 N 行的编辑」归成「编辑」。"""
    return _TR_ROW_INDEX_RE.sub("tr", primary or "")


def generalize_locators(locators: list[dict]) -> list[dict]:
    """去掉 css 定位器里的表格行索引。

    逐行重复的元素合并成一条后,再留着 `tr:nth-of-type(3)` 只会在行数变化时失效;
    去掉行号即定位到「任意一行」,配 Playwright 的 `.first` 才稳定。
    只去 tr 的行号 —— td 的列索引和其它层级 index 是结构性特征,不能动。
    """
    out = []
    for loc in locators or []:
        if loc.get("strategy") != "css":
            out.append(loc)
            continue
        out.append({**loc, "value": _TR_ROW_INDEX_RE.sub("tr", loc.get("value") or "")})
    return out


# 主定位器值在多字段间天然撞车的策略:placeholder 常是「请输入」,text/css 也随
# 结构复用。仅用它做去重键会把弹窗里不同字段合成一条(实测 checkLocation 被
# hazardCategory 吞掉,只增 occurrences)。
_SHARED_PRIMARY_STRATEGIES = frozenset({"placeholder", "text", "css", "xpath", ""})


def _dedupe_key(element: dict) -> tuple:
    """去重键:同名同结构视为同一类元素。

    表格逐行的「编辑/删除/复选框」结构完全一致,只有 `tr:nth-of-type(N)` 不同 ——
    它们是同一类控件在各行的实例。按行号区分会把元素表淹成几十条同名条目
    (实测某列表页 112 个元素里 68 个属于重复组),而按结构归并后只留一条并记
    `occurrences`,定位器落到「任意一行」,这恰好是写用例时想要的粒度。

    主定位器是 placeholder/text 等弱语义值时,额外比 css 结构签名
    (`structural_key` 会去掉 tr 行号,跨 frame/跨行的同类元素仍归并):
    否则「请输入」会把 hazardCategory 与 checkLocation 等不同字段合成一条。
    role/testid/name 等强语义主定位器保持原行为,避免跨 frame 副本因 css 路径
    不同而拆开。

    `probePath` 参与键:基线与各浮层、不同浮层之间的同结构元素不互相吞并
    (弹窗字段与基线同指纹时若并入基线,子树会残缺且执行时不会先开浮层)。
    同一浮层内的表格行仍按结构归并计次。
    """
    locators = element.get("locators") or []
    primary = locators[0] if locators else {}
    primary_value = primary.get("value") or ""
    fp = element.get("fingerprint") or {}
    probe_path = tuple(str(s) for s in (element.get("probePath") or []))
    key = (
        fp.get("tag"),
        fp.get("input_type"),
        fp.get("name"),
        structural_key(primary_value),
    )
    if primary.get("strategy") in _SHARED_PRIMARY_STRATEGIES:
        css = next(
            (loc.get("value") or "" for loc in locators if loc.get("strategy") == "css"),
            "",
        )
        return (*key, structural_key(css), probe_path)
    return (*key, probe_path)


def normalize_elements(raw_elements: list[dict]) -> list[dict]:
    """批量归一化 + 去重(无候选定位器的元素丢弃,同名同结构合并计次)。"""
    normalized: list[dict] = []
    by_key: dict = {}
    used_keys: set = set()
    for index, attrs in enumerate(raw_elements or [], 1):
        if not isinstance(attrs, dict):
            continue
        element = normalize_element(attrs, index, used_keys)
        if not element["locators"]:
            continue
        dedupe_key = _dedupe_key(element)
        existing = by_key.get(dedupe_key)
        if existing is not None:
            # 同一类元素在第 N 行又出现一次:只计次,不再新增条目
            existing["occurrences"] = int(existing.get("occurrences") or 1) + 1
            continue
        element["occurrences"] = 1
        by_key[dedupe_key] = element
        normalized.append(element)
    for element in normalized:
        if int(element.get("occurrences") or 1) > 1:
            element["locators"] = generalize_locators(element["locators"])
    _disambiguate_names(normalized)
    return normalized


def _disambiguate_names(elements: list[dict]) -> None:
    """给真重名的元素追加序号(就地改)。

    去重后仍重名说明它们是定位器不同的多个元素 —— 典型是页面上几个无文案的图标
    按钮(querySelectorAll 只能给出「按钮」)。同名多条时用户在元素列表里无从区分
    该用哪一个,所以在名称上补序号。

    注意:表格里逐行重复的同类元素(如 20 行的「编辑」)已在去重阶段合并成一条,
    不会走到这里,不会被误加序号。
    """
    counts: dict[str, int] = {}
    for element in elements:
        name = element.get("name") or ""
        counts[name] = counts.get(name, 0) + 1
    seen: dict[str, int] = {}
    for element in elements:
        name = element.get("name") or ""
        if counts[name] > 1:
            seen[name] = seen.get(name, 0) + 1
            element["name"] = f"{name}{seen[name]}"


# ---------------------------------------------------------------------------
# 浏览器层(延迟导入 playwright)
# ---------------------------------------------------------------------------

EXTRACT_JS = r"""
() => {
""" + WUJIE_SCOPE_JS + r"""
  const SELECTOR = "input, textarea, select, button, a[href], [role='button'], [role='link'], [role='textbox'], [role='checkbox'], [role='radio'], [role='combobox'], [role='tab'], [contenteditable='true'], [data-testid], [data-test], [data-cy]";
  // 上传控件专项扫描的元素:
  //   - file input 是 set_input_files 唯一可靠的目标,但 Element UI / antd 等
  //     上传组件把它设成 display:none / 0 尺寸,会被 isVisible 全部滤掉,必须豁免。
  //   - 触发器(labelless 的 div/span 包装)不在 SELECTOR 里,单独识别后补采。
  const FILE_SELECTOR = "input[type='file']";
  const UPLOAD_WRAPPER = "label, button, [role='button'], a[href], [class*='upload' i], [class*='uploader' i], [class*='dropzone' i], [class*='filePicker' i], [class*='file-picker' i]";
  const NATIVE_TRIGGER = "button, [role='button'], a[href]";
  // 低代码/自研平台的表单控件常走两条标准选择器覆盖不到的路径:
  //   ① 原生控件被框架样式隐藏(如 dangerLevel 的 input display:none),视觉壳在 label/span 上;
  //   ② 整个控件用 div/span 模拟,不渲染任何原生控件(如 textRadio、自定义输入框)。
  // 前者靠「自身隐藏但父链未隐藏」判定为视觉替换;后者靠可交互信号识别。
  const CONTROL_SELECTOR = "input, textarea, select";
  const CUSTOM_SELECTOR = "div, span, li, td, label, a";
  const CUSTOM_HINT_RE = /(radio|checkbox|select|input|picker|switch|toggle|upload|textbox|textarea|dropdown|combobox|slider|stepper|date|file|btn|button)/i;
  const isAriaHidden = (el) => el.getAttribute('aria-hidden') === 'true';
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  // shadow 边界:shadow root 内顶层元素的 parentElement 为 null,宿主在 getRootNode().host。
  // Playwright 的 CSS 引擎会穿透 open shadow root,所以宿主路径 + 内部路径拼接后仍可定位。
  const shadowHost = (node) => {
    const root = node.getRootNode ? node.getRootNode() : null;
    return root && root.host ? root.host : null;
  };
  // 穿透 open shadow root 的查询。低代码平台/微前端常用 shadow DOM 封装组件,
  // 只用 document.querySelectorAll 会整片漏采。
  // 注:closed shadow root 的 shadowRoot 为 null,浏览器层面拿不到,无法覆盖。
  // 微前端:起根只在「当前路由对应的那个」wujie 子应用的 shadow 上 —— 主框架的
  // header/侧栏/面包屑在 light DOM 里,从 document 起根会整片采进来;从选中子应用
  // 的 shadow 起根则天然只拿到子框架内容。旁支 wujie 子应用继续整片跳过。
  // 非微前端页面回落 document,行为不变。
  const extractRoots = () => (
    wujieScope && wujieScope.shadowRoot ? [wujieScope.shadowRoot] : [document]
  );
  const queryAllDeep = (selector) => {
    const found = [];
    const pending = extractRoots();
    while (pending.length) {
      const root = pending.pop();
      try {
        root.querySelectorAll(selector).forEach((el) => found.push(el));
      } catch (err) { /* 非法选择器/失效 root 直接跳过 */ }
      let all;
      try {
        all = root.querySelectorAll('*');
      } catch (err) {
        all = [];
      }
      for (const el of all) {
        if (!el.shadowRoot || isOtherWujie(el)) continue;
        pending.push(el.shadowRoot);
      }
    }
    return found;
  };
  const cssPath = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      const testId = node.getAttribute('data-testid') || node.getAttribute('data-test') || node.getAttribute('data-cy');
      if (testId) { parts.unshift(`${part}[data-testid="${testId}"]`); break; }
      if (node.id && !/\d{5,}|:r[0-9a-z]+:/i.test(node.id)) { parts.unshift(`${part}#${CSS.escape(node.id)}`); break; }
      const parent = node.parentElement || shadowHost(node);
      if (parent) {
        const sameTag = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (sameTag.length > 1) part += `:nth-of-type(${sameTag.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  };
  // label[for] 在 root(shadow/document)内查找:低代码常把 for 指到包装容器。
  const findLabelFor = (root, id) => {
    if (!id) return null;
    const scope = (root && root.querySelectorAll) ? root : document;
    for (const lab of scope.querySelectorAll('label[for]')) {
      if (lab.getAttribute('for') === id) return lab;
    }
    if (scope !== document) {
      for (const lab of document.querySelectorAll('label[for]')) {
        if (lab.getAttribute('for') === id) return lab;
      }
    }
    return null;
  };
  // 直接关联才算绑定:el.labels / 包裹 label / label[for]=el.id / aria-labelledby。
  // 间接(label[for] 指向祖先容器)只取文案命名,不生成 label 定位器。
  const labelText = (el) => {
    if (el.labels && el.labels.length) return (el.labels[0].innerText || el.labels[0].textContent || '').trim();
    const aria = el.getAttribute('aria-labelledby');
    if (aria) {
      const root = el.getRootNode ? el.getRootNode() : document;
      const target = (root && root.getElementById) ? root.getElementById(aria) : null;
      const fallback = (!target && root !== document) ? document.getElementById(aria) : null;
      const hit = target || fallback;
      if (hit) return (hit.innerText || hit.textContent || '').trim();
    }
    const parentLabel = el.closest('label');
    if (parentLabel) return (parentLabel.innerText || parentLabel.textContent || '').trim();
    // 低代码/antd 变体:<label for="checkLocation"> 指向 span#checkLocation,
    // 真正的 input 在 span 内且自身无 id —— el.labels 为空,业务名只能从祖先 id 反查。
    const root = el.getRootNode ? el.getRootNode() : document;
    let node = el;
    for (let depth = 0; node && depth < 8; depth++) {
      if (node.id) {
        const lab = findLabelFor(root, node.id) || (root !== document ? findLabelFor(document, node.id) : null);
        if (lab) return (lab.innerText || lab.textContent || '').trim();
      }
      node = node.parentElement || (node.getRootNode && node.getRootNode().host) || null;
    }
    return '';
  };
  const isLabelBound = (el) => {
    if (el.labels && el.labels.length) return true;
    if (el.closest('label')) return true;
    const aria = el.getAttribute('aria-labelledby');
    if (aria) {
      const root = el.getRootNode ? el.getRootNode() : document;
      if ((root && root.getElementById && root.getElementById(aria)) || document.getElementById(aria)) return true;
    }
    if (el.id) {
      const root = el.getRootNode ? el.getRootNode() : document;
      if (findLabelFor(root, el.id) || findLabelFor(document, el.id)) return true;
    }
    return false;
  };
  const contextText = (el) => {
    const container = el.closest('form, [role="dialog"], [class*="login"], [class*="modal"]');
    if (!container) return '';
    const heading = container.querySelector('h1, h2, h3, legend');
    if (heading) return (heading.innerText || heading.textContent || '').trim().slice(0, 40);
    const text = (container.innerText || container.textContent || '').trim();
    return text.slice(0, 40);
  };
  // 隐藏只源于元素自身样式 → 框架用自定义视觉替换了它,控件功能仍可用,应当采集。
  // 祖先把整棵子树藏了(未激活 tab、未打开的弹窗)→ 真的不可见,应当丢弃。
  const isVisuallyReplaced = (el) => {
    let node = el.parentElement;
    while (node && node !== document.body) {
      const style = window.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      node = node.parentElement;
    }
    const parent = el.parentElement;
    if (!parent) return false;
    if (Array.from(parent.children).some((child) => child !== el && isVisible(child))) return true;
    return isVisible(parent);
  };
  const hasInteractiveHint = (el) => {
    const tabindex = el.getAttribute('tabindex');
    if (tabindex !== null && Number(tabindex) >= 0) return true;
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (role && role !== 'presentation' && role !== 'none') return true;
    if (CUSTOM_HINT_RE.test(el.getAttribute('class') || '')) return true;
    const field = `${el.getAttribute('data-field') || ''} ${el.getAttribute('data-name') || ''} ${el.getAttribute('data-key') || ''}`;
    return CUSTOM_HINT_RE.test(field);
  };
  // 文本类字段统一规整空白后再截断:innerText 会把块级子元素的换行带进来,
  // 实测「数字孪生平台\n在建」这种整段文本直接当元素名,列表里没法看。
  const flat = (value, limit) => String(value == null ? '' : value)
    .replace(/\s+/g, ' ').trim().slice(0, limit);
  const collect = (el) => ({
    tag: el.tagName.toLowerCase(),
    inputType: (el.getAttribute('type') || '').toLowerCase(),
    id: el.id || '',
    name: el.getAttribute('name') || '',
    class: el.getAttribute('class') || '',
    role: (el.getAttribute('role') || '').toLowerCase(),
    ariaLabel: flat(el.getAttribute('aria-label'), 60),
    // title 是图标按钮(无文案)最常见的语义来源,不采就只剩「button元素」
    title: flat(el.getAttribute('title'), 60),
    placeholder: flat(el.getAttribute('placeholder'), 60),
    text: flat(el.innerText || el.textContent, 60),
    label: flat(labelText(el), 60),
    // 直接关联才允许当 label 定位器;间接(for 指祖先)只参与命名
    labelBound: isLabelBound(el),
    accept: el.getAttribute('accept') || '',
    autocomplete: el.getAttribute('autocomplete') || '',
    testId: el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy') || '',
    href: el.getAttribute('href') || '',
    disabled: !!el.disabled,
    cssPath: cssPath(el),
    context: flat(contextText(el), 40),
  });
  // 从父节点起找:file input 自身的 class 常含 upload(如 ant-upload-input),
  // 从自身起 closest 会命中自己,拿不到真正的可见包装。
  const findTrigger = (el) => (el.parentElement ? el.parentElement.closest(UPLOAD_WRAPPER) : null);
  const out = [];
  const seen = new Set();
  queryAllDeep(SELECTOR).forEach((el) => {
    if (seen.has(el) || !isVisible(el)) return;
    seen.add(el);
    out.push(collect(el));
  });
  queryAllDeep(FILE_SELECTOR).forEach((el) => {
    if (seen.has(el)) return;
    seen.add(el);
    const trigger = findTrigger(el);
    const triggerText = trigger
      ? (trigger.innerText || trigger.textContent || '').trim().slice(0, 40)
      : '';
    // 豁免可见性:隐藏的 file input 照样是上传动作的目标
    out.push({ ...collect(el), isFileInput: true, triggerText });
    // 触发器自身如果没有原生按钮(纯 label/div/span 包装),再补采一条可见元素,
    // 否则元素列表里只剩「文件上传」,界面上那个「上传图片」无从对应。
    // label 已与该 input 关联时跳过:input 的 label 定位器就是它,补采只会重复。
    const hasNativeChild =
      trigger && trigger.querySelector(NATIVE_TRIGGER) && isVisible(trigger.querySelector(NATIVE_TRIGGER));
    const labelBound =
      trigger && trigger.tagName === 'LABEL' && el.labels
        ? Array.prototype.includes.call(el.labels, trigger)
        : false;
    if (
      trigger && trigger !== el && !seen.has(trigger) && isVisible(trigger)
      && !hasNativeChild && !labelBound
    ) {
      seen.add(trigger);
      out.push({ ...collect(trigger), uploadTrigger: true, triggerText: triggerText || collect(trigger).text });
    }
  });
  // 补充扫描 ①:被视觉替换的原生控件。
  // 原生 radio/checkbox/input 被框架设成隐藏,交互落到 label/span 壳上(低代码 textRadio
  // 的典型形态)。这类控件功能完全可用,只是原生节点被藏了,漏采会让用例写不出来。
  queryAllDeep(CONTROL_SELECTOR).forEach((el) => {
    if (seen.has(el)) return;
    if ((el.getAttribute('type') || '').toLowerCase() === 'hidden') return;
    if (isAriaHidden(el) || isVisible(el)) return;
    if (!isVisuallyReplaced(el)) return;
    seen.add(el);
    // 隐藏控件本身点不动(带 force 也未必成),真实交互目标是承载视觉的 label/壳。
    // 一并抽出它的选择器,执行侧直接操作失败时改点这里。
    const shell = el.labels && el.labels.length ? el.labels[0] : el.closest('label');
    out.push({
      ...collect(el),
      visualReplaced: true,
      triggerSelector: shell ? cssPath(shell) : '',
      triggerText: shell ? (shell.innerText || shell.textContent || '').trim().slice(0, 40) : '',
    });
  });
  // 补充扫描 ②:div/span 模拟的自定义控件(textRadio、自定义输入框、自定义下拉)。
  // 条件收紧到「有可交互信号 + 自身不含原生控件 + 文本短」,避免把表单容器整片吞进来。
  // 展示层后缀(-text / -label / -display / -wrap 等)只是视觉壳,不是控件。
  const DISPLAY_SUFFIX_RE = /(?:-|_)(?:text|label|title|caption|tip|display|placeholder|wrapper|wrap)$/i;
  queryAllDeep(CUSTOM_SELECTOR).forEach((el) => {
    if (seen.has(el)) return;
    if (isAriaHidden(el) || !isVisible(el) || !hasInteractiveHint(el)) return;
    if (el.querySelector("input, textarea, select")) return;
    if (DISPLAY_SUFFIX_RE.test(el.getAttribute('class') || '')) return;
    const text = (el.innerText || el.textContent || '').trim();
    if (!text || text.length > 40) return;
    seen.add(el);
    out.push({ ...collect(el), customControl: true, triggerText: text.slice(0, 40) });
  });
  // 诊断清单:页面里「疑似可交互但没被采集」的元素。抽取器覆盖不到某类控件时,
  // 它会在这里留痕 —— 让「漏采」可见,而不是等写用例时才发现。
  // reason=visible-not-collected 才是真漏洞;hidden 多为未激活面板/未打开的弹窗。
  // 自身内部(或 label 关联)已有采集过的元素时不算漏 —— 那只是控件的包装壳。
  const containsCollected = (el) => {
    for (const node of el.querySelectorAll('*')) {
      if (seen.has(node)) return true;
    }
    return el.tagName === 'LABEL' && el.control ? seen.has(el.control) : false;
  };
  const skipped = [];
  const SKIP_LIMIT = 60;
  queryAllDeep("input, textarea, select, button, a, div, span, li, td, label").forEach((el) => {
    if (skipped.length >= SKIP_LIMIT || seen.has(el)) return;
    if (isAriaHidden(el)) return;
    const tag = el.tagName.toLowerCase();
    const isControl = tag === 'input' || tag === 'textarea' || tag === 'select';
    if (isControl && (el.getAttribute('type') || '').toLowerCase() === 'hidden') return;
    if (!isControl && !hasInteractiveHint(el)) return;
    const text = (el.innerText || el.textContent || '').trim().slice(0, 30);
    if (!isControl && !text) return;
    if (containsCollected(el)) return;
    // 采集阶段已认定的展示层(text/label/display/wrap)不算漏采
    if (!isControl && DISPLAY_SUFFIX_RE.test(el.getAttribute('class') || '')) return;
    const visible = isVisible(el);
    skipped.push({
      tag,
      inputType: (el.getAttribute('type') || '').toLowerCase(),
      name: el.getAttribute('name') || '',
      class: (el.getAttribute('class') || '').slice(0, 60),
      text,
      visible,
      reason: visible ? 'visible-not-collected' : 'hidden',
      cssPath: cssPath(el),
    });
  });
  return {
    elements: out,
    skipped,
    // 微前端:本次实际穿透的 wujie 子应用(data-wujie-id),空串表示非微前端页面。
    // 回传给 Python 侧仅作可观测性(日志),便于确认采的是不是当前路由那个。
    wujieScope: wujieScope ? (wujieScope.getAttribute(WUJIE_ID_ATTR) || '') : '',
  };
}
"""


# ---------------------------------------------------------------------------
# 交互探针:列表页上的「新增/新建」按钮触发的弹窗/抽屉表单没有独立路由,
# 纯 URL 打开 + 静默采集抽不到。探针在基线抽取后自动点击触发按钮 → 等弹窗
# 出现 → 重跑 EXTRACT_JS 做增量 diff → 关闭弹窗继续,全程 try/except 降级。
# ---------------------------------------------------------------------------

# 默认触发词白名单(避免误点「导出」「登出」等无关按钮)。
# 前缀动词 + 可选「批量/全部」前缀 + 最多 8 字后缀宾语,覆盖「新增隐患」
# 「编辑用户」「批量删除」等真实按钮文案;删除走「点按钮 → 弹确认框」路径,
# 探针只点触发按钮、绝不点确认框里的「确定」,关窗即取消,不会真删数据。
DEFAULT_TRIGGER_PATTERN = (
    r"^(批量|全部)?(新增|新建|添加|创建|录入|上传|导入|授权|登记|申请"
    r"|编辑|修改|删除|移除)[\u4e00-\u9fa5A-Za-z0-9]{0,8}$"
)

# 触发按钮点击后的落稳等待(ms):弹窗渲染 + 挂载表单
DIALOG_OPEN_WAIT_MS = 1000
# 关闭浮层的重试轮数与每轮等待(ms)
CLOSE_RETRY = 3
CLOSE_WAIT_MS = 700
# 关闭浮层的候选按钮文案(按顺序尝试;匹配容忍字符间空白,见 _loose_text_pattern)
CLOSE_BUTTON_TEXTS = ("取消", "关闭", "关闭弹窗")
# 关闭尝试全部失败后重载页面回到基线。
# 实测 wujie 子应用弹窗对 Escape 无响应、「取消」也可能不在可见区,原实现直接终止
# 后续探针,命中从 306 掉到 153(只探到前 1~2 个触发按钮)。重载虽要等子应用重新
# 挂载,但比丢掉后面所有触发按钮划算。
CLOSE_RELOAD_FALLBACK = True

# 渲染落稳轮询:采集前的等待策略。
# 不用 wait_for_load_state("networkidle"):微前端(wujie/qiankun)把子应用放在
# iframe + shadow root 里异步挂载,主应用加载完会有个静默窗口,networkidle 在此
# 期间就满足并返回,此时采集只能拿到外壳(实测同一页面命中数在 2~306 之间随机)。
# 改成轮询交互元素数,连续 SETTLE_STABLE_ROUNDS 次不变即认为落稳。
#
# SETTLE_MIN_WAIT_MS 是预热期,此前只观察不判稳。子应用挂载前主应用外壳会长时间
# 稳定在个位数元素上(实测该站点 t=0~3s 恒为 2 个、t=4s 才跳到 202 个),光看
# 「计数不变」会在外壳上提前判稳,采回空壳(实测命中 18 而非 306)。
#
# 但子应用挂载耗时不是定值(实测 4~8s 波动),固定预热期同样会被波动骗到 ——
# 所以主判据是 SETTLE_QUIET_MS:「最近一次网络活动距今超过该值」才算加载结束。
# 子应用还在拉 chunk 时网络不安静,计数即使不变也不判稳。
SETTLE_INTERVAL_MS = 1000
SETTLE_STABLE_ROUNDS = 3
SETTLE_MIN_WAIT_MS = 2000
SETTLE_QUIET_MS = 700
SETTLE_TIMEOUT_MS = 15000

# 落稳轮询专用:只数交互元素个数(不采集属性,单轮开销远小于 EXTRACT_JS)。
# 必须穿透 open shadow root —— 顶层 document.querySelectorAll 在 wujie 场景下
# 恒为 1(子应用 DOM 全在 <wujie-app> 的 shadow root 内),用它做判据会立刻误判"已稳定"。
PAGE_COUNT_JS = r"""
() => {
""" + WUJIE_SCOPE_JS + r"""
  const SEL = "input, textarea, select, button, a[href], "
    + "[role='button'], [role='link'], [role='textbox'], [role='checkbox'], "
    + "[role='radio'], [role='combobox'], [role='tab'], "
    + "[contenteditable='true'], [data-testid], [data-test], [data-cy]";
  const queryAllDeep = (root, sel) => {
    const found = [];
    const pending = [root];
    while (pending.length) {
      const r = pending.pop();
      try { r.querySelectorAll(sel).forEach((el) => found.push(el)); } catch (err) {}
      let all;
      try { all = r.querySelectorAll('*'); } catch (err) { all = []; }
      for (const el of all) {
        if (!el.shadowRoot || isOtherWujie(el)) continue;
        pending.push(el.shadowRoot);
      }
    }
    return found;
  };
  // 计数根与 EXTRACT_JS 一致:微前端只数选中子应用 shadow 内的元素,主框架壳不计入。
  const countRoot = wujieScope && wujieScope.shadowRoot ? wujieScope.shadowRoot : document;
  return queryAllDeep(countRoot, SEL).length;
}
"""

FIND_TRIGGERS_JS = r"""
(pattern) => {
""" + WUJIE_SCOPE_JS + r"""
  const re = new RegExp(pattern);
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  // 与 EXTRACT_JS 同一根:微前端页面触发按钮在选中子应用的 shadow 内,
  // 从 document 查会整片落空;从 shadow 起根同时避免误点主框架壳上的按钮。
  const searchRoot = wujieScope && wujieScope.shadowRoot ? wujieScope.shadowRoot : document;
  const out = [];
  const seen = new Set();
  searchRoot.querySelectorAll("button, a[href], [role='button']").forEach((el) => {
    if (!isVisible(el) || el.disabled) return;
    const text = (el.innerText || el.textContent || '').trim();
    if (!text || text.length > 12 || !re.test(text)) return;
    if (seen.has(text)) return;
    seen.add(text);
    out.push({ text: text.slice(0, 20) });
  });
  return out;
}
"""

# 返回按钮中心坐标(滚动到可视区后测量);点击用 page.mouse(真实可信输入)。
# 不用 JS el.click():部分平台(如 sg-security)校验 event.isTrusted,合成点击无效;
# 不用 Playwright locator/ElementHandle:列表页轮询重渲染频繁替换节点,
# 定位等待期内节点必被替换,且 closed shadow root 微前端下选择器引擎不可用。
FIND_POINT_JS = r"""
(args) => {
""" + WUJIE_SCOPE_JS + r"""
  const re = new RegExp(args.pattern);
  const text = args.text;
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  // 与 EXTRACT_JS 同一根:微前端只在选中子应用 shadow 内定位,避免点到主框架壳。
  const searchRoot = wujieScope && wujieScope.shadowRoot ? wujieScope.shadowRoot : document;
  for (const el of searchRoot.querySelectorAll("button, a[href], [role='button']")) {
    if (!isVisible(el) || el.disabled) continue;
    // 规整空白后再比:按钮文案常被渲染成「取 消」「提 交」(字符间插空格/换行)
    const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    const want = String(args.text == null ? '' : args.text).replace(/\s+/g, ' ').trim();
    // 必须与目标文案相等(都去空白后比)。只靠 pattern 会点到白名单里的第一个
    // 按钮 —— 后续每个触发词都会重复点它,后面的弹窗表单永远探不到。
    if (t && t.length <= 20 && re.test(t) && t === want) {
      el.scrollIntoView({block: 'center', behavior: 'instant'});
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }
  }
  return null;
}
"""

# 当前 frame 原点相对父 frame 视口的偏移(逐层累加换算主视口坐标)
FRAME_OFFSET_JS = r"""
() => {
  const el = window.frameElement;
  if (!el) return { x: 0, y: 0 };
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y };
}
"""


def _probe_key(attrs: dict) -> tuple:
    """探针增量 diff 的元素键(基线里已出现的不再重复采集)。"""
    return (
        (attrs.get("tag") or "").lower(),
        (attrs.get("inputType") or "").lower(),
        attrs.get("name") or "",
        attrs.get("id") or "",
        attrs.get("placeholder") or "",
        attrs.get("cssPath") or "",
    )


class _NetworkActivity:
    """记录最近一次网络活动的时刻,供落稳判据判断页面是否还在加载。

    微前端子应用挂载耗时不固定(实测同一页面 4~8s 波动),"计数不变"无法区分
    「已加载完」和「还没开始加载」——外壳元素数在挂载前会长时间不动。网络活动
    是更强的信号:子应用还在拉 chunk 时,quiet_ms() 达不到阈值,就不会判稳。
    """

    def __init__(self) -> None:
        self.last = time.monotonic()

    def touch(self, *_args: Any, **_kwargs: Any) -> None:
        self.last = time.monotonic()

    def quiet_ms(self) -> float:
        return (time.monotonic() - self.last) * 1000


async def _count_interactive(page) -> int:
    """跨 frame 穿透 shadow root 统计交互元素数(落稳轮询用,只计数不采集属性)。

    frame 在轮询期间可能卸载/跨域,单个失败只跳过该 frame,不影响整体判稳。
    """
    total = 0
    for frame in list(page.frames):
        try:
            total += int(await frame.evaluate(PAGE_COUNT_JS) or 0)
        except Exception:
            continue
    return total


async def _wait_for_settle(
    page,
    *,
    activity: Optional[_NetworkActivity] = None,
    quiet_ms: int = SETTLE_QUIET_MS,
    min_wait_ms: int = SETTLE_MIN_WAIT_MS,
    interval_ms: int = SETTLE_INTERVAL_MS,
    stable_rounds: int = SETTLE_STABLE_ROUNDS,
    timeout_ms: int = SETTLE_TIMEOUT_MS,
) -> int:
    """等页面渲染落稳,返回最后一轮的交互元素计数。

    判据是「穿透 shadow root 后的元素数连续 stable_rounds 轮不变」,而不是
    networkidle —— 后者在微前端子应用异步挂载时会在主应用静默窗口提前满足。

    另外三道防线,都来自实测:
      - 计数为 0 时不判稳(页面还没开始渲染);
      - min_wait_ms 预热期内只观察不判稳 —— 子应用挂载前主应用外壳会长时间
        稳定在个位数元素上(实测 t=0~3s 恒为 2 个);
      - 传了 activity 时还要求网络安静 quiet_ms —— 子应用挂载耗时波动大
        (实测 4~8s),固定预热期兜不住,「还在拉 chunk」是比计数更强的信号。
    """
    start = time.monotonic()
    warm_until = start + min_wait_ms / 1000
    deadline = start + timeout_ms / 1000
    previous = -1
    stable = 0
    current = 0
    while True:
        current = await _count_interactive(page)
        if current > 0 and current == previous:
            stable += 1
        else:
            stable = 0
        if (
            stable >= stable_rounds
            and time.monotonic() >= warm_until
            and (activity is None or activity.quiet_ms() >= quiet_ms)
        ):
            return current
        previous = current
        if time.monotonic() >= deadline:
            return current
        await asyncio.sleep(interval_ms / 1000)


async def _collect_all_frames(
    page,
    skipped: Optional[list] = None,
    scope_out: Optional[list] = None,
) -> list[dict]:
    """遍历所有 frame 跑 EXTRACT_JS,返回原始元素列表(非 main frame 带 frame 字段)。

    传入 `skipped` 列表可同时收下「疑似可交互但未采集」的诊断清单(见 EXTRACT_JS)。
    传入 `scope_out` 则收下本次实际穿透的 wujie 子应用 id(只收首个,避免探针多轮
    重复采集时把日志刷屏),供调用方确认采的是不是当前路由那个子应用。
    """
    items: list[dict] = []
    for frame in page.frames:
        try:
            payload = await frame.evaluate(EXTRACT_JS)
        except Exception:
            continue
        # EXTRACT_JS 返回 {elements, skipped, wujieScope};兼容旧版直接返回 list 的情况
        if isinstance(payload, dict):
            part = payload.get("elements") or []
            frame_skipped = payload.get("skipped") or []
            if frame is page.main_frame and scope_out is not None and not scope_out:
                scope = payload.get("wujieScope") or ""
                if scope:
                    scope_out.append(scope)
        else:
            part, frame_skipped = payload, []
        if not isinstance(part, list):
            continue
        frame_url = "" if frame is page.main_frame else (frame.url or "")
        for item in part:
            if isinstance(item, dict):
                if frame_url:
                    item["frame"] = frame_url
                items.append(item)
        if skipped is not None:
            for item in frame_skipped:
                if isinstance(item, dict):
                    skipped.append({**item, "frame": frame_url})
    return items


async def _frame_viewport_offset(page, frame) -> Optional[dict]:
    """iframe 原点在主视口中的累计偏移(逐层向上累加 frameElement 位置)。"""
    total = {"x": 0.0, "y": 0.0}
    f = frame
    while f is not page.main_frame:
        try:
            off = await f.evaluate(FRAME_OFFSET_JS)
        except Exception:
            return None
        total["x"] += off["x"]
        total["y"] += off["y"]
        f = f.parent_frame
        if f is None:
            return None
    return total


def _loose_text_pattern(text: str) -> str:
    """文案正则:字符之间允许任意空白。

    按钮常被渲染成「取 消」「提 交」(字符间插空格/换行),严格 `^取消$` 匹配不到。
    """
    chars = [re.escape(ch) for ch in str(text or "") if not ch.isspace()]
    if not chars:
        return r"^$"
    return r"^\s*" + r"\s*".join(chars) + r"\s*$"


async def _click_button_by_text(page, text: str) -> bool:
    """跨 frame 按文案找按钮并坐标点击(用于兜底点「取消」关浮层)。

    文案匹配放宽到容忍字符间空白,见 `_loose_text_pattern`。
    """
    pattern = _loose_text_pattern(text)
    for frame in page.frames:
        try:
            point = await frame.evaluate(FIND_POINT_JS, {"pattern": pattern, "text": text})
            if not point:
                continue
            offset = await _frame_viewport_offset(page, frame)
            if offset is None:
                continue
            await page.mouse.click(offset["x"] + point["x"], offset["y"] + point["y"])
            return True
        except Exception:
            continue
    return False


async def _overlay_gone(page, added_keys: set) -> bool:
    """浮层是否已关闭:本轮新增的浮层元素是否全部消失。

    平台无关闭环:不依赖弹窗容器结构(AntD/鲁班/自研各不相同),只看「新增的
    浮层元素还在不在」—— 这个判据对所有实现都成立。
    """
    try:
        current = {_probe_key(item) for item in await _collect_all_frames(page)}
    except Exception:
        return False
    return not (added_keys & current)


async def _close_overlay(
    page,
    added_keys: set,
    timeout_ms: int,
    activity=None,
    *,
    allow_reload: bool = True,
) -> bool:
    """尽最大努力关闭浮层,返回是否确认关闭。

    逐级降级:Escape → 点「取消」/「关闭」→ (可选)重载页面。原实现只试 Escape + 「取消」,
    失败就返回 False 让调用方终止整轮探针 —— 实测 wujie 子应用弹窗两者都无效,
    结果只探到前 1~2 个触发按钮(命中 306 → 153)。重载是最后一道防线,只有它也
    失败才认输。

    `allow_reload=False` 时禁用重载兜底:深度递归中父浮层还开着,重载会把整页
    打回基线、父层状态全丢,后续兄弟触发词无法继续 —— 嵌套层关不掉只能中止该枝。
    """
    for _ in range(CLOSE_RETRY):
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(CLOSE_WAIT_MS)
        except Exception:
            pass
        if await _overlay_gone(page, added_keys):
            return True

        for text in CLOSE_BUTTON_TEXTS:
            if not await _click_button_by_text(page, text):
                continue
            try:
                await page.wait_for_timeout(CLOSE_WAIT_MS)
            except Exception:
                break
            if await _overlay_gone(page, added_keys):
                return True

    if not allow_reload or not CLOSE_RELOAD_FALLBACK:
        return False
    try:
        await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
        await _wait_for_settle(page, activity=activity)
        logger.info("[page_extractor] 浮层关不掉,已重载页面回到基线继续探针")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[page_extractor] 浮层关闭失败且重载异常: {exc}")
        return False


async def _click_trigger(page, pattern: str, text: str, timeout_ms: int) -> str:
    """点击触发按钮,返回状态: `clicked` | `not_found` | `routed`。

    刻意不接收 frame 句柄:每次调用都在**当前** frame 列表里重新找按钮。关闭浮层的
    兜底重载(见 `_close_overlay`)会让页面重建,重载前抓的 frame 句柄会全部失效。
    绝不点击「确定」类提交按钮。
    """
    base_url = page.url
    point = None
    frame = None
    for candidate in list(page.frames):
        try:
            point = await candidate.evaluate(FIND_POINT_JS, {"pattern": pattern, "text": text})
        except Exception:
            continue
        if point:
            frame = candidate
            break
    if not point or frame is None:
        return "not_found"
    offset = await _frame_viewport_offset(page, frame)
    if offset is None:
        return "not_found"
    try:
        await page.mouse.click(offset["x"] + point["x"], offset["y"] + point["y"])
    except Exception:
        return "not_found"
    if page.url != base_url:
        # 路由跳转型触发:回到列表页继续,不采集跳转后的新页面(仅浮层递归)
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(DIALOG_OPEN_WAIT_MS)
        except Exception:
            pass
        return "routed"
    try:
        await page.wait_for_timeout(DIALOG_OPEN_WAIT_MS)
        try:
            await page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
    except Exception:
        return "not_found"
    return "clicked"


def _triggers_from_raw(items: list[dict], pattern: str, limit: int) -> list[str]:
    """从本层新增的 raw 元素里找出可继续下探的触发按钮文案(不整页重扫)。"""
    regex = re.compile(pattern)
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        tag = (item.get("tag") or "").lower()
        role = (item.get("role") or "").lower()
        text = (item.get("text") or "").strip()
        if not text or len(text) > 12:
            continue
        if tag not in ("button", "a") and role != "button":
            continue
        if not regex.search(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


class _ProbeBudget:
    """全局触发点击预算:防止 3 层 × 每层多触发词把探针拖到超时。"""

    def __init__(self, max_total: int) -> None:
        self.max_total = max(0, int(max_total))
        self.used = 0

    def consume(self) -> bool:
        if self.used >= self.max_total:
            return False
        self.used += 1
        return True


async def _probe_trigger(
    page,
    pattern: str,
    text: str,
    baseline_keys: set,
    timeout_ms: int,
    activity=None,
    *,
    path: Optional[list[str]] = None,
    allow_reload: bool = True,
) -> tuple[list[dict], bool]:
    """触发单个按钮 → 落稳后增量采集 → 关闭浮层并验证(单层,不递归)。

    返回 (新增元素列表, 浮层是否确认关闭)。`path` 写入每个新增元素的
    probePath/probeTrigger(建树用);缺省为 [text]。
    """
    probe_path = list(path) if path is not None else [text]
    status = await _click_trigger(page, pattern, text, timeout_ms)
    if status == "not_found":
        return [], True
    if status == "routed":
        return [], True
    try:
        added: list[dict] = []
        added_keys: set = set()
        for item in await _collect_all_frames(page):
            key = _probe_key(item)
            if key in baseline_keys or key in added_keys:
                continue
            added_keys.add(key)
            item["probePath"] = probe_path
            item["probeTrigger"] = "/".join(probe_path)
            added.append(item)
    except Exception:
        return [], False

    if not added:
        # 无新增元素(点击可能落空或浮层无新元素):Escape 一次兜底后返回
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(CLOSE_WAIT_MS)
        except Exception:
            pass
        return [], True

    closed = await _close_overlay(
        page, added_keys, timeout_ms, activity, allow_reload=allow_reload
    )
    return added, closed


async def _probe_recursive(
    page,
    pattern: str,
    pending: list[str],
    ancestor_keys: set,
    path: list[str],
    depth: int,
    max_depth: int,
    budget: _ProbeBudget,
    visited: set,
    timeout_ms: int,
    activity=None,
    max_triggers: int = 8,
) -> tuple[list[dict], bool]:
    """在当前浮层状态下递归探针。

    `path`: 祖先触发路径(基线为 []);本层点击 text 后新增元素的 probePath = path+[text]。
    `depth`: 本层对应的浮层深度(path 长度);depth+1 为即将采集的元素深度。
    `ancestor_keys`: 本层点击前已可见的所有元素键(基线 ∪ 祖先浮层),用于增量 diff。
    `allow_reload`: 仅 depth==0(回基线)允许重载兜底。

    返回 (本枝及子枝新增元素, 是否全程成功关闭)。
    """
    collected: list[dict] = []
    ok = True
    for text in pending:
        if not text:
            continue
        visit_key = (tuple(path), text)
        if visit_key in visited:
            continue
        if not budget.consume():
            logger.info(f"[page_extractor] 触发预算耗尽({budget.max_total}),停止下探")
            break
        visited.add(visit_key)

        item_path = path + [text]
        status = await _click_trigger(page, pattern, text, timeout_ms)
        if status in ("not_found", "routed"):
            continue

        try:
            added: list[dict] = []
            added_keys: set = set()
            for item in await _collect_all_frames(page):
                key = _probe_key(item)
                if key in ancestor_keys or key in added_keys:
                    continue
                added_keys.add(key)
                item["probePath"] = item_path
                item["probeTrigger"] = "/".join(item_path)
                added.append(item)
        except Exception:
            ok = False
            break

        if not added:
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(CLOSE_WAIT_MS)
            except Exception:
                pass
            continue

        collected.extend(added)
        logger.info(
            f"[page_extractor] 探针{'/'.join(item_path)} 新增采集 {len(added)} 个元素"
        )

        # 深度递归:本层新增元素里若还有触发按钮,且未达 max_depth,继续下探
        child_depth = depth + 1
        if child_depth < max_depth:
            nested_texts = _triggers_from_raw(added, pattern, max_triggers)
            if nested_texts:
                nested_ancestor = set(ancestor_keys)
                for item in added:
                    nested_ancestor.add(_probe_key(item))
                child_ok = True
                try:
                    child_added, child_ok = await _probe_recursive(
                        page,
                        pattern,
                        nested_texts,
                        nested_ancestor,
                        item_path,
                        child_depth,
                        max_depth,
                        budget,
                        visited,
                        timeout_ms,
                        activity,
                        max_triggers,
                    )
                    collected.extend(child_added)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[page_extractor] 深度探针「{'/'.join(item_path)}」异常: {exc}")
                    child_ok = False
                if not child_ok:
                    ok = False
                    break

        # 关闭本层浮层(子层应已自行关闭);嵌套层禁用重载
        allow_reload = depth == 0
        closed = await _close_overlay(
            page, added_keys, timeout_ms, activity, allow_reload=allow_reload
        )
        if not closed:
            logger.warning(
                f"[page_extractor] 探针「{'/'.join(item_path)}」浮层无法关闭,终止后续探针"
            )
            ok = False
            break

    return collected, ok


async def extract_elements(
    url: str,
    *,
    storage_state: Optional[str] = None,
    timeout_ms: int = 30000,
    trigger_texts: Optional[list[str]] = None,
    max_triggers: int = 8,
    max_depth: int = 3,
    max_total_triggers: int = 24,
    skipped_out: Optional[list] = None,
    settle_timeout_ms: int = SETTLE_TIMEOUT_MS,
) -> list[dict]:
    """打开页面并采集原始交互元素属性(真浏览器,延迟导入)。

    采集分两个阶段:
      0. 落稳等待:轮询「穿透 shadow root 的交互元素数」+ 网络安静度,双双满足才
         继续。微前端平台的子应用异步挂载,固定等 networkidle 会采到只有外壳的
         半成品页面(实测同一页面命中数在 2~306 之间随机)。
      1. 基线抽取:遍历所有 frame 采集当前可见的交互元素。低代码/微前端平台
         的页面正文常渲染在 iframe 里,主 frame 往往只有外壳(搜索框、头像),
         只扫主 frame 会漏掉绝大部分元素。非 main frame 采集到的元素附带
         `frame` 字段(该 frame 的 URL),供验证/执行阶段路由到正确 frame。
      2. 交互探针(可深度):自动点击「新增/新建」等触发按钮,等弹窗/抽屉渲染后
         重跑采集脚本,仅保留祖先层中没有的元素(附带 `probePath`/`probeTrigger`),
         关闭弹窗继续。`max_depth>1` 时对弹窗内新出现的触发按钮递归下探
         (最多 max_depth 层浮层;仅浮层,不跟路由跳转)。`trigger_texts` 可自定义
         触发词白名单(前缀匹配按钮文案)。
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context_kwargs: dict = {}
            if storage_state:
                context_kwargs["storage_state"] = storage_state
            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()
            # 网络活动监听必须在 goto 之前注册,才能覆盖子应用 chunk 的加载过程
            activity = _NetworkActivity()
            for event in ("request", "requestfinished", "requestfailed"):
                page.on(event, activity.touch)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            settled = await _wait_for_settle(
                page, activity=activity, timeout_ms=settle_timeout_ms
            )
            logger.info(f"[page_extractor] 页面落稳:交互元素 {settled} 个")
            scopes: list[str] = []
            raw = await _collect_all_frames(page, skipped_out, scopes)
            if scopes:
                logger.info(f"[page_extractor] 微前端作用域: {scopes[0]}")

            # ---- 交互探针:触发「新增」类弹窗并增量采集(支持深度递归) ----
            if max_depth > 0:
                if trigger_texts:
                    pattern = "|".join(re.escape(t.strip()) for t in trigger_texts if t.strip())
                else:
                    pattern = DEFAULT_TRIGGER_PATTERN
                if pattern:
                    baseline_keys = {_probe_key(item) for item in raw}
                    # 先跨 frame 收集所有触发文案(同文案按钮如每行的「编辑」只探一次)。
                    # 之后按文案逐个探测,而不是按 frame 快照遍历 —— 关浮层的兜底重载会
                    # 重建页面,帧快照里的句柄会全部失效,后续探针会被静默跳过。
                    pending: list[str] = []
                    for frame in list(page.frames):
                        try:
                            triggers = await frame.evaluate(FIND_TRIGGERS_JS, pattern)
                        except Exception:
                            continue
                        for trig in (triggers or [])[:max_triggers]:
                            text = ((trig or {}).get("text") or "").strip()
                            if text and text not in pending:
                                pending.append(text)
                    budget = _ProbeBudget(max_total_triggers)
                    visited: set = set()
                    added, _ok = await _probe_recursive(
                        page,
                        pattern,
                        pending,
                        baseline_keys,
                        [],
                        0,
                        max_depth,
                        budget,
                        visited,
                        timeout_ms,
                        activity,
                        max_triggers,
                    )
                    for item in added:
                        baseline_keys.add(_probe_key(item))
                        raw.append(item)
            return raw
        finally:
            await browser.close()


def build_llm_naming_prompt(raw_elements: list[dict]) -> str:
    """构造 LLM 语义命名请求(紧凑元素特征列表)。"""
    compact = []
    for index, attrs in enumerate(raw_elements, 1):
        compact.append({
            "index": index,
            "tag": attrs.get("tag"),
            "type": attrs.get("inputType"),
            "name": attrs.get("name"),
            "id": attrs.get("id"),
            "placeholder": attrs.get("placeholder"),
            "label": attrs.get("label"),
            "aria_label": attrs.get("ariaLabel"),
            "text": attrs.get("text"),
            "context": attrs.get("context"),
            "probe_trigger": attrs.get("probeTrigger"),
            "probe_path": "/".join(attrs.get("probePath") or []) or attrs.get("probeTrigger") or "",
        })
    return (
        "你是 UI 测试专家。下面是网页上抽取到的可交互元素属性列表。\n"
        "probe_trigger/probe_path 表示该元素是通过点击路径上的按钮打开的弹窗/抽屉里的元素"
        "(如「新增/高级设置」表示先点新增再点高级设置),命名时可参考层级上下文。\n"
        "请为每个元素给出简洁的中文业务名称(name)、类型(type: input/button/link/select/textarea/checkbox/radio)和用途说明(description)。\n"
        "名称要能体现业务用途(如「用户名输入框」「登录按钮」),不要使用随机 id。\n"
        "只输出 JSON 对象,格式: {\"1\": {\"name\": \"...\", \"type\": \"...\", \"description\": \"...\"}, ...}\n\n"
        f"元素列表:\n{json.dumps(compact, ensure_ascii=False)}"
    )


def parse_llm_names(text: str) -> dict:
    """从 LLM 输出中解析 JSON 命名映射;解析失败返回空 dict。"""
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    candidates = [raw]
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def apply_llm_names(raw_elements: list[dict], names: dict) -> list[dict]:
    """把 LLM 命名结果合并回原始元素属性(新增 llmName/llmType/llmDescription)。"""
    result = [dict(item) for item in raw_elements]
    for raw_index, naming in (names or {}).items():
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(result) and isinstance(naming, dict):
            merged = result[index - 1]
            if naming.get("name"):
                merged["llmName"] = str(naming["name"])[:60]
            if naming.get("type"):
                merged["llmType"] = str(naming["type"]).strip().lower()
            if naming.get("description"):
                merged["llmDescription"] = str(naming["description"])[:200]
    return result
