import asyncio
import json
import logging
import os
import re
import signal
import time
from typing import Optional, List

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from utils.lanhu_mcp_adapter import MCPClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])

# MCP 工具列表 TTL 缓存：{cache_key: (timestamp, result)}
_tools_cache: dict[str, tuple[float, dict]] = {}
_TOOLS_CACHE_TTL = 30  # 缓存 30 秒


def _make_cache_key(server: "McpServerConfig") -> str:
    """为服务器配置生成缓存键。"""
    if server.type == "http":
        return f"http|{server.url}"
    elif server.type == "stdio":
        return f"stdio|{server.command}|{' '.join(server.args)}"
    return f"{server.type}|{server.name}"


def _get_cached_result(server: "McpServerConfig") -> dict | None:
    """从缓存获取工具列表结果。"""
    key = _make_cache_key(server)
    cached = _tools_cache.get(key)
    if cached:
        ts, result = cached
        if time.monotonic() - ts < _TOOLS_CACHE_TTL:
            logger.info(f"MCP 缓存命中: {server.name} (key={key})")
            return result
        else:
            del _tools_cache[key]
    return None


def _set_cached_result(server: "McpServerConfig", result: dict):
    """缓存工具列表结果。"""
    key = _make_cache_key(server)
    _tools_cache[key] = (time.monotonic(), result)
    # 清理过期缓存项（惰性，每隔几次设置触发）
    if len(_tools_cache) > 50:
        now = time.monotonic()
        stale = [k for k, (ts, _) in _tools_cache.items() if now - ts > _TOOLS_CACHE_TTL * 2]
        for k in stale:
            del _tools_cache[k]


class McpServerConfig:
    def __init__(self, name: str, type: str, url: Optional[str] = None,
                 command: Optional[str] = None, args: Optional[list[str]] = None,
                 timeout: Optional[int] = 60, env: Optional[list[dict[str, str]]] = None,
                 enabled: bool = True, enabled_tools: Optional[list[str]] = None,
                 **kwargs):
        self.name = name
        self.type = type
        self.url = url
        self.command = command
        self.args = args or []
        self.timeout = timeout or 60
        self.env = env or []
        self.enabled = enabled
        self.enabled_tools = enabled_tools  # None 或工具名列表，None=全部启用


def _build_env(server: McpServerConfig) -> dict | None:
    """合并父进程环境与服务器环境变量"""
    if not server.env:
        return None
    env = dict(os.environ)
    for e in server.env:
        if e.get("key"):
            env[e["key"]] = e.get("value", "")
    return env


def _safe_kill(proc):
    """安全终止进程及其子进程（使用进程组，避免孤儿进程）。"""
    if proc is None or proc.returncode is not None:
        return
    try:
        # 优先 kill 整个进程组（避免 npx 的子进程变成孤儿进程继续运行）
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
        return
    except AttributeError:
        pass  # Windows: os.getpgid/os.killpg 不可用
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


async def _read_initial_stream(stream, timeout_sec: float = 3.0) -> str:
    """从进程流读取启动输出。读到第一行后后续读取只用短超时，避免空等。"""
    lines = []
    first = True
    try:
        while True:
            to = timeout_sec if first else 0.3
            try:
                line = await asyncio.wait_for(stream.readline(), timeout=to)
            except asyncio.TimeoutError:
                break
            if not line:
                break
            text = line.decode(errors="replace").rstrip()
            if text:
                lines.append(text)
                first = False
    except Exception:
        pass
    return "\n".join(lines)


