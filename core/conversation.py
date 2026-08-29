"""Per-wake conversation lifecycle owned by a long-lived device connection."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class ConversationState(str, Enum):
    WAIT_WAKE_WORD = "WAIT_WAKE_WORD"
    ACTIVE = "ACTIVE"
    ENDING = "ENDING"


@dataclass
class ConversationSession:
    conversation_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    active: bool = True
    destroyed: bool = False

    def stop(self) -> None:
        self.active = False

    async def stop_processing(self) -> None:
        """Stop this conversation's processing without touching its connection."""
        self.active = False

    async def destroy(self, reason: str = "conversation_end") -> None:
        """Release conversation state; the owning WebSocket remains alive."""
        await self.stop_processing()
        self.destroyed = True
