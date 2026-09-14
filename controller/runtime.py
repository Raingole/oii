from __future__ import annotations

from pathlib import Path
from typing import Any
from cognitive_core.runtime import CognitiveCore
from cognitive_core.memory import InMemoryAdapter, LegacyMemoryPort, TencentMemoryAdapter
from .action_outbox import ActionOutbox
from .action_dispatcher import ActionDispatcher
from .event_router import EventRouter
from .approval_service import ApprovalService
from cognitive_core.scheduler.heartbeat import HeartbeatScheduler


class ControllerRuntime:
    def __init__(self, config: dict[str, Any], legacy_memory: Any = None) -> None:
        cc = config.get("cognitive_core", {}) if isinstance(config.get("cognitive_core", {}), dict) else {}
        db_path = str(cc.get("db_path", "data/cognitive/cognitive.db"))
        memory = LegacyMemoryPort(legacy_memory, str(config.get("owner_id", "owner"))) if legacy_memory is not None and cc.get("share_legacy_memory", True) else InMemoryAdapter()
        self.core = CognitiveCore(db_path=db_path, agent_id=str(cc.get("agent_id", "central-controller")), memory=memory, config=cc)
        self.outbox = ActionOutbox(db_path)
        self.approval = ApprovalService(db_path)
        self.dispatcher = ActionDispatcher(self.outbox, approval=self.approval, max_attempts=int(cc.get("action_max_attempts", 3)), timeout=float(cc.get("action_timeout", 15)))
        self.router = EventRouter(self.core, self.dispatcher)
        self.dispatcher.event_router = self.router
        self.autonomous_action_enabled = bool(cc.get("autonomous_action_enabled", False))
        self.scheduler = HeartbeatScheduler(self.core, float(cc.get("heartbeat_interval", 60)), self.dispatcher, self.autonomous_action_enabled)
        self.controller_available = True
        self.lifecycle_trace: list[str] = []

    async def recover(self) -> list[Any]:
        self.lifecycle_trace.append("recover_outbox")
        return await self.dispatcher.recover()

    async def start_heartbeat(self) -> None:
        self.lifecycle_trace.append("heartbeat_start")
        # The scheduler also owns persisted schedules and retries, which must
        # run even when autonomous cognition is disabled.
        await self.scheduler.start()

    async def shutdown(self) -> None:
        self.lifecycle_trace.append("stop_accepting")
        self.dispatcher.stop_accepting(); self.lifecycle_trace.append("heartbeat_stop"); await self.scheduler.stop()
        self.lifecycle_trace.append("outbox_idle"); await self.dispatcher.wait_for_idle()

    def health(self) -> dict[str, Any]:
        return {"controller_bridge": self.controller_available, "action_outbox": True, "sqlite": self.core.store.path.exists(), "memory": not getattr(self.core.memory, "degraded", False)}
