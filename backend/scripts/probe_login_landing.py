"""探测「同一 URL 带/不带登录态分别落到哪个页面」。

排查登录相关 UI 自动化问题的第一步:定位器全部失效时,先看浏览器到底停在了
哪个页面 —— 带了登录态被站点直接带进业务页(登录页元素自然一个都不在),还是
匿名访问被踢回登录页。两种情况的修法完全不同。

用法:
  python scripts/probe_login_landing.py --url http://192.168.13.178:8182/
  python scripts/probe_login_landing.py --url <url> --state data/browser_sessions/pages/po514.json
  python scripts/probe_login_landing.py --url <url> --state none      # 只跑匿名
  python scripts/probe_login_landing.py --url <url> --state auto      # 默认:通用缓存 + 匿名各跑一次
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

PROBE_JS = """
() => {
  const q = (s) => document.querySelectorAll(s).length;
  return {
    href: location.href,
    title: document.title,
    userInputs: q('input[placeholder*="用户"], input[name*="user"], input[id*="user"]'),
    passwordInputs: q('input[type="password"]'),
    buttons: q('button'),
    bodyText: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, 160),
  };
}
"""


async def probe(url: str, storage_state: str | None, settle_ms: int) -> dict:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        kwargs = {"storage_state": storage_state} if storage_state else {}
        context = await browser.new_context(**kwargs)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(settle_ms)
        info = await page.evaluate(PROBE_JS)
        state = await context.storage_state()
        info["cookies"] = len(state.get("cookies", []))
        info["local_storage_keys"] = sorted(
            {i["name"] for o in state.get("origins", []) for i in o.get("localStorage", [])}
        )
        await browser.close()
        return info


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--state", default="auto", help="登录态文件路径 / none / auto")
    ap.add_argument("--settle-ms", type=int, default=6000)
    args = ap.parse_args()

    targets: list[tuple[str, str | None]] = []
    if args.state == "auto":
        from app.services import browser_agent as ba
        from urllib.parse import urlparse

        parsed = urlparse(args.url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cache = ba._cache_path(origin)
        if cache.exists():
            targets.append((f"通用缓存 {cache.name}", str(cache)))
        else:
            print(f"# 通用缓存不存在: {cache}")
        targets.append(("匿名", None))
    elif args.state.lower() == "none":
        targets.append(("匿名", None))
    else:
        targets.append((args.state, args.state))

    for label, state in targets:
        try:
            info = await probe(args.url, state, args.settle_ms)
        except Exception as exc:  # noqa: BLE001
            print(f"\n### {label} -> 探测失败: {exc}")
            continue
        print(f"\n### {label}")
        print(f"  落地 URL   : {info['href']}")
        print(f"  用户名输入 : {info['userInputs']}  密码输入: {info['passwordInputs']}  button: {info['buttons']}")
        print(f"  cookies    : {info['cookies']}  localStorage: {json.dumps(info['local_storage_keys'], ensure_ascii=False)}")
        print(f"  正文开头   : {info['bodyText']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
