"""Mock 正向代理核心服务。

以 asyncio 实现的正向 HTTP 代理（含 HTTPS MITM）：
- 明文 HTTP 请求（绝对 URI 形式）：按「主机 + 路径」规则匹配，
  命中 → 调用现有 Mock 两层匹配链返回 Mock 响应；未命中 → httpx 透传真实服务。
- HTTPS CONNECT 请求：主机命中任一启用规则时做 MITM 解密（自签 CA 动态签发证书）
  后进入与明文相同的匹配管线；否则盲隧道透传不解密。
- 全量流量写入 MockLog（config_name 以 [代理] / [代理透传] 前缀区分来源）。
"""
import asyncio
import json
import logging
import re
import ssl
from typing import Optional
from urllib.parse import urlsplit, unquote

import httpx

from db.db import engine
from db.models import MockConfig, MockLog, MockProxyRule
from sqlmodel import Session, select

from app.services.mock_matcher import (
    compile_path_pattern,
    match_address,
    match_host,
    path_candidates,
    path_matches,
)

logger = logging.getLogger(__name__)

# hop-by-hop 头不应转发给上游
_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
    "host", "content-length",
}

# httpx 会自动解压响应体（resp.content 是解压后的字节），
# 因此回给客户端的响应头必须去掉 content-encoding，否则客户端会二次解压报错。
_RESPONSE_STRIP_HEADERS = _HOP_BY_HOP_HEADERS | {"content-encoding"}

UPSTREAM_TIMEOUT = 30.0


class _ProxyState:
    """代理运行状态。"""

    def __init__(self):
        self.running = False
        self.port: Optional[int] = None
        self.server: Optional[asyncio.AbstractServer] = None


state = _ProxyState()
_start_lock = asyncio.Lock()

# 全局复用的上游客户端；verify=False 因 MITM 场景下目标证书由真实站点提供，
# 但内网测试环境常见自签证书，统一放宽由用户自行评估。
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(verify=False, timeout=UPSTREAM_TIMEOUT, follow_redirects=False)
    return _http_client


# ---------------------------------------------------------------------------
# 规则匹配引擎
# ---------------------------------------------------------------------------

def _enabled_rules() -> list:
    with Session(engine) as session:
        return list(
            session.exec(
                select(MockProxyRule).where(MockProxyRule.enabled == True)
            ).all()
        )


def find_matching_rule(host: str, path: str) -> Optional[MockProxyRule]:
    """在启用的规则中查找第一个命中「主机+路径」的规则。

    按 host_pattern 长度降序尝试（更具体的模式优先）。
    路径匹配同时看「原样路径」与「规则服务前缀剥离后的路径」，
    因此规则里可以写 /contract/（内网）或 /service/contract/*（网关）。
    """
    for rule in sorted(_enabled_rules(), key=lambda r: len(r.host_pattern or ""), reverse=True):
        if not match_host(rule.host_pattern, host):
            continue
        candidates = path_candidates(path, rule.service_prefix)
        compiled = compile_path_pattern(rule.path_pattern)
        if any(path_matches(compiled, candidate) for candidate in candidates):
            return rule
    return None


def has_address_binding(host: str, path: str) -> bool:
    """是否有启用的 MockConfig 通过 proxy_urls 显式绑定了这个接口地址。

    用于「用户配了真实接口地址 + 响应，监听下次调用就返回」的场景：
    即使没有转发规则，也能自动拦截这个接口。
    """
    with Session(engine) as session:
        configs = session.exec(
            select(MockConfig).where(MockConfig.enabled == True)
        ).all()
    for config in configs:
        for url in getattr(config, "proxy_urls", None) or []:
            hit, _ = match_address(url, host, path)
            if hit:
                return True
    return False


