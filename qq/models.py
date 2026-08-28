"""Small, protocol-focused OneBot models used by the QQ gateway."""

from dataclasses import dataclass
from typing import Any, Optional


def _string_id(value: Any) -> Optional[str]:
    return None if value is None else str(value)


@dataclass(frozen=True)
class QQMessage:
    user_id: str
    message: str
    message_type: str = "private"
    group_id: Optional[str] = None
    message_id: Optional[str] = None
    raw_message: str = ""
    sub_type: Optional[str] = None
    sender: Any = None
    time: Any = None
    self_id: Optional[str] = None

    @property
    def session_key(self) -> str:
        if self.message_type == "group" and self.group_id:
            return f"qq:group:{self.group_id}:{self.user_id}"
        return f"qq:private:{self.user_id}"

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "QQMessage":
        return cls(
            user_id=_string_id(event.get("user_id")) or "",
            message_type=str(event.get("message_type") or "private"),
            group_id=_string_id(event.get("group_id")),
            message_id=_string_id(event.get("message_id")),
            raw_message=str(event.get("raw_message") or ""),
            message=_extract_text(event.get("message"), event.get("raw_message")),
            sub_type=event.get("sub_type"),
            sender=event.get("sender"),
            time=event.get("time"),
            self_id=_string_id(event.get("self_id")),
        )


def _extract_text(message: Any, raw_message: Any) -> str:
    """Extract supported text segments and safely describe unsupported ones."""
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list):
        parts = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            if segment.get("type") == "text":
                parts.append(str(segment.get("data", {}).get("text", "")))
            elif segment.get("type") in {"image", "file", "record", "video", "forward"}:
                parts.append(f"[不支持的{segment.get('type')}消息]")
        return "".join(parts).strip()
    return str(raw_message or "").strip()

