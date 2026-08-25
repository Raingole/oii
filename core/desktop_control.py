"""Server-side registry and command channel for desktop companion clients."""

import secrets
import uuid
from typing import Any

from aiohttp import web


class DesktopControl:
    """Keeps authenticated desktop WebSocket clients and sends commands."""

    def __init__(self, config: dict, logger):
        self.logger = logger
        self.clients: set[web.WebSocketResponse] = set()
        configured = config.get("server", {}).get("desktop_token", "")
        self.token = str(configured or config.get("server", {}).get("auth_key", ""))

    def authenticate(self, request: web.Request) -> bool:
        supplied = request.query.get("token") or request.headers.get("X-Desktop-Token", "")
        return bool(self.token) and secrets.compare_digest(str(supplied), self.token)

    async def handle_websocket(self, request: web.Request) -> web.StreamResponse:
        if not self.authenticate(request):
            return web.json_response({"ok": False, "error": "桌面插件认证失败"}, status=401)

        websocket = web.WebSocketResponse(heartbeat=30)
        await websocket.prepare(request)
        self.clients.add(websocket)
        self.logger.bind(tag="desktop_control").info("桌面插件已连接")
        try:
            await websocket.send_json({"type": "desktop_hello", "status": "connected"})
            async for message in websocket:
                if message.type == web.WSMsgType.TEXT:
                    # The client may send an acknowledgement for diagnostics.
                    try:
                        payload = message.json()
                        if payload.get("type") == "desktop_ack":
                            self.logger.bind(tag="desktop_control").info(
                                f"桌面插件执行结果: {payload.get('status', 'unknown')}"
                            )
                    except (TypeError, ValueError):
                        continue
                elif message.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        finally:
            self.clients.discard(websocket)
            self.logger.bind(tag="desktop_control").info("桌面插件已断开")
        return websocket

    async def open_app(self, target: str, url: str = "", path: str = "") -> dict[str, Any]:
        if not self.clients:
            return {"ok": False, "error": "没有在线的桌面插件"}
        command = {
            "type": "desktop_command",
            "command_id": uuid.uuid4().hex,
            "action": "open",
            "target": target,
        }
        if url:
            command["url"] = url
        if path:
            command["path"] = path
        sent = 0
        for websocket in list(self.clients):
            try:
                await websocket.send_json(command)
                sent += 1
            except Exception:
                self.clients.discard(websocket)
        if sent == 0:
            return {"ok": False, "error": "桌面插件连接已失效"}
        return {"ok": True, "target": target, "url": url}
