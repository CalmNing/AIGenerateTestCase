"""ui_execution compile/bindings/run execution_mode 路由测试(纯函数级,不走 HTTP)。"""
import asyncio
import pytest
from fastapi import HTTPException
from sqlmodel import select

from db.models import PageObject, TestCase, UIExecutionRecord
from app.services.ui_step_binder import normalize_incoming_binding
from app.services.ui_binding_schemas import UIBoundAssert, UIBoundStep, LEVEL_L1, LEVEL_L3


@pytest.fixture()
def fake_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester")


@pytest.fixture()
def fake_tc(db_session):
    tc = TestCase(
        case_name="登录",
        steps=["打开登录页", "输入用户名 admin", "点击登录"],
        expected_results=["跳转首页", "显示欢迎信息"],
    )
    db_session.add(tc)
    db_session.commit()
    db_session.refresh(tc)
    return tc


@pytest.fixture()
def fake_po(db_session):
    po = PageObject(
        name="LoginPage",
        site_origin="http://a.com",
        page_path="#/login",
        url="http://a.com#/login",
        elements=[
            {"key": "username", "name": "用户名", "type": "input",
             "locators": [{"strategy": "placeholder", "value": "请输入用户名", "stability": 0.9}]},
            {"key": "login_btn", "name": "登录按钮", "type": "button",
             "locators": [{"strategy": "role", "value": 'button[name="登录"]', "stability": 0.95}]},
        ],
    )
    db_session.add(po)
    db_session.commit()
    db_session.refresh(po)
    return po


class TestResolveExecutionMode:
    def test_auto_no_binding_agent(self):
        from app.routes.ui_execution import _resolve_execution_mode
        assert _resolve_execution_mode("auto", None) == "agent"

    def test_auto_with_l1_binding_hybrid(self):
        from app.routes.ui_execution import _resolve_execution_mode
        binding = {"steps": [{"level": "L1"}], "asserts": []}
        assert _resolve_execution_mode("auto", binding) == "hybrid"

    def test_auto_l3_only_agent(self):
        from app.routes.ui_execution import _resolve_execution_mode
        binding = {"steps": [{"level": "L3"}], "asserts": [{"level": "L3"}]}
        assert _resolve_execution_mode("auto", binding) == "agent"

    def test_explicit_agent(self):
        from app.routes.ui_execution import _resolve_execution_mode
        binding = {"steps": [{"level": "L1"}]}
        assert _resolve_execution_mode("agent", binding) == "agent"

    def test_explicit_hybrid_with_binding(self):
        from app.routes.ui_execution import _resolve_execution_mode
        binding = {"steps": [{"level": "L1"}]}
        assert _resolve_execution_mode("hybrid", binding) == "hybrid"

    def test_explicit_hybrid_no_binding_fallback_agent(self):
        from app.routes.ui_execution import _resolve_execution_mode
        assert _resolve_execution_mode("hybrid", None) == "agent"


class TestHasBoundLevels:
    def test_none(self):
        from app.routes.ui_execution import _has_bound_levels
        assert _has_bound_levels(None) is False

    def test_l1_step(self):
        from app.routes.ui_execution import _has_bound_levels
        assert _has_bound_levels({"steps": [{"level": "L1"}]}) is True

    def test_l2_assert(self):
        from app.routes.ui_execution import _has_bound_levels
        assert _has_bound_levels({"asserts": [{"level": "L2"}]}) is True

    def test_all_l3(self):
        from app.routes.ui_execution import _has_bound_levels
        assert _has_bound_levels({"steps": [{"level": "L3"}], "asserts": [{"level": "L3"}]}) is False


class TestValidateAndAttachBinding:
    def test_valid_binding_attaches(self, db_session, fake_user, fake_tc, fake_po):
        from app.routes.ui_execution import _validate_and_attach_binding
        raw = {
            "page_object_id": fake_po.id,
            "steps": [{"step_no": 1, "action": "click", "element_key": "login_btn", "confidence": 0.9}],
            "asserts": [{"index": 1, "action": "assert_text", "element_key": "username", "value": "admin"}],
        }
        binding = _validate_and_attach_binding(db_session, fake_tc, raw)
        assert binding is not None
        assert binding["page_object_id"] == fake_po.id
        assert fake_tc.ui_binding is not None
        assert fake_tc.ui_binding["page_object_id"] == fake_po.id

    def test_missing_po_400(self, db_session, fake_user, fake_tc):
        from app.routes.ui_execution import _validate_and_attach_binding
        raw = {"page_object_id": 99999, "steps": []}
        with pytest.raises(HTTPException) as exc:
            _validate_and_attach_binding(db_session, fake_tc, raw)
        assert exc.value.status_code == 400

    def test_invalid_element_key_400(self, db_session, fake_user, fake_tc, fake_po):
        from app.routes.ui_execution import _validate_and_attach_binding
        raw = {
            "page_object_id": fake_po.id,
            "steps": [{"step_no": 1, "action": "click", "element_key": "ghost", "confidence": 0.9}],
        }
        with pytest.raises(HTTPException) as exc:
            _validate_and_attach_binding(db_session, fake_tc, raw)
        assert exc.value.status_code == 400
        assert "ghost" in exc.value.detail

    def test_empty_raw_returns_none(self, db_session, fake_user, fake_tc):
        from app.routes.ui_execution import _validate_and_attach_binding
        assert _validate_and_attach_binding(db_session, fake_tc, {}) is None