async def _detect_http_url_from_stdout(stdout_data: str) -> str | None:
    """从进程 stdout 启动输出中提取 HTTP URL"""
    if not stdout_data:
        return None

    # 尝试解析整段输出为 JSON port 宣告
    for line in stdout_data.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                port = data.get("port") or data.get("serverPort") or data.get("listenPort")
                if isinstance(port, (int, float)) and 0 < port < 65536:
                    return f"http://localhost:{int(port)}/mcp"
            elif isinstance(data, (int, float)) and 0 < data < 65536:
                return f"http://localhost:{int(data)}/mcp"
        except (json.JSONDecodeError, ValueError):
            pass

        # 查找 http URL 模式
        url_match = re.search(r'(https?://localhost(?::\d+)?(?:/[^\s,;)]*)?)', line)
        if url_match:
            return url_match.group(1)

        # 查找 "listening on port N" / "running on port N" / "port N" 在行首附近
        port_match = re.search(
            r'(?:listening\s+(?:on|at|:)\s*(?:port\s+)?|running\s+(?:on|at|:)\s*(?:port\s+)?|port\s*[:：=]\s*)(\d{4,5})',
            line, re.IGNORECASE
        )
        if port_match:
            return f"http://localhost:{port_match.group(1)}/mcp"

    return None


async def _fetch_tools_via_http(url: str, server: McpServerConfig) -> dict:
    """通过 HTTP 连接 MCP 服务器并获取工具列表"""
    # 尝试多个 URL 变体（MCP 服务器可能使用 /mcp, /message 等路径而非 /sse）
    urls_to_try = [url]
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url)
    if parsed.path and parsed.path not in ("/mcp", "/messages", "/", ""):
        base = urlunparse((parsed.scheme, parsed.netloc, "", None, None, None))
        urls_to_try.append(f"{base}/mcp")
        urls_to_try.append(f"{base}/messages")
        urls_to_try.append(f"{base}/message")

    errors = []
    for attempt_url in urls_to_try:
        client = MCPClient()
        try:
            ok = await client.connect(attempt_url)
            if ok:
                return {
                    "name": server.name,
                    "type": "http",
                    "url": attempt_url,
                    "available": True,
                    "tools": [
                        {"name": t["name"], "description": t.get("description", "")}
                        for t in client.tools
                    ],
                }
            await client.disconnect()
        except Exception as e:
            errors.append(f"{attempt_url}: {e}")
            await client.disconnect()
            continue

    return {
        "name": server.name,
        "type": "http",
        "url": url,
        "available": False,
        "tools": [],
        "error": f"MCP 连接失败: {'; '.join(errors)}" if errors else "HTTP 连接失败",
    }


def _parse_stdio_initialize(text: str) -> dict | None:
    """检查文本是否包含 JSON-RPC initialize 响应。

    纯 STDIO 服务器不输出 HTTP 端口，但会在 stdout 输出初始化响应。
    检测到后可直接进入 STDIO 协议流程。
    """
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                rid = data.get("id")
                method = data.get("method")
                # initialize 响应: {"jsonrpc":"2.0","id":"initialize","result":{...}}
                # 或者 server 推送: {"jsonrpc":"2.0","method":"...",...}
                if rid == "initialize" and "result" in data:
                    return data
        except (json.JSONDecodeError, ValueError):
            continue
    return None


async def _send_stdio_request(
    proc, server_name: str, method: str, params: Optional[dict] = None, timeout_sec: float = 60
) -> dict | None:
    """通过 STDIO 发送 JSON-RPC 请求并读取响应。"""
    req = json.dumps({
        "jsonrpc": "2.0",
        "id": method,
        "method": method,
        "params": params or {},
    }, ensure_ascii=False)

    if proc.returncode is not None:
        logger.warning(f"STDIO[{server_name}] 进程已退出，跳过请求: {method}")
        return None

    proc.stdin.write((req + "\n").encode())
    await proc.stdin.drain()

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 3.0))
        except asyncio.TimeoutError:
            if proc.returncode is not None:
                break
            continue
        if not line:
            break
        stripped = line.decode(errors="replace").strip()
        if not stripped:
            continue
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            logger.debug(f"STDIO[{server_name}] 非 JSON 行（跳过）: {stripped[:100]}")
            continue
    return None


