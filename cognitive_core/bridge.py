from __future__ import annotations

from typing import Any, Awaitable, Callable
from .contracts import CognitiveEvent
from .runtime import CognitiveCore


class ControllerBridge:
    """Controller-side boundary: adapters create events; controller executes actions."""
    def __init__(self, core: CognitiveCore, executor: Callable[[dict[str, Any]], Awaitable[Any]] | None = None) -> None:
        self.core, self.executor = core, executor

    async def dispatch(self, event: CognitiveEvent) -> dict[str, Any]:
        result = await self.core.process_event(event)
        executed = []
        for action in result.get("actions", []):
            if self.executor is not None: executed.append(await self.executor(action))
        result["executed"] = executed
        return result

    async def qq_message(self, user_id: str, text: str, session_id: str) -> dict[str, Any]:
        return await self.dispatch(CognitiveEvent.create("qq", "message", f"qq:{user_id}", "agent", {"text": text}, session_id))

    async def esp32_speech(self, device_id: str, text: str, session_id: str) -> dict[str, Any]:
        return await self.dispatch(CognitiveEvent.create("esp32", "speech", device_id, "self", {"text": text}, session_id))
