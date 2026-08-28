"""Independent OneBot 11 reverse-WebSocket gateway."""

import asyncio
import json
import os
import secrets
import uuid
from typing import Optional

from aiohttp import WSMsgType, web
from config.logger import setup_logging

from .agent import QQAgent
from .models import QQMessage
from .service import QQService

TAG = __name__


class QQGateway:
    def __init__(self, config: dict, llm, controller=None):
        self.config = config
        self.qq_config = dict(config.get("qq", {}))
        # Environment variables override file configuration and keep production
        # credentials out of config.yaml and Git.
        if os.environ.get("QQ_ENABLED") is not None:
            self.qq_config["enabled"] = os.environ["QQ_ENABLED"].lower() in {"1", "true", "yes", "on"}
        for env_name, config_name in {
            "ONEBOT_WS_TOKEN": "onebot_ws_token",
            "NAPCAT_HTTP_TOKEN": "napcat_http_token",
            "NAPCAT_HTTP_URL": "napcat_http_url",
        }.items():
            if os.environ.get(env_name):
                self.qq_config[config_name] = os.environ[env_name]
        config["qq"] = self.qq_config
        self.logger = setup_logging(config)
        self.agent = QQAgent(config, llm, controller=controller)
        self.service = QQService(config)
        self.server: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.stop_event = asyncio.Event()
        self.websocket = None
        self.self_id: Optional[str] = None
        self.recent_message_ids: set[str] = set()
        self.pending_actions: dict[str, asyncio.Future] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.qq_config.get("enabled", False))

    async def start(self) -> None:
        if not self.enabled:
            self.logger.bind(tag=TAG).info("QQ Gateway disabled")
            await self.stop_event.wait()
            return
        app = web.Application()
        app.router.add_get(self.qq_config.get("onebot_ws_path", "/onebot"), self.handle_websocket)
        self.server = web.AppRunner(app)
        await self.server.setup()
        host = self.qq_config.get("onebot_ws_host", "0.0.0.0")
        port = int(self.qq_config.get("onebot_ws_port", 8082))
        self.site = web.TCPSite(self.server, host, port)
        await self.site.start()
        self.logger.bind(tag=TAG).info(f"OneBot Gateway listening on {host}:{port}")
        await self.agent.start()
        try:
            await self.stop_event.wait()
        finally:
            await self.close()

    async def close(self) -> None:
        self.stop_event.set()
        if self.websocket is not None and not self.websocket.closed:
            await self.websocket.close()
        self.websocket = None
        for future in self.pending_actions.values():
            if not future.done():
                future.cancel()
        self.pending_actions.clear()
        await self.service.close()
        if self.server is not None:
            await self.server.cleanup()
            self.server = None

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        if not self._authorized(request):
            self.logger.bind(tag=TAG).warning("Rejected OneBot connection: invalid token")
            raise web.HTTPUnauthorized()
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        if self.websocket is not None and not self.websocket.closed:
            await self.websocket.close()
        self.websocket = ws
        self.logger.bind(tag=TAG).info("NapCat OneBot connected")
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    try:
                        await self._handle_payload(message.data)
                    except Exception as exc:
                        # Isolate malformed or failed events; keep the reverse
                        # WebSocket available for the next OneBot event.
                        self.logger.bind(tag=TAG).error(f"OneBot event failed: {exc}")
                elif message.type == WSMsgType.ERROR:
                    self.logger.bind(tag=TAG).error(f"OneBot WebSocket error: {ws.exception()}")
                elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break
        except Exception as exc:
            self.logger.bind(tag=TAG).error(f"OneBot connection loop failed: {exc}")
        finally:
            if self.websocket is ws:
                self.websocket = None
            self.logger.bind(tag=TAG).info("NapCat OneBot disconnected")
        return ws

    async def send_action(self, action: str, params: Optional[dict] = None, timeout: float = 10):
        """Send a OneBot action over the reverse socket and await its echo."""
        if self.websocket is None or self.websocket.closed:
            raise RuntimeError("NapCat OneBot WebSocket is not connected")
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending_actions[echo] = future
        try:
            await self.websocket.send_json({"action": action, "params": params or {}, "echo": echo})
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self.pending_actions.pop(echo, None)
            self.logger.bind(tag=TAG).warning(f"OneBot action timeout: action={action}")
            raise
        except Exception:
            self.pending_actions.pop(echo, None)
            raise

    def _authorized(self, request: web.Request) -> bool:
        expected = str(self.qq_config.get("onebot_ws_token") or "")
        if not expected:
            return bool(self.qq_config.get("allow_unauthenticated", False))
        supplied = request.headers.get("Authorization", "")
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:]
        if not supplied:
            supplied = request.query.get("access_token", "")
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    async def _handle_payload(self, raw: str) -> None:
        if not raw or not raw.strip():
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.bind(tag=TAG).warning("Ignored invalid OneBot JSON")
            return
        if not isinstance(event, dict):
            return
        if event.get("self_id") is not None:
            self.self_id = str(event["self_id"])
        echo = event.get("echo")
        if echo is not None and str(echo) in self.pending_actions:
            future = self.pending_actions.pop(str(echo))
            if not future.done():
                future.set_result(event)
            return
        post_type = event.get("post_type")
        if post_type == "meta_event":
            if event.get("meta_event_type") == "lifecycle":
                self.logger.bind(tag=TAG).info(f"OneBot lifecycle: {event.get('sub_type')}")
            return
        if (
            post_type != "message"
            or event.get("message_type") != "private"
            or not self.qq_config.get("private_message_enabled", True)
        ):
            return
        message = QQMessage.from_event(event)
        self.self_id = self.self_id or message.self_id
        if message.self_id and message.user_id == message.self_id:
            return
        if self.self_id and message.user_id == self.self_id:
            return
        if message.message_id and message.message_id in self.recent_message_ids:
            return
        if message.message_id:
            self.recent_message_ids.add(message.message_id)
            if len(self.recent_message_ids) > 10000:
                self.recent_message_ids = set(list(self.recent_message_ids)[-5000:])
        if not message.message:
            return
        allowed = self.qq_config.get("allowed_users", [])
        if allowed and message.user_id not in {str(item) for item in allowed}:
            return
        self.logger.bind(tag=TAG).info(f"QQ private message received: user_id={message.user_id}, message_id={message.message_id}")
        answer = await self.agent.reply(message.session_key, message.message)
        await self.service.send_private_message(message.user_id, answer)