def host_has_any_rule(host: str) -> bool:
    """该主机是否需要 MITM 解密。

    满足任一条件即需要：
    - 命中任一启用转发规则的 host_pattern；
    - 有启用的 Mock 通过 proxy_urls 显式绑定了该主机上的接口地址
      （用户「配地址 + 响应就返回」的场景，通常没有转发规则）。
    否则走盲隧道透传，不解密。
    """
    with Session(engine) as session:
        rules = session.exec(
            select(MockProxyRule).where(MockProxyRule.enabled == True)
        ).all()
        if any(match_host(r.host_pattern, host) for r in rules):
            return True
        configs = session.exec(
            select(MockConfig).where(MockConfig.enabled == True)
        ).all()

    from app.services.mock_matcher import parse_address

    for config in configs:
        for url in getattr(config, "proxy_urls", None) or []:
            parsed = parse_address(url)
            if parsed is not None and parsed.host is not None and match_host(parsed.host, host):
                return True
    return False


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def _save_proxy_log(config_name: str, method: str, path: str, headers: dict,
                    query: dict, body: Optional[str], status_code: int,
                    resp_headers, resp_body: Optional[str], matched: bool,
                    config_id: Optional[int] = None):
    """写入 MockLog；失败仅告警不影响转发。"""
    try:
        with Session(engine) as session:
            log = MockLog(
                config_id=config_id,
                config_name=config_name,
                request_method=method,
                request_path=path,
                request_headers=[{"key": k, "value": v} for k, v in headers.items()],
                request_query_params=json.dumps(query or {}, ensure_ascii=False),
                request_body=body,
                response_status_code=status_code,
                response_headers=list(resp_headers or []),
                response_body=resp_body,
                matched=matched,
                user_id=None,
            )
            session.add(log)
            session.commit()
    except Exception as exc:
        logger.warning("Failed to save proxy log: %s", exc)


# ---------------------------------------------------------------------------
# 上游透传
# ---------------------------------------------------------------------------

async def forward_upstream(method: str, scheme: str, host: str, port: Optional[int],
                           path: str, query: dict, headers: dict, body: Optional[bytes]):
    """把请求原样转发到真实上游，返回 (status_code, headers_dict, body_bytes) 或抛异常。"""
    netloc = f"{host}:{port}" if port and port not in (80, 443) else host
    url = f"{scheme}://{netloc}{path}"
    if query:
        from urllib.parse import urlencode
        url += "?" + urlencode(query)

    fwd_headers = {
        k: v for k, v in headers.items()
        if k.lower() not in _HOP_BY_HOP_HEADERS
    }

    client = _get_http_client()
    resp = await client.request(method.upper(), url, headers=fwd_headers,
                                content=body if body is not None else None)
    content = resp.content
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _RESPONSE_STRIP_HEADERS}
    return resp.status_code, resp_headers, content


def build_error_response(status_code: int, reason: str) -> tuple[int, bytes, str]:
    """构造给调用方的错误响应。(status_code, body_bytes, content_type)"""
    body = f"Mock Proxy Error: {reason}".encode()
    return status_code, body, "text/plain; charset=utf-8"


# ---------------------------------------------------------------------------
# 明文 HTTP 请求管线（MITM 解密后的请求也走这里）
# ---------------------------------------------------------------------------

class ParsedRequest:
    def __init__(self, method: str, scheme: str, host: str, port: Optional[int],
                 path: str, query: dict, headers: dict, body: Optional[bytes]):
        self.method = method
        self.scheme = scheme
        self.host = host
        self.port = port
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body


def parse_request_line(line: str) -> Optional[tuple[str, str]]:
    """解析请求行 'METHOD URI HTTP/1.1' → (method, uri)。"""
    parts = line.split(" ")
    if len(parts) < 3:
        return None
    return parts[0].upper(), parts[1]


def parse_absolute_uri(uri: str) -> tuple[str, str, Optional[int], str, dict]:
    """解析绝对 URI（正向代理形式）→ (scheme, host, port, path_with_query, query_dict)。"""
    if not uri.startswith(("http://", "https://")):
        # 相对形式（MITM 解密后常见）：默认 https 由调用方传入 scheme 决定，这里兜底 http
        split = urlsplit("http://" + uri.lstrip("/")) if not uri.startswith("/") else urlsplit(f"http://placeholder{uri}")
        from urllib.parse import parse_qsl
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        return "http", "placeholder", None, split.path or "/", query

    split = urlsplit(uri)
    port = split.port
    if port is None:
        port = 443 if split.scheme == "https" else 80
    from urllib.parse import parse_qsl
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    return split.scheme, split.hostname or "", port, split.path or "/", query


