"""Red/Green 测试：API 测试模式下阶段1/2 必须显式注入接口 Schema。

验证 generate-v2 的修复：
- RequirementSplitter.analyze 在 is_api_test=True 且提供 api_context 时，
  构造的 prompt 必须把接口 Schema 作为主体、用户文本作为辅助。
- TestPointGenerator.generate 同理。

通过 mock utils.model_utils._structured_output_robust 捕获 messages 来断言。

注：生成器 __init__ 会构造 ChatOpenAI（依赖 langchain）。
本文件应在 CI 健康环境运行；当前开发沙箱的系统 Python
导入 langchain 会 segfault（exit 139），无法本地执行，
主验证改用运行中的 --reload 后端 + curl 端到端校验。
"""
import asyncio
from unittest import mock

import pytest

import utils.model_utils as mu
from app.pipeline import requirement_splitter as rs_mod
from app.pipeline import test_point_generator as tpg_mod
from app.pipeline.requirement_splitter import RequirementSplitter
from app.pipeline.test_point_generator import TestPointGenerator


API_CONTEXT = (
    "===== [1] 登录接口 =====\n"
    "接口描述: 用户登录\n"
    "请求方法: POST\n"
    "请求路径: /api/login\n"
    '请求参数: [{"key": "username", "in": "body"}]\n'
    "请求骨架:\n方法: POST\n路径: /api/login\n必填字段: username, password"
)
USER_AUX = "用户说要测登录，顺便覆盖下验证码"


def _make_fake_capture(captured):
    async def _fake(model, schema, messages, **kwargs):
        captured["messages"] = messages
        # 返回一个非 None 的对象（既非 *Result 也非 dict），
        # 让生成器安全地得到空列表、不抛异常。
        return type("R", (), {})()
    return _fake


def _join(messages):
    return "\n".join(m["content"] for m in messages)


@pytest.fixture
def fake_capture(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        mu, "_structured_output_robust", _make_fake_capture(captured)
    )
    return captured


def test_analyze_injects_schema_as_primary(fake_capture):
    s = RequirementSplitter(
        api_key="x", api_base_url="https://api.deepseek.com",
        api_model="deepseek-v4-flash",
    )
    asyncio.run(s.analyze(USER_AUX, is_api_test=True, api_context=API_CONTEXT))
    joined = _join(fake_capture["messages"])
    # 接口 Schema 主体必须出现
    assert "/api/login" in joined
    assert "username" in joined
    # 用户文本仍作为辅助出现
    assert "验证码" in joined
    # 系统提示应为 API 变体
    assert "接口测试需求分析专家" in fake_capture["messages"][0]["content"]
    # 缓存关键：接口 Schema 必须落在 system（前缀）消息，而非 user，
    # 这样才能被 DeepSeek 前缀缓存命中、跨阶段/跨点复用。
    assert "/api/login" in fake_capture["messages"][0]["content"]


def test_analyze_functional_mode_unchanged(fake_capture):
    s = RequirementSplitter(
        api_key="x", api_base_url="https://api.deepseek.com",
        api_model="deepseek-v4-flash",
    )
    asyncio.run(s.analyze(USER_AUX))
    joined = _join(fake_capture["messages"])
    assert USER_AUX in joined
    assert "/api/login" not in joined
    assert "专业的需求分析专家" in fake_capture["messages"][0]["content"]


def test_generate_injects_schema_as_primary(fake_capture):
    g = TestPointGenerator(
        api_key="x", api_base_url="https://api.deepseek.com",
        api_model="deepseek-v4-flash",
    )
    asyncio.run(g.generate("登录功能", is_api_test=True, api_context=API_CONTEXT))
    joined = _join(fake_capture["messages"])
    assert "/api/login" in joined
    assert "登录功能" in joined
    assert "接口测试点生成专家" in fake_capture["messages"][0]["content"]
    # 接口 Schema 必须落在 system（前缀）消息，便于缓存命中
    assert "/api/login" in fake_capture["messages"][0]["content"]
