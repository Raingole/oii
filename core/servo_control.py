"""Standalone MG946R WebSocket bridge for ESP32 devices and controllers."""

import asyncio
import json
import secrets
from typing import Any

import websockets


class ServoControlServer:
    def __init__(self, config: dict, logger):
        server_config = config.get("server", {})
        self.host = str(server_config.get("ip", "0.0.0.0"))
        self.port = int(server_config.get("servo_port", 8777))
        self.device_id = str(server_config.get("servo_device_id", "mg946r-001"))
        self.token = str(server_config.get("servo_token") or server_config.get("auth_key", ""))
        self.logger = logger
        self.devices: dict[str, Any] = {}
        self.controllers: set[Any] = set()
        self.last_status: dict[str, dict] = {}

    async def start(self) -> None:
        async with websockets.serve(self.handle, self.host, self.port, ping_interval=30, ping_timeout=30):
            self.logger.bind(tag="servo_control").info(
                "MG946R WebSocket: ws://%s:%s/ws", self.host, self.port
            )
            await asyncio.Future()

    async def handle(self, websocket) -> None:
        request_path = getattr(getattr(websocket, "request", None), "path", getattr(websocket, "path", "/ws"))
        if request_path.split("?", 1)[0] != "/ws":
            await websocket.close(code=1008, reason="invalid path")
            return
        role = "controller"
        device_id = ""
        authenticated = False
        try:
            async for raw in websocket:
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    await self.send(websocket, {"type": "error", "success": False, "message": "message must be JSON"})
                    continue
                if not authenticated:
                    if payload.get("type") != "auth":
                        await self.send(websocket, {"type": "error", "success": False, "message": "auth required"})
                        await websocket.close(code=1008, reason="auth required")
                        return
                    device_id = str(payload.get("device_id", "")).strip()
                    supplied = str(payload.get("token", ""))
                    if device_id != self.device_id or not self.token or not secrets.compare_digest(supplied, self.token):
                        await self.send(websocket, {"type": "auth_ack", "success": False, "device_id": device_id, "message": "authentication failed"})
                        await websocket.close(code=1008, reason="authentication failed")
                        return
                    role = str(payload.get("role", "device")).lower()
                    authenticated = True
                    if role == "device":
                        self.devices[device_id] = websocket
                    else:
                        self.controllers.add(websocket)
                    await self.send(websocket, {"type": "auth_ack", "success": True, "device_id": device_id, "role": role})
                    if role != "device":
                        await self.send(websocket, self.last_status.get(device_id, {"type": "status", "device_id": device_id, "online": device_id in self.devices}))
                    continue
                if role == "device":
                    await self.handle_device_message(device_id, payload)
                else:
                    await self.handle_controller_message(websocket, device_id, payload)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if role == "device" and self.devices.get(device_id) is websocket:
                self.devices.pop(device_id, None)
                await self.broadcast({"type": "status", "device_id": device_id, "online": False})
            self.controllers.discard(websocket)

    async def handle_device_message(self, device_id: str, payload: dict) -> None:
        message_type = str(payload.get("type", "")).lower()
        if message_type in {"online", "heartbeat", "status"}:
            status = dict(payload)
            status.setdefault("type", "status")
            status["device_id"] = device_id
            status["online"] = True
            self.last_status[device_id] = status
            await self.broadcast(status)
        else:
            await self.broadcast(payload)

    async def handle_controller_message(self, websocket, device_id: str, payload: dict) -> None:
        command = str(payload.get("cmd", "")).lower()
        if command not in {"left", "right", "center", "sweep", "angle"}:
            await self.send(websocket, {"type": "error", "device_id": device_id, "success": False, "message": "unknown cmd"})
            return
        if command == "angle":
            try:
                value = int(payload.get("value"))
            except (TypeError, ValueError):
                value = -1
            if not 0 <= value <= 180:
                await self.send(websocket, {"type": "error", "device_id": device_id, "success": False, "message": "angle must be between 0 and 180"})
                return
        device = self.devices.get(device_id)
        if device is None:
            await self.send(websocket, {"type": "status", "device_id": device_id, "online": False, "success": False, "message": "device offline"})
            return
        await self.send(device, payload)
        await self.send(websocket, {"type": "status", "device_id": device_id, "online": True, "success": True, "cmd": command, "queued": True})

    async def send(self, websocket, payload: dict) -> None:
        await websocket.send(json.dumps(payload, ensure_ascii=False))

    async def broadcast(self, payload: dict) -> None:
        for controller in list(self.controllers):
            try:
                await self.send(controller, payload)
            except Exception:
                self.controllers.discard(controller)

    async def send_command(self, command: str, value: int | None = None, device_id: str | None = None) -> dict:
        target = device_id or self.device_id
        device = self.devices.get(target)
        if device is None:
            return {"success": False, "error": "MG946R device offline", "device_id": target}
        payload = {"device_id": target, "cmd": command}
        if command == "angle":
            if value is None or not 0 <= int(value) <= 180:
                return {"success": False, "error": "angle must be between 0 and 180"}
            payload["value"] = int(value)
        await self.send(device, payload)
        return {"success": True, "device_id": target, "cmd": command, "queued": True}

    async def execute(self, tool_name: str, arguments: dict, context: dict | None = None) -> dict:
        command = tool_name.removeprefix("mg946r_")
        return await self.send_command(command, arguments.get("value"), arguments.get("device_id"))

    async def discover(self) -> list[dict]:
        return [
            {"type": "function", "function": {"name": "mg946r_left", "description": "MG946R 舵机左转", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "mg946r_right", "description": "MG946R 舵机右转", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "mg946r_center", "description": "MG946R 舵机回中", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "mg946r_sweep", "description": "MG946R 舵机扫描", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "mg946r_angle", "description": "设置 MG946R 舵机角度，范围 0 到 180", "parameters": {"type": "object", "properties": {"value": {"type": "integer", "minimum": 0, "maximum": 180, "description": "目标角度"}}, "required": ["value"]}}},
        ]
