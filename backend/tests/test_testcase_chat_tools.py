"""AI 用例对话写操作工具测试。

覆盖：权限拒绝、字段白名单、级别校验、human_edited 保持、删除保护
（已执行整批拒绝）、会话隔离、模式门控。全部使用 in-memory SQLite，
不触网、不调用真实 LLM。
"""
import pytest
from sqlmodel import Session as SQLSession, select

from app.auth import UserInfo
from db.models import Session as SessionModel, TestCase, StatusValue

import app.services.testcase_chat_tools as tools_module
from app.services.testcase_chat_tools import (
    build_testcase_tools,
    load_testcase_write_tools,
)


@pytest.fixture
def patched_engine(monkeypatch, db_engine):
    """工具模块内部通过 from db.db import engine 取连接；
    测试里把它替换为 in-memory 引擎。"""
    monkeypatch.setattr(tools_module, "engine", db_engine)
    return db_engine


def _seed(engine, cases):
    """建一个会话 + 若干用例，返回 (session_id, {name: id})。"""
    with SQLSession(engine) as s:
        sess = SessionModel(name="测试会话", user_id="u1")
        s.add(sess)
        s.commit()
        s.refresh(sess)
        ids = {}
        for spec in cases:
            tc = TestCase(session_id=sess.id, **spec)
            s.add(tc)
            s.commit()
            s.refresh(tc)
            ids[spec["case_name"]] = tc.id
        return sess.id, ids


def _admin():
    return UserInfo(user_id="u1", username="admin", roles=["admin"])


def _readonly():
    return UserInfo(user_id="u2", username="ro", roles=["user_no_perms"])


def _tools(session_id, user):
    return {t.name: t.func for t in build_testcase_tools(session_id, user)}


# ===== 权限 =====

def test_update_denied_without_permission(patched_engine):
    session_id, ids = _seed(patched_engine, [
        {"case_name": "登录成功", "steps": ["打开页面"]},
    ])
    fn = _tools(session_id, _readonly())["update_testcase"]
    result = fn(testcase_id=ids["登录成功"], patch={"case_name": "改名"})
    assert "权限不足" in result
    assert "testcase:update" in result
    with SQLSession(patched_engine) as s:
        tc = s.get(TestCase, ids["登录成功"])
        assert tc.case_name == "登录成功"


def test_delete_denied_without_permission(patched_engine):
    session_id, ids = _seed(patched_engine, [
        {"case_name": "待删用例"},
    ])
    fn = _tools(session_id, _readonly())["delete_testcases"]
    result = fn(testcase_ids=[ids["待删用例"]])
    assert "权限不足" in result
    with SQLSession(patched_engine) as s:
        assert s.get(TestCase, ids["待删用例"]) is not None


def test_anonymous_user_denied(patched_engine):
    session_id, ids = _seed(patched_engine, [
        {"case_name": "登录成功"},
    ])
    fn = _tools(session_id, None)["update_testcase"]
    result = fn(testcase_id=ids["登录成功"], patch={"case_name": "改名"})
    assert "权限不足" in result


# ===== 查询详情 =====

def test_get_detail_exact_and_fuzzy(patched_engine):
    session_id, _ = _seed(patched_engine, [
        {"case_name": "登录成功-正常账号", "steps": ["打开页面", "输入账号"], "expected_results": ["跳转首页"]},
        {"case_name": "登录失败-密码错误"},
    ])
    fn = _tools(session_id, _admin())["get_testcase_detail"]
    exact = fn(testcase_name="登录成功-正常账号")
    assert "[id=" in exact and "测试步骤" in exact and "跳转首页" in exact
    fuzzy = fn(testcase_name="登录")
    assert "相近用例" in fuzzy and "登录成功-正常账号" in fuzzy and "登录失败-密码错误" in fuzzy
    miss = fn(testcase_name="不存在的东西")
    assert "不存在" in miss


# ===== 更新 =====

