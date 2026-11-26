"""页面洞察工具测试。

覆盖：权限拒绝、URL 校验、登录态缓存复用、元素摘要渲染与截断、
LOGIN_REQUIRED 标记、登录成功/失败路径、门控。
全部 mock 浏览器层（extract_elements / establish_login_session），
不触网、不启动真实 Chromium。
"""
import asyncio
import pytest

from app.auth import UserInfo

import app.services.page_insight_tools as pi
from app.services.page_insight_tools import (
    LOGIN_REQUIRED_MARKER,
    _format_elements,
    build_page_insight_tools,
    load_page_insight_tools,
)


def _admin():
    return UserInfo(user_id="u1", username="admin", roles=["admin"])


def _noperm():
    return UserInfo(user_id="u2", username="ro", roles=["user_no_perms"])


def _tools(user):
    """把 async 工具函数包装成同步调用（asyncio.run），便于直接断言。"""
    import functools

    def syncify(coro_fn):
        @functools.wraps(coro_fn)
        def wrapper(*args, **kwargs):
            return asyncio.run(coro_fn(*args, **kwargs))
        return wrapper

    return {t.name: syncify(t.coroutine) for t in build_page_insight_tools(user)}


# ===== 权限与校验 =====

def test_browse_denied_without_permission():
    result = _tools(_noperm())["browse_page"](url="https://example.com/list")
    assert "权限不足" in result and "ui_automation:manage" in result

def test_login_denied_without_permission():
    result = _tools(_noperm())["submit_login_credentials"](username="a", password="b")
    assert "权限不足" in result

def test_browse_rejects_invalid_url():
    fn = _tools(_admin())["browse_page"]
    assert "URL 非法" in fn(url="ftp://x")
    assert "URL 非法" in fn(url="not-a-url")

def test_login_requires_prior_browse():
    result = _tools(_admin())["submit_login_credentials"](username="a", password="b")
    assert "browse_page" in result

def test_login_rejects_empty_credentials():
    tools = _tools(_admin())
    tools["browse_page"](url="https://example.com/list")
    result = tools["submit_login_credentials"](username="", password="b")
    assert "不能为空" in result


# ===== 元素摘要渲染 =====

def test_format_elements_groups_by_type():
    """字段与 extract_elements 真实输出对齐（tag/text/placeholder/role）。"""
    raw = [
        {"tag": "button", "text": "新增"},
        {"tag": "button", "text": "确定"},
        {"tag": "input", "placeholder": "请输入材料名称"},
        {"tag": "a", "text": "返回列表"},
        {"tag": "div", "role": "tab", "text": "物资部位管理"},
    ]
    out = _format_elements(raw)
    assert "### 按钮（2 个）" in out
    assert "### 输入框（1 个）" in out
    assert "### 链接（1 个）" in out
    assert "### 页签（1 个）" in out
    assert "新增" in out and "请输入材料名称" in out and "物资部位管理" in out


def test_format_elements_dedupes_and_skips_empty():
    """表格行内重复按钮去重；无文本容器（div 壳）不进摘要。"""
    raw = [
        {"tag": "div", "text": "项目材料库 合作单位管理 库房管理"},
        {"tag": "button", "text": "查看"},
        {"tag": "button", "text": "查看"},
        {"tag": "button", "text": "查看"},
        {"tag": "span"},
        {"tag": "input"},
    ]
    out = _format_elements(raw)
    assert out.count("- 查看") == 1
    # div 壳仍保留（有文本），空 span/input 不占行
    assert "项目材料库" in out

def test_format_elements_truncates_long_output():
    raw = [
        {"tag": "button", "text": f"按钮-{i}-" + "B" * 180} for i in range(100)
    ]
    out = _format_elements(raw, limit=2000)
    assert len(out) < 4000
    assert "省略" in out


# ===== browse_page 主流程（mock 浏览器层）=====

def test_browse_uses_cached_session(monkeypatch):
    """有登录态缓存时直接带 storage_state 抽取元素。"""
    captured = {}

    async def fake_extract(url, storage_state=None, timeout_ms=30000, **kw):
        captured["url"] = url
        captured["storage_state"] = storage_state
        return [{"tag": "button", "text": "保存"}]

    monkeypatch.setattr(pi, "_origin_of", lambda url: "https://cd.luban.fit")

    import app.services.browser_agent as ba
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: True)

    import app.services.page_extractor as pe
    monkeypatch.setattr(pe, "extract_elements", fake_extract)

    result = _tools(_admin())["browse_page"](url="https://cd.luban.fit/#/list")
    assert "页面访问成功" in result
    assert "使用已有登录态" in result
    assert captured["storage_state"], "缓存命中时必须带 storage_state"


