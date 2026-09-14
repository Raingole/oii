from __future__ import annotations

from typing import Any
import uuid


class Timeline:
    def __init__(self) -> None: self.events: list[dict[str, Any]] = []; self.periods: list[dict[str, Any]] = []
    def add(self, title: str, kind: str, evidence: list[str] | None = None) -> dict[str, Any]:
        item = {"id": str(uuid.uuid4()), "title": title, "kind": kind, "evidence": evidence or []}; self.events.append(item); return item
    def add_period(self, title: str, start: str, end: str | None = None) -> dict[str, Any]:
        item = {"id": str(uuid.uuid4()), "title": title, "start": start, "end": end}; self.periods.append(item); return item
