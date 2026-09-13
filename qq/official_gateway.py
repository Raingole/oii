"""Native QQ Official Bot WebSocket gateway client."""

import asyncio
import json
import os
import time
from typing import Any

import aiohttp
import websockets


class QQOfficialGateway:
    TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
    API_BASE = "https://api.sgroup.qq.com"

    def __init__(self, config: dict, agent: Any, logger: Any):
        section = config.get("qq_official", {})
        self.enabled = bool(section.get("enabled", False))
        self.app_id = str(os.environ.get("QQ_OFFICIAL_APP_ID") or section.get("app_id") or "")
        self.app_secret = str(os.environ.get("QQ_OFFICIAL_APP_SECRET") or section.get("app_secret") or "")
        self.logger = logger
        self.agent = agent
        self.memory_identity = str(section.get("memory_identity") or "").strip()
        self.intents = int(section.get("intents", 1 << 25))
        self.stop_event = asyncio.Event()
        self.websocket = None
        self.access_token = ""
        self.sequence = None
        self.session_id = None
        self.resume_gateway_url = None
        self._heartbeat_task = None
        self._send_lock = asyncio.Lock()

    async def start(self) -> None:
        if not self.enabled:
            self.logger.info("QQ Official Bot gateway disabled")
            await self.stop_event.wait()
            return
        if not self.app_id or not self.app_secret:
            self.logger.error("QQ Official Bot requires app_id and app_secret")
            await self.stop_event.wait()
            return
        while not self.stop_event.is_set():
            try:
                await self._run_connection()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.error(f"QQ Official Bot connection failed: {exc}")
            if not self.stop_event.is_set():
                await asyncio.sleep(5)

    async def close(self) -> None:
        self.stop_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self.websocket:
            await self.websocket.close()
        self.websocket = None

    async def _get_gateway_url(self) -> tuple[str, str]:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.TOKEN_URL,
                json={"appId": self.app_id, "clientSecret": self.app_secret},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                token = await response.json(content_type=None)
                if response.status >= 400 or not token.get("access_token"):
                    raise RuntimeError(f"access token failed: HTTP {response.status}")
            headers = {
                "Authorization": f"QQBot {token['access_token']}",
                "User-Agent": "oii-qq-official-gateway/1.0",
            }
            async with session.get(
                f"{self.API_BASE}/gateway/bot",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                gateway = await response.json(content_type=None)
                if response.status >= 400 or not gateway.get("url"):
                    raise RuntimeError(f"gateway discovery failed: HTTP {response.status}")
                return gateway["url"], token["access_token"]

    async def _run_connection(self) -> None:
        gateway_url, access_token = await self._get_gateway_url()
        url = self.resume_gateway_url or gateway_url
        async with websockets.connect(url, ping_interval=None, max_size=4 * 1024 * 1024) as websocket:
            self.websocket = websocket
            self.access_token = access_token
            try:
                hello = json.loads(await websocket.recv())
                if hello.get("op") != 10:
                    raise RuntimeError(f"unexpected gateway hello: {hello.get('op')}")
                self.logger.bind(tag=__name__).info(
                    f"QQ Official Bot Gateway hello: heartbeat_interval={hello.get('d', {}).get('heartbeat_interval')}"
                )
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat(websocket, hello.get("d", {}).get("heartbeat_interval", 45000))
                )
                if self.session_id and self.sequence is not None:
                    self.logger.bind(tag=__name__).info(
                        f"QQ Official Bot resuming session: session_id={self.session_id}, sequence={self.sequence}"
                    )
                    await websocket.send(json.dumps({
                        "op": 6,
                        "d": {
                            "token": f"QQBot {access_token}",
                            "session_id": self.session_id,
                            "seq": self.sequence,
                        },
                    }))
                else:
                    await websocket.send(json.dumps({
                        "op": 2,
                        "d": {
                            "token": f"QQBot {access_token}",
                            "intents": self.intents,
                            "shard": [0, 1],
                            "properties": {"$os": "linux", "$browser": "oii", "$device": "oii"},
                        },
                    }))
                async for raw in websocket:
                    packet = json.loads(raw)
                    if packet.get("s") is not None:
                        self.sequence = packet["s"]
                    if packet.get("op") == 0:
                        if packet.get("t") == "READY":
                            ready = packet.get("d") or {}
                            self.session_id = ready.get("session_id") or self.session_id
                            self.resume_gateway_url = ready.get("resume_gateway_url") or self.resume_gateway_url
                            self.logger.bind(tag=__name__).info("QQ Official Bot Gateway ready")
                        elif packet.get("t") == "RESUMED":
                            self.logger.bind(tag=__name__).info("QQ Official Bot Gateway session resumed")
                        await self._handle_dispatch(packet.get("t"), packet.get("d") or {})
                    elif packet.get("op") == 1:
                        await websocket.send(json.dumps({"op": 1, "d": self.sequence}))
                    elif packet.get("op") == 7:
                        self.logger.bind(tag=__name__).info("QQ Gateway requested reconnect; closing for session resume")
                        await websocket.close(code=4000, reason="server requested reconnect")
                        break
                    elif packet.get("op") == 9:
                        resumable = bool(packet.get("d"))
                        self.logger.bind(tag=__name__).warning(
                            f"QQ Gateway invalid session: resumable={resumable}"
                        )
                        if not resumable:
                            self.session_id = None
                            self.resume_gateway_url = None
                            self.sequence = None
                        await websocket.close(code=4000, reason="invalid session")
                        break
                    elif packet.get("op") == 11:
                        self.logger.bind(tag=__name__).debug("QQ Official Bot heartbeat acknowledged")
            finally:
                if getattr(websocket, "close_code", None) == 4009:
                    self.logger.bind(tag=__name__).warning(
                        "QQ Official Bot session timed out; starting a fresh session"
                    )
                    self.session_id = None
                    self.resume_gateway_url = None
                    self.sequence = None
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    self._heartbeat_task = None
                self.websocket = None
                self.access_token = ""

    async def _heartbeat(self, websocket, interval_ms: int) -> None:
        while True:
            await asyncio.sleep(max(1, interval_ms / 1000 * 0.8))
            try:
                await websocket.send(json.dumps({"op": 1, "d": self.sequence}))
            except Exception:
                return

    async def _handle_dispatch(self, event_type: str | None, data: dict) -> None:
        if event_type != "C2C_MESSAGE_CREATE":
            return
        user_id = str(data.get("author", {}).get("user_openid") or "").strip()
        text = str(data.get("content") or "").strip()
        message_id = str(data.get("id") or "").strip()
        if not user_id or not text:
            return
        session_id = self.memory_identity or user_id
        session_key = f"qq:private:{session_id}"
        answer = await self.agent.reply(session_key, text)
        await self._send_c2c_message(user_id, answer, message_id)

    async def _send_c2c_message(self, user_openid: str, text: str, message_id: str) -> None:
        if not self.websocket:
            raise RuntimeError("QQ Official Bot gateway is not connected")
        # Official QQ API replies are sent through REST, while the gateway WS
        # is used for receiving events and lifecycle control.
        if not self.access_token:
            raise RuntimeError("QQ Official Bot access token is unavailable")
        headers = {"Authorization": f"QQBot {self.access_token}", "Content-Type": "application/json"}
        async with self._send_lock:
            for attempt in range(2):
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.API_BASE}/v2/users/{user_openid}/messages",
                        headers=headers,
                        json={"content": str(text)[:4000], "msg_type": 0, "msg_id": message_id},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status < 400:
                            return
                        body = await response.text()
                        if response.status == 401 and attempt == 0:
                            _, self.access_token = await self._get_gateway_url()
                            headers["Authorization"] = f"QQBot {self.access_token}"
                            continue
                        raise RuntimeError(f"QQ Official Bot send failed: HTTP {response.status} {body[:200]}")
