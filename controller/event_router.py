from __future__ import annotations

from typing import Any
from cognitive_core.contracts import CognitiveEvent
from cognitive_core.runtime import CognitiveCore
from .action_dispatcher import ActionDispatcher
from contracts.results import ActionResult

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)


class EventRouter:
    """The only controller ingress into cognition and the only action handoff."""
    def __init__(self, core: CognitiveCore, dispatcher: ActionDispatcher): self.core, self.dispatcher = core, dispatcher

    async def route(self, event: CognitiveEvent) -> dict[str, Any]:
        logger.info(f"cognitive event received event_id={event.event_id} source={event.source} type={event.type}")
        result = await self.core.process_event(event)
        if result.get("duplicate"): return result
        actions = result.get("actions", [])
        logger.info(f"action planned event_id={event.event_id} count={len(actions)}")
        dispatched = []
        feedback = []
        for action in actions:
            logger.info(f"action dispatched event_id={event.event_id} action_id={action.get('action_id')} type={action.get('type')}")
            action_result = await self.dispatcher.dispatch(event.event_id, action)
            logger.info(f"action result event_id={event.event_id} action_id={action_result.action_id} status={action_result.status} success={action_result.success}")
            dispatched.append(action_result.to_dict())
            result_event = self.build("controller", "action_result", "self", "environment", source_event_id=f"{action_result.action_id}:{action_result.status}", content={"action_id": action_result.action_id, "success": action_result.success, "result": action_result.result, "error": action_result.error}, session_id=event.session_id, metadata={"parent_event_id": event.event_id, "action_type": action.get("type")})
            feedback.append(await self.core.process_event(result_event))
        result["dispatched"] = dispatched
        result["feedback"] = feedback
        return result

    async def handle_device_action_result(self, action_id: str, status: str, device_id: str = "", result: dict[str, Any] | None = None) -> ActionResult:
        action_result = await self.dispatcher.handle_device_action_result(action_id, status, device_id, result)
        event = self.build("controller", "action_result", "self", "environment", source_event_id=f"{action_id}:{status}", content={"action_id": action_id, "success": action_result.success, "status": action_result.status, "result": action_result.result, "error": action_result.error}, metadata={"device_id": device_id, "action_status": status})
        await self.core.process_event(event)
        return action_result

    async def wait_for_device_confirmation(self, action_id: str, timeout: float | None = None) -> ActionResult | None:
        return await self.dispatcher.wait_for_device_confirmation(action_id, timeout)

    def build(self, source: str, type: str, actor_id: str, target_id: str, source_event_id: str = "", **kwargs: Any) -> CognitiveEvent:
        event_id = f"{source}:{source_event_id}" if source_event_id else None
        return CognitiveEvent.create(source, type, actor_id, target_id, event_id=event_id, source_event_id=source_event_id, **kwargs)
