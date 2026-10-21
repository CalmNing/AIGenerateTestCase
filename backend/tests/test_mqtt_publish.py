"""Tests for MQTT 发布路由：Broker 地址解析、变量替换、发布日志与连通性测试。

纯单元测试，使用内存 SQLite（conftest.db_session），网络层通过 monkeypatch 替换，
不连接任何真实 Broker / 网络。
"""
import asyncio

import pytest
from sqlmodel import select

import app.routes.mqtt as mqtt_mod
from app.auth import UserInfo
from app.routes.mqtt import (
    MqttPublishRequest,
    MqttTestConnectionRequest,
    _normalize_parameters,
    _parse_broker_url,
    publish_mqtt,
    test_connection as mqtt_test_connection,
)
from db.models import GlobalParameter, PushLog


def _user(roles=None, user_id="u1") -> UserInfo:
    """构造测试用户（roles 为空时无任何权限）"""
    return UserInfo(user_id=user_id, username="tester", roles=roles or [])


def _publish(db_session, user, **kwargs):
    """在事件循环里调用发布接口（接口本身是 async 的）"""
    request = MqttPublishRequest(**kwargs)
    return asyncio.run(publish_mqtt(request=request, user=user, db=db_session))


def _test_conn(user, **kwargs):
    """在事件循环里调用连通性测试接口"""
    request = MqttTestConnectionRequest(**kwargs)
    return asyncio.run(mqtt_test_connection(request=request, user=user))


class TestParseBrokerUrl:
    def test_mqtt_scheme_with_port(self):
        cfg = _parse_broker_url("mqtt://broker.example.com:1884")
        assert cfg == {
            "host": "broker.example.com", "port": 1884,
            "transport": "tcp", "tls": False, "path": "",
        }

    def test_mqtt_scheme_default_port(self):
        assert _parse_broker_url("mqtt://broker.example.com")["port"] == 1883

    def test_tcp_scheme(self):
        cfg = _parse_broker_url("tcp://10.0.0.5:1883")
        assert cfg["host"] == "10.0.0.5"
        assert cfg["port"] == 1883
        assert cfg["transport"] == "tcp"
        assert cfg["tls"] is False

    def test_mqtts_enables_tls_with_default_port(self):
        cfg = _parse_broker_url("mqtts://secure.example.com")
        assert cfg["port"] == 8883
        assert cfg["tls"] is True
        assert cfg["transport"] == "tcp"

    def test_ssl_scheme_enables_tls(self):
        cfg = _parse_broker_url("ssl://secure.example.com:9883")
        assert cfg["port"] == 9883
        assert cfg["tls"] is True

    def test_ws_default_port(self):
        cfg = _parse_broker_url("ws://broker.example.com")
        assert cfg["port"] == 8083
        assert cfg["transport"] == "websockets"
        assert cfg["tls"] is False

    def test_wss_default_port_and_tls(self):
        cfg = _parse_broker_url("wss://broker.example.com")
        assert cfg["port"] == 8084
        assert cfg["transport"] == "websockets"
        assert cfg["tls"] is True

    def test_ws_path_preserved(self):
        cfg = _parse_broker_url("ws://broker.example.com:8083/mqtt")
        assert cfg["path"] == "/mqtt"
        assert cfg["port"] == 8083

    def test_bare_host_uses_mqtt_defaults(self):
        cfg = _parse_broker_url("broker.example.com")
        assert cfg == {
            "host": "broker.example.com", "port": 1883,
            "transport": "tcp", "tls": False, "path": "",
        }

    def test_bare_host_with_port(self):
        cfg = _parse_broker_url("192.168.1.10:1884")
        assert cfg["host"] == "192.168.1.10"
        assert cfg["port"] == 1884

    def test_empty_broker_raises(self):
        with pytest.raises(ValueError):
            _parse_broker_url("")

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError):
            _parse_broker_url("http://broker.example.com")