async def _list_tools_stdio(server: McpServerConfig) -> dict:
    """通过 STDIO 协议连接 MCP 服务器并获取工具列表。

    如果进程 stdout 输出了 HTTP 端口/URL（如 feishu-mcp --port 0），
    则自动切换到 HTTP 传输。
    """
    t0 = time.monotonic()
    logger.info("[耗时] _list_tools_stdio 开始: %s %s %s", server.name, server.command, server.args)
    if not server.command:
        return {"name": server.name, "type": "stdio", "available": False, "tools": [], "error": "命令为空"}

    env = _build_env(server)

    try:
        proc = await asyncio.create_subprocess_exec(
            server.command,
            *server.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        logger.info("[耗时] _list_tools_stdio 进程启动: %.2fs %s", time.monotonic() - t0, server.name)
    except FileNotFoundError:
        return {"name": server.name, "type": "stdio", "available": False, "tools": [],
                "error": f"命令不存在: {server.command}"}
    except Exception as e:
        return {"name": server.name, "type": "stdio", "available": False, "tools": [], "error": str(e)}

    # 用于收集 stderr 的列表
    stderr_lines: list[str] = []

    try:
        deadline = time.monotonic() + min(server.timeout, 60)

        # 策略：一次性写入 initialize 请求，然后在轮询循环中同时检测：
        #   1) HTTP 端口宣告（从 stdout 或 stderr）
        #   2) JSON-RPC initialize 响应（从 stdout）
        # 谁先出现就用谁，避免分阶段带来的竞态条件

        # 先写入 initialize 请求到 stdin（服务器就绪后会自动处理）
        init_req = json.dumps({
            "jsonrpc": "2.0", "id": "initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ai-testcase-backend", "version": "1.0.0"},
            },
        }, ensure_ascii=False)
        proc.stdin.write((init_req + "\n").encode())
        await proc.stdin.drain()
        logger.info("[耗时] STDIO initialize 已写入: %.2fs %s", time.monotonic() - t0, server.name)

        # 初始化状态
        stdio_initialized = False  # STDIO 初始化是否完成
        http_url = None
        empty_rounds = 0
        init_written = True  # initialize 已写入标记

        while time.monotonic() < deadline and not stdio_initialized and not http_url:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            if empty_rounds > 5:
                await asyncio.sleep(2.0)

            to = min(remaining, 3.0 if empty_rounds <= 3 else 1.0)
            chunk_stdout, chunk_stderr = await asyncio.gather(
                _read_initial_stream(proc.stdout, timeout_sec=to),
                _read_initial_stream(proc.stderr, timeout_sec=to),
            )

            # 检查 stdout：HTTP 端口 或 JSON-RPC 响应
            if chunk_stdout:
                empty_rounds = 0
                logger.info(f"STDIO[{server.name}] stdout: {chunk_stdout[:300]}")
                http_url = await _detect_http_url_from_stdout(chunk_stdout)
                if not http_url:
                    stdio_init = _parse_stdio_initialize(chunk_stdout)
                    if stdio_init:
                        logger.info("[耗时] STDIO 检测到init响应: %.2fs %s",
                                   time.monotonic() - t0, server.name)
                        stdio_initialized = True

            # 检查 stderr：HTTP 端口
            if not http_url and chunk_stderr:
                empty_rounds = 0
                logger.info(f"STDIO[{server.name}] stderr: {chunk_stderr[:200]}")
                http_url = await _detect_http_url_from_stdout(chunk_stderr)
                stderr_lines.append(f"<startup> {chunk_stderr}")

            # HTTP 发现优先
            if http_url:
                logger.info("[耗时] STDIO 发现 HTTP 端口: %.2fs %s url=%s",
                           time.monotonic() - t0, server.name, http_url)
                return await _fetch_tools_via_http(http_url, server)

            # STDIO 初始化完成，继续 tools/list
            if stdio_initialized:
                break

            # 进程退出检查
            if proc.returncode is not None:
                break

            if not chunk_stdout and not chunk_stderr:
                empty_rounds += 1
                if empty_rounds <= 3:
                    await asyncio.sleep(0.3)
                # 连续多轮无输出且 init 已写入但无响应，重发一次 init
                if empty_rounds == 8 and init_written:
                    logger.info("[耗时] STDIO 重发initialize: %.2fs %s", time.monotonic() - t0, server.name)
                    proc.stdin.write((init_req + "\n").encode())
                    await proc.stdin.drain()

        # 检查结果
        process_exited = proc.returncode is not None

        # STDIO 模式：发送 notification 并获取 tools/list
        if stdio_initialized:
            notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}, ensure_ascii=False)
            proc.stdin.write((notif + "\n").encode())
            await proc.stdin.drain()
            t_list = time.monotonic()
            tools_result = await _send_stdio_request(proc, server.name, "tools/list", None,
                                                     timeout_sec=min(30.0, remaining if remaining > 0 else 30.0))
            logger.info("[耗时] STDIO->tools/list: %.2fs %s", time.monotonic() - t_list, server.name)
            if tools_result:
                tools = tools_result.get("result", {}).get("tools", [])
                logger.info("[耗时] STDIO 总耗时: %.2fs %s 工具数=%d",
                            time.monotonic() - t0, server.name, len(tools))
                return {
                    "name": server.name, "type": "stdio",
                    "command": f"{server.command} {' '.join(server.args)}",
                    "available": True,
                    "tools": [{"name": t["name"], "description": t.get("description", "")} for t in tools],
                }
            return {"name": server.name, "type": "stdio", "available": False, "tools": [], "error": "tools/list 无响应"}

        # 无结果
        if process_exited:
            stderr_text = "\n".join(stderr_lines)
            error_msg = f"进程已退出 (code={proc.returncode})"
            if stderr_text:
                error_msg += f": {stderr_text}"
            logger.warning(f"STDIO[{server.name}] {error_msg}")
            return {"name": server.name, "type": "stdio", "available": False, "tools": [], "error": error_msg}

        logger.info("[耗时] STDIO 未检测到响应: %.2fs %s empty=%d",
                    time.monotonic() - t0, server.name, empty_rounds)
        return {"name": server.name, "type": "stdio", "available": False, "tools": [], "error": "STDIO 连接超时"}
    except asyncio.TimeoutError:
        logger.info("[耗时] _list_tools_stdio 超时: %.2fs %s", time.monotonic() - t0, server.name)
        return {"name": server.name, "type": "stdio", "available": False, "tools": [], "error": f"请求超时 ({server.timeout}s)"}
    except Exception as e:
        logger.info("[耗时] _list_tools_stdio 异常: %.2fs %s err=%s", time.monotonic() - t0, server.name, e)
        return {"name": server.name, "type": "stdio", "available": False, "tools": [], "error": str(e)}
    finally:
        if proc and proc.returncode is None:
            _safe_kill(proc)
            await proc.wait()