async def read_http_message(reader: asyncio.StreamReader, first_line: str,
                            initial_data: bytes = b"") -> Optional[ParsedRequest]:
    """从流中读取完整的 HTTP 请求（头部 + 可能的 body），组装为 ParsedRequest。

    initial_data 是已读出但属于本请求的多余字节（一般没有）。
    """
    method, uri = first_line.split(" ", 1)[0], first_line.split(" ")[1]

    raw_headers = bytearray(initial_data)
    header_end = -1
    while True:
        chunk = await reader.readuntil(b"\r\n\r\n") if b"\r\n\r\n" not in bytes(raw_headers) else b""
        raw_headers.extend(chunk)
        idx = bytes(raw_headers).find(b"\r\n\r\n")
        if idx != -1:
            header_end = idx
            break
        if len(raw_headers) > 1024 * 512:
            logger.warning("Proxy request headers too large")
            return None

    head_bytes = bytes(raw_headers[:header_end])
    extra = bytes(raw_headers[header_end + 4:])

    lines = head_bytes.decode("utf-8", errors="replace").split("\r\n")
    headers = {}
    for hline in lines[1:]:
        if ":" in hline:
            k, _, v = hline.partition(":")
            headers[k.strip()] = v.strip()

    content_length = int(headers.get("Content-Length") or headers.get("content-length") or 0)
    is_chunked = "chunked" in (headers.get("Transfer-Encoding") or headers.get("transfer-encoding") or "").lower()

    if is_chunked:
        # 读完整 chunked body
        body_chunks = bytearray(extra)
        while True:
            size_line = await reader.readuntil(b"\r\n")
            try:
                size = int(size_line.strip().split(b";")[0], 16)
            except ValueError:
                break
            if size == 0:
                await reader.readuntil(b"\r\n")  # 尾部 CRLF
                break
            data = await reader.readexactly(size)
            body_chunks.extend(data)
            await reader.readexactly(2)  # chunk 结尾 CRLF
        body = bytes(body_chunks)
    elif content_length > 0:
        need = content_length - len(extra)
        if need > 0:
            extra += await reader.readexactly(need)
        body = extra[:content_length]
    else:
        body = extra if extra else None

    scheme, host, port, path_with_query, query = parse_absolute_uri(uri)
    return ParsedRequest(method, scheme, host, port, path_with_query.split("?")[0], query, headers, body)


async def handle_plain_request(req: ParsedRequest, writer: asyncio.StreamWriter):
    """明文请求处理管线：规则/接口地址匹配 → Mock / 透传，并写日志。

    拦截判定顺序：
    1. 命中转发规则（主机 + 路径范围）→ 走 Mock 匹配链；
    2. 未命中规则，但该接口地址被某条 Mock 配置的 proxy_urls 显式绑定
       （用户「配了接口地址 + 响应，下次监听到就返回」）→ 同样走 Mock；
    3. 都不满足 → 透传真实服务。
    """
    display_path = f"{req.host}:{req.port or ''}{req.path}"
    body_text = req.body.decode("utf-8", errors="replace") if req.body else None
    request_url = f"{req.scheme}://{display_path}"
    try:
        rule = find_matching_rule(req.host, req.path)
        address_bound = rule is None and has_address_binding(req.host, req.path)

        if rule is not None or address_bound:
            # 复用平台同一套 Mock 匹配链；save_log=False 由代理统一写一条完整日志
            from app.routes.mock_server import resolve_mock_response

            service_prefix = (rule.service_prefix or "") if rule is not None else ""
            candidates = path_candidates(req.path, service_prefix)
            if rule is not None:
                rule_name = (rule.name or "").strip() or f"规则{rule.id}"
                label = f"[代理→Mock:{rule_name}]"
            else:
                label = "[代理→Mock:接口地址]"
            resolution = None
            resolution_error = None
            try:
                resolution = await resolve_mock_response(
                    req.path, req.method, req.query, body_text, dict(req.headers),
                    host=req.host, path_candidates=candidates,
                    log_label=label, save_log=False,
                )
            except Exception as exc:  # 解析失败不应让请求直接 502，退回透传
                resolution_error = exc
                logger.warning("Proxy mock resolution failed, fallback to upstream: %s", exc)

            if resolution is not None:
                resp_headers_out = {"Content-Type": "application/json"}
                for h in resolution.headers:
                    if isinstance(h, dict) and h.get("key"):
                        resp_headers_out[h["key"]] = h["value"]
                resp_body = (resolution.body or "").encode()
                await _write_response(writer, resolution.status_code, resp_headers_out, resp_body)
                _save_proxy_log(
                    config_name=resolution.config_name,
                    method=req.method, path=request_url,
                    headers=dict(req.headers), query=req.query,
                    body=body_text, status_code=resolution.status_code,
                    resp_headers=[{"key": k, "value": v} for k, v in resp_headers_out.items()],
                    resp_body=(resolution.body or "")[:65536],
                    matched=True, config_id=resolution.config_id,
                )
                return

            if resolution_error is not None:
                _save_proxy_log(
                    f"[代理错误] {label}", req.method, request_url,
                    dict(req.headers), req.query, body_text, 0, {}, str(resolution_error)[:65536], False,
                )

        # 未命中规则（或 Mock 链无匹配）→ 透传真实服务
        try:
            status_code, up_headers, up_body = await forward_upstream(
                req.method, req.scheme, req.host, req.port,
                req.path, req.query, dict(req.headers), req.body
            )
            await _write_response(writer, status_code, up_headers, up_body)
            body_str = None
            try:
                body_str = up_body.decode("utf-8") if up_body else None
            except UnicodeDecodeError:
                body_str = f"<binary {len(up_body)} bytes>"
            _save_proxy_log(
                config_name="[代理透传]" if rule is None else (
                    f"[代理→透传:{(rule.name or '').strip() or f'规则{rule.id}'}]"
                ),
                method=req.method, path=f"{req.scheme}://{display_path}",
                headers=dict(req.headers), query=req.query,
                body=req.body.decode("utf-8", errors="replace") if req.body else None,
                status_code=status_code, resp_headers=up_headers,
                resp_body=(body_str or "")[:65536], matched=False,
            )
        except (httpx.TimeoutException, asyncio.TimeoutError):
            code, err_body, ctype = build_error_response(504, f"upstream timeout: {display_path}")
            await _write_response(writer, code, {"Content-Type": ctype}, err_body)
            _save_proxy_log("[代理超时]", req.method, request_url,
                            dict(req.headers), req.query, None, code, {}, err_body.decode(), False)
        except Exception as exc:
            code, err_body, ctype = build_error_response(502, f"upstream error: {exc}")
            await _write_response(writer, code, {"Content-Type": ctype}, err_body)
            _save_proxy_log("[代理错误]", req.method, request_url,
                            dict(req.headers), req.query, None, code, {}, err_body.decode(), False)
    except Exception as exc:
        logger.exception("handle_plain_request failed: %s", exc)
        try:
            code, err_body, ctype = build_error_response(502, str(exc))
            await _write_response(writer, code, {"Content-Type": ctype}, err_body)
        except Exception:
            pass