def test_update_success(patched_engine):
    session_id, ids = _seed(patched_engine, [
        {"case_name": "登录成功", "case_level": 1, "steps": ["打开页面"]},
    ])
    fn = _tools(session_id, _admin())["update_testcase"]
    result = fn(testcase_id=ids["登录成功"], patch={
        "case_level": 2,
        "steps": ["打开页面", "输入正确账号", "点击登录"],
    })
    assert "已更新" in result
    with SQLSession(patched_engine) as s:
        tc = s.get(TestCase, ids["登录成功"])
        assert tc.case_level == 2
        assert len(tc.steps) == 3


def test_update_strips_fields_outside_whitelist(patched_engine):
    session_id, ids = _seed(patched_engine, [
        {"case_name": "登录成功", "status": StatusValue.NOT_RUN.value},
    ])
    fn = _tools(session_id, _admin())["update_testcase"]
    result = fn(testcase_id=ids["登录成功"], patch={
        "status": StatusValue.PASSED.value,
        "bug_id": 999,
        "human_edited": True,
        "reject_reason": "x",
        "case_name": "改名成功",
    })
    assert "已更新" in result
    with SQLSession(patched_engine) as s:
        tc = s.get(TestCase, ids["登录成功"])
        assert tc.case_name == "改名成功"
        assert tc.status == StatusValue.NOT_RUN.value
        assert tc.bug_id is None
        assert tc.human_edited is False
        assert tc.reject_reason is None


def test_update_keeps_existing_human_edited_flag(patched_engine):
    """AI 修改不应清掉已有的人工编辑标记，也不应新建它。"""
    session_id, ids = _seed(patched_engine, [
        {"case_name": "人工改过", "human_edited": True},
    ])
    fn = _tools(session_id, _admin())["update_testcase"]
    fn(testcase_id=ids["人工改过"], patch={"case_name": "AI 又改"})
    with SQLSession(patched_engine) as s:
        tc = s.get(TestCase, ids["人工改过"])
        assert tc.human_edited is True


def test_update_invalid_level_rejected(patched_engine):
    session_id, ids = _seed(patched_engine, [{"case_name": "登录成功"}])
    fn = _tools(session_id, _admin())["update_testcase"]
    result = fn(testcase_id=ids["登录成功"], patch={"case_level": 9})
    assert "1~4" in result
    result2 = fn(testcase_id=ids["登录成功"], patch={"case_level": "边界"})
    assert "1~4" in result2


def test_update_list_field_must_be_list(patched_engine):
    session_id, ids = _seed(patched_engine, [{"case_name": "登录成功"}])
    fn = _tools(session_id, _admin())["update_testcase"]
    result = fn(testcase_id=ids["登录成功"], patch={"steps": {"a": 1}})
    assert "字符串数组" in result


def test_update_empty_patch_rejected(patched_engine):
    session_id, ids = _seed(patched_engine, [{"case_name": "登录成功"}])
    fn = _tools(session_id, _admin())["update_testcase"]
    result = fn(testcase_id=ids["登录成功"], patch={"status": "PASSED"})
    assert "未提供有效修改字段" in result


def test_update_no_actual_change(patched_engine):
    session_id, ids = _seed(patched_engine, [{"case_name": "登录成功", "steps": ["打开页面"]}])
    fn = _tools(session_id, _admin())["update_testcase"]
    result = fn(testcase_id=ids["登录成功"], patch={"steps": ["打开页面"]})
    assert "没有任何变化" in result


def test_update_wrong_session_rejected(patched_engine):
    session_id, ids = _seed(patched_engine, [{"case_name": "本会话用例"}])
    fn = _tools(session_id + 100, _admin())["update_testcase"]
    result = fn(testcase_id=ids["本会话用例"], patch={"case_name": "改名"})
    assert "不属于当前会话" in result


# ===== 删除 =====