class TestPublishVariables:
    def test_substitutes_broker_topic_payload(self, db_session, monkeypatch):
        env = GlobalParameter(
            name="test-env",
            is_default=True,
            parameters=[
                {"key": "broker_host", "value": "mqtt.example.com"},
                {"key": "device_id", "value": "sensor-7"},
                {"key": "token", "value": "abc"},
            ],
        )
        db_session.add(env)
        db_session.commit()

        captured = {}

        def fake_publish(cfg, topic, payload, qos, retain):
            captured.update(
                {"cfg": cfg, "topic": topic, "payload": payload, "qos": qos, "retain": retain}
            )

        monkeypatch.setattr(mqtt_mod, "_publish_sync", fake_publish)

        res = _publish(
            db_session, _user(),
            broker="mqtt://{{broker_host}}:1883",
            topic="iot/{{device_id}}/data",
            payload='{"token":"{{token}}"}',
            qos=1,
            retain=True,
        )

        assert res.code == 200
        assert res.data["success"] is True
        assert res.data["broker"] == "mqtt://mqtt.example.com:1883"
        assert res.data["topic"] == "iot/sensor-7/data"
        assert res.data["payload"] == '{"token":"abc"}'
        assert res.data["qos"] == 1
        assert res.data["retain"] is True
        assert res.data["error"] is None
        # 传给发布函数的连接配置与替换后的值一致
        assert captured["cfg"]["host"] == "mqtt.example.com"
        assert captured["cfg"]["port"] == 1883
        assert captured["topic"] == "iot/sensor-7/data"
        assert captured["payload"] == '{"token":"abc"}'

    def test_local_parameters_override_environment(self, db_session, monkeypatch):
        db_session.add(GlobalParameter(
            name="env", is_default=True,
            parameters=[{"key": "device_id", "value": "from-env"}],
        ))
        db_session.commit()
        monkeypatch.setattr(mqtt_mod, "_publish_sync", lambda *a, **k: None)

        res = _publish(
            db_session, _user(),
            broker="mqtt://broker.example.com",
            topic="iot/{{device_id}}/data",
            parameters=[{"key": "device_id", "value": "from-request"}],
        )

        assert res.code == 200
        assert res.data["topic"] == "iot/from-request/data"

    def test_unresolved_variable_returns_400_and_logs(self, db_session, monkeypatch):
        called = []
        monkeypatch.setattr(mqtt_mod, "_publish_sync", lambda *a, **k: called.append(1))

        res = _publish(
            db_session, _user(),
            broker="mqtt://broker.example.com",
            topic="iot/{{unknown_var}}/data",
            payload="hello",
        )

        assert res.code == 400
        assert res.data["success"] is False
        assert "unknown_var" in res.message
        assert "unknown_var" in res.data["error"]
        assert called == []  # 未解析变量时不应发起真实发布

        log = db_session.exec(select(PushLog)).one()
        assert log.protocol == "mqtt"
        assert log.error is not None
        assert "unknown_var" in log.error
        assert log.mqtt_topic == "iot/{{unknown_var}}/data"

    def test_invalid_broker_returns_400(self, db_session, monkeypatch):
        monkeypatch.setattr(mqtt_mod, "_publish_sync", lambda *a, **k: None)
        res = _publish(
            db_session, _user(),
            broker="http://broker.example.com",
            topic="iot/data",
        )
        assert res.code == 400
        assert res.data["success"] is False
        assert "Broker 地址非法" in res.message

    def test_environment_downgraded_without_permission(self, db_session, monkeypatch):
        """无 global_parameter:manage 权限时忽略传入的 environment_id，使用默认环境"""
        target_env = GlobalParameter(
            name="restricted", is_default=False,
            parameters=[{"key": "device_id", "value": "restricted-device"}],
        )
        default_env = GlobalParameter(
            name="default", is_default=True,
            parameters=[{"key": "device_id", "value": "default-device"}],
        )
        db_session.add_all([target_env, default_env])
        db_session.commit()
        db_session.refresh(target_env)

        monkeypatch.setattr(mqtt_mod, "_publish_sync", lambda *a, **k: None)
        res = _publish(
            db_session, _user(roles=[]),
            broker="mqtt://broker.example.com",
            topic="iot/{{device_id}}",
            environment_id=target_env.id,
        )

        assert res.code == 200
        assert res.data["topic"] == "iot/default-device"
        log = db_session.exec(select(PushLog)).one()
        assert log.environment_id is None


