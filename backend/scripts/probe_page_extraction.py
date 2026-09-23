"""页面元素抽取诊断:回答「这个按钮为什么没被抽到」。

目的
----
UI 自动化工具的元素抽取走 `page_extractor.EXTRACT_JS`(真浏览器注入 JS)。当
界面上的某个控件没有出现在抽取结果里时,靠读代码猜原因效率很低。本脚本直接
把抽取器跑起来,打印「原始命中」与「归一化后」两层结果,并列出页面上真实存在
但被丢弃的元素特征,用来区分三种典型原因:

  1. 选择器不覆盖 —— 元素既不是 input/button,也没有 role,压根不在 SELECTOR 里;
  2. 可见性过滤 —— 元素在 DOM 里但 display:none / 0 尺寸,被 isVisible 滤掉;
  3. 无候选定位器 —— 归一化阶段因拿不到任何定位器被丢弃。

上传控件(Element UI / antd)属于第 2 类:框架把 input[type=file] 设成隐藏,
所以抽取器对 file input 单独扫描并豁免可见性,同时补采它的可见触发器。

用法
----
    # 1. 跑内置样例:覆盖 7 种常见上传组件形态,用于回归抽取逻辑
    python scripts/probe_page_extraction.py

    # 2. 抽真实页面(可带登录态),打印全部元素与定位器
    python scripts/probe_page_extraction.py --url http://192.168.13.110/safety/check
    python scripts/probe_page_extraction.py --url %s --storage-state state.json

    # 3. 只看上传类元素
    python scripts/probe_page_extraction.py --url %s --only-upload

`--storage-state` 传 Playwright 的 storage_state JSON(UI 自动化执行时缓存的登录态),
路径可从 backend/data 下的登录态文件复制。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.page_extractor import (  # noqa: E402
    EXTRACT_JS,
    extract_elements,
    normalize_elements,
)

SAMPLE_HTML = r"""
<!doctype html><html><body>
<h3>1. Element UI 图片卡片上传</h3>
<div class="el-upload el-upload--picture-card" tabindex="0">
  <i class="el-icon-plus"></i>
  <input type="file" name="file" accept="image/*" class="el-upload__input" style="display:none">
</div>

<h3>2. Element UI 按钮式上传(button 包 input)</h3>
<div class="el-upload">
  <button type="button" class="el-button el-button--primary">
    <span>上传图片</span>
    <input type="file" name="imageFile" accept="image/*" style="display:none">
  </button>
</div>

<h3>3. antd Upload</h3>
<span class="ant-upload-wrapper">
  <div class="ant-upload ant-upload-select">
    <span class="ant-upload" tabindex="0">
      <input type="file" accept="image/*" class="ant-upload-input" style="display:none">
      <button type="button" class="ant-btn"><span>上传图片</span></button>
    </span>
  </div>
</span>

<h3>4. label 包裹 input</h3>
<label class="upload-btn">
  <input type="file" accept="image/*" hidden>
  上传图片
</label>

<h3>5. 纯 div 自定义触发 + 隐藏 input</h3>
<div class="upload-box" onclick="document.getElementById('f5').click()">
  <span class="upload-text">上传图片</span>
  <input id="f5" type="file" accept="image/*" style="position:absolute;width:0;height:0;opacity:0">
</div>

<h3>6. 正常可见 file input(对照组)</h3>
<input type="file" name="visibleUpload" accept="image/*">

<h3>7. 图标按钮(无文字)</h3>
<button type="button" class="icon-btn" title="上传">
  <i class="el-icon-upload2"></i>
</button>

<h3>8. 低代码 textRadio(隐藏原生 radio + span 视觉壳)</h3>
<div class="textRadio" data-field="dangerLevel">
  <label class="textRadio-item"><input type="radio" name="dangerLevel" value="1" style="display:none"><span>一般隐患</span></label>
  <label class="textRadio-item"><input type="radio" name="dangerLevel" value="2" style="display:none"><span>重大隐患</span></label>
</div>

<h3>9. 低代码纯 div 模拟输入框</h3>
<div class="lc-input" tabindex="0" contenteditable="true" style="border:1px solid #ccc;padding:6px">请输入检查部位</div>

