from __future__ import annotations

from typing import Any


class BodyRegistry:
    def __init__(self) -> None: self.bodies: dict[str, dict[str, Any]] = {}
    def register(self, body_id: str, ownership: str = "self", capabilities: list[str] | None = None) -> dict[str, Any]:
        body = {"id": body_id, "ownership": ownership, "capabilities": capabilities or [], "online": True}; self.bodies[body_id] = body; return body
    def set_online(self, body_id: str, online: bool) -> None:
        if body_id in self.bodies: self.bodies[body_id]["online"] = online
    def snapshot(self) -> list[dict[str, Any]]: return list(self.bodies.values())
