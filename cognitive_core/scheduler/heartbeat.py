from __future__ import annotations

import asyncio
import time
from typing import Any
from ..runtime import CognitiveCore


class HeartbeatScheduler:
    def __init__(self, core: CognitiveCore, interval: float = 60.0, dispatcher: Any = None, autonomous_enabled: bool = True):
        self.core, self.interval, self.dispatcher, self.autonomous_enabled = core, max(0.1, interval), dispatcher, autonomous_enabled; self._task: asyncio.Task | None = None; self._stop = asyncio.Event(); self.last_tick: float = 0.0; self.running = False

    async def start(self) -> None:
        if self._task and not self._task.done(): return
        self._stop.clear(); self.running = True; self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set(); self.running = False
        if self._task: await self._task

    async def run_once(self) -> dict[str, Any]:
        result={"full_tick":False,"actions":[]}
        if self.autonomous_enabled and (not self.last_tick or time.monotonic() - self.last_tick >= self.interval / 2):
            self.last_tick = time.monotonic(); result = await self.core.run_once()
            if self.dispatcher:
                for action in result.get("actions", []): await self.dispatcher.dispatch(result.get("event_id", "heartbeat"), action)
        if self.dispatcher:
            result["scheduled_results"] = [x.to_dict() for x in await self.dispatcher.process_due_schedules()]
            result["retry_results"] = [x.to_dict() for x in await self.dispatcher.process_due_retries()]
        return result

    async def _run(self) -> None:
        while not self._stop.is_set():
            try: await self.run_once()
            except asyncio.CancelledError: raise
            except Exception: await asyncio.sleep(min(self.interval, 5.0))
            try: await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError: pass
