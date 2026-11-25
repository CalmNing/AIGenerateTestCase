"""MQTT 发布路由

为 IoT 数据推送平台提供 MQTT 协议的消息发布能力：
- POST /mqtt/publish         发布消息到指定 Broker（支持环境变量替换）
- POST /mqtt/test-connection 测试 Broker 连通性

发布使用 paho-mqtt 2.x 同步客户端，实际网络调用通过 asyncio.to_thread 放到线程池，
避免阻塞事件循环；每次发布（成功或失败）都会写入一条 PushLog 便于追溯。
"""
import asyncio
import logging
import threading
import time
import uuid
from typing import List, Optional
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.deps import get_db
from app.deps import CurrentUser
from app.permissions import Permission, get_user_permissions
from app.routes.proxy import build_param_map, substitute_variables
from db.models import PushLog
from utils.base_response import Response
from utils.log_redact import redact_base64

router = APIRouter(prefix="/mqtt", tags=["mqtt"])
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 连接/发布超时时间（秒）
MQTT_TIMEOUT_SECONDS = 10

# 各 scheme 的默认端口
DEFAULT_PORTS = {
    "mqtt": 1883,
    "tcp": 1883,
    "mqtts": 8883,
    "ssl": 8883,
    "ws": 8083,
    "wss": 8084,
}

# 常见 CONNACK 拒绝原因 → 可操作的中文提示
# 注意：paho-mqtt 2.x 的 reason_code.value 是 MQTT 5 的 0x80+ 编码（如 135 = 0x87 Not authorized），
# 不是 MQTT 3.1.1 的 1~5，所以这里两种编码都覆盖。
CONNACK_HINTS = {
    # MQTT 3.1.1
    1: "协议版本不被 Broker 支持，请确认 Broker 支持 MQTT 3.1.1",
    2: "Client ID 被拒绝：ID 格式非法或已被占用（阿里云需形如 GID_xxx@@@deviceId）",
    3: "服务不可用，Broker 内部错误或正在维护",
    4: "用户名或密码错误：请检查 Username/Password（阿里云需 DeviceCredential 或 Signature 格式）",
    5: "未授权：账号无权限，请检查用户名密码与 Topic 授权",
    # MQTT 5（paho 2.x 实际返回的编码）
    0x80: "未指定错误，Broker 拒绝了连接",
    0x81: "报文格式错误",
    0x82: "协议版本不被 Broker 支持，请确认 Broker 支持 MQTT 3.1.1 / 5.0",
    0x83: "Client ID 不被接受：ID 格式非法或已被占用（阿里云需形如 GID_xxx@@@deviceId）",
    0x84: "服务不可用，Broker 内部错误或正在维护",
    0x85: "用户名或密码错误：请检查 Username/Password（阿里云需 DeviceCredential 或 Signature 格式）",
    0x86: "未授权：账号无权限，请检查用户名密码与 Topic 授权",
    0x87: "未授权（Not authorized）：账号或 ClientID 不被该实例接受，请检查 ClientID 格式、用户名密码是否匹配该实例",
    0x88: "服务不可用，Broker 内部错误或正在维护",
    0x89: "Broker 繁忙，请稍后重试",
    0x8A: "连接被禁止：账号被禁用或 IP 不在白名单",
    0x8C: "认证方式不被支持",
    0x8D: "Topic 名称无效",
    0x8E: "报文过大",
    0x8F: "Topic 别名无效",
    0x90: "报文频率过高，被限流",
    0x91: "配额超限",
    0x93: "需要 TLS：当前为明文连接，请改用 8883 端口并开启 TLS",
    0x94: "Topic 过滤规则无效",
    0x95: "Payload 格式错误",
    0x97: "不支持的 QoS 等级",
    0x99: "不支持的保留消息（Retain）",
    0x9A: "不支持的会话过期时间",
    0x9B: "不支持的接收上限",
    0x9C: "不支持的 Topic 别名",
    0x9D: "不支持的报文类型",
}


class MqttPublishRequest(BaseModel):
    """MQTT 发布请求模型"""
    broker: str
    topic: str
    payload: str = ""
    qos: int = 0
    retain: bool = False
    client_id: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    tls: bool = False
    environment_id: Optional[int] = None
    # 兼容两种形态：映射 {"k": "v"}（前端当前实现）或列表 [{"key": "k", "value": "v"}]
    parameters: Optional[dict | List[dict]] = None


