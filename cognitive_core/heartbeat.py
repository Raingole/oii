from __future__ import annotations

from .runtime import CognitiveCore


class AutonomousLoop:
    def __init__(self, core: CognitiveCore, interval: float = 60.0, enabled: bool = True) -> None:
        self.core, self.interval, self.enabled = core, interval, enabled
    async def run_once(self) -> dict:
        return await self.core.run_once()
