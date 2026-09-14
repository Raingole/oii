from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class CognitiveEvent:
    event_id: str
    timestamp: str
    source: str
    type: str
    actor_id: str
    target_id: str
    session_id: str = ""
    source_event_id: str = ""
    content: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    SOURCES: ClassVar[set[str]] = {"qq", "esp32", "sms", "email", "timer", "mcp", "system", "controller"}

    def __post_init__(self) -> None:
        for name in ("event_id", "timestamp", "source", "type", "actor_id", "target_id"):
            if not getattr(self, name) or not isinstance(getattr(self, name), str):
                raise ValueError(f"event.{name} is required")
        if self.source not in self.SOURCES:
            raise ValueError(f"unsupported event source: {self.source}")
        if not isinstance(self.content, dict) or not isinstance(self.metadata, dict):
            raise ValueError("event.content and event.metadata must be objects")

    @classmethod
    def create(cls, source: str, type: str, actor_id: str, target_id: str,
               content: dict[str, Any] | None = None, session_id: str = "",
               metadata: dict[str, Any] | None = None, event_id: str | None = None,
               source_event_id: str = "") -> "CognitiveEvent":
        stable_id = event_id or (f"{source}:{source_event_id}" if source_event_id else _id())
        return cls(stable_id, _now(), source, type, actor_id, target_id,
                   session_id, source_event_id, content or {}, metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Action:
    action_id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    channel: str = ""
    target: str = ""
    risk_level: str = "low"
    reason: str = ""
    requires_controller_approval: bool = False

    TYPES: ClassVar[set[str]] = {"send_message", "speak", "display", "call_mcp", "wait", "observe", "do_nothing", "schedule", "request_confirmation"}
    RISKS: ClassVar[set[str]] = {"low", "medium", "high", "critical"}

    def __post_init__(self) -> None:
        if self.type not in self.TYPES:
            raise ValueError(f"unsupported action type: {self.type}")
        if self.risk_level not in self.RISKS:
            raise ValueError(f"unsupported action risk: {self.risk_level}")
        if not isinstance(self.payload, dict):
            raise ValueError("action.payload must be an object")
        if self.risk_level in {"high", "critical"} and not self.requires_controller_approval:
            raise ValueError("high-risk actions require controller approval")

    @classmethod
    def create(cls, type: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> "Action":
        return cls(action_id=kwargs.pop("action_id", _id()), type=type, payload=payload or {}, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