class TestCompileEndpoint:
    def test_compile_variable_substitution(self, db_session, fake_user, fake_tc, monkeypatch):
        """站点地址 {{var}} 按环境解析;调用 compile_ui_binding 并 mock 其返回。"""
        import app.routes.ui_execution as route
        from db.models import GlobalParameter
        from app.services.ui_binding_schemas import UICompileResult

        db_session.add(GlobalParameter(
            name="默认环境", is_default=True,
            parameters=[{"key": "web_url", "value": "http://env-a.com"}],
        ))
        db_session.commit()

        captured = {}

        async def fake_compile(db, *, site_origin, page_path, testcase_steps, expected_results, user_id=None, model=None):
            captured.update(site_origin=site_origin, page_path=page_path,
                            steps=testcase_steps, expected=expected_results)
            return UICompileResult(compile_status="ok", site_origin=site_origin, page_path=page_path)

        monkeypatch.setattr(route, "compile_ui_binding", fake_compile)

        from app.routes.ui_execution import CompileRequest
        result = asyncio.run(route.compile_binding(
            CompileRequest(testcase_id=fake_tc.id, site_origin="{{web_url}}", page_path="#/login"),
            db_session, fake_user,
        ))
        assert captured["site_origin"] == "http://env-a.com"
        assert captured["page_path"] == "#/login"
        assert result.data is not None and result.data["compile_status"] == "ok"

    def test_compile_undefined_var_400(self, db_session, fake_user, fake_tc):
        import app.routes.ui_execution as route
        from app.routes.ui_execution import CompileRequest
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.compile_binding(
                CompileRequest(testcase_id=fake_tc.id, site_origin="{{nope}}", page_path=""),
                db_session, fake_user,
            ))
        assert exc.value.status_code == 400

    def test_compile_rejects_api_case(self, db_session, fake_user, fake_tc):
        import app.routes.ui_execution as route
        from app.routes.ui_execution import CompileRequest
        fake_tc.steps = [{"type": "api_call", "endpoint_id": 1}]
        db_session.add(fake_tc)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.compile_binding(
                CompileRequest(testcase_id=fake_tc.id, site_origin="http://a.com", page_path=""),
                db_session, fake_user,
            ))
        assert exc.value.status_code == 400


class TestSaveBindingEndpoint:
    def test_save_valid(self, db_session, fake_user, fake_tc, fake_po):
        import app.routes.ui_execution as route
        from app.routes.ui_execution import SaveBindingRequest
        body = SaveBindingRequest(
            testcase_id=fake_tc.id,
            ui_binding={
                "page_object_id": fake_po.id,
                "steps": [{"step_no": 1, "action": "click", "element_key": "login_btn", "confidence": 0.9}],
                "asserts": [],
            },
        )
        result = route.save_binding(body, db_session, fake_user)
        assert result.data is not None
        assert result.data["saved"] is True
        assert result.data["page_object_id"] == fake_po.id
        assert fake_tc.ui_binding is not None

    def test_save_invalid_key_400(self, db_session, fake_user, fake_tc, fake_po):
        import app.routes.ui_execution as route
        from app.routes.ui_execution import SaveBindingRequest
        body = SaveBindingRequest(
            testcase_id=fake_tc.id,
            ui_binding={"page_object_id": fake_po.id,
                        "steps": [{"step_no": 1, "action": "click", "element_key": "ghost"}]},
        )
        with pytest.raises(HTTPException) as exc:
            route.save_binding(body, db_session, fake_user)
        assert exc.value.status_code == 400


