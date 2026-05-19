"""蓝湖 MCP 适配器 - 将 lanhu-mcp MCP 服务器工具包装为 LangChain 工具。

lanhu-mcp 使用 FastMCP Streamable HTTP 传输。
该传输在 HTTP POST 请求/响应之上使用 SSE 事件格式（event: message\\ndata: {...}）。
"""

import json
import logging
import re
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _get_mcp_urls() -> list[str]:
    """从配置或默认值获取 MCP 服务器地址列表。"""
    try:
        from config import config_manager
        return [
            config_manager.get_mcp_server_url(),
            config_manager.get_mcp_server_url_fallback(),
        ]
    except Exception:
        return ["http://lanhu-mcp:8000/mcp", "http://localhost:8002/mcp"]


def _parse_sse_response(text: str) -> dict | None:
    """从 SSE 格式响应中提取 JSON 数据。

    FastMCP 返回格式：
        event: message
        data: {"jsonrpc":"2.0","id":"1","result":{...}}

    FastMCP 还会发送心跳 ping（: ping - timestamp）需要跳过。
    """
    lines = text.strip().splitlines()
    data_lines = [l for l in lines if l.startswith("data:")]
    for dl in data_lines:
        try:
            return json.loads(dl[5:].strip())
        except (json.JSONDecodeError, ValueError):
            continue
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _strip_mcp_ai_meta(text: str) -> str:
    """剥离 MCP 响应中内嵌的 AI 工作流指令元数据。

    lanhu-mcp 在响应中嵌入了大量 AI 指令（__AI_INSTRUCTION__、ai_suggestion 等），
    这些是给 MCP 自身 AI 助手的，对测试用例生成无意义且占用大量上下文。
    """
    import re as _re

    # 剥离 __AI_INSTRUCTION__ 块
    text = _re.sub(
        r'__AI_INSTRUCTION__["\']?\s*:\s*["\'].*?=== END OF DIRECTIVE.*?["\']',
        '"__AI_INSTRUCTION__":"<stripped>"',
        text,
        flags=_re.DOTALL,
    )
    # 剥离 ai_suggestion
    text = _re.sub(
        r'"ai_suggestion"\s*:\s*\{[^}]*\}',
        '"ai_suggestion":{}',
        text,
    )
    return text


# 单次 MCP 工具响应的最大字符数（预取内容直接入 prompt，不再经 ReAct 循环）
_MAX_TOOL_RESPONSE = 200000


