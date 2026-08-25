import asyncio
import json
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict, websocket_server=None):
        self.config = config
        self.websocket_server = websocket_server
        self.logger = setup_logging()
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)

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
        expected_token = self.config.get("server", {}).get("notification_token", "")
        provided_token = request.headers.get("X-Notify-Token", "")
        if not expected_token or provided_token != expected_token:
            return web.json_response({"ok": False, "error": "通知接口未授权"}, status=401)
        if not self.websocket_server:
            return web.json_response({"ok": False, "error": "WebSocket服务未初始化"}, status=503)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"ok": False, "error": "请求必须是JSON"}, status=400)

        device_id = str(payload.get("device_id", "")).strip()
        text = str(payload.get("text", "")).strip()
        if not device_id or not text:
            return web.json_response({"ok": False, "error": "缺少device_id或text"}, status=400)
        conn = self.websocket_server.get_connection(device_id)
        if not conn:
            return web.json_response({"ok": False, "error": "设备当前不在线"}, status=404)
        try:
            await conn.notify_text(text[:500])
        except Exception as exc:
            self.logger.bind(tag=TAG).error(f"下发通知失败: {exc}")
            return web.json_response({"ok": False, "error": str(exc)}, status=503)
        return web.json_response({"ok": True})