async def _write_response(writer: asyncio.StreamWriter, status_code: int,
                          headers: dict, body: bytes):
    """写回一个完整的 HTTP/1.1 响应并保持连接（由客户端决定关闭）。"""
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 502: "Bad Gateway",
              504: "Gateway Timeout"}.get(status_code, "OK")
    lines = [f"HTTP/1.1 {status_code} {reason}"]
    has_length = any(k.lower() == "content-length" for k in headers)
    for k, v in (headers or {}).items():
        if k.lower() in ("transfer-encoding",):
            continue
        lines.append(f"{k}: {v}")
    if not has_length:
        lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", errors="replace")
    writer.write(head + (body or b""))
    await writer.drain()


# ---------------------------------------------------------------------------
# CONNECT / MITM
# ---------------------------------------------------------------------------

def _make_mitm_ssl_context(hostname: str) -> ssl.SSLContext:
    """为主机动态签发证书并构建 server 端 SSLContext。失败抛异常由上层降级。"""
    import os
    import tempfile

    from app.services.proxy_cert import get_cert_for_host

    pem = get_cert_for_host(hostname)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as tmp:
        tmp.write(pem)
        tmp_path = tmp.name
    try:
        ctx.load_cert_chain(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return ctx


async def _blind_tunnel(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                        host: str, port: int):
    """双向盲转发 TCP 字节流（不解密）。"""
    try:
        up_reader, up_writer = await asyncio.open_connection(host, port)
    except Exception as exc:
        logger.warning("Blind tunnel connect failed %s:%s: %s", host, port, exc)
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()

    async def pump(src, dst):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except Exception:
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass

    await asyncio.gather(pump(reader, up_writer), pump(up_reader, writer))


async def handle_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                         authority: str):
    """处理 CONNECT host:port 请求。"""
    host, _, port_str = authority.partition(":")
    port = int(port_str) if port_str.isdigit() else 443
    host_clean = unquote(host).strip().lower().rstrip(".")

    if not host_has_any_rule(host_clean):
        await _blind_tunnel(reader, writer, host_clean, port)
        return

    # 命中规则 → MITM
    try:
        await _write_simple(writer, b"HTTP/1.1 200 Connection established\r\n\r\n")
        ssl_ctx = _make_mitm_ssl_context(host_clean)
        loop = asyncio.get_running_loop()
        # 创建全新的 StreamReader/Protocol 承接 TLS 升级：
        # start_tls 完成后新 protocol 已自动挂在升级后的 transport 上，
        # 解密数据会流入 new_reader（复用旧 protocol 在 Proactor 下不工作）
        new_reader = asyncio.StreamReader()
        new_protocol = asyncio.StreamReaderProtocol(new_reader)
        tls_transport = await loop.start_tls(
            writer.transport, new_protocol, ssl_ctx, server_side=True,
        )
        new_writer = asyncio.StreamWriter(tls_transport, new_protocol, new_reader, loop)
        reader = new_reader  # 后续循环改从新 reader 读解密后的请求
    except Exception as exc:
        logger.warning("MITM handshake failed for %s, falling back to blind tunnel: %s", host_clean, exc)
        try:
            await _blind_tunnel_after_200(reader, writer, host_clean, port)
        except Exception:
            pass
        return

    # TLS 已建立，后续是解密后的明文 HTTP 请求（可能多个 keep-alive 请求）
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            request_line = line.decode("utf-8", errors="replace").strip()
            if not request_line:
                continue
            parsed_first = parse_request_line(request_line)
            if parsed_first is None:
                break
            req = await read_http_message(reader, request_line)
            if req is None:
                break
            # MITM 解密后的请求目标是真实主机
            req.scheme = "https"
            req.host = host_clean
            req.port = port
            await handle_plain_request(req, new_writer)
            if (req.headers.get("Connection", "").lower() == "close"):
                break
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    except Exception as exc:
        logger.exception("MITM pipeline error for %s: %s", host_clean, exc)
    finally:
        try:
            new_writer.close()
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass


