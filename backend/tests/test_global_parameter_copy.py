"""global-parameters 复制接口测试：参数完整复制、名称唯一递增、不继承默认标记、源不存在返回 404。"""

import pytest
from fastapi import HTTPException
from sqlmodel import select

import app.routes.global_parameter as route
from db.models import GlobalParameter


@pytest.fixture()
def fake_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester")


def _seed_env(session, name="开发环境", is_default=False, parameters=None):
    env = GlobalParameter(
        name=name,
        is_default=is_default,
        parameters=parameters if parameters is not None else [],
    )
    session.add(env)
    session.commit()
    session.refresh(env)
    return env


def test_copy_creates_new_env_with_same_parameters(db_session, fake_user):
    """副本参数与源环境逐条一致，且是新记录。"""
    source = _seed_env(
        db_session,
        name="开发环境",
        parameters=[
            {"key": "baseUrl", "value": "http://a.com", "description": "基础地址"},
            {"key": "token", "value": "abc123"},
        ],
    )

    result = route.copy_global_parameter(source.id, db_session, fake_user)

    assert result.code == 200
    copied = result.data
    assert copied.id != source.id
    assert copied.name == "开发环境 (复制)"
    assert copied.parameters == source.parameters
    assert copied.parameters[0]["description"] == "基础地址"
    assert copied.user_id == "u1"

    # 源环境不受影响
    db_session.refresh(source)
    assert source.name == "开发环境"
    assert len(source.parameters) == 2


def test_copy_name_increments_on_conflict(db_session, fake_user):
    """重名时自动递增为 (复制 2)；复制副本沿用同一基名序列。"""
    source = _seed_env(db_session, name="开发环境")

    first = route.copy_global_parameter(source.id, db_session, fake_user)
    assert first.data.name == "开发环境 (复制)"

    second = route.copy_global_parameter(source.id, db_session, fake_user)
    assert second.data.name == "开发环境 (复制 2)"

    third = route.copy_global_parameter(first.data.id, db_session, fake_user)
    assert third.data.name == "开发环境 (复制 3)"


def test_copy_does_not_inherit_default_flag(db_session, fake_user):
    """副本不继承 is_default，源环境的默认标记保持不变。"""
    source = _seed_env(db_session, name="默认环境", is_default=True)

    result = route.copy_global_parameter(source.id, db_session, fake_user)

    assert result.data.is_default is False
    db_session.refresh(source)
    assert source.is_default is True

    defaults = db_session.exec(
        select(GlobalParameter).where(GlobalParameter.is_default == True)
    ).all()
    assert [env.name for env in defaults] == ["默认环境"]


def test_copy_missing_source_raises_404(db_session, fake_user):
    """源环境不存在时返回 404。"""
    with pytest.raises(HTTPException) as exc:
        route.copy_global_parameter(999999, db_session, fake_user)

    assert exc.value.status_code == 404
    assert "不存在" in exc.value.detail