class TestNormalizeParameters:
    """参数归一化：前端传映射对象，历史调用方传 [{key,value}] 列表"""

    def test_none_returns_empty_list(self):
        assert _normalize_parameters(None) == []

    def test_mapping_form(self):
        assert _normalize_parameters({"device": "sensor-1", "token": "abc"}) == [
            {"key": "device", "value": "sensor-1"},
            {"key": "token", "value": "abc"},
        ]

    def test_mapping_coerces_values_to_str(self):
        assert _normalize_parameters({"count": 3, "enabled": True}) == [
            {"key": "count", "value": "3"},
            {"key": "enabled", "value": "True"},
        ]

    def test_mapping_drops_empty_keys(self):
        assert _normalize_parameters({"": "x", "device": "sensor-1"}) == [
            {"key": "device", "value": "sensor-1"},
        ]

    def test_list_form(self):
        assert _normalize_parameters([{"key": "device", "value": "sensor-1"}]) == [
            {"key": "device", "value": "sensor-1"},
        ]

    def test_list_coerces_values_and_skips_invalid_items(self):
        params = [
            {"key": "device", "value": "sensor-1"},
            {"key": "count", "value": 5},
            {"value": "no-key"},
            {"key": ""},
            "not-a-dict",
            None,
        ]
        assert _normalize_parameters(params) == [
            {"key": "device", "value": "sensor-1"},
            {"key": "count", "value": "5"},
        ]

    def test_list_missing_value_defaults_to_empty_str(self):
        assert _normalize_parameters([{"key": "device"}]) == [
            {"key": "device", "value": ""},
        ]

    def test_other_types_return_empty_list(self):
        assert _normalize_parameters("device=sensor-1") == []
        assert _normalize_parameters(42) == []


class TestParametersForms:
    """两种 parameters 形态都要让变量替换生效（前端传映射，历史调用方传列表）"""

    def _run(self, db_session, monkeypatch, parameters, environment_id=None):
        captured = {}

        def fake_publish(cfg, topic, payload, qos, retain):
            captured.update({"topic": topic, "payload": payload})

        monkeypatch.setattr(mqtt_mod, "_publish_sync", fake_publish)
        res = _publish(
            db_session, _user(),
            broker="mqtt://broker.example.com",
            topic="iot/{{device}}/data",
            payload='{"device":"{{device}}"}',
            environment_id=environment_id,
            parameters=parameters,
        )
        return res, captured

    def test_mapping_parameters_are_substituted(self, db_session, monkeypatch):
        res, captured = self._run(db_session, monkeypatch, {"device": "sensor-1"})
        assert res.code == 200
        assert res.data["success"] is True
        assert captured["topic"] == "iot/sensor-1/data"
        assert captured["payload"] == '{"device":"sensor-1"}'

    def test_list_parameters_are_substituted(self, db_session, monkeypatch):
        res, captured = self._run(
            db_session, monkeypatch, [{"key": "device", "value": "sensor-1"}]
        )
        assert res.code == 200
        assert captured["topic"] == "iot/sensor-1/data"
        assert captured["payload"] == '{"device":"sensor-1"}'

    def test_mapping_parameters_accepted_by_request_model(self):
        """pydantic 校验层要同时接受映射与列表两种形态"""
        as_mapping = MqttPublishRequest(
            broker="mqtt://broker.example.com", topic="t", parameters={"device": "sensor-1"}
        )
        assert as_mapping.parameters == {"device": "sensor-1"}
        as_list = MqttPublishRequest(
            broker="mqtt://broker.example.com", topic="t",
            parameters=[{"key": "device", "value": "sensor-1"}],
        )
        assert as_list.parameters == [{"key": "device", "value": "sensor-1"}]

    def test_mapping_parameters_without_env_permission(self, db_session, monkeypatch):
        """无 global_parameter:manage 权限、environment_id 被降级时，本地映射参数仍生效"""
        env = GlobalParameter(name="restricted", is_default=False, parameters=[])
        db_session.add(env)
        db_session.commit()
        db_session.refresh(env)

        res, captured = self._run(
            db_session, monkeypatch, {"device": "local-device"}, environment_id=env.id
        )
        assert res.code == 200
        assert res.data["success"] is True
        assert captured["topic"] == "iot/local-device/data"