async def _fetch_server_tools(server: McpServerConfig) -> dict:
    """连接单个 MCP 服务器并获取工具列表（带缓存）。"""
    t0 = time.monotonic()
    server_label = f"{server.name}({server.type}:{server.url or server.command})"
    logger.info("[耗时] _fetch_server_tools 开始: %s", server_label)

    # 检查缓存
    cached = _get_cached_result(server)
    if cached:
        logger.info("[耗时] _fetch_server_tools 缓存命中: %.2fs %s", time.monotonic() - t0, server_label)
        return cached

    if server.type == "http" and server.url:
        try:
            client = MCPClient()
            ok = await client.connect(server.url)
            if ok:
                result = {
                    "name": server.name,
                    "url": server.url,
                    "available": True,
                    "tools": [
                        {"name": t["name"], "description": t.get("description", "")}
                        for t in client.tools
                    ],
                }
            else:
                result = {
                    "name": server.name,
                    "url": server.url,
                    "available": False,
                    "tools": [],
                    "error": "连接失败",
                }
            await client.disconnect()
        except Exception as e:
            logger.error(f"获取 MCP 工具列表失败: {server.name} - {e}")
            result = {
                "name": server.name,
                "url": server.url,
                "available": False,
                "tools": [],
                "error": str(e),
            }
    elif server.type == "stdio":
        result = await _list_tools_stdio(server)
    else:
        result = {
            "name": server.name,
            "type": server.type,
            "available": False,
            "tools": [],
            "error": "不支持的传输类型",
        }

    logger.info("[耗时] _fetch_server_tools 结束: %.2fs %s available=%s",
                time.monotonic() - t0, server_label, result.get("available"))
    # 成功的才缓存（失败不缓存，下次可重试）
    if result.get("available"):
        _set_cached_result(server, result)
    return result


