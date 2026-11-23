"""定时任务分组测试：CRUD、树形结构、变量优先级。"""

import pytest
from fastapi import HTTPException

import app.routes.task_group as route
from db.models import TaskGroup, ScheduledTask, GlobalParameter


@pytest.fixture()
def fake_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester", roles=["admin"])


def _mk_group(session, name, parent_id=None, variables=None, environment_id=None):
    g = TaskGroup(
        name=name,
        parent_id=parent_id,
        variables=variables or [],
        environment_id=environment_id,
    )
    session.add(g)
    session.commit()
    session.refresh(g)
    return g


class TestCreate:
    def test_create_root_group(self, db_session, fake_user):
        res = route.create_task_group(db_session, fake_user, TaskGroup(name="运维任务"))
        assert res.code == 200
        assert res.data.name == "运维任务"
        assert res.data.parent_id is None
        assert res.data.user_id == "u1"

    def test_create_child_group(self, db_session, fake_user):
        parent = _mk_group(db_session, "父")
        res = route.create_task_group(
            db_session, fake_user, TaskGroup(name="子", parent_id=parent.id)
        )
        assert res.data.parent_id == parent.id

    def test_empty_name_rejected(self, db_session, fake_user):
        with pytest.raises(HTTPException) as exc:
            route.create_task_group(db_session, fake_user, TaskGroup(name="  "))
        assert exc.value.status_code == 400

    def test_missing_parent_rejected(self, db_session, fake_user):
        with pytest.raises(HTTPException) as exc:
            route.create_task_group(
                db_session, fake_user, TaskGroup(name="子", parent_id=999999)
            )
        assert exc.value.status_code == 400


class TestTree:
    def test_tree_nesting(self, db_session, fake_user):
        root = _mk_group(db_session, "根")
        child = _mk_group(db_session, "子", parent_id=root.id)
        grand = _mk_group(db_session, "孙", parent_id=child.id)

        res = route.get_task_groups(db_session, fake_user)
        roots = res.data
        assert len(roots) == 1
        assert roots[0]["id"] == root.id
        assert roots[0]["children"][0]["id"] == child.id
        assert roots[0]["children"][0]["children"][0]["id"] == grand.id

    def test_task_count_attached(self, db_session, fake_user):
        group = _mk_group(db_session, "组")
        db_session.add(ScheduledTask(name="t1", group_id=group.id))
        db_session.add(ScheduledTask(name="t2", group_id=group.id))
        db_session.commit()

        res = route.get_task_groups(db_session, fake_user)
        assert res.data[0]["task_count"] == 2

    def test_orphan_parent_becomes_root(self, db_session, fake_user):
        _mk_group(db_session, "孤儿", parent_id=999999)
        res = route.get_task_groups(db_session, fake_user)
        assert len(res.data) == 1
        assert res.data[0]["name"] == "孤儿"


class TestUpdate:
    def test_update_name_and_variables(self, db_session, fake_user):
        group = _mk_group(db_session, "旧名")
        res = route.update_task_group(
            db_session,
            fake_user,
            group.id,
            TaskGroup(name="新名", variables=[{"key": "k", "value": "v"}]),
        )
        assert res.data.name == "新名"
        assert res.data.variables == [{"key": "k", "value": "v"}]

    def test_update_missing_group_404(self, db_session, fake_user):
        with pytest.raises(HTTPException) as exc:
            route.update_task_group(db_session, fake_user, 999999, TaskGroup(name="x"))
        assert exc.value.status_code == 404

    def test_cannot_set_self_as_parent(self, db_session, fake_user):
        group = _mk_group(db_session, "组")
        with pytest.raises(HTTPException) as exc:
            route.update_task_group(
                db_session, fake_user, group.id, TaskGroup(parent_id=group.id)
            )
        assert exc.value.status_code == 400

    def test_cannot_set_child_as_parent(self, db_session, fake_user):
        parent = _mk_group(db_session, "父")
        child = _mk_group(db_session, "子", parent_id=parent.id)
        with pytest.raises(HTTPException) as exc:
            route.update_task_group(
                db_session, fake_user, parent.id, TaskGroup(parent_id=child.id)
            )
        assert exc.value.status_code == 400


class TestDelete:
    def test_delete_removes_descendants(self, db_session, fake_user):
        root = _mk_group(db_session, "根")
        child = _mk_group(db_session, "子", parent_id=root.id)
        route.delete_task_group(db_session, fake_user, root.id)

        res = route.get_task_groups(db_session, fake_user)
        assert res.data == []
        assert db_session.get(TaskGroup, child.id) is None

    def test_delete_moves_tasks_to_ungrouped(self, db_session, fake_user):
        group = _mk_group(db_session, "组")
        task = ScheduledTask(name="t1", group_id=group.id)
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)

        route.delete_task_group(db_session, fake_user, group.id)

        db_session.refresh(task)
        assert task.group_id is None
        assert db_session.get(ScheduledTask, task.id) is not None

    def test_delete_missing_group_404(self, db_session, fake_user):
        with pytest.raises(HTTPException) as exc:
            route.delete_task_group(db_session, fake_user, 999999)
        assert exc.value.status_code == 404


class TestGroupVariablePrecedence:
    """分组变量 < 环境变量 < 任务参数。"""

    def _param_map(self, db_session, task, group):
        from app.scheduler import build_task_param_map

        return build_task_param_map(db_session, task)

    def test_group_variable_used_when_nothing_else(self, db_session):
        group = _mk_group(
            db_session, "组", variables=[{"key": "shared", "value": "from-group"}]
        )
        task = ScheduledTask(name="t", group_id=group.id)
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)

        assert self._param_map(db_session, task, group)["shared"] == "from-group"

    def test_env_overrides_group(self, db_session):
        env = GlobalParameter(
            name="环境",
            is_default=True,
            parameters=[{"key": "shared", "value": "from-env"}],
        )
        db_session.add(env)
        db_session.commit()

        group = _mk_group(
            db_session, "组", variables=[{"key": "shared", "value": "from-group"}]
        )
        task = ScheduledTask(name="t", group_id=group.id)
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)

        assert self._param_map(db_session, task, group)["shared"] == "from-env"

    def test_task_param_overrides_everything(self, db_session):
        env = GlobalParameter(
            name="环境",
            is_default=True,
            parameters=[{"key": "shared", "value": "from-env"}],
        )
        db_session.add(env)
        db_session.commit()

        group = _mk_group(
            db_session, "组", variables=[{"key": "shared", "value": "from-group"}]
        )
        task = ScheduledTask(
            name="t",
            group_id=group.id,
            parameters=[{"key": "shared", "value": "from-task", "type": "text"}],
        )
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)

        assert self._param_map(db_session, task, group)["shared"] == "from-task"

    def test_group_env_used_when_task_has_no_env(self, db_session):
        env = GlobalParameter(
            name="组环境",
            is_default=False,
            parameters=[{"key": "fromGroupEnv", "value": "yes"}],
        )
        db_session.add(env)
        db_session.commit()
        db_session.refresh(env)

        group = _mk_group(db_session, "组", environment_id=env.id)
        task = ScheduledTask(name="t", group_id=group.id)
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)

        assert self._param_map(db_session, task, group)["fromGroupEnv"] == "yes"