class TestPublishLog:
    def test_success_writes_mqtt_log(self, db_session, monkeypatch):
        monkeypatch.setattr(mqtt_mod, "_publish_sync", lambda *a, **k: None)

        res = _publish(
            db_session, _user(user_id="u9"),
            broker="mqtt://broker.example.com:1883",
            topic="iot/sensor/data",
            payload='{"v":1}',
            qos=2,
            retain=False,
            client_id="client-1",
        )

        assert res.code == 200
        log = db_session.exec(select(PushLog)).one()
        assert log.protocol == "mqtt"
        assert log.request_method == "PUBLISH"
        assert log.source_url == "mqtt://broker.example.com:1883"
        assert log.final_url == "mqtt://broker.example.com:1883"
        assert log.request_body == '{"v":1}'
        assert log.final_body == '{"v":1}'
        assert log.response_status == 0
        assert log.response_headers == {}
        assert log.mqtt_broker == "mqtt://broker.example.com:1883"
        assert log.mqtt_topic == "iot/sensor/data"
        assert log.mqtt_qos == 2
        assert log.mqtt_retain is False
        assert log.mqtt_client_id == "client-1"
        assert log.error is None
        assert log.duration_ms >= 0
        assert log.user_id == "u9"

    def test_failure_writes_failed_log(self, db_session, monkeypatch):
        def boom(cfg, topic, payload, qos, retain):
            raise RuntimeError("连接 MQTT Broker 失败: Connection refused")

        monkeypatch.setattr(mqtt_mod, "_publish_sync", boom)

        res = _publish(
            db_session, _user(user_id="u2"),
            broker="mqtt://broker.example.com",
            topic="iot/sensor/data",
            payload="x",
            qos=0,
            retain=True,
        )

        assert res.code == 500
        assert res.data["success"] is False
        assert "连接 MQTT Broker 失败" in res.message
        assert res.data["error"] == "连接 MQTT Broker 失败: Connection refused"

        log = db_session.exec(select(PushLog)).one()
        assert log.protocol == "mqtt"
        assert log.error == "连接 MQTT Broker 失败: Connection refused"
        assert log.mqtt_broker == "mqtt://broker.example.com"
        assert log.mqtt_topic == "iot/sensor/data"
        assert log.mqtt_retain is True
        assert log.user_id == "u2"

    def test_log_save_failure_does_not_break_publish(self, db_session, monkeypatch):
        """落库失败只告警：_save_mqtt_log 内部吞掉异常，不影响发布结果"""
        monkeypatch.setattr(mqtt_mod, "_publish_sync", lambda *a, **k: None)

        def broken_commit():
            raise RuntimeError("db down")

        monkeypatch.setattr(db_session, "commit", broken_commit)
        monkeypatch.setattr(db_session, "rollback", lambda: None)

        res = _publish(
            db_session, _user(),
            broker="mqtt://broker.example.com",
            topic="iot/data",
        )
        assert res.code == 200
        assert res.data["success"] is True


class TestPublishSync:
    def test_publish_sync_connects_publishes_and_closes(self, monkeypatch):
        events = []

        class FakeInfo:
            def wait_for_publish(self, timeout=None):
                events.append(("wait_for_publish", timeout))

            def is_published(self):
                return True

        class FakeClient:
            connect_timeout = 0

            def connect(self, host, port, keepalive=60):
                events.append(("connect", host, port))

            def loop_start(self):
                events.append(("loop_start",))

            def loop_stop(self):
                events.append(("loop_stop",))

            def disconnect(self):
                events.append(("disconnect",))

            def publish(self, topic, payload=None, qos=0, retain=False):
                events.append(("publish", topic, payload, qos, retain))
                return FakeInfo()

        monkeypatch.setattr(mqtt_mod, "_create_client", lambda cfg: FakeClient())

        mqtt_mod._publish_sync(
            {"host": "broker.example.com", "port": 1883, "transport": "tcp",
             "tls": False, "path": "", "client_id": "c1"},
            "iot/data", "payload", 1, True,
        )

        assert ("connect", "broker.example.com", 1883) in events
        assert ("publish", "iot/data", "payload", 1, True) in events
        assert ("loop_stop",) in events
        assert ("disconnect",) in events

    def test_publish_sync_connect_error_raises(self, monkeypatch):
        class FakeClient:
            connect_timeout = 0

            def connect(self, host, port, keepalive=60):
                raise OSError("Connection refused")

            def loop_stop(self):
                pass

            def disconnect(self):
                pass

        monkeypatch.setattr(mqtt_mod, "_create_client", lambda cfg: FakeClient())
        with pytest.raises(RuntimeError, match="连接 MQTT Broker 失败"):
            mqtt_mod._publish_sync(
                {"host": "h", "port": 1883, "transport": "tcp",
                 "tls": False, "path": "", "client_id": "c1"},
                "t", "p", 0, False,
            )

    def test_publish_sync_unconfirmed_raises(self, monkeypatch):
        class FakeInfo:
            def wait_for_publish(self, timeout=None):
                pass

            def is_published(self):
                return False

        class FakeClient:
            connect_timeout = 0

            def connect(self, host, port, keepalive=60):
                pass

            def loop_start(self):
                pass

            def loop_stop(self):
                pass

            def disconnect(self):
                pass

            def publish(self, topic, payload=None, qos=0, retain=False):
                return FakeInfo()

        monkeypatch.setattr(mqtt_mod, "_create_client", lambda cfg: FakeClient())
        with pytest.raises(RuntimeError, match="未确认"):
            mqtt_mod._publish_sync(
                {"host": "h", "port": 1883, "transport": "tcp",
                 "tls": False, "path": "", "client_id": "c1"},
                "t", "p", 0, False,
            )


