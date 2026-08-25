"""Receives Windows notification clients and logs incoming notifications.

Stage 3 scope: accept register/notification JSON frames and log them. TTS and
ESP32 delivery are intentionally not implemented here yet.
"""

import secrets

from aiohttp import web, WSMsgType


class WindowsNotificationHub:
    def __init__(self, config: dict, logger, deliver=None):
        self.logger = logger
        server_config = config.get("server", {})
        configured = server_config.get("desktop_token", "")
        self.token = str(configured or server_config.get("auth_key", ""))
        self.clients: dict[str, web.WebSocketResponse] = {}
        # deliver(text) sends spoken text to the ESP board via server TTS.
        self.deliver = deliver

    def authenticate(self, request: web.Request) -> bool:
        supplied = request.query.get("token") or request.headers.get("X-Desktop-Token", "")
        return bool(self.token) and secrets.compare_digest(str(supplied), self.token)

    async def handle_websocket(self, request: web.Request) -> web.StreamResponse:
        if not self.authenticate(request):
            return web.json_response({"ok": False, "error": "通知客户端认证失败"}, status=401)

        socket = web.WebSocketResponse(heartbeat=30)
        await socket.prepare(request)
        device_id = ""
        try:
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = message.json()
                except (TypeError, ValueError):
                    continue
                msg_type = payload.get("type")
                if msg_type == "register":
                    device_id = str(payload.get("device_id") or "").strip()
                    if not device_id:
                        await socket.send_json({"type": "register_rejected", "error": "device_id is required"})
                        break
                    self.clients[device_id] = socket
                    self.logger.bind(tag="windows_notify").info(
                        f"Windows 通知客户端已注册: {device_id}"
                    )
                    await socket.send_json({"type": "registered", "device_id": device_id})
                elif msg_type == "notification":
                    app_name = str(payload.get("app_name") or "未知应用")
                    title = str(payload.get("title", "")).strip()
                    content = str(payload.get("content", "")).strip()
                    self.logger.bind(tag="windows_notify").info(
                        f"Notification received App={app_name} "
                        f"Title={title} Content={content} "
                        f"Id={payload.get('notification_id', '')}"
                    )
                    if self.deliver is None:
                        continue
                    text = f"收到新消息：{title}，{content}" if title else f"收到新消息：{content}"
                    try:
                        await self.deliver(text[:500])
                    except Exception as exc:
                        self.logger.bind(tag="windows_notify").error(
                            f"下发 ESP 播报失败: {exc}"
                        )
                elif msg_type == "ping":
                    await socket.send_json({"type": "pong"})
        finally:
            if device_id and self.clients.get(device_id) is socket:
                self.clients.pop(device_id, None)
                self.logger.bind(tag="windows_notify").info(
                    f"Windows 通知客户端已断开: {device_id}"
                )
        return socket