def test_browse_requires_login_when_redirected_to_login_page(monkeypatch):
    """无缓存且采集到登录页元素（密码框）→ LOGIN_REQUIRED 标记。"""
    monkeypatch.setattr(pi, "_origin_of", lambda url: "https://cd.luban.fit")

    import app.services.browser_agent as ba
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: False)

    import app.services.page_extractor as pe
    async def fake_extract(url, storage_state=None, **kw):
        return [
            {"tag": "input", "inputType": "text", "placeholder": "请输入账号"},
            {"tag": "input", "inputType": "password", "placeholder": "请输入密码"},
            {"tag": "button", "text": "登录"},
        ]
    monkeypatch.setattr(pe, "extract_elements", fake_extract)

    result = _tools(_admin())["browse_page"](url="https://cd.luban.fit/#/list")
    assert result.startswith(LOGIN_REQUIRED_MARKER)
    assert "submit_login_credentials" in result
    assert "全局参数" in result


def test_browse_failure_returns_error_not_login(monkeypatch):
    """真异常（网络/超时）不再泛化为 LOGIN_REQUIRED，如实返回错误。"""
    monkeypatch.setattr(pi, "_origin_of", lambda url: "https://cd.luban.fit")

    import app.services.browser_agent as ba
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: False)

    import app.services.page_extractor as pe
    async def boom(url, storage_state=None, **kw):
        raise RuntimeError("net::ERR_INTERNET_DISCONNECTED")
    monkeypatch.setattr(pe, "extract_elements", boom)

    result = _tools(_admin())["browse_page"](url="https://cd.luban.fit/#/list")
    assert not result.startswith(LOGIN_REQUIRED_MARKER)
    assert "页面访问失败" in result


def test_browse_returns_elements_summary(monkeypatch):
    monkeypatch.setattr(pi, "_origin_of", lambda url: "https://cd.luban.fit")
    import app.services.browser_agent as ba
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: False)

    import app.services.page_extractor as pe
    async def fake_extract(url, storage_state=None, **kw):
        return [
            {"tag": "button", "text": "确定"},
            {"tag": "input", "placeholder": "请输入材料编码"},
        ]
    monkeypatch.setattr(pe, "extract_elements", fake_extract)
    result = _tools(_admin())["browse_page"](url="https://cd.luban.fit/#/list")
    assert "共采集到 2 个交互元素" in result
    assert "确定" in result and "请输入材料编码" in result


# ===== 登录路径 =====

def test_submit_login_success(monkeypatch):
    tools = _tools(_admin())
    tools["browse_page"](url="https://cd.luban.fit/#/list")  # 记录目标

    captured = {}
    import app.services.browser_agent as ba
    async def fake_establish(db, target, overrides, **kw):
        captured["target"] = target
        captured["username"] = overrides["username"]
        captured["password"] = overrides["password"]
        return {"ok": True, "origin": "https://cd.luban.fit", "reason": ""}
    monkeypatch.setattr(ba, "establish_login_session", fake_establish)

    # 登录成功后会自动重访目标页：mock 抽取返回正常元素（非登录页）
    import app.services.page_extractor as pe
    async def fake_extract(url, storage_state=None, **kw):
        return [{"tag": "button", "text": "保存"}, {"tag": "input", "placeholder": "请输入名称"}]
    monkeypatch.setattr(pe, "extract_elements", fake_extract)
    # 重访走「带登录态」分支：让缓存检查通过
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: True)

    result = tools["submit_login_credentials"](username="alice", password="secret")
    assert "登录成功" in result
    assert "保存" in result, "登录成功后必须带回页面实况"
    assert captured["target"] == "https://cd.luban.fit/#/list"
    assert captured["username"] == "alice" and captured["password"] == "secret"


def test_submit_login_state_not_reusable(monkeypatch):
    """登录流程完成但重访仍被重定向 → 如实报告（SSO/跨实例会话场景）。"""
    tools = _tools(_admin())
    tools["browse_page"](url="https://cd.luban.fit/#/list")

    import app.services.browser_agent as ba
    async def fake_establish(db, target, overrides, **kw):
        return {"ok": True, "origin": "https://cd.luban.fit", "reason": ""}
    monkeypatch.setattr(ba, "establish_login_session", fake_establish)
    # 重访仍见登录页
    import app.services.page_extractor as pe
    async def fake_extract(url, storage_state=None, **kw):
        return [
            {"tag": "input", "inputType": "password", "placeholder": "密码"},
            {"tag": "button", "text": "登录"},
        ]
    monkeypatch.setattr(pe, "extract_elements", fake_extract)
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: False)

    result = tools["submit_login_credentials"](username="a", password="b")
    assert "登录流程完成" in result and "重定向到登录页" in result