class TestPushLogProtocolFilter:
    """推送日志按 protocol 过滤（http / mqtt 分开；历史空串按 http 处理）"""

    def test_filter_by_protocol(self, db_session):
        import app.routes.push_log as push_log_mod

        db_session.add(PushLog(
            request_method="POST", source_url="https://iot.example.com/x",
            final_url="https://iot.example.com/x", response_status=200,
            protocol="http", duration_ms=3, user_id="u1",
        ))
        db_session.add(PushLog(
            request_method="PUBLISH", source_url="mqtt://broker.example.com",
            final_url="mqtt://broker.example.com", response_status=0,
            protocol="mqtt", mqtt_broker="mqtt://broker.example.com",
            mqtt_topic="iot/data", mqtt_qos=1, mqtt_retain=False,
            mqtt_client_id="c1", duration_ms=8, user_id="u1",
        ))
        # 自动迁移补列的历史记录：protocol 为空字符串，语义上属于 HTTP
        db_session.add(PushLog(
            request_method="POST", source_url="https://legacy.example.com/x",
            final_url="https://legacy.example.com/x", response_status=200,
            protocol="", duration_ms=3, user_id="u1",
        ))
        db_session.commit()

        res_http = push_log_mod.get_push_logs(
            session=db_session, user=None, page=1, page_size=20,
            method=None, search=None, mocked=None, failed=None, protocol="http",
        )
        assert res_http.pagination["total"] == 2
        assert all(r.protocol in ("http", "") for r in res_http.data)

        res_mqtt = push_log_mod.get_push_logs(
            session=db_session, user=None, page=1, page_size=20,
            method=None, search=None, mocked=None, failed=None, protocol="mqtt",
        )
        assert res_mqtt.pagination["total"] == 1
        assert res_mqtt.data[0].protocol == "mqtt"
        assert res_mqtt.data[0].mqtt_topic == "iot/data"

        res_all = push_log_mod.get_push_logs(
            session=db_session, user=None, page=1, page_size=20,
            method=None, search=None, mocked=None, failed=None, protocol=None,
        )
        assert res_all.pagination["total"] == 3


class TestTestConnection:
    def test_connected_success(self, monkeypatch):
        monkeypatch.setattr(mqtt_mod, "_test_connection_sync", lambda cfg: None)
        res = _test_conn(_user(), broker="mqtt://broker.example.com:1883")
        assert res.code == 200
        assert res.data["connected"] is True
        assert res.data["message"] == "连接成功"
        assert res.data["duration_ms"] >= 0

    def test_connected_failure_keeps_code_200(self, monkeypatch):
        def boom(cfg):
            raise RuntimeError("连接 MQTT Broker 失败: Connection refused")

        monkeypatch.setattr(mqtt_mod, "_test_connection_sync", boom)
        res = _test_conn(_user(), broker="mqtt://broker.example.com:1883")
        assert res.code == 200
        assert res.data["connected"] is False
        assert "Connection refused" in res.data["message"]
        assert res.data["duration_ms"] >= 0

    def test_invalid_broker_returns_not_connected(self):
        res = _test_conn(_user(), broker="http://broker.example.com")
        assert res.code == 200
        assert res.data["connected"] is False
        assert "Broker 地址非法" in res.data["message"]

    def test_passes_credentials_and_tls_to_sync(self, monkeypatch):
        captured = {}

        def fake_sync(cfg):
            captured.update(cfg)

        monkeypatch.setattr(mqtt_mod, "_test_connection_sync", fake_sync)
        _test_conn(
            _user(), broker="mqtts://secure.example.com",
            client_id="c9", username="user", password="pass",
        )
        assert captured["host"] == "secure.example.com"
        assert captured["port"] == 8883
        assert captured["tls"] is True
        assert captured["username"] == "user"
        assert captured["password"] == "pass"
        assert captured["client_id"] == "c9"
