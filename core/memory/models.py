"""Backend-neutral memory value objects."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryIdentity:
    team_id: str
    agent_id: str
    user_id: str


@dataclass(frozen=True)
class MemoryTurn:
    user_id: str
    session_id: str
    turn_id: int | str
    user_text: str
    assistant_text: str
    source: str = "unknown"


@dataclass(frozen=True)
class ToolMemoryEvent:
    user_id: str
    session_id: str
    turn_id: int | str
    tool_name: str
    tool_arguments: Any
    tool_result: Any