def test_delete_not_run_success(patched_engine):
    session_id, ids = _seed(patched_engine, [
        {"case_name": "用例A"},
        {"case_name": "用例B"},
    ])
    fn = _tools(session_id, _admin())["delete_testcases"]
    result = fn(testcase_ids=[ids["用例A"], ids["用例B"]])
    assert "已删除 2 条" in result
    with SQLSession(patched_engine) as s:
        remaining = s.exec(select(TestCase)).all()
        assert remaining == []


def test_delete_executed_rejects_entire_batch(patched_engine):
    session_id, ids = _seed(patched_engine, [
        {"case_name": "已通过", "status": StatusValue.PASSED.value},
        {"case_name": "未执行"},
    ])
    fn = _tools(session_id, _admin())["delete_testcases"]
    result = fn(testcase_ids=[ids["已通过"], ids["未执行"]])
    assert "整批未删除" in result and "已通过" in result
    with SQLSession(patched_engine) as s:
        assert s.get(TestCase, ids["未执行"]) is not None
        assert s.get(TestCase, ids["已通过"]) is not None


def test_delete_missing_id_rejects_batch(patched_engine):
    session_id, ids = _seed(patched_engine, [{"case_name": "存在用例"}])
    fn = _tools(session_id, _admin())["delete_testcases"]
    result = fn(testcase_ids=[ids["存在用例"], 99999])
    assert "整批未删除" in result and "99999" in result
    with SQLSession(patched_engine) as s:
        assert s.get(TestCase, ids["存在用例"]) is not None


def test_delete_empty_ids_rejected(patched_engine):
    session_id, _ = _seed(patched_engine, [{"case_name": "存在用例"}])
    fn = _tools(session_id, _admin())["delete_testcases"]
    result = fn(testcase_ids=[])
    assert "请提供" in result


# ===== 门控 =====

def test_load_gate_general_mode():
    assert load_testcase_write_tools("general", 5, _admin()) == []


def test_load_gate_missing_session():
    assert load_testcase_write_tools("testcase_optimize", None, _admin()) == []
    assert load_testcase_write_tools("testcase_optimize", 0, _admin()) == []


def test_load_gate_optimize_mode_builds_four(patched_engine):
    tools = load_testcase_write_tools("testcase_optimize", 3, _admin())
    assert sorted(t.name for t in tools) == [
        "create_testcase", "delete_testcases", "get_testcase_detail", "update_testcase",
    ]


# ===== 新增 =====

def test_create_success_with_module(patched_engine):
    session_id, _ = _seed(patched_engine, [])
    tools = {t.name: t.func for t in build_testcase_tools(session_id, _admin(), testcase_module_id=7)}
    result = tools["create_testcase"](
        case_name="边界-超长用户名",
        steps=["输入 256 字用户名", "点击注册"],
        expected_results=["提示长度超限"],
        module_id=7,
        preset_conditions=["已进入注册页"],
        case_level=2,
    )
    assert "已创建用例" in result and "[id=" in result
    with SQLSession(patched_engine) as s:
        tc = s.exec(select(TestCase).where(TestCase.case_name == "边界-超长用户名")).first()
        assert tc is not None
        assert tc.session_id == session_id
        assert tc.module_id == 7, "用例必须归入指定模块"
        assert tc.case_level == 2
        assert tc.status == StatusValue.NOT_RUN.value
        assert len(tc.steps) == 2 and len(tc.expected_results) == 1
        assert tc.user_id is None, "AI 创建不冒充真实用户"


def test_create_module_id_required(patched_engine):
    """回归：module_id 缺失曾让用例落到模块外，筛选后不可见。"""
    session_id, _ = _seed(patched_engine, [])
    tools = {t.name: t.func for t in build_testcase_tools(session_id, _admin(), testcase_module_id=7)}
    fn = tools["create_testcase"]
    r1 = fn(case_name="无模块", steps=["s"], expected_results=["e"])
    assert "module_id 必填" in r1
    r2 = fn(case_name="零值", steps=["s"], expected_results=["e"], module_id=0)
    assert "module_id 必填" in r2
    with SQLSession(patched_engine) as s:
        assert s.exec(select(TestCase)).all() == [], "缺模块不得落库"