<h3>10. opacity:0 覆盖层输入框</h3>
<div class="lc-input-wrap" style="position:relative">
  <div class="lc-input-display">请输入检查部位</div>
  <input name="checkPartOpacity" style="position:absolute;inset:0;opacity:0">
</div>

<h3>11. open shadow DOM 内的控件</h3>
<div id="shadowHost"></div>

<h3>12. 未激活 tab 里的表单(对照组,应当不采集)</h3>
<div class="tab-panel" style="display:none">
  <input name="inInactiveTab" placeholder="未激活tab里的输入框">
</div>

<script>
  const shadowRoot = document.getElementById('shadowHost').attachShadow({ mode: 'open' });
  shadowRoot.innerHTML = '<input name="shadowInput" placeholder="shadow 里的检查部位">'
    + '<div class="textRadio"><label><input type="radio" name="shadowRadio" style="display:none"><span>shadow一般</span></label></div>';
</script>
</body></html>
"""

# 页面里真实存在但可能被丢弃的候选元素(诊断可见性/定位器问题的参照系)
AUDIT_JS = r"""
() => {
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
    id: el.id || '',
    name: el.getAttribute('name') || '',
    class: (el.getAttribute('class') || '').slice(0, 50),
    text: (el.innerText || el.textContent || '').trim().slice(0, 30),
    visible: isVisible(el),
    inSelector: el.matches("input, textarea, select, button, a[href], [role='button'], [role='link'], [role='textbox'], [role='checkbox'], [role='radio'], [role='combobox'], [role='tab'], [contenteditable='true'], [data-testid], [data-test], [data-cy]"),
  });
  return {
    fileInputs: Array.from(document.querySelectorAll("input[type='file']")).map(describe),
    buttons: Array.from(document.querySelectorAll("button, [role='button'], [class*='upload' i]")).map(describe),
  };
}
"""


def _brief(element: dict) -> dict:
    brief = {
        "key": element.get("key"),
        "name": element.get("name"),
        "type": element.get("type"),
        "frame": element.get("frame") or "",
        "probeTrigger": element.get("probeTrigger") or "",
        "locators": [f'{loc["strategy"]}={loc["value"]}' for loc in element.get("locators") or []],
    }
    if element.get("trigger"):
        brief["trigger"] = f'{element["trigger"]["strategy"]}={element["trigger"]["value"]}'
    return brief


def _flag(item: dict) -> str:
    for field, label in (
        ("isFileInput", "file"),
        ("uploadTrigger", "upload-shell"),
        ("visualReplaced", "hidden-native"),
        ("customControl", "custom"),
    ):
        if item.get(field):
            return f" [{label}]"
    return ""


async def _audit(page) -> dict:
    """列页面里真实存在的 file input / 按钮及其丢弃原因(逐 frame)。"""
    report: dict = {"fileInputs": [], "buttons": []}
    for frame in page.frames:
        try:
            data = await frame.evaluate(AUDIT_JS)
        except Exception:  # noqa: BLE001 - 跨域 frame 读不到就跳过
            continue
        prefix = "" if frame is page.main_frame else f"[frame {frame.url[:60]}] "
        for key in ("fileInputs", "buttons"):
            for item in data.get(key) or []:
                item["frame"] = prefix
                report[key].append(item)
    return report


async def run_sample() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(SAMPLE_HTML)
            data = await page.evaluate(EXTRACT_JS)
            raw = (data.get("elements") or []) if isinstance(data, dict) else data
            skipped = (data.get("skipped") or []) if isinstance(data, dict) else []
            print(f"=== 内置样例:EXTRACT_JS 原始命中 {len(raw)} 个 ===")
            for item in raw:
                print(
                    f'  {item["tag"]:<8} type={item["inputType"]:<8} text={item["text"][:16]:<16}'
                    f' class={item["class"][:26]:<26}{_flag(item)}'
                )
            elements = normalize_elements(raw)
            print(f"\n=== 归一化后 {len(elements)} 个 ===")
            for element in elements:
                print("  " + json.dumps(_brief(element), ensure_ascii=False))
            suspicious = [item for item in skipped if item.get("reason") == "visible-not-collected"]
            print(f"\n=== 未采集:可见(可疑) {len(suspicious)} 个 / 隐藏 {len(skipped) - len(suspicious)} 个 ===")
            for item in suspicious:
                print(f'  {item["tag"]} class={(item["class"] or "-")[:32]} text={(item["text"] or "-")[:22]}')
        finally:
            await browser.close()


async def run_url(url: str, storage_state: str | None, only_upload: bool, show_hidden: bool) -> None:
    kwargs = {"storage_state": storage_state} if storage_state else {}
    skipped: list = []
    raw = await extract_elements(url, skipped_out=skipped, **kwargs)
    elements = normalize_elements(raw)
    uploads = [el for el in elements if el.get("type") == "upload"]

    print(f"=== {url} ===")
    print(f"原始命中 {len(raw)} 个 → 归一化 {len(elements)} 个,其中 upload 类型 {len(uploads)} 个\n")

    if uploads:
        print("上传类元素:")
        for element in uploads:
            print("  " + json.dumps(_brief(element), ensure_ascii=False))
    else:
        print("!! 没有抽到任何 upload 类型的元素")
    print()

    if not only_upload:
        print("全部元素:")
        for element in elements:
            print("  " + json.dumps(_brief(element), ensure_ascii=False))
        print()

    # ---- 未采集清单:抽取器漏掉控件的直接证据 ----
    # reason=visible-not-collected 表示「界面上看得见、像控件,却没被采集」—— 真缺口。
    # hidden 多为未激活 tab / 未打开的弹窗,属预期丢弃。
    suspicious = [item for item in skipped if item.get("reason") == "visible-not-collected"]
    hidden = [item for item in skipped if item.get("reason") != "visible-not-collected"]
    print("=== 页面存在但未采集的元素 ===")
    print(f"可见却未采集(可疑) {len(suspicious)} 个 / 隐藏 {len(hidden)} 个")
    for item in suspicious:
        print(
            f'  {item.get("frame")}{item.get("tag")} name={item.get("name") or "-"} '
            f'class={(item.get("class") or "-")[:34]} text={(item.get("text") or "-")[:22]}'
        )
    if suspicious:
        print("  ↑ 抽取器覆盖不到这些元素,把上面几行贴出来即可定位缺口")
    if show_hidden:
        for item in hidden:
            print(
                f'  [hidden] {item.get("frame")}{item.get("tag")} name={item.get("name") or "-"} '
                f'class={(item.get("class") or "-")[:34]} text={(item.get("text") or "-")[:22]}'
            )

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(**dict(kwargs))
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            report = await _audit(page)
        finally:
            await browser.close()

    print("\n=== 页面真实存在的候选元素(诊断用) ===")
    print(f"input[type=file] 共 {len(report['fileInputs'])} 个:")
    for item in report["fileInputs"]:
        print(
            f'  {item["frame"]}{item["tag"]} name={item["name"] or "-"} class={item["class"] or "-"} '
            f'visible={item["visible"]}'
        )
    hidden_files = [i for i in report["fileInputs"] if not i["visible"]]
    if hidden_files:
        print(f"  其中 {len(hidden_files)} 个不可见 —— 框架上传控件属此类,已由抽取器豁免可见性")
    print(f"按钮/上传容器 共 {len(report['buttons'])} 个,未命中选择器的:")
    for item in report["buttons"]:
        if not item["inSelector"]:
            print(
                f'  {item["frame"]}{item["tag"]} class={item["class"] or "-"} '
                f'text={item["text"] or "-"} visible={item["visible"]}'
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="页面元素抽取诊断")
    parser.add_argument("--url", help="要抽取的页面 URL;不传则跑内置上传组件样例")
    parser.add_argument("--storage-state", help="Playwright storage_state JSON 路径(登录态)")
    parser.add_argument("--only-upload", action="store_true", help="只列出上传类元素")
    parser.add_argument("--show-hidden", action="store_true", help="同时列出被隐藏而未采集的元素")
    args = parser.parse_args()

    if args.url:
        asyncio.run(run_url(args.url, args.storage_state, args.only_upload, args.show_hidden))
    else:
        asyncio.run(run_sample())


if __name__ == "__main__":
    main()
