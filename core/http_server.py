import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import re
import time
from pathlib import Path
from collections import deque
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.desktop_control import DesktopControl
from core.notification_hub import WindowsNotificationHub
from core.mailpilot_webhook import MailPilotWebhookHandler
from core.sms_webhook import SmsWebhookHandler

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict, websocket_server=None):
        self.config = config
        self.websocket_server = websocket_server
        self.logger = setup_logging()
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)
        # Keep a small in-memory buffer so a notification is not lost while the
        # board is reconnecting. This is intentionally not persistent storage.
        self.pending_notifications = deque(maxlen=100)
        self.notification_lock = asyncio.Lock()
        self.desktop_control = DesktopControl(config, self.logger)
        self.notification_hub = WindowsNotificationHub(config, self.logger, self.deliver_notification)
        self.mailpilot_webhook = None
        self.sms_webhook = None
        self.sim_qq_users_path = Path(__file__).resolve().parents[1] / "data" / "simulated_qq_users.json"
        self.sim_qq_users_path.parent.mkdir(parents=True, exist_ok=True)
        self.sim_qq_users = self._load_sim_qq_users()

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """获取websocket地址

        Args:
            local_ip: 本地IP地址
            port: 端口号

        Returns:
            str: websocket地址
        """
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")

        if websocket_config and "你" not in websocket_config:
            return websocket_config
        else:
            return f"ws://{local_ip}:{port}/xiaozhi/v1/"

    async def start(self):
        try:
            server_config = self.config["server"]
            read_config_from_api = self.config.get("read_config_from_api", False)
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))

            if port:
                app = web.Application()
                qq_service = getattr(self.websocket_server, "qq_service", None)
                if qq_service is not None:
                    event_router = getattr(self.websocket_server, "event_router", None)
                    self.mailpilot_webhook = MailPilotWebhookHandler(self.config, qq_service, event_router)
                    app.router.add_post("/webhook/mailpilot", self.mailpilot_webhook.handle)
                    app.router.add_post("/webhook/mailpilot/{token}", self.mailpilot_webhook.handle)
                    self.sms_webhook = SmsWebhookHandler(self.config, qq_service, self.logger, event_router)
                    app.router.add_post("/api/events/sms", self.sms_webhook.handle)

                if not read_config_from_api:
                    # 如果没有开启智控台，只是单模块运行，就需要再添加简单OTA接口，用于下发websocket接口
                    app.add_routes(
                        [
                            web.get("/xiaozhi/ota/", self.ota_handler.handle_get),
                            web.post("/xiaozhi/ota/", self.ota_handler.handle_post),
                            web.options(
                                "/xiaozhi/ota/", self.ota_handler.handle_options
                            ),
                            # 下载接口，仅提供 data/bin/*.bin 下载
                            web.get(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_download,
                            ),
                            web.options(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_options,
                            ),
                        ]
                    )
                # 添加路由
                app.add_routes(
                    [
                        web.get("/cognitive/health", self.handle_cognitive_health),
                        web.get("/cognitive/state", self.handle_cognitive_state),
                        web.get("/cognitive/debug", self.handle_cognitive_state),
                        web.get("/", self.handle_ui_index),
                        web.get("/styles.css", self.handle_ui_styles),
                        web.get("/app.js", self.handle_ui_app),
                        web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                        web.post(
                            "/mcp/vision/explain", self.vision_handler.handle_post
                        ),
                        web.options(
                            "/mcp/vision/explain", self.vision_handler.handle_options
                        ),
                        web.post("/api/cloud/push", self.handle_cloud_push),
                        web.post("/api/sim-qq/login", self.handle_sim_qq_login),
                        web.get("/api/sim-qq/session", self.handle_sim_qq_session),
                        web.post("/api/sim-qq/message", self.handle_sim_qq_message),
                        web.post("/api/sim-qq/logout", self.handle_sim_qq_logout),
                        web.get("/api/desktop", self.desktop_control.handle_websocket),
                        web.get("/api/desktop/", self.desktop_control.handle_websocket),
                        web.get("/ws/windows", self.notification_hub.handle_websocket),
                        web.get("/ws/windows/", self.notification_hub.handle_websocket),
                    ]
                )

                # 运行服务
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()

                # 保持服务运行
                while True:
                    await asyncio.sleep(3600)  # 每隔 1 小时检查一次
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP服务器启动失败: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            raise

    async def handle_ui_index(self, request):
        """Serve the oii voice guide through the existing public HTTP port."""
        index_path = Path(__file__).resolve().parents[1] / "ui" / "index.html"
        return web.FileResponse(index_path)

    def _cognitive_authorized(self, request) -> bool:
        runtime = getattr(self.websocket_server, "cognitive_runtime", None)
        if runtime is None:
            return False
        config = runtime.core.config
        token = str(config.get("api_token", "") or "")
        if not token:
            return True
        return request.headers.get("Authorization", "") == f"Bearer {token}"

    async def handle_cognitive_health(self, request):
        runtime = getattr(self.websocket_server, "cognitive_runtime", None)
        if runtime is None:
            return web.json_response({"ok": False, "error": "cognitive runtime disabled"}, status=503)
        if not self._cognitive_authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        health = runtime.health()
        return web.json_response({"ok": all(health.values()), "service": "cognitive-runtime", "dependencies": health}, status=200 if all(health.values()) else 503)

    async def handle_cognitive_state(self, request):
        runtime = getattr(self.websocket_server, "cognitive_runtime", None)
        if runtime is None:
            return web.json_response({"ok": False, "error": "cognitive runtime disabled"}, status=503)
        if not self._cognitive_authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        snapshot = runtime.core.snapshot()
        snapshot["lifecycle_trace"] = list(runtime.lifecycle_trace)
        snapshot["scheduler"] = {
            "running": bool(getattr(runtime.scheduler, "_task", None) and not runtime.scheduler._task.done()),
            "autonomous_action_enabled": runtime.autonomous_action_enabled,
        }
        return web.json_response(snapshot)

    async def handle_ui_styles(self, request):
        return web.FileResponse(Path(__file__).resolve().parents[1] / "ui" / "styles.css")

    async def handle_ui_app(self, request):
        return web.FileResponse(Path(__file__).resolve().parents[1] / "ui" / "app.js")

    def _load_sim_qq_users(self) -> dict:
        try:
            data = json.loads(self.sim_qq_users_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_sim_qq_users(self) -> None:
        self.sim_qq_users_path.write_text(
            json.dumps(self.sim_qq_users, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _sim_cookie_secret(self) -> bytes:
        return str(self.config.get("server", {}).get("auth_key", "sim-qq-local-secret")).encode()

    def _sim_cookie_value(self, username: str, qq_id: str) -> str:
        payload = f"{username}|{qq_id}"
        signature = hmac.new(self._sim_cookie_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return base64.urlsafe_b64encode(f"{payload}|{signature}".encode()).decode()

    def _sim_identity(self, request):
        raw = request.cookies.get("sim_qq_session", "")
        try:
            username, qq_id, signature = base64.urlsafe_b64decode(raw.encode()).decode().split("|", 2)
        except (ValueError, UnicodeDecodeError, base64.binascii.Error):
            return None
        expected = hmac.new(self._sim_cookie_secret(), f"{username}|{qq_id}".encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(signature, expected) or self.sim_qq_users.get(username) != qq_id:
            return None
        return {"username": username, "qq_id": qq_id, "is_owner": qq_id == self._owner_qq()}

    def _owner_qq(self) -> str:
        return str(self.config.get("qq", {}).get("owner_qq", "2496303940")).strip() or "2496303940"

    async def handle_sim_qq_login(self, request):
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"ok": False, "error": "请求必须是 JSON"}, status=400)
        username = str(payload.get("username", "")).strip()
        qq_id = str(payload.get("qq_id", "")).strip()
        if not re.fullmatch(r"[\w\u4e00-\u9fff-]{2,24}", username) or not re.fullmatch(r"\d{5,14}", qq_id):
            return web.json_response({"ok": False, "error": "请输入 2-24 位 user 名称和有效 QQ 号"}, status=400)
        existing_qq = self.sim_qq_users.get(username)
        if existing_qq and existing_qq != qq_id:
            return web.json_response({"ok": False, "error": "这个 user 已经绑定了另一个 QQ"}, status=409)
        other_user = next((name for name, value in self.sim_qq_users.items() if value == qq_id and name != username), None)
        if other_user:
            return web.json_response({"ok": False, "error": f"这个 QQ 已绑定 user：{other_user}"}, status=409)
        self.sim_qq_users[username] = qq_id
        self._save_sim_qq_users()
        identity = {"username": username, "qq_id": qq_id, "is_owner": qq_id == self._owner_qq()}
        response = web.json_response({"ok": True, "identity": identity})
        response.set_cookie("sim_qq_session", self._sim_cookie_value(username, qq_id), max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax")
        return response

    async def handle_sim_qq_session(self, request):
        identity = self._sim_identity(request)
        if not identity:
            return web.json_response({"ok": False}, status=401)
        return web.json_response({"ok": True, "identity": identity})

    async def handle_sim_qq_message(self, request):
        identity = self._sim_identity(request)
        if not identity:
            return web.json_response({"ok": False, "error": "请先登录模拟 QQ"}, status=401)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"ok": False, "error": "请求必须是 JSON"}, status=400)
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > 4000:
            return web.json_response({"ok": False, "error": "消息不能为空且不能超过 4000 字"}, status=400)
        agent = getattr(getattr(self.websocket_server, "qq_gateway", None), "agent", None)
        if agent is None:
            agent = getattr(self.websocket_server, "qq_agent", None)
        if agent is None:
            return web.json_response({"ok": False, "error": "中控 Agent 尚未就绪"}, status=503)
        # Owner uses the real private QQ identity; every other registration gets an isolated session.
        session_key = f"qq:private:{self._owner_qq()}" if identity["is_owner"] else f"simqq:{identity['username']}:{identity['qq_id']}"
        answer = await agent.reply(session_key, text)
        return web.json_response({"ok": True, "answer": answer or "中控没有返回文字回复。", "identity": identity, "session": session_key})

    async def handle_sim_qq_logout(self, request):
        response = web.json_response({"ok": True})
        response.del_cookie("sim_qq_session")
        return response

    async def deliver_notification(self, text: str) -> bool:
        """Send text to the ESP board via server TTS; queue while offline."""
        async with self.notification_lock:
            connections = list(self.websocket_server.connections.values())
            if not connections:
                self.pending_notifications.append(text[:500])
                return False
            try:
                await connections[0].notify_text(text[:500])
            except Exception as exc:
                self.pending_notifications.append(text[:500])
                self.logger.bind(tag=TAG).error(f"下发通知失败，已排队: {exc}")
                return False
            return True

    def _cloud_token_valid(self, request: web.Request) -> bool:
        server_config = self.config.get("server", {})
        expected = str(
            server_config.get("cloud_push_token")
            or server_config.get("desktop_token")
            or server_config.get("auth_key", "")
        )
        supplied = request.headers.get("X-Cloud-Token", "")
        if not supplied:
            authorization = request.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                supplied = authorization[7:]
        return bool(expected) and secrets.compare_digest(str(supplied), expected)

    async def handle_cloud_push(self, request):
        """云端主动下发 TTS 或设备指令；不依赖 ConversationSession。"""
        if not self._cloud_token_valid(request):
            return web.json_response({"ok": False, "error": "云端推送鉴权失败"}, status=401)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"ok": False, "error": "请求必须是JSON"}, status=400)

        message_type = str(payload.get("type", "speak")).strip().lower()
        if message_type == "speak":
            text = str(payload.get("text", "")).strip()
            if not text:
                return web.json_response({"ok": False, "error": "缺少text"}, status=400)
            delivered = await self.deliver_notification(text[:500])
            return web.json_response({"ok": True, "queued": not delivered}, status=202 if not delivered else 200)

        if message_type == "command":
            command = str(payload.get("command", "")).strip()
            if not command:
                return web.json_response({"ok": False, "error": "缺少command"}, status=400)
            device_id = str(payload.get("device_id", "")).strip()
            connections = list(self.websocket_server.connections.values())
            if device_id:
                connections = [self.websocket_server.get_connection(device_id)]
            connections = [connection for connection in connections if connection is not None]
            if not connections:
                return web.json_response({"ok": False, "error": "设备不在线"}, status=503)
            message = {
                "type": "device_command",
                "command": command,
                "params": payload.get("params", {}),
                "request_id": str(payload.get("request_id", "")),
            }
            for connection in connections:
                await connection.websocket.send(json.dumps(message, ensure_ascii=False))
            return web.json_response({"ok": True, "sent": len(connections)})

        return web.json_response({"ok": False, "error": "type必须是speak或command"}, status=400)

    async def deliver_pending_notifications(self, connection):
        """Deliver buffered desktop notifications after a board reconnects."""
        async with self.notification_lock:
            while self.pending_notifications:
                if self.websocket_server.get_connection(connection.device_id) is not connection:
                    return
                text = self.pending_notifications.popleft()
                try:
                    await connection.notify_text(text)
                except Exception as exc:
                    self.pending_notifications.appendleft(text)
                    self.logger.bind(tag=TAG).warning(f"排队通知等待设备就绪: {exc}")
                    return
