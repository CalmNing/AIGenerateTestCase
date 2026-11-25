"""log_redact 工具测试 + 推送/日志链路 base64 脱敏集成测试。

纯单元测试，使用内存 SQLite（conftest.db_engine/db_session）。
"""
import base64


def _png_b64(size: int = 8000) -> str:
    """造一段足够长（>120 字符）的合法 base64 串"""
    return base64.b64encode(b"\\x89PNG" + b"0" * size).decode("ascii")


class TestRedactBase64:
    def test_short_string_untouched(self):
        from utils.log_redact import redact_base64

        assert redact_base64("hello world") == "hello world"
        assert redact_base64("abc123-_x") == "abc123-_x"

    def test_long_base64_replaced(self):
        from utils.log_redact import redact_base64

        b64 = _png_b64()
        out = redact_base64(b64)
        assert out != b64
        assert "base64" in out
        assert "已省略" in out
        # 长度信息保留，方便判断原始大小
        assert str(len(b64)) in out

    def test_data_url_keeps_prefix(self):
        from utils.log_redact import redact_base64

        b64 = _png_b64()
        out = redact_base64("data:image/png;base64," + b64)
        assert out.startswith("data:image/png;base64,")
        assert b64 not in out

    def test_dict_and_list_recursive(self):
        from utils.log_redact import redact_base64

        b64 = _png_b64()
        data = {"img": b64, "nested": {"list": [1, "ok", {"b": b64}]}}
        out = redact_base64(data)
        assert out["img"] != b64
        assert out["nested"]["list"][0] == 1
        assert out["nested"]["list"][1] == "ok"
        assert out["nested"]["list"][2]["b"] != b64

    def test_none_and_non_string_passthrough(self):
        from utils.log_redact import redact_base64

        assert redact_base64(None) is None
        assert redact_base64(123) == 123
        assert redact_base64({"a": 1}) == {"a": 1}

    def test_custom_min_chars(self):
        from utils.log_redact import redact_base64

        value = "A" * 50
        assert redact_base64(value) == value
        out = redact_base64(value, min_chars=40)
        assert "base64" in out


class TestBodyToTextRedacted:
    def test_body_to_text_redacts_base64(self):
        from app.routes.proxy import _body_to_text

        b64 = _png_b64()
        text = _body_to_text({"img": b64, "name": "dev1"})
        assert text is not None
        assert b64 not in text
        assert "dev1" in text
        assert '"name"' in text

    def test_body_to_text_plain_untouched(self):
        from app.routes.proxy import _body_to_text

        assert _body_to_text({"a": 1}) == '{"a": 1}'

    def test_str_body_redacted(self):
        from app.routes.proxy import _body_to_text

        b64 = _png_b64()
        text = _body_to_text("payload=" + b64)
        assert b64 not in text


class TestTrimPreStepResults:
    def test_extracted_base64_redacted(self):
        from app.routes.proxy import _trim_pre_step_results

        b64 = _png_b64()
        trimmed = _trim_pre_step_results([
            {"type": "request", "name": "s1", "extracted": {"img": b64, "id": "x1"}},
        ])
        assert trimmed[0]["extracted"]["id"] == "x1"
        assert b64 not in trimmed[0]["extracted"]["img"]

    def test_params_base64_redacted(self):
        from app.routes.proxy import _trim_pre_step_results

        b64 = _png_b64()
        trimmed = _trim_pre_step_results([
            {"type": "params", "name": "row1", "params": {"img": b64}},
        ])
        assert b64 not in trimmed[0]["params"]["img"]


class TestSavePushLogRedacted:
    def test_save_push_log_redacts_bodies(self, db_session):
        from sqlmodel import select

        from app.auth import UserInfo
        from app.routes.proxy import ProxyRequest, _save_push_log
        from db.models import PushLog

        b64 = _png_b64()
        user = UserInfo(user_id="u1", username="alice", roles=[])
        req = ProxyRequest(url="https://iot.example.com/push", method="POST")
        _save_push_log(
            db_session, user=user, request=req, environment_id=None,
            final_url="https://iot.example.com/push",
            final_headers=None, final_params=None,
            final_data={"img": b64},
            duration_ms=10,
            response_status=200,
            response_body={"echo": b64},
        )
        log = db_session.exec(select(PushLog)).one()
        assert b64 not in log.final_body
        assert b64 not in log.response_body
        assert "已省略" in log.final_body


class TestMqttLogRedacted:
    def test_mqtt_payload_redacted(self, db_session):
        import app.routes.mqtt as mqtt_mod
        from sqlmodel import select

        from app.auth import UserInfo
        from app.routes.mqtt import MqttPublishRequest, _save_mqtt_log
        from db.models import PushLog

        b64 = _png_b64()
        user = UserInfo(user_id="u1", username="alice", roles=[])
        req = MqttPublishRequest(
            broker="mqtt://broker.example.com:1883",
            topic="iot/data",
            payload='{"img": "' + b64 + '"}',
        )
        _save_mqtt_log(
            db_session, user=user, request=req, environment_id=None,
            final_broker="mqtt://broker.example.com:1883",
            final_topic="iot/data",
            final_payload=req.payload,
            qos=0, retain=False, client_id=None, duration_ms=5,
        )
        log = db_session.exec(select(PushLog)).one()
        assert b64 not in log.final_body
        assert "已省略" in log.final_body