class MqttTestConnectionRequest(BaseModel):
    """MQTT 连通性测试请求模型"""
    broker: str
    client_id: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    tls: bool = False


def _parse_broker_url(broker: str) -> dict:
    """解析 MQTT Broker 地址为连接配置

    支持 mqtt:// / tcp:// / mqtts:// / ssl:// / ws:// / wss:// 以及裸 host、host:port。
    默认端口：1883 / 8883 / 8083 / 8084；mqtts、ssl、wss 视为 TLS 连接。

    返回 {"host", "port", "transport", "tls", "path"}。
    """
    raw = (broker or "").strip()
    if not raw:
        raise ValueError("broker 不能为空")
    # 未带 scheme 的裸 host / host:port 按普通 MQTT(TCP) 处理
    if "://" not in raw:
        raw = f"mqtt://{raw}"

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "mqtt").lower()
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"无法解析 broker 地址: {broker}")
    if scheme not in DEFAULT_PORTS:
        raise ValueError(f"不支持的 broker 协议: {scheme}")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"broker 端口非法: {exc}") from exc

    return {
        "host": host,
        "port": port or DEFAULT_PORTS[scheme],
        "transport": "websockets" if scheme in {"ws", "wss"} else "tcp",
        "tls": scheme in {"mqtts", "ssl", "wss"},
        "path": parsed.path or "",
    }


def _normalize_parameters(parameters) -> List[dict]:
    """把请求里的参数归一化为 [{"key": ..., "value": ...}] 列表

    前端可能传映射对象 {"device": "sensor-1"}，历史/其他调用方传
    [{"key": "device", "value": "sensor-1"}]，这里统一成 build_param_map 认识的列表。
    非法项（无 key、非 dict、空 key）直接丢弃。
    """
    if not parameters:
        return []

    # 1. 映射对象：{"device": "sensor-1"}
    if isinstance(parameters, dict):
        return [
            {"key": str(k), "value": str(v)}
            for k, v in parameters.items()
            if k is not None and str(k)
        ]

    # 2. 列表：[{"key": "device", "value": "sensor-1"}]
    if isinstance(parameters, (list, tuple)):
        normalized: List[dict] = []
        for item in parameters:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key is None or not str(key):
                continue
            normalized.append({"key": str(key), "value": str(item.get("value", ""))})
        return normalized

    # 3. 其他类型一律忽略
    return []


def _create_client(cfg: dict):
    """按连接配置创建 paho-mqtt 2.x 客户端（尚未连接）"""
    transport = cfg.get("transport") or "tcp"
    client_id = cfg.get("client_id") or f"iot-push-{uuid.uuid4().hex[:12]}"
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
        transport=transport,
    )
    client.connect_timeout = MQTT_TIMEOUT_SECONDS
    if cfg.get("username"):
        client.username_pw_set(cfg["username"], cfg.get("password") or None)
    if cfg.get("tls"):
        client.tls_set()
    if transport == "websockets":
        client.ws_set_options(path=cfg.get("path") or "/mqtt")
    return client


def _safe_close(client) -> None:
    """安全关闭客户端：停止网络循环并断开连接，异常忽略"""
    try:
        client.loop_stop()
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响结果
        logger.warning("停止 MQTT 网络循环失败(忽略): %s", exc)
    try:
        client.disconnect()
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响结果
        logger.warning("断开 MQTT 连接失败(忽略): %s", exc)


def _publish_sync(cfg: dict, topic: str, payload: str, qos: int, retain: bool) -> None:
    """同步发布一条 MQTT 消息（阻塞调用，供 asyncio.to_thread 使用），失败抛异常"""
    client = _create_client(cfg)
    try:
        try:
            client.connect(cfg["host"], cfg["port"], keepalive=MQTT_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - 统一包装为可读错误
            raise RuntimeError(f"连接 MQTT Broker 失败: {exc}") from exc

        client.loop_start()
        try:
            info = client.publish(topic, payload=payload, qos=qos, retain=retain)
            info.wait_for_publish(timeout=MQTT_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - 统一包装为可读错误
            raise RuntimeError(f"MQTT 消息发布失败: {exc}") from exc

        try:
            published = info.is_published()
        except Exception as exc:  # noqa: BLE001 - 统一包装为可读错误
            raise RuntimeError(f"MQTT 消息发布失败: {exc}") from exc
        if not published:
            rc = getattr(info, "rc", 0)
            if rc:
                hint = CONNACK_HINTS.get(int(rc), "") if str(rc).isdigit() else ""
                raise RuntimeError(
                    f"MQTT 消息发布被拒绝（rc={rc}）" + (f"：{hint}" if hint else "")
                )
            raise RuntimeError(f"MQTT 消息发布超时（{MQTT_TIMEOUT_SECONDS}s 内未确认）")
    finally:
        _safe_close(client)


def _test_connection_sync(cfg: dict) -> None:
    """同步测试 MQTT Broker 连通性（等待 CONNACK），失败抛异常"""
    client = _create_client(cfg)
    done = threading.Event()
    conn_errors: List[str] = []

    def _on_connect(_client, _userdata, _flags, reason_code, _properties=None):
        code = getattr(reason_code, "value", reason_code)
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = -1
        if code == 0:
            done.set()
            return
        # 把常见 CONNACK 拒绝原因翻译成可操作的中文提示，便于前端直接展示
        hint = CONNACK_HINTS.get(code)
        conn_errors.append(
            f"CONNACK 返回 {reason_code}"
            + (f"（{hint}）" if hint else "")
        )
        done.set()

    client.on_connect = _on_connect
    try:
        try:
            client.connect(cfg["host"], cfg["port"], keepalive=MQTT_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - 统一包装为可读错误
            raise RuntimeError(f"连接 MQTT Broker 失败: {exc}") from exc

        client.loop_start()
        if not done.wait(MQTT_TIMEOUT_SECONDS):
            raise RuntimeError(f"连接超时（{MQTT_TIMEOUT_SECONDS}s 内未收到 CONNACK）")
        if conn_errors:
            raise RuntimeError(f"连接 MQTT Broker 失败: {conn_errors[0]}")
    finally:
        _safe_close(client)


def _save_mqtt_log(
    db: Session,
    *,
    user: CurrentUser,
    request: MqttPublishRequest,
    environment_id: Optional[int],
    final_broker: str,
    final_topic: str,
    final_payload: str,
    qos: int,
    retain: bool,
    client_id: Optional[str],
    duration_ms: int,
    error: Optional[str] = None,
):
    """持久化一条 MQTT 发布记录（原始 + 变量替换后），失败只告警不阻断主流程"""
    try:
        log = PushLog(
            request_method="PUBLISH",
            source_url=request.broker or "",
            final_url=final_broker or "",
            request_headers={},
            final_headers={},
            request_params={},
            final_params={},
            request_body=redact_base64(request.payload or ""),
            final_body=redact_base64(final_payload or ""),
            response_status=0,
            response_headers={},
            mocked=False,
            error=error,
            duration_ms=duration_ms,
            environment_id=environment_id,
            user_id=getattr(user, "user_id", None),
            protocol="mqtt",
            mqtt_broker=final_broker,
            mqtt_topic=final_topic,
            mqtt_qos=qos,
            mqtt_retain=retain,
            mqtt_client_id=client_id,
        )
        db.add(log)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 记录失败不影响发布结果返回
        db.rollback()
        logger.warning("保存 MQTT 发布记录失败: %s", exc)


@router.post("/publish", response_model=Response[dict])
async def publish_mqtt(
    request: MqttPublishRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    """通过 MQTT 发布消息，支持环境变量替换；无论成功失败都会记录推送日志"""
    start_time = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - start_time) * 1000)

    def _result(success: bool, broker: str, topic: str, payload: str,
                duration_ms: int, error: Optional[str]) -> dict:
        return {
            "success": success,
            "broker": broker,
            "topic": topic,
            "qos": request.qos,
            "retain": request.retain,
            "payload": payload,
            "duration_ms": duration_ms,
            "error": error,
        }

    # 环境权限处理与 proxy.forward_request 一致：无 GLOBAL_PARAMETER_MANAGE 时降级为 None
    environment_id = request.environment_id
    if environment_id and (
        user is None or Permission.GLOBAL_PARAMETER_MANAGE not in get_user_permissions(user)
    ):
        environment_id = None
    param_map = build_param_map(db, environment_id, _normalize_parameters(request.parameters))

    # 对 broker / topic / payload / client_id 做变量替换，收集未解析变量
    unresolved: set[str] = set()
    final_broker = substitute_variables(request.broker or "", param_map, unresolved)
    final_topic = substitute_variables(request.topic or "", param_map, unresolved)
    final_payload = substitute_variables(request.payload or "", param_map, unresolved)
    final_client_id = (
        substitute_variables(request.client_id, param_map, unresolved)
        if request.client_id else None
    )
    # 未指定 client_id 时生成一个，便于日志追溯本次连接
    if not final_client_id:
        final_client_id = f"iot-push-{uuid.uuid4().hex[:12]}"

    # 1. 存在未解析变量：记录失败日志并返回 400
    if unresolved:
        error = f"以下变量未在环境参数中定义: {', '.join(sorted(unresolved))}"
        _save_mqtt_log(
            db, user=user, request=request, environment_id=environment_id,
            final_broker=final_broker, final_topic=final_topic, final_payload=final_payload,
            qos=request.qos, retain=request.retain, client_id=final_client_id,
            duration_ms=_elapsed_ms(), error=error,
        )
        return Response(
            code=400,
            message=error,
            data=_result(False, final_broker, final_topic, final_payload, _elapsed_ms(), error),
        )

    # 2. 解析 Broker 地址
    try:
        cfg = _parse_broker_url(final_broker)
    except ValueError as exc:
        error = f"Broker 地址非法: {exc}"
        _save_mqtt_log(
            db, user=user, request=request, environment_id=environment_id,
            final_broker=final_broker, final_topic=final_topic, final_payload=final_payload,
            qos=request.qos, retain=request.retain, client_id=final_client_id,
            duration_ms=_elapsed_ms(), error=error,
        )
        return Response(
            code=400,
            message=error,
            data=_result(False, final_broker, final_topic, final_payload, _elapsed_ms(), error),
        )
    cfg.update({
        "broker": final_broker,
        "client_id": final_client_id,
        "username": request.username,
        "password": request.password,
        "tls": bool(request.tls or cfg.get("tls")),
    })

    # 3. 在线程池中同步发布，避免阻塞事件循环
    error: Optional[str] = None
    try:
        await asyncio.to_thread(
            _publish_sync, cfg, final_topic, final_payload, request.qos, request.retain
        )
    except Exception as exc:  # noqa: BLE001 - 发布失败统一转结构化错误
        error = str(exc) or exc.__class__.__name__
        logger.warning("MQTT 发布失败: %s", error)

    duration_ms = _elapsed_ms()
    _save_mqtt_log(
        db, user=user, request=request, environment_id=environment_id,
        final_broker=final_broker, final_topic=final_topic, final_payload=final_payload,
        qos=request.qos, retain=request.retain, client_id=final_client_id,
        duration_ms=duration_ms, error=error,
    )

    if error:
        return Response(
            code=500,
            message=f"MQTT 发布失败: {error}",
            data=_result(False, final_broker, final_topic, final_payload, duration_ms, error),
        )
    return Response(
        data=_result(True, final_broker, final_topic, final_payload, duration_ms, None)
    )


@router.post("/test-connection", response_model=Response[dict])
async def test_connection(request: MqttTestConnectionRequest, user: CurrentUser):
    """测试 MQTT Broker 连通性；连接失败仍返回 code=200，由 connected=false 标识"""
    start_time = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - start_time) * 1000)

    try:
        cfg = _parse_broker_url(request.broker)
    except ValueError as exc:
        return Response(data={
            "connected": False,
            "message": f"Broker 地址非法: {exc}",
            "duration_ms": _elapsed_ms(),
        })
    cfg.update({
        "broker": request.broker,
        "client_id": request.client_id,
        "username": request.username,
        "password": request.password,
        "tls": bool(request.tls or cfg.get("tls")),
    })

    try:
        await asyncio.to_thread(_test_connection_sync, cfg)
    except Exception as exc:  # noqa: BLE001 - 连接失败以 connected=false 返回
        return Response(data={
            "connected": False,
            "message": str(exc) or exc.__class__.__name__,
            "duration_ms": _elapsed_ms(),
        })

    return Response(data={
        "connected": True,
        "message": "连接成功",
        "duration_ms": _elapsed_ms(),
    })
