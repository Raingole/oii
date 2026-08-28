"""Per-wake conversation lifecycle owned by a long-lived device connection."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class ConversationSession:
    conversation_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    active: bool = True

    def stop(self) -> None:
        self.active = False