def test_submit_login_failure_returns_reason(monkeypatch):
    tools = _tools(_admin())
    tools["browse_page"](url="https://cd.luban.fit/#/list")

    import app.services.browser_agent as ba
    async def fake_establish(db, target, overrides, **kw):
        return {"ok": False, "origin": "https://cd.luban.fit", "reason": "登录未成功"}
    monkeypatch.setattr(ba, "establish_login_session", fake_establish)

    result = tools["submit_login_credentials"](username="a", password="b")
    assert "登录失败" in result and "登录未成功" in result


# ===== 门控 =====

def test_load_gate_general_mode():
    assert load_page_insight_tools("general", 5, _admin()) == []

def test_load_gate_missing_session():
    assert load_page_insight_tools("testcase_optimize", None, _admin()) == []

def test_load_gate_optimize_mode_builds_two():
    tools = load_page_insight_tools("testcase_optimize", 3, _admin())
    assert sorted(t.name for t in tools) == ["browse_page", "submit_login_credentials"]

def test_browse_uses_proactor_phase_wrapper(monkeypatch):
    """回归：uvicorn --reload（SelectorEventLoop）无法启动 Chromium，

    extract_elements 必须经 browser_agent._run_browser_phase 兜底。
    """
    import asyncio

    import app.services.browser_agent as ba
    import app.services.page_extractor as pe

    captured = {}

    def fake_phase(coro):
        captured["wrapped"] = True
        # 在测试 loop 里直接驱动 coro（等价于 Proactor 分支的行为）
        return coro

    monkeypatch.setattr(ba, "_run_browser_phase", fake_phase)
    monkeypatch.setattr(pi, "_origin_of", lambda url: "https://cd.luban.fit")
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: False)

    async def fake_extract(url, storage_state=None, **kw):
        captured["called"] = True
        return [{"tag": "button", "text": "确定"}]

    monkeypatch.setattr(pe, "extract_elements", fake_extract)

    result = _tools(_admin())["browse_page"](url="https://cd.luban.fit/#/list")
    assert captured["wrapped"], "extract_elements 必须经 _run_browser_phase 包装"
    assert captured["called"]
    assert "页面访问成功" in result


def test_browse_error_detail_never_empty(monkeypatch):
    """异常 str() 为空时（如 NotImplementedError()），错误信息必须带类型名。"""
    import app.services.browser_agent as ba
    import app.services.page_extractor as pe

    monkeypatch.setattr(pi, "_origin_of", lambda url: "https://cd.luban.fit")
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: False)

    async def boom(url, storage_state=None, **kw):
        raise NotImplementedError()  # 空消息异常

    monkeypatch.setattr(pe, "extract_elements", boom)

    result = _tools(_admin())["browse_page"](url="https://cd.luban.fit/#/list")
    assert "页面访问失败" in result
    assert "NotImplementedError" in result, "空异常消息必须回退到类型名"

# ===== 定向点击（页签切换）=====

def test_browse_passes_clicks_to_extractor(monkeypatch):
    """clicks 参数必须透传给 extract_elements 的 post_open_clicks。"""
    import app.services.browser_agent as ba
    import app.services.page_extractor as pe

    captured = {}

    def fake_phase(coro):
        return coro

    monkeypatch.setattr(ba, "_run_browser_phase", fake_phase)
    monkeypatch.setattr(pi, "_origin_of", lambda url: "https://cd.luban.fit")
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: True)

    async def fake_extract(url, storage_state=None, timeout_ms=30000, post_open_clicks=None, **kw):
        captured["clicks"] = post_open_clicks
        return [
            {"tag": "div", "role": "tab", "text": "物资部位管理"},
            {"tag": "button", "text": "新增部位"},
        ]

    monkeypatch.setattr(pe, "extract_elements", fake_extract)

    result = _tools(_admin())["browse_page"](
        url="https://cd.luban.fit/#/list", clicks=["物资部位管理"]
    )
    assert captured["clicks"] == ["物资部位管理"]
    assert "物资部位管理" in result and "新增部位" in result


def test_browse_clicks_empty_defaults_to_none(monkeypatch):
    """不带 clicks 时 post_open_clicks 为 None（行为与旧版一致）。"""
    import app.services.browser_agent as ba
    import app.services.page_extractor as pe

    captured = {}

    def fake_phase(coro):
        return coro

    monkeypatch.setattr(ba, "_run_browser_phase", fake_phase)
    monkeypatch.setattr(pi, "_origin_of", lambda url: "https://cd.luban.fit")
    monkeypatch.setattr(ba, "has_valid_state_file", lambda p: True)

    async def fake_extract(url, storage_state=None, timeout_ms=30000, post_open_clicks=None, **kw):
        captured["clicks"] = post_open_clicks
        return [{"tag": "button", "text": "确定"}]

    monkeypatch.setattr(pe, "extract_elements", fake_extract)

    _tools(_admin())["browse_page"](url="https://cd.luban.fit/#/list")
    assert captured["clicks"] == []