"""WebSocket 订阅路由

为 IoT 数据推送平台提供 WebSocket 协议的连接与消息采集能力：
- POST /ws/connect         连接 WebSocket 并收集消息（支持环境变量替换）
- POST /ws/test-connection 仅测试握手连通性

连接使用 websockets 异步客户端，在 open 超时、空闲超时、收满条数或服务端关闭时结束；
无论成功失败都会写入一条 PushLog（protocol="websocket"）便于追溯。
"""
import asyncio
import base64
import logging
import time
from typing import List, Optional
from urllib.parse import urlparse

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    InvalidHandshake,
    InvalidURI,
    WebSocketException,
)
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.deps import get_db
from app.deps import CurrentUser
from app.permissions import Permission, get_user_permissions
from app.routes.mqtt import _normalize_parameters
from app.routes.proxy import build_param_map, substitute_in_headers, substitute_variables
from db.models import PushLog
from utils.base_response import Response

router = APIRouter(prefix="/ws", tags=["websocket"])
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# WebSocket 握手（open）超时时间（秒）
WS_OPEN_TIMEOUT = 10
# 单条文本消息写入日志时的最大长度
MAX_LOG_BODY_CHARS = 4000
# 二进制消息 base64 字段最大长度（超出截断，避免日志过大）
MAX_BASE64_CHARS = 2000


class WsConnectRequest(BaseModel):
    """WebSocket 连接请求模型"""
    url: str
    headers: Optional[dict] = None
    environment_id: Optional[int] = None
    # 兼容两种形态：映射 {"k": "v"} 或列表 [{"key": "k", "value": "v"}]
    parameters: Optional[dict | List[dict]] = None
    max_messages: int = Field(default=50, ge=1, le=500)
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    protocols: Optional[List[str]] = None
    send_message: Optional[str] = None


class WsTestConnectionRequest(BaseModel):
    """WebSocket 连通性测试请求模型"""
    url: str
    headers: Optional[dict] = None
    protocols: Optional[List[str]] = None
    timeout_seconds: int = Field(default=8, ge=1, le=60)


