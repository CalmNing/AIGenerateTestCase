"""Characterization test for generate_testcases orchestration.

Drives the full function with an in-memory DB and a monkeypatched model call
(no network/LLM). Plain-text requirement skips all MCP/Lanhu/Feishu branches.
This is the safety net for extracting _prepare_requirement / _invoke_model /
_persist_testcases.

Uses asyncio.run (no pytest-asyncio dependency).
"""
import asyncio

import pytest

import utils.model_utils as mu
from utils.models import FunctionalTestCase, FunctionalResponseFormat
from db.models import Session as DbSession


@pytest.fixture()
def _no_network(monkeypatch):
    """Stub the model call and doc-prefetch so the orchestration runs offline."""
    async def fake_structured_output(model, schema, messages, **kwargs):
        tc = FunctionalTestCase(
            case_name="登录成功",
            case_level=1,
            steps=["打开登录页", "输入账号密码", "点击登录"],
            preset_conditions=["用户已注册"],
            expected_results=["跳转到首页"],
        )
        return FunctionalResponseFormat(response=[tc])

    async def fake_lanhu_size(requirement):
        return None

    async def fake_feishu(requirement, mcp_configs, vision_config=None):
        # generate_testcases 必须把当前模型配置下传用于图片解析
        assert isinstance(vision_config, dict)
        assert vision_config.get("api_key") == "sk-test"
        assert vision_config.get("api_model")
        return None

    monkeypatch.setattr(mu, "_structured_output_robust", fake_structured_output)
    monkeypatch.setattr(mu, "_check_lanhu_document_size", fake_lanhu_size)
    monkeypatch.setattr(mu, "_fetch_feishu_document_content", fake_feishu)


def test_generate_testcases_functional_plain_text(_no_network, db_session):
    sess = DbSession(name="s1")
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)

    db_testcases, effective_requirement = asyncio.run(mu.generate_testcases(
        session_id=sess.id,
        module_id=None,
        requirement="用户应能使用账号密码登录",
        model_type="api",
        api_key="sk-test",
        is_api_test=False,
        db_session=db_session,
        user_id="u1",
    ))

    assert len(db_testcases) == 1
    tc = db_testcases[0]
    assert tc.case_name == "登录成功"
    assert tc.case_level == 1
    assert tc.session_id == sess.id
    assert tc.steps == ["打开登录页", "输入账号密码", "点击登录"]
    assert tc.expected_results == ["跳转到首页"]
    assert "账号密码登录" in effective_requirement


def test_generate_testcases_empty_requirement_raises(_no_network):
    with pytest.raises(ValueError):
        asyncio.run(mu.generate_testcases(
            session_id=1,
            module_id=None,
            requirement=None,
            model_type="api",
            api_key="sk-test",
        ))
