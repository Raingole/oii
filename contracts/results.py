from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ActionResult:
    action_id: str
    status: str
    success: bool
    result: Any = None
    error: str = ""
    attempts: int = 0
    external_id: str | None = None

    def to_dict(self) -> dict[str, Any]: return asdict(self)
