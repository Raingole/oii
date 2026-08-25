"""WebSocket MCP aggregator compatible with Xiaozhi's MCP endpoint client."""

import asyncio
import json
import os
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets


HOST = os.getenv("MCP_AGGREGATOR_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_AGGREGATOR_PORT", "8765"))
MCP_TOKEN = os.getenv("MCP_AGGREGATOR_TOKEN", "")
DEFAULT_BACKENDS = "restaurant=ws://127.0.0.1:8766/mcp/"


def backend_configs() -> list[tuple[str, str]]:
    """Read backend definitions: name=url,name2=url2."""
    raw = os.getenv("MCP_BACKENDS", DEFAULT_BACKENDS)
    configs = []
    for item in raw.split(","):
        if "=" not in item:
            continue
        name, url = item.split("=", 1)
        if name.strip() and url.strip():
            configs.append((name.strip(), url.strip()))
    return configs


class BackendConnection:
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self.websocket = None
        self.listener_task = None
        self.pending: dict[int, asyncio.Future] = {}
        self.next_id = 1

    async def connect(self) -> list[dict[str, Any]]:
        self.websocket = await websockets.connect(self.url)
        self.listener_task = asyncio.create_task(self.listen())
        await self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "XiaozhiMCPAggregator", "version": "1.0.0"},
        })
        await self.notify("notifications/initialized")
        result = await self.request("tools/list")
        return result.get("tools", []) if isinstance(result, dict) else []

    async def listen(self) -> None:
        try:
            async for raw_message in self.websocket:
                message = json.loads(raw_message)
                request_id = message.get("id")
                if request_id in self.pending:
                    future = self.pending.pop(request_id)
                    if not future.done():
                        future.set_result(message)
        except Exception as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)
            self.pending.clear()

    async def request(self, method: str, params: dict | None = None) -> Any:
        if self.websocket is None:
            raise RuntimeError(f"MCP后端 {self.name} 未连接")
        request_id = self.next_id
        self.next_id += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        await self.websocket.send(json.dumps(payload, ensure_ascii=False))
        message = await asyncio.wait_for(future, timeout=30)
        if "error" in message:
            raise RuntimeError(message["error"].get("message", "MCP后端调用失败"))
        return message.get("result", {})

    async def notify(self, method: str) -> None:
        if self.websocket:
            await self.websocket.send(json.dumps({"jsonrpc": "2.0", "method": method}))

    async def close(self) -> None:
        if self.listener_task:
            self.listener_task.cancel()
        if self.websocket:
            await self.websocket.close()


async def send_error(websocket, request_id, code: int, message: str) -> None:
    await websocket.send(json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }, ensure_ascii=False))


async def handle(websocket) -> None:
    query = parse_qs(urlparse(websocket.request.path).query)
    if MCP_TOKEN and query.get("token", [""])[0] != MCP_TOKEN:
        await websocket.close(code=1008, reason="invalid token")
        return

    backends: list[BackendConnection] = []
    tools: dict[str, tuple[BackendConnection, str]] = {}
    initialized = False
    try:
        async for raw_message in websocket:
            request = json.loads(raw_message)
            method = request.get("method")
            request_id = request.get("id")

            if method == "initialize":
                for name, url in backend_configs():
                    backend = BackendConnection(name, url)
                    try:
                        backend_tools = await backend.connect()
                        for tool in backend_tools:
                            original_name = tool.get("name", "")
                            exposed_name = original_name
                            if exposed_name in tools:
                                exposed_name = f"{name}__{original_name}"
                            exposed_tool = dict(tool)
                            exposed_tool["name"] = exposed_name
                            if exposed_name != original_name:
                                exposed_tool["description"] = (
                                    f"[{name}] {tool.get('description', '')}"
                                )
                            tools[exposed_name] = (backend, original_name)
                            backend_tools[backend_tools.index(tool)] = exposed_tool
                        backends.append(backend)
                        print(f"[INFO] MCP后端 {name} 已连接，工具数: {len(backend_tools)}")
                    except Exception as exc:
                        print(f"[WARN] MCP后端 {name} 连接失败: {exc}", file=sys.stderr)
                        await backend.close()
                initialized = True
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "xiaozhi-mcp-aggregator", "version": "1.0.0"},
                    },
                }))
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                if not initialized:
                    await send_error(websocket, request_id, -32000, "聚合器尚未初始化")
                    continue
                visible_tools = []
                for backend in backends:
                    result = await backend.request("tools/list")
                    for tool in result.get("tools", []):
                        tool_name = next(
                            (name for name, (owner, original) in tools.items()
                             if owner is backend and original == tool.get("name")),
                            tool.get("name"),
                        )
                        item = dict(tool)
                        item["name"] = tool_name
                        visible_tools.append(item)
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": request_id, "result": {"tools": visible_tools}
                }, ensure_ascii=False))
            elif method == "tools/call":
                params = request.get("params", {})
                exposed_name = params.get("name")
                route = tools.get(exposed_name)
                if route is None:
                    await send_error(websocket, request_id, -32601, f"未知工具: {exposed_name}")
                    continue
                backend, original_name = route
                try:
                    result = await backend.request("tools/call", {
                        "name": original_name,
                        "arguments": params.get("arguments", {}),
                    })
                    await websocket.send(json.dumps({
                        "jsonrpc": "2.0", "id": request_id, "result": result
                    }, ensure_ascii=False))
                except Exception as exc:
                    await send_error(websocket, request_id, -32000, str(exc))
            elif request_id is not None:
                await send_error(websocket, request_id, -32601, f"不支持的方法: {method}")
    finally:
        await asyncio.gather(*(backend.close() for backend in backends), return_exceptions=True)


async def main() -> None:
    print(f"MCP聚合服务监听 ws://{HOST}:{PORT}/mcp/")
    print(f"MCP后端配置: {backend_configs()}")
    async with websockets.serve(handle, HOST, PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