@router.post("/list-tools")
async def list_mcp_tools(servers: list[dict], user=Depends(get_current_user)):
    """连接 MCP 服务器并获取其工具/方法列表（并行连接 + 缓存）。"""
    t0 = time.monotonic()
    configs = [McpServerConfig(**s) for s in servers]
    logger.info("[耗时] list_mcp_tools 开始: %d 个服务器", len(configs))
    results = await asyncio.gather(*[_fetch_server_tools(s) for s in configs])
    logger.info("[耗时] list_mcp_tools 总耗时: %.2fs %d 个服务器", time.monotonic() - t0, len(configs))
    return {"code": 200, "message": "ok", "data": list(results)}


# ---- MCP 服务器配置持久化 CRUD ----

from app.deps import SessionDep, CurrentUser
from db.models import McpServer
from sqlmodel import select
from utils.base_response import Response


@router.get("/servers", response_model=Response[List[McpServer]])
def list_mcp_servers(session: SessionDep, user: CurrentUser):
    """获取当前用户的所有持久化 MCP 服务器配置"""
    servers = session.exec(
        select(McpServer)
        .where(McpServer.user_id == user.user_id)
        .order_by(McpServer.created_at.desc())
    ).all()
    return Response(data=servers)


@router.post("/servers", response_model=Response[McpServer])
def create_mcp_server(session: SessionDep, user: CurrentUser, server: McpServer):
    """创建 MCP 服务器配置"""
    server.user_id = user.user_id
    session.add(server)
    session.commit()
    session.refresh(server)
    return Response(data=server, message="MCP 服务器配置已创建")


@router.put("/servers/{server_id}", response_model=Response[McpServer])
def update_mcp_server(session: SessionDep, user: CurrentUser, server_id: int, server: McpServer):
    """更新 MCP 服务器配置"""
    db_server = session.get(McpServer, server_id)
    if not db_server:
        return Response(code=404, message="MCP 服务器配置不存在")
    if db_server.user_id != user.user_id:
        return Response(code=403, message="无权修改此配置")
    server_data = server.model_dump(exclude_unset=True)
    server_data.pop("created_at", None)
    server_data.pop("updated_at", None)
    server_data.pop("user_id", None)
    db_server.sqlmodel_update(server_data)
    session.add(db_server)
    session.commit()
    session.refresh(db_server)
    return Response(data=db_server, message="MCP 服务器配置已更新")


@router.delete("/servers/{server_id}", response_model=Response)
def delete_mcp_server(session: SessionDep, user: CurrentUser, server_id: int):
    """删除 MCP 服务器配置"""
    db_server = session.get(McpServer, server_id)
    if not db_server:
        return Response(code=404, message="MCP 服务器配置不存在")
    if db_server.user_id != user.user_id:
        return Response(code=403, message="无权删除此配置")
    session.delete(db_server)
    session.commit()
    return Response(message="MCP 服务器配置已删除")