async def _blind_tunnel_after_200(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                                  host: str, port: int):
    """已经回复过 200 之后建立盲隧道（用于 MITM 握手失败的降级）。"""
    try:
        up_reader, up_writer = await asyncio.open_connection(host, port)
    except Exception as exc:
        logger.warning("Fallback blind tunnel failed %s:%s: %s", host, port, exc)
        return

    async def pump(src, dst):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except Exception:
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass

    await asyncio.gather(pump(reader, up_writer), pump(up_reader, writer))


async def _write_simple(writer: asyncio.StreamWriter, data: bytes):
    writer.write(data)
    await writer.drain()


# ---------------------------------------------------------------------------
# 连接入口 & 服务生命周期
# ---------------------------------------------------------------------------

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """每个客户端连接的入口。"""
    peer = writer.get_extra_info("peername")
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=60)
        if not line:
            return
        request_line = line.decode("utf-8", errors="replace").strip()
        if not request_line:
            return

        parts = request_line.split(" ")
        if len(parts) >= 2 and parts[0].upper() == "CONNECT":
            await handle_connect(reader, writer, parts[1])
            return

        # 普通明文 HTTP 代理请求（绝对 URI 形式）
        if len(parts) >= 3 and parts[2].upper().startswith("HTTP/"):
            req = await read_http_message(reader, request_line)
            if req is None:
                return
            await handle_plain_request(req, writer)
            return

        # 不是代理形式的请求
        await _write_simple(writer, b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    except asyncio.LimitOverrunError:
        logger.warning("Client %s sent oversized line", peer)
    except Exception:
        logger.exception("handle_client error from %s", peer)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def start_proxy(port: int) -> bool:
    """启动代理监听。已在运行返回 False。线程安全（事件循环内）。"""
    global state
    async with _start_lock:
        if state.running:
            return False
        server = await asyncio.start_server(handle_client, host="0.0.0.0", port=port)
        state.server = server
        state.running = True
        state.port = port
        logger.info("Mock proxy started on 0.0.0.0:%d", port)
        return True


async def stop_proxy():
    """停止代理监听。未运行则忽略。"""
    global state
    async with _start_lock:
        if not state.running:
            return
        if state.server is not None:
            state.server.close()
            await state.server.wait_closed()
        state.server = None
        state.running = False
        state.port = None
        logger.info("Mock proxy stopped")


def get_proxy_state() -> dict:
    """当前状态快照（供管理 API）。"""
    return {"running": state.running, "port": state.port}