def test_create_module_mismatch_rejected(patched_engine):
    """module_id 与上下文模块不一致时拒绝，防止用例落到当前筛选之外。"""
    session_id, _ = _seed(patched_engine, [])
    tools = {t.name: t.func for t in build_testcase_tools(session_id, _admin(), testcase_module_id=7)}
    result = tools["create_testcase"](
        case_name="跑错模块", steps=["s"], expected_results=["e"], module_id=99,
    )
    assert "不一致" in result and "id=7" in result
    with SQLSession(patched_engine) as s:
        assert s.exec(select(TestCase)).all() == []


def test_create_all_modules_view_any_positive_module_allowed(patched_engine):
    """全部模块视图（无绑定）时任意正整数模块均可。"""
    session_id, _ = _seed(patched_engine, [])
    tools = {t.name: t.func for t in build_testcase_tools(session_id, _admin(), testcase_module_id=None)}
    result = tools["create_testcase"](
        case_name="自由归属", steps=["s"], expected_results=["e"], module_id=3,
    )
    assert "已创建用例" in result
    with SQLSession(patched_engine) as s:
        tc = s.exec(select(TestCase).where(TestCase.case_name == "自由归属")).first()
        assert tc.module_id == 3


def test_create_denied_without_permission(patched_engine):
    session_id, _ = _seed(patched_engine, [])
    fn = _tools(session_id, _readonly())["create_testcase"]
    result = fn(case_name="x", steps=["s"], expected_results=["e"], module_id=1)
    assert "权限不足" in result and "testcase:create" in result
    with SQLSession(patched_engine) as s:
        assert s.exec(select(TestCase)).all() == []


def test_create_validates_inputs(patched_engine):
    session_id, _ = _seed(patched_engine, [])
    fn = _tools(session_id, _admin())["create_testcase"]
    assert "不能为空" in fn(case_name="  ", steps=["s"], expected_results=["e"], module_id=1)
    assert "1~4" in fn(case_name="x", steps=["s"], expected_results=["e"], module_id=1, case_level=9)
    assert "非空字符串数组" in fn(case_name="x", steps=[], expected_results=["e"], module_id=1)
    assert "非空字符串数组" in fn(case_name="x", steps=["s"], expected_results=[], module_id=1)
    assert "非空字符串数组" in fn(case_name="x", steps={"bad": 1}, expected_results=["e"], module_id=1)
    with SQLSession(patched_engine) as s:
        assert s.exec(select(TestCase)).all() == [], "校验失败不得落库"


def test_create_defaults_and_normalization(patched_engine):
    session_id, _ = _seed(patched_engine, [])
    fn = _tools(session_id, _admin())["create_testcase"]
    result = fn(case_name="默认级别", steps=["s1"], expected_results=["e1"], module_id=2)
    assert "已创建用例" in result
    with SQLSession(patched_engine) as s:
        tc = s.exec(select(TestCase).where(TestCase.case_name == "默认级别")).first()
        assert tc.case_level == 1, "缺省级别为功能(1)"
        assert tc.preset_conditions == [], "缺省前置为空数组"
        assert tc.module_id == 2


def test_update_module_move_out_rejected(patched_engine):
    """update 也不能把用例移出上下文模块。"""
    session_id, ids = _seed(patched_engine, [
        {"case_name": "本模块用例", "module_id": 7},
    ])
    tools = {t.name: t.func for t in build_testcase_tools(session_id, _admin(), testcase_module_id=7)}
    result = tools["update_testcase"](
        testcase_id=ids["本模块用例"],
        patch={"module_id": 99},
    )
    assert "不一致" in result
    with SQLSession(patched_engine) as s:
        assert s.get(TestCase, ids["本模块用例"]).module_id == 7

