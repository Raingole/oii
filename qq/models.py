"""Small, protocol-focused OneBot models used by the QQ gateway."""

from dataclasses import dataclass, field
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
    images: list[dict] = field(default_factory=list)

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
            images=_extract_images(event.get('message')),
        )


def _extract_text(message: Any, raw_message: Any) -> str:
    """Extract supported text segments and safely describe unsupported ones."""
    if isinstance(message, str):
        import re
        return re.sub(r'\[CQ:image,[^\]]+\]','[图片]',message).strip()
    if isinstance(message, list):
        parts = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            if segment.get("type") == "text":
                parts.append(str(segment.get("data", {}).get("text", "")))
            elif segment.get('type') == 'image':
                parts.append('[图片]')
            elif segment.get("type") in {"file", "record", "video", "forward"}:
                parts.append(f"[不支持的{segment.get('type')}消息]")
        return "".join(parts).strip()
    return str(raw_message or "").strip()


def _extract_images(message):
    if isinstance(message,list):
        result=[]
        for segment in message:
            if isinstance(segment,dict) and segment.get('type')=='image' and isinstance(segment.get('data'),dict):
                data=segment['data']
                result.append({key:str(data[key]) for key in ('url','file','summary','sub_type') if key in data})
        return result[:3]
    if isinstance(message,str):
        import re
        result=[]
        for match in re.finditer(r'\[CQ:image,([^\]]+)\]',message):
            fields=dict(item.split('=',1) for item in match[1].split(',') if '=' in item)
            if fields.get('url') or fields.get('file'):
                result.append({key:value.replace('&#44;',',').replace('&amp;','&') for key,value in fields.items() if key in {'url','file','summary'}})
        return result[:3]
    return []

