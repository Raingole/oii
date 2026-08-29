"""Stable memory service contract used by the controller."""

from abc import ABC, abstractmethod
from typing import Any


class MemoryService(ABC):
    @abstractmethod
    def initialize(self) -> None:
        """Initialize the client and perform a non-fatal health check."""

    @abstractmethod
    def health_check(self) -> bool:
        """Return whether the remote memory service is reachable."""

    @abstractmethod
    def recall_for_turn(
        self, user_id: str, session_id: str, turn_id: int | str, query: str
    ) -> str:
        """Return a bounded prompt fragment; failures must return an empty string."""

    @abstractmethod
    def commit_turn(
        self,
        user_id: str,
        session_id: str,
        turn_id: int | str,
        user_text: str,
        assistant_text: str,
        source: str = "unknown",
    ) -> Any:
        """Persist a completed user/assistant turn."""

    @abstractmethod
    def commit_tool_result(
        self,
        user_id: str,
        session_id: str,
        turn_id: int | str,
        tool_name: str,
        tool_arguments: Any,
        tool_result: Any,
    ) -> Any:
        """Persist a tool pair as a non-blocking side effect."""

    @abstractmethod
    def finalize_session(self, user_id: str, session_id: str) -> Any:
        """Flush/close a remote session when explicitly requested."""

    @abstractmethod
    def close(self) -> None:
        """Release client resources."""