class TestRunExecutionMode:
    def _run(self, db_session, fake_user, fake_tc, monkeypatch, *, execution_mode="auto", ui_binding=None):
        import app.routes.ui_execution as route
        from app.routes.ui_execution import RunRequest

        captured = {}

        def fake_launch(db, record_id, agent_factory=None, login_overrides=None,
                        max_steps=None, environment_id=None, final_judgement=False, **kwargs):
            captured["record_id"] = record_id
            captured["final_judgement"] = final_judgement
            return "task-1"

        monkeypatch.setattr(route, "launch_ui_execution", fake_launch)
        body = RunRequest(
            testcase_id=fake_tc.id,
            site_origin="http://a.com",
            page_path="#/login",
            execution_mode=execution_mode,
            ui_binding=ui_binding,
        )
        result = asyncio.run(route.run_ui_execution(body, db_session, fake_user))
        return result, captured

    def test_auto_with_l1_binding_sets_hybrid(self, db_session, fake_user, fake_tc, fake_po, monkeypatch):
        binding = {
            "page_object_id": fake_po.id,
            "steps": [{"step_no": 1, "action": "navigate", "confidence": 1.0, "level": LEVEL_L1}],
            "asserts": [{"index": 1, "action": "assert_text", "element_key": "username", "level": LEVEL_L1}],
        }
        result, _ = self._run(db_session, fake_user, fake_tc, monkeypatch, ui_binding=binding)
        assert result.data is not None
        assert result.data["execution_mode"] == "hybrid"
        rec = db_session.exec(select(UIExecutionRecord)).first()
        assert rec.execution_mode == "hybrid"
        assert rec.page_object_id == fake_po.id
        assert rec.binding_snapshot  # 单元素列表

    def test_auto_no_binding_agent(self, db_session, fake_user, fake_tc, monkeypatch):
        result, _ = self._run(db_session, fake_user, fake_tc, monkeypatch)
        assert result.data is not None
        assert result.data["execution_mode"] == "agent"
        rec = db_session.exec(select(UIExecutionRecord)).first()
        assert rec.execution_mode == "agent"
        assert rec.binding_snapshot == []

    def test_saved_binding_reused(self, db_session, fake_user, fake_tc, fake_po, monkeypatch):
        """请求体不带 ui_binding 时,用例上已保存的绑定优先生效。"""
        fake_tc.ui_binding = {
            "page_object_id": fake_po.id,
            "steps": [{"step_no": 1, "action": "click", "element_key": "login_btn", "confidence": 0.9, "level": LEVEL_L1}],
            "asserts": [],
        }
        db_session.add(fake_tc)
        db_session.commit()

        result, _ = self._run(db_session, fake_user, fake_tc, monkeypatch)
        assert result.data is not None
        assert result.data["execution_mode"] == "hybrid"
        rec = db_session.exec(select(UIExecutionRecord)).first()
        assert rec.page_object_id == fake_po.id

    def test_explicit_agent_overrides_binding(self, db_session, fake_user, fake_tc, fake_po, monkeypatch):
        binding = {"page_object_id": fake_po.id,
                   "steps": [{"step_no": 1, "action": "click", "element_key": "login_btn", "level": LEVEL_L1}]}
        result, _ = self._run(db_session, fake_user, fake_tc, monkeypatch,
                              execution_mode="agent", ui_binding=binding)
        assert result.data is not None
        assert result.data["execution_mode"] == "agent"
        rec = db_session.exec(select(UIExecutionRecord)).first()
        assert rec.execution_mode == "agent"

    def test_final_judgement_passthrough(self, db_session, fake_user, fake_tc, monkeypatch):
        import app.routes.ui_execution as route
        from app.routes.ui_execution import RunRequest

        captured = {}

        def fake_launch(db, record_id, **kwargs):
            captured.update(kwargs)
            return "task-1"

        monkeypatch.setattr(route, "launch_ui_execution", fake_launch)
        body = RunRequest(
            testcase_id=fake_tc.id, site_origin="http://a.com", page_path="#/login",
            final_judgement=True,
        )
        asyncio.run(route.run_ui_execution(body, db_session, fake_user))
        assert captured.get("final_judgement") is True

    def test_body_binding_overrides_saved(self, db_session, fake_user, fake_tc, fake_po, monkeypatch):
        """请求体 ui_binding 覆盖用例已保存绑定(前端修正后直接 run)。"""
        fake_tc.ui_binding = {"page_object_id": fake_po.id, "steps": [], "asserts": [],
                              "compile_status": "ok"}
        db_session.add(fake_tc)
        db_session.commit()

        # 请求体带不同绑定:覆盖
        binding = {"page_object_id": None, "steps": [{"step_no": 1, "action": "agent", "level": LEVEL_L3}],
                   "asserts": [], "compile_status": "ok"}
        result, _ = self._run(db_session, fake_user, fake_tc, monkeypatch, ui_binding=binding)
        rec = db_session.exec(select(UIExecutionRecord)).first()
        # 全 L3 → auto 解析为 agent
        assert result.data is not None
        assert result.data["execution_mode"] == "agent"
        assert rec.page_object_id is None