class MCPClient:
    """MCP 客户端，支持两种传输：
    1. FastMCP Streamable HTTP (POST {} → session ID → JSON-RPC)
    2. 标准 MCP SSE (GET /sse → SSE 事件 → POST JSON-RPC 到消息端点)
    """

    def __init__(self):
        self._base_url: str | None = None
        self._session_id: str | None = None
        self._message_url: str | None = None
        self._proc = None
        self._is_stdio: bool = False
        self.available: bool = False
        self.tools: list[dict] = []

    async def connect(self, url: str | None = None) -> bool:
        """连接 MCP 服务器：创建会话、初始化、获取工具列表。

        按成功率从高到低尝试协议：
        1. 直接 JSON-RPC（简单 POST，大多数 MCP 服务器支持）
        2. Streamable HTTP（POST + session ID）
        3. MCP SSE（GET 流式传输，最复杂）
        避免串行等待所有协议超时。
        """
        urls = [url] if url else _get_mcp_urls()
        for target in urls:
            t0 = time.monotonic()
            # 先试最简单直接的 JSON-RPC（通常最快）
            t1 = time.monotonic()
            if await self._try_direct_jsonrpc(target):
                logger.info("[耗时] connect->direct_jsonrpc 成功: %.2fs target=%s", time.monotonic() - t1, target)
                return True
            logger.info("[耗时] connect->direct_jsonrpc 失败: %.2fs target=%s", time.monotonic() - t1, target)

            t2 = time.monotonic()
            if await self._connect_streamable_http(target):
                logger.info("[耗时] connect->streamable_http 成功: %.2fs target=%s", time.monotonic() - t2, target)
                return True
            logger.info("[耗时] connect->streamable_http 失败: %.2fs target=%s", time.monotonic() - t2, target)

            t3 = time.monotonic()
            if await self._connect_sse(target):
                logger.info("[耗时] connect->sse 成功: %.2fs target=%s", time.monotonic() - t3, target)
                return True
            logger.info("[耗时] connect->sse 失败: %.2fs target=%s", time.monotonic() - t3, target)

            logger.info("[耗时] connect->全部协议失败: %.2fs target=%s", time.monotonic() - t0, target)

        logger.warning("所有 MCP 地址均连接失败，MCP 不可用")
        self.available = False
        return False

    async def _connect_sse(self, target: str) -> bool:
        """使用标准 MCP SSE 传输连接（feishu-mcp 等使用此协议）。

        MCP SSE 协议流程：
        1. GET /sse → 服务端推送 SSE 事件流（首个事件包含消息端点）
        2. POST 消息端点 → 发送 JSON-RPC 请求
        3. 通过 SSE 流读取 JSON-RPC 响应
        """
        t0 = time.monotonic()
        from urllib.parse import urlparse, urlunparse
        import re as _re

        parsed = urlparse(target)
        sse_paths = ["/sse", "/events"]

        async def _read_sse_event(line_iter) -> str:
            """从 SSE 行迭代器读取一个完整的事件并返回其文本。"""
            lines = []
            try:
                async for line in line_iter:
                    lines.append(line)
                    if line == "":
                        break
            except Exception:
                pass
            return "\n".join(lines)

        def _parse_endpoint(sse_text: str) -> str | None:
            """从 SSE 事件文本中提取消息端点 URL。"""
            for match in _re.finditer(
                r'event:\s*(endpoint|message)\s*\r?\ndata:\s*([^\r\n]+)',
                sse_text, _re.IGNORECASE
            ):
                url = (match.group(2) or "").strip()
                if url:
                    return url
            return None

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for sse_path in sse_paths:
                    sse_url = urlunparse((
                        parsed.scheme, parsed.netloc, sse_path, "", "", ""
                    ))
                    t_path = time.monotonic()

                    try:
                        async with client.stream(
                            "GET", sse_url,
                            headers={"Accept": "text/event-stream"},
                        ) as sse_stream:
                            if sse_stream.status_code != 200:
                                logger.info("[耗时] SSE 非200: %.2fs url=%s", time.monotonic() - t_path, sse_url)
                                continue

                            logger.info("[耗时] SSE 连接成功: %.2fs url=%s", time.monotonic() - t_path, sse_url)

                            # 获取行迭代器（只能创建一个，多次调用会 StreamConsumed）
                            line_iter = sse_stream.aiter_lines()

                            # Step 1: 读取 endpoint 事件
                            sse_text = await _read_sse_event(line_iter)
                            logger.info(f"SSE 首个事件: {sse_text[:300]}")

                            endpoint = _parse_endpoint(sse_text)
                            if endpoint:
                                if not endpoint.startswith("http"):
                                    endpoint = urlunparse((
                                        parsed.scheme, parsed.netloc,
                                        endpoint if endpoint.startswith("/") else f"/{endpoint}",
                                        "", "", ""
                                    ))
                                message_url = endpoint
                            else:
                                message_url = target

                            logger.info(f"MCP 消息端点: {message_url}")

                            # Step 2: initialize
                            await client.post(message_url, headers=_MCP_HEADERS, json={
                                "jsonrpc": "2.0", "id": "init", "method": "initialize",
                                "params": {
                                    "protocolVersion": "2024-11-05",
                                    "capabilities": {},
                                    "clientInfo": {
                                        "name": "ai-testcase-backend",
                                        "version": "1.0.0",
                                    },
                                },
                            })
                            init_text = await _read_sse_event(line_iter)
                            init_result = _parse_sse_response(init_text)
                            if not init_result:
                                logger.warning(f"SSE MCP initialize 失败: {init_text[:200]}")
                                return False
                            logger.info("MCP initialize OK (SSE)")

                            # Step 3: notifications/initialized（无响应）
                            await client.post(message_url, headers=_MCP_HEADERS, json={
                                "jsonrpc": "2.0",
                                "method": "notifications/initialized",
                            })

                            # Step 4: tools/list
                            await client.post(message_url, headers=_MCP_HEADERS, json={
                                "jsonrpc": "2.0", "id": "tools", "method": "tools/list",
                            })
                            tools_text = await _read_sse_event(line_iter)
                            tools_result = _parse_sse_response(tools_text)
                            if not tools_result or "result" not in tools_result:
                                logger.warning(f"SSE MCP tools/list 失败: {tools_text[:200]}")
                                return False

                            self.tools = tools_result["result"].get("tools", [])
                            logger.info("[耗时] SSE 总耗时: %.2fs url=%s 工具数=%d",
                                       time.monotonic() - t0, sse_url, len(self.tools))
                            self._base_url = message_url
                            self._message_url = message_url
                            self.available = True
                            return True

                    except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
                        logger.info("[耗时] SSE 连接异常: %.2fs url=%s", time.monotonic() - t_path, sse_url)
                        continue
                    except Exception as e:
                        logger.info("[耗时] SSE 连接异常: %.2fs url=%s err=%s", time.monotonic() - t_path, sse_url, e)
                        continue

        except Exception as e:
            logger.info("[耗时] SSE 客户端异常: %.2fs target=%s err=%s", time.monotonic() - t0, target, e)
            return False

        logger.info("[耗时] SSE 全部路径失败: %.2fs target=%s", time.monotonic() - t0, target)
        return False

    async def _jsonrpc_request(
        self, client: httpx.AsyncClient, url: str, method: str, params: dict | None
    ) -> dict | None:
        """发送 JSON-RPC 请求并解析响应。"""
        payload = {
            "jsonrpc": "2.0",
            "id": method,
            "method": method,
        }
        if method != "notifications/initialized":
            payload["params"] = params or {}

        resp = await client.post(url, headers=_MCP_HEADERS, json=payload)
        result = _parse_sse_response(resp.text)
        if not result:
            logger.warning(f"MCP {method} 响应解析失败: {resp.text[:200]}")
            return None
        return result

    async def _try_direct_jsonrpc(self, target: str) -> bool:
        """直接在目标 URL 上 POST JSON-RPC（无会话管理）。"""
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                t1 = time.monotonic()
                result = await self._jsonrpc_request(client, target, "initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "ai-testcase-backend",
                        "version": "1.0.0",
                    },
                })
                logger.info("[耗时] direct_jsonrpc->initialize: %.2fs target=%s", time.monotonic() - t1, target)
                if not result:
                    return False

                await self._jsonrpc_request(
                    client, target, "notifications/initialized", None
                )

                t2 = time.monotonic()
                tools_result = await self._jsonrpc_request(
                    client, target, "tools/list", None
                )
                logger.info("[耗时] direct_jsonrpc->tools/list: %.2fs target=%s", time.monotonic() - t2, target)
                if not tools_result or "result" not in tools_result:
                    return False

                self.tools = tools_result["result"].get("tools", [])
                logger.info("[耗时] direct_jsonrpc 总耗时: %.2fs target=%s 工具数=%d",
                           time.monotonic() - t0, target, len(self.tools))
                self._base_url = target
                self._message_url = target
                self.available = True
                return True
        except Exception as e:
            logger.info("[耗时] direct_jsonrpc 异常: %.2fs target=%s err=%s", time.monotonic() - t0, target, e)
            return False

    async def _connect_streamable_http(self, target: str) -> bool:
        """使用 FastMCP Streamable HTTP 连接（旧方式：POST {} 获取 session ID）。"""
        t0 = time.monotonic()
        try:
            logger.info(f"尝试连接 MCP 服务器: {target}")
            async with httpx.AsyncClient(timeout=10) as client:
                # Step 1: 首次 POST 创建会话，获取 session ID
                t1 = time.monotonic()
                resp = await client.post(target, headers=_MCP_HEADERS, json={})
                logger.info("[耗时] streamable_http->session: %.2fs target=%s", time.monotonic() - t1, target)
                session_id = resp.headers.get("mcp-session-id")
                if not session_id:
                    logger.warning(f"MCP 未返回 session ID: {target}")
                    return False

                headers = {**_MCP_HEADERS, "mcp-session-id": session_id}

                # Step 2: initialize
                t2 = time.monotonic()
                init_payload = {
                    "jsonrpc": "2.0",
                    "id": "init-1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "ai-testcase-backend",
                            "version": "1.0.0",
                        },
                    },
                }
                resp = await client.post(target, headers=headers, json=init_payload)
                result = _parse_sse_response(resp.text)
                if not result:
                    logger.warning(f"MCP initialize 响应解析失败: {resp.text[:200]}")
                    return False
                logger.info("[耗时] streamable_http->initialize: %.2fs target=%s", time.monotonic() - t2, target)

                # Step 3: notifications/initialized
                await client.post(target, headers=headers, json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                })

                # Step 4: tools/list
                t3 = time.monotonic()
                resp = await client.post(target, headers=headers, json={
                    "jsonrpc": "2.0",
                    "id": "list-1",
                    "method": "tools/list",
                })
                tools_result = _parse_sse_response(resp.text)
                if not tools_result or "result" not in tools_result:
                    logger.warning(f"MCP tools/list 失败: {resp.text[:300]}")
                    return False
                logger.info("[耗时] streamable_http->tools/list: %.2fs target=%s", time.monotonic() - t3, target)

                self.tools = tools_result["result"].get("tools", [])
                logger.info("[耗时] streamable_http 总耗时: %.2fs target=%s 工具数=%d",
                           time.monotonic() - t0, target, len(self.tools))

            self._base_url = target
            self._session_id = session_id
            self.available = True
            return True
        except Exception as e:
            logger.info("[耗时] streamable_http 异常: %.2fs target=%s err=%s", time.monotonic() - t0, target, e)
            return False

    async def call_tool(self, name: str, arguments: dict) -> str:
        """调用 MCP 工具（支持 HTTP 和 STDIO）。"""
        if not self.available:
            return json.dumps({"error": "MCP 未连接"}, ensure_ascii=False)

        if self._is_stdio:
            return await self._call_tool_stdio(name, arguments)

        return await self._call_tool_http(name, arguments)

    async def _call_tool_http(self, name: str, arguments: dict) -> str:
        """通过 HTTP 调用 MCP 工具。"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": f"call-{name}",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
            url = self._message_url or self._base_url
            headers = dict(_MCP_HEADERS)
            if self._session_id:
                headers["mcp-session-id"] = self._session_id
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(url, headers=headers, json=payload)
                result = _parse_sse_response(resp.text)

            if not result:
                return json.dumps({"error": "空响应"}, ensure_ascii=False)
            if "error" in result:
                return json.dumps(result["error"], ensure_ascii=False)

            content = result.get("result", {}).get("content", [])
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", item.get("data", ""))))
                else:
                    parts.append(str(item))
            reply = "\n".join(parts) if parts else json.dumps(result["result"], ensure_ascii=False)
            reply = _strip_mcp_ai_meta(reply)
            if len(reply) > _MAX_TOOL_RESPONSE:
                logger.info(f"MCP {name} 响应截断: {len(reply)} -> {_MAX_TOOL_RESPONSE} chars")
                reply = reply[:_MAX_TOOL_RESPONSE] + f"\n... (截断, 原长度 {len(reply)} chars)"
            return reply
        except Exception as e:
            logger.error(f"MCP HTTP 工具调用失败: {name}, error={e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def _call_tool_stdio(self, name: str, arguments: dict) -> str:
        """通过 STDIO 调用 MCP 工具。"""
        import asyncio as _asyncio

        try:
            req = json.dumps({
                "jsonrpc": "2.0",
                "id": f"call-{name}",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }, ensure_ascii=False)
            self._proc.stdin.write((req + "\n").encode())
            await self._proc.stdin.drain()

            deadline = time.monotonic() + 300
            result = None
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    line = await _asyncio.wait_for(self._proc.stdout.readline(), timeout=remaining)
                except _asyncio.TimeoutError:
                    break
                if not line:
                    break
                stripped = line.decode().strip()
                if not stripped:
                    continue
                try:
                    result = json.loads(stripped)
                    break
                except json.JSONDecodeError:
                    continue

            if result is None:
                return json.dumps({"error": "空响应"}, ensure_ascii=False)
            if "error" in result:
                return json.dumps(result["error"], ensure_ascii=False)

            content = result.get("result", {}).get("content", [])
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", item.get("data", ""))))
                else:
                    parts.append(str(item))
            reply = "\n".join(parts) if parts else json.dumps(result["result"], ensure_ascii=False)
            reply = _strip_mcp_ai_meta(reply)
            if len(reply) > _MAX_TOOL_RESPONSE:
                logger.info(f"MCP {name} 响应截断: {len(reply)} -> {_MAX_TOOL_RESPONSE} chars")
                reply = reply[:_MAX_TOOL_RESPONSE] + f"\n... (截断, 原长度 {len(reply)} chars)"
            return reply
        except _asyncio.TimeoutError:
            return json.dumps({"error": f"工具调用超时: {name}"}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"MCP STDIO 工具调用失败: {name}, error={e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def connect_stdio(self, command: str, args: list[str] | None = None, env: dict | None = None) -> bool:
        """通过 STDIO 连接 MCP 服务器：启动进程、初始化、获取工具列表。"""
        import asyncio as _asyncio

        try:
            self._proc = await _asyncio.create_subprocess_exec(
                command,
                *(args or []),
                stdin=_asyncio.subprocess.PIPE,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
        except _asyncio.CancelledError:
            raise
        except FileNotFoundError:
            logger.warning(f"STDIO 命令不存在: {command}")
            return False
        except Exception as e:
            logger.warning(f"STDIO 进程启动失败: {e}")
            return False

        async def _send_req(method: str, params: dict | None = None) -> dict | None:
            req = json.dumps({
                "jsonrpc": "2.0",
                "id": method,
                "method": method,
                "params": params or {},
            }, ensure_ascii=False)
            self._proc.stdin.write((req + "\n").encode())
            await self._proc.stdin.drain()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    line = await _asyncio.wait_for(self._proc.stdout.readline(), timeout=remaining)
                except _asyncio.TimeoutError:
                    break
                if not line:
                    break
                stripped = line.decode().strip()
                if not stripped:
                    continue
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    logger.debug(f"STDIO 非 JSON 行（跳过）: {stripped[:100]}")
                    continue
            return None

        try:
            result = await _send_req("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ai-testcase-backend", "version": "1.0.0"},
            })
            if not result:
                self._safe_kill_proc()
                return False

            notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}, ensure_ascii=False)
            self._proc.stdin.write((notif + "\n").encode())
            await self._proc.stdin.drain()

            result = await _send_req("tools/list")
            if not result or "result" not in result:
                self._safe_kill_proc()
                return False

            self.tools = result["result"].get("tools", [])
            self._is_stdio = True
            self.available = True
            logger.info(f"STDIO MCP 连接成功: {command}, 工具数: {len(self.tools)}")
            return True
        except Exception as e:
            logger.warning(f"STDIO MCP 初始化失败: {e}")
            self._safe_kill_proc()
            return False

    def _safe_kill_proc(self):
        if self._proc is None:
            return
        if self._proc.returncode is not None:
            return
        try:
            import os as _os
            import signal as _signal
            pgid = _os.getpgid(self._proc.pid)
            _os.killpg(pgid, _signal.SIGKILL)
            return
        except AttributeError:
            pass
        except (ProcessLookupError, OSError):
            pass
        try:
            self._proc.kill()
        except ProcessLookupError:
            pass

    async def disconnect(self):
        """断开 MCP 连接。"""
        self.available = False
        self.tools = []
        self._session_id = None
        self._base_url = None
        self._safe_kill_proc()
        self._proc = None
        self._is_stdio = False


# 全局单例
_mcp_client = MCPClient()
_mcp_initialized = False


async def get_mcp_client() -> MCPClient:
    """获取 MCP 客户端（延迟初始化）。"""
    global _mcp_initialized
    if not _mcp_initialized:
        await _mcp_client.connect()
        _mcp_initialized = True
    return _mcp_client


def _make_tool_function(name: str):
    """为一个 MCP 工具名生成对应的异步调用函数。"""
    async def _call(**kwargs) -> str:
        client = await get_mcp_client()
        return await client.call_tool(name, kwargs)
    _call.__name__ = name
    _call.__qualname__ = name
    return _call


_MAX_DESC_LENGTH = 200


# 只暴露给 agent 的蓝湖工具（与测试用例生成相关的工具）
_ALLOWED_MCP_TOOLS = frozenset({
    "lanhu_resolve_invite_link",          # 解析邀请链接
    "lanhu_get_pages",                    # 获取原型页面列表（需求文档）
    "lanhu_get_ai_analyze_page_result",   # 分析原型页面内容（核心工具）
    "lanhu_get_designs",                  # 获取设计图列表
    "lanhu_get_ai_analyze_design_result", # 分析设计图内容
    "lanhu_get_design_slices",            # 获取设计切图
    "lanhu_get_members",                  # 获取项目协作者
    "lanhu_say_list",                     # 查看留言（了解需求讨论）
})


# 覆盖 MCP 工具描述，避免 MCP 自带的工作流指令（如 "FOUR-STAGE WORKFLOW"）
# 误导 agent 进入无限循环调用。只保留简洁的功能说明。
_CUSTOM_TOOL_DESCRIPTIONS = {
    "lanhu_resolve_invite_link": "解析蓝湖邀请链接为可用项目 URL，获取 tid/pid/docId 参数",
    "lanhu_get_pages": "获取蓝湖 Axure 原型页面列表（含页面名称和总数）",
    "lanhu_get_ai_analyze_page_result": "分析指定原型页面的内容。page_names 只传用户明确提到的页面名，不要一次分析所有页面。先用 text_only 快速浏览，再用 full+tester 深度分析",
    "lanhu_get_designs": "获取蓝湖 UI 设计图列表",
    "lanhu_get_ai_analyze_design_result": "分析 UI 设计图内容",
    "lanhu_get_design_slices": "获取设计图切片/图标资源下载信息",
    "lanhu_get_members": "获取项目协作者列表",
    "lanhu_say_list": "查看项目留言板消息列表",
}


async def build_langchain_tools():
    """构建 LangChain 可用的工具列表（仅包含与测试用例生成相关的蓝湖工具）。"""
    from langchain_core.tools import StructuredTool

    client = await get_mcp_client()
    if not client.available or not client.tools:
        logger.info("MCP 不可用，返回空工具列表")
        return []

    tools = []
    for t in client.tools:
        name = t["name"]
        if name not in _ALLOWED_MCP_TOOLS:
            continue
        func = _make_tool_function(name)
        desc = _CUSTOM_TOOL_DESCRIPTIONS.get(name) or (t.get("description") or f"蓝湖 MCP 工具: {name}")[:_MAX_DESC_LENGTH]
        tool = StructuredTool(
            name=name,
            description=desc,
            coroutine=func,
            args_schema=_build_schema(t.get("inputSchema", {})),
        )
        tools.append(tool)

    logger.info(f"已构建 {len(tools)} 个 MCP LangChain 工具（从 {len(client.tools)} 个中筛选）")
    return tools


def _build_schema(input_schema: dict) -> Any:
    """将 MCP JSON Schema 转换为 Pydantic 模型。"""
    from pydantic import create_model
    from pydantic.fields import FieldInfo

    if not input_schema or "properties" not in input_schema:
        return None

    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    fields = {}
    for name, prop in properties.items():
        ptype = prop.get("type", "string")
        py_type = _json_type_to_python(ptype)
        description = prop.get("description", "")
        if name in required:
            fields[name] = (py_type, FieldInfo(description=description))
        else:
            fields[name] = (Optional[py_type], FieldInfo(default=None, description=description))

    return create_model(f"{input_schema.get('title', 'MCPToolArgs')}", **fields)


def _json_type_to_python(js_type: str):
    mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }
    return mapping.get(js_type, str)


def _make_tool_function_for_client(client, name: str):
    """为指定 MCPClient 实例生成工具调用函数。"""
    async def _call(**kwargs) -> str:
        return await client.call_tool(name, kwargs)
    _call.__name__ = name
    _call.__qualname__ = name
    return _call


async def _connect_single_server(config: dict) -> tuple[str, MCPClient | None, Exception | None]:
    """连接单个 MCP 服务器，返回 (server_name, client, error)。"""
    server_name = config.get("name", "mcp-server")
    server_type = config.get("type", "http")
    client = MCPClient()

    try:
        if server_type == "http":
            url = config.get("url")
            if url:
                ok = await client.connect(url)
                if not ok:
                    await client.disconnect()
                    return server_name, None, Exception("HTTP 连接失败")
        elif server_type == "stdio":
            command = config.get("command")
            args = config.get("args") or []
            if command:
                env_list = config.get("env") or []
                env = None
                if env_list:
                    import os as _os
                    env = dict(_os.environ)
                    for e in env_list:
                        if e.get("key"):
                            env[e["key"]] = e.get("value", "")
                ok = await client.connect_stdio(command, args, env)
                if not ok:
                    await client.disconnect()
                    return server_name, None, Exception("STDIO 连接失败")
        else:
            return server_name, None, Exception(f"不支持的传输类型: {server_type}")
    except Exception as e:
        await client.disconnect()
        return server_name, None, e

    return server_name, client, None


async def build_tools_from_configs(mcp_configs: list[dict]) -> list:
    """从 MCP 服务器配置列表构建 LangChain 工具。

    支持 HTTP (Streamable HTTP) 和 STDIO 两种传输类型。
    使用 asyncio.gather 并行连接所有启用的服务器。
    """
    from langchain_core.tools import StructuredTool
    import asyncio

    if not mcp_configs:
        return []

    # 筛选启用的服务器，并行连接
    enabled_configs = [c for c in mcp_configs if c.get("enabled", True)]
    if not enabled_configs:
        return []

    results = await asyncio.gather(*[
        _connect_single_server(c) for c in enabled_configs
    ], return_exceptions=False)

    tools = []
    for (server_name, client, error), config in zip(results, enabled_configs):
        if error or client is None:
            logger.warning(f"MCP 服务器 {server_name} 不可用: {error}")
            continue
        if not client.tools:
            logger.warning(f"MCP 服务器 {server_name} 无可用工具")
            await client.disconnect()
            continue

        for t in client.tools:
            tool_name = t["name"]

            # 检查工具是否在启用列表中
            enabled_tools_list = config.get("enabled_tools")
            if enabled_tools_list is not None and isinstance(enabled_tools_list, list) and len(enabled_tools_list) > 0:
                if tool_name not in enabled_tools_list:
                    logger.info(f"MCP 服务器 {server_name}: 跳过已禁用的工具 {tool_name}")
                    continue

            desc = t.get("description", f"MCP tool: {tool_name}")
            func = _make_tool_function_for_client(client, tool_name)
            input_schema = t.get("inputSchema", {})
            args_schema = _build_schema(input_schema) if input_schema and "properties" in input_schema else None

            tools.append(StructuredTool(
                name=tool_name,
                description=desc[:200],
                coroutine=func,
                args_schema=args_schema,
            ))

        logger.info(f"MCP 服务器 {server_name}: {len(client.tools)} 个工具已加载")

    return tools
