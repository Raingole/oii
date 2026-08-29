"""Turn-scoped lifecycle primitives shared by ESP and server-side channels."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConnectionState(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


class AudioInputState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"


class TurnState(str, Enum):
    IDLE = "IDLE"
    ASR = "ASR"
    THINKING = "THINKING"
    TOOL_CALLING = "TOOL_CALLING"
    RESPONDING = "RESPONDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TTSState(str, Enum):
    IDLE = "IDLE"
    STREAMING = "STREAMING"
    CANCELLED = "CANCELLED"


@dataclass
class TurnContext:
    turn_id: int
    client_event_id: str = ""
    state: TurnState = TurnState.IDLE
    tts_state: TTSState = TTSState.IDLE
    asr_buffer: list[bytes] = field(default_factory=list)
    llm_task: asyncio.Task | None = None
    mcp_tasks: set[asyncio.Task] = field(default_factory=set)
    tts_task: asyncio.Task | None = None
    input_timeout_task: asyncio.Task | None = None
    cancelled_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def cancelled(self) -> bool:
        return self.cancelled_event.is_set() or self.state == TurnState.CANCELLED

    def is_current(self, active_turn_id: int | None) -> bool:
        return not self.cancelled and active_turn_id == self.turn_id

    def cancel(self) -> None:
        self.state = TurnState.CANCELLED
        self.tts_state = TTSState.CANCELLED
        self.cancelled_event.set()
        for task in (self.llm_task, self.tts_task, self.input_timeout_task):
            if task and not task.done():
                task.cancel()
        for task in tuple(self.mcp_tasks):
            if not task.done():
                task.cancel()

    def track(self, task: asyncio.Task, kind: str = "mcp") -> asyncio.Task:
        if kind == "llm":
            self.llm_task = task
        elif kind == "tts":
            self.tts_task = task
        else:
            self.mcp_tasks.add(task)
        return task


class TurnManager:
    """Own turn identity, cancellation and listen-event idempotency."""

    def __init__(self, logger=None):
        self.logger = logger
        self._next_turn_id = 100
        self.active_turn: TurnContext | None = None
        self._event_turns: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def start(self, client_event_id: str = "") -> tuple[TurnContext, bool]:
        async with self._lock:
            event_id = str(client_event_id or "")
            if event_id and event_id in self._event_turns:
                turn_id = self._event_turns[event_id]
                if self.active_turn and self.active_turn.turn_id == turn_id:
                    return self.active_turn, True
                # A late retransmission must acknowledge its original turn,
                # never resurrect it or create a new turn.
                return TurnContext(turn_id=turn_id, client_event_id=event_id), True

            previous = self.active_turn
            if previous and not previous.cancelled:
                previous.cancel()
                if self.logger:
                    self.logger.info(
                        f"[turn={previous.turn_id}] new turn, cancelling previous turn"
                    )

            self._next_turn_id += 1
            turn = TurnContext(turn_id=self._next_turn_id, client_event_id=event_id)
            self.active_turn = turn
            if event_id:
                self._event_turns[event_id] = turn.turn_id
                if len(self._event_turns) > 256:
                    self._event_turns.pop(next(iter(self._event_turns)))
            return turn, False

    async def cancel_active(self) -> TurnContext | None:
        async with self._lock:
            turn = self.active_turn
            if turn:
                turn.cancel()
            return turn

    def is_current(self, turn_id: int | None) -> bool:
        return bool(
            turn_id is not None
            and self.active_turn
            and self.active_turn.is_current(turn_id)
        )


def get_turn_id(value: Any) -> int | None:
    """Read a turn id from DTO-like objects without breaking old callers."""
    value = getattr(value, "turn_id", value)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
