import asyncio
import json
import secrets
from pathlib import Path
from collections import deque
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.desktop_control import DesktopControl
from core.notification_hub import WindowsNotificationHub
from core.mailpilot_webhook import MailPilotWebhookHandler

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
                    self.mailpilot_webhook = MailPilotWebhookHandler(self.config, qq_service)
                    app.router.add_post("/webhook/mailpilot", self.mailpilot_webhook.handle)
                    app.router.add_post("/webhook/mailpilot/{token}", self.mailpilot_webhook.handle)

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

    async def handle_ui_styles(self, request):
        return web.FileResponse(Path(__file__).resolve().parents[1] / "ui" / "styles.css")

    async def handle_ui_app(self, request):
        return web.FileResponse(Path(__file__).resolve().parents[1] / "ui" / "app.js")

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
