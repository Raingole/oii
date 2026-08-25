import asyncio
import json
from collections import deque
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.desktop_control import DesktopControl
from core.notification_hub import WindowsNotificationHub

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
        self.notification_hub = WindowsNotificationHub(config, self.logger)

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
                        web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                        web.post(
                            "/mcp/vision/explain", self.vision_handler.handle_post
                        ),
                        web.options(
                            "/mcp/vision/explain", self.vision_handler.handle_options
                        ),
                        web.post("/api/notify", self.handle_notify),
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

    async def handle_notify(self, request):
        """接受桌面监听器消息并转为设备 TTS。"""
        if not self.websocket_server:
            return web.json_response({"ok": False, "error": "WebSocket服务未初始化"}, status=503)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"ok": False, "error": "请求必须是JSON"}, status=400)

        text = str(payload.get("text", "")).strip()
        if not text:
            return web.json_response({"ok": False, "error": "缺少text"}, status=400)
        async with self.notification_lock:
            connections = list(self.websocket_server.connections.values())
            if not connections:
                self.pending_notifications.append(text[:500])
                return web.json_response({"ok": True, "queued": True}, status=202)
            try:
                await connections[0].notify_text(text[:500])
            except Exception as exc:
                self.pending_notifications.append(text[:500])
                self.logger.bind(tag=TAG).error(f"下发通知失败，已排队: {exc}")
                return web.json_response({"ok": False, "queued": True, "error": str(exc)}, status=503)
        return web.json_response({"ok": True})

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