def _normalize_ws_url(url: str) -> str:
    """归一化 WebSocket 地址

    裸 host / host:port 补 ws://；http:// 转 ws://、https:// 转 wss://；
    仅接受 ws / wss，其余 scheme 抛 ValueError。
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("url 不能为空")
    if "://" not in raw:
        raw = f"ws://{raw}"

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    # http(s) 地址按语义映射到 ws(s)
    if scheme in {"http", "https"}:
        rest = raw.split("://", 1)[1]
        raw = f"{'wss' if scheme == 'https' else 'ws'}://{rest}"
        parsed = urlparse(raw)
        scheme = parsed.scheme.lower()

    if scheme not in {"ws", "wss"}:
        raise ValueError(f"不支持的 WebSocket 协议: {scheme}")
    if not parsed.hostname:
        raise ValueError(f"无法解析 WebSocket 地址: {url}")
    return raw


async def _open_ws(url: str, headers: Optional[dict], protocols: Optional[List[str]]):
    """建立 WebSocket 连接（握手阶段由外层 asyncio.wait_for 控制超时）"""
    return await websockets.connect(
        url,
        additional_headers=headers or None,
        subprotocols=protocols or None,
        open_timeout=WS_OPEN_TIMEOUT,
        # 采集场景生命周期短，空闲判定由 timeout_seconds 自行控制，关闭自动 ping
        ping_interval=None,
        ping_timeout=None,
        close_timeout=3,
        max_size=None,
        # 内网直连，不继承系统代理配置
        proxy=None,
    )


def _build_message_item(seq: int, raw, start: float) -> dict:
    """构造单条消息记录 {seq, type, size, content, base64?, ts}"""
    ts = round(time.perf_counter() - start, 3)
    if isinstance(raw, (bytes, bytearray)):
        payload = bytes(raw)
        item = {
            "seq": seq,
            "type": "binary",
            "size": len(payload),
            "content": f"<binary {len(payload)} bytes>",
            "ts": ts,
        }
        encoded = base64.b64encode(payload).decode("ascii")
        if encoded:
            item["base64"] = encoded[:MAX_BASE64_CHARS]
        return item
    text = str(raw)
    return {
        "seq": seq,
        "type": "text",
        "size": len(text.encode("utf-8")),
        "content": text,
        "ts": ts,
    }


async def _collect_messages(cfg: dict) -> dict:
    """连接 WebSocket 并采集消息（核心实现，便于单测 monkeypatch）

    cfg 支持：url / headers / protocols / max_messages / timeout_seconds / send_message。
    返回契约结构，另附内部字段 "_last_content"（最后一条消息内容，供写日志使用）。
    """
    url = cfg.get("url") or ""
    headers = cfg.get("headers") or None
    protocols = cfg.get("protocols") or None
    max_messages = max(1, int(cfg.get("max_messages") or 50))
    idle_timeout = max(0.1, float(cfg.get("timeout_seconds") or 10))
    send_message = cfg.get("send_message")

    start = time.perf_counter()
    messages: List[dict] = []
    connected = False
    closed_by = "error"
    error: Optional[str] = None
    ws = None

    try:
        ws = await asyncio.wait_for(
            _open_ws(url, headers, protocols), timeout=WS_OPEN_TIMEOUT
        )
        connected = True

        # 连接成功后先发一条文本消息（如订阅指令）
        if send_message:
            await asyncio.wait_for(ws.send(send_message), timeout=idle_timeout)

        while len(messages) < max_messages:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                closed_by = "timeout"
                break
            messages.append(_build_message_item(len(messages), raw, start))
            if len(messages) >= max_messages:
                closed_by = "max_messages"
                break
    except asyncio.TimeoutError:
        error = f"连接超时（{WS_OPEN_TIMEOUT}s 内未完成 WebSocket 握手）"
        closed_by = "error"
    except ConnectionClosed as exc:
        # 服务端主动关闭（含正常关闭与异常关闭）
        closed_by = "server"
        if isinstance(exc, ConnectionClosedError):
            error = f"服务端异常关闭连接: {exc}"
    except (OSError, InvalidHandshake, InvalidURI, WebSocketException) as exc:
        error = str(exc) or exc.__class__.__name__
        closed_by = "error"
    except Exception as exc:  # noqa: BLE001 - 统一转结构化错误
        error = str(exc) or exc.__class__.__name__
        closed_by = "error"
    finally:
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=3)
            except Exception as exc:  # noqa: BLE001 - 关闭失败不影响结果
                logger.warning("关闭 WebSocket 连接失败(忽略): %s", exc)

    duration_ms = int((time.perf_counter() - start) * 1000)
    return {
        "success": connected and error is None,
        "url": url,
        "connected": connected,
        "messages": messages,
        "received": len(messages),
        "duration_ms": duration_ms,
        "closed_by": closed_by,
        "error": error,
        "_last_content": messages[-1]["content"] if messages else None,
    }


async def _probe_connection(cfg: dict) -> None:
    """仅完成一次 WebSocket 握手后立即关闭，失败抛异常"""
    ws = None
    try:
        ws = await asyncio.wait_for(
            _open_ws(cfg.get("url") or "", cfg.get("headers"), cfg.get("protocols")),
            timeout=WS_OPEN_TIMEOUT,
        )
    finally:
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=3)
            except Exception as exc:  # noqa: BLE001 - 关闭失败不影响结果
                logger.warning("关闭 WebSocket 连接失败(忽略): %s", exc)


def _save_ws_log(
    db: Session,
    *,
    user: CurrentUser,
    request: WsConnectRequest,
    environment_id: Optional[int],
    final_url: str,
    final_headers: Optional[dict],
    last_content: Optional[str],
    duration_ms: int,
    error: Optional[str] = None,
):
    """持久化一条 WebSocket 连接记录（原始 + 变量替换后），失败只告警不阻断主流程"""
    try:
        log = PushLog(
            request_method="WS",
            source_url=request.url or "",
            final_url=final_url or "",
            request_headers=request.headers or {},
            final_headers=final_headers or {},
            request_params={},
            final_params={},
            request_body=request.send_message,
            final_body=(last_content or "")[:MAX_LOG_BODY_CHARS] or None,
            response_status=0,
            response_headers={},
            mocked=False,
            error=error,
            duration_ms=duration_ms,
            environment_id=environment_id,
            user_id=getattr(user, "user_id", None),
            protocol="websocket",
        )
        db.add(log)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 记录失败不影响结果返回
        db.rollback()
        logger.warning("保存 WebSocket 连接记录失败: %s", exc)


@router.post("/connect", response_model=Response[dict])
async def connect_ws(
    request: WsConnectRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    """连接 WebSocket 并采集消息，支持环境变量替换；无论成功失败都会记录推送日志"""
    start_time = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - start_time) * 1000)

    def _result(success: bool, url: str, connected: bool, messages: list,
                received: int, duration_ms: int, closed_by: str,
                error: Optional[str]) -> dict:
        return {
            "success": success,
            "url": url,
            "connected": connected,
            "messages": messages,
            "received": received,
            "duration_ms": duration_ms,
            "closed_by": closed_by,
            "error": error,
        }

    # 环境权限处理与 proxy.forward_request 一致：无 GLOBAL_PARAMETER_MANAGE 时降级为 None
    environment_id = request.environment_id
    if environment_id and (
        user is None or Permission.GLOBAL_PARAMETER_MANAGE not in get_user_permissions(user)
    ):
        environment_id = None
    param_map = build_param_map(db, environment_id, _normalize_parameters(request.parameters))

    # 对 url 与 headers 值做变量替换，收集未解析变量
    unresolved: set[str] = set()
    final_url = substitute_variables(request.url or "", param_map, unresolved)
    final_headers = substitute_in_headers(request.headers, param_map, unresolved)

    # 1. 存在未解析变量：记录失败日志并返回 400
    if unresolved:
        error = f"以下变量未在环境参数中定义: {', '.join(sorted(unresolved))}"
        _save_ws_log(
            db, user=user, request=request, environment_id=environment_id,
            final_url=final_url, final_headers=final_headers, last_content=None,
            duration_ms=_elapsed_ms(), error=error,
        )
        return Response(
            code=400,
            message=error,
            data=_result(False, final_url, False, [], 0, _elapsed_ms(), "error", error),
        )

    # 2. 校验并归一化地址
    try:
        normalized_url = _normalize_ws_url(final_url)
    except ValueError as exc:
        error = f"WebSocket 地址非法: {exc}"
        _save_ws_log(
            db, user=user, request=request, environment_id=environment_id,
            final_url=final_url, final_headers=final_headers, last_content=None,
            duration_ms=_elapsed_ms(), error=error,
        )
        return Response(
            code=400,
            message=error,
            data=_result(False, final_url, False, [], 0, _elapsed_ms(), "error", error),
        )

    # 3. 连接并采集消息
    result = await _collect_messages({
        "url": normalized_url,
        "headers": final_headers,
        "protocols": request.protocols,
        "max_messages": request.max_messages,
        "timeout_seconds": request.timeout_seconds,
        "send_message": request.send_message,
    })

    last_content = result.pop("_last_content", None)
    duration_ms = result.get("duration_ms", _elapsed_ms())
    error = result.get("error")

    _save_ws_log(
        db, user=user, request=request, environment_id=environment_id,
        final_url=normalized_url, final_headers=final_headers,
        last_content=last_content, duration_ms=duration_ms, error=error,
    )

    if error:
        return Response(
            code=500,
            message=f"WebSocket 连接失败: {error}",
            data=_result(
                False, normalized_url, bool(result.get("connected")),
                result.get("messages") or [], int(result.get("received") or 0),
                duration_ms, result.get("closed_by") or "error", error,
            ),
        )
    return Response(
        data=_result(
            bool(result.get("success")), normalized_url, bool(result.get("connected")),
            result.get("messages") or [], int(result.get("received") or 0),
            duration_ms, result.get("closed_by") or "error", None,
        )
    )


@router.post("/test-connection", response_model=Response[dict])
async def test_ws_connection(request: WsTestConnectionRequest, user: CurrentUser):
    """测试 WebSocket 握手连通性；连接失败仍返回 code=200，由 connected=false 标识"""
    start_time = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - start_time) * 1000)

    try:
        normalized_url = _normalize_ws_url(request.url)
    except ValueError as exc:
        return Response(data={
            "connected": False,
            "message": f"WebSocket 地址非法: {exc}",
            "duration_ms": _elapsed_ms(),
        })

    try:
        await _probe_connection({
            "url": normalized_url,
            "headers": request.headers,
            "protocols": request.protocols,
        })
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
