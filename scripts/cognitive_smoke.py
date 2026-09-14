from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cognitive_core.runtime import CognitiveCore
from controller.action_dispatcher import ActionDispatcher
from controller.action_outbox import ActionOutbox
from controller.event_router import EventRouter


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = CognitiveCore(str(Path(directory) / "core.db"), config={"initiative_threshold": 0.3})
        dispatcher = ActionDispatcher(ActionOutbox(Path(directory) / "outbox.db"))
        napcat_calls, esp32_calls = [], []

        async def napcat(action):
            napcat_calls.append(action); return {"mock": "napcat", "accepted": True}

        async def esp32(action):
            esp32_calls.append(action); return {"mock": "esp32", "accepted": True}

        dispatcher.register("send_message", napcat)
        dispatcher.register("speak", esp32)
        router = EventRouter(core, dispatcher)
        qq_event = router.build("qq", "message", "qq:1", "agent", source_event_id="m1", content={"text": "hello"}, session_id="qq:1")
        esp_event = router.build("esp32", "speech", "esp32_main", "self", source_event_id="turn-1", content={"text": "hot"}, session_id="esp32-main")
        qq_result = await router.route(qq_event)
        esp_result = await router.route(esp_event)
        duplicate = await router.route(router.build("qq", "message", "qq:1", "agent", source_event_id="m1", content={"text": "hello"}, session_id="qq:1"))
        retry_dispatcher = ActionDispatcher(ActionOutbox(Path(directory) / "retry.db"), max_attempts=2, timeout=.02)
        retry_calls = []
        async def queued_then_complete(action):
            retry_calls.append(action["action_id"])
            return {"status": "queued", "action_id": action["action_id"]} if len(retry_calls) == 1 else {"status": "completed", "output_sent": True}
        retry_dispatcher.register("speak", queued_then_complete)
        retry_action = {"action_id": "esp-retry", "type": "speak", "payload": {"text": "retry"}}
        await retry_dispatcher.dispatch("retry-event", retry_action)
        await retry_dispatcher.handle_device_action_result("esp-retry", "timeout", "esp32_main", {"error": "confirmation timeout"})
        retry_result = (await retry_dispatcher.process_due_retries())[0]
        old_completed = await retry_dispatcher.handle_device_action_result("esp-retry", "completed", "esp32_main", {"output_sent": True, "external_id": "old-confirmation"})
        duplicate_completed = await retry_dispatcher.handle_device_action_result("esp-retry", "completed", "esp32_main", {"output_sent": True, "external_id": "duplicate-confirmation"})
        schedule_dispatcher = ActionDispatcher(ActionOutbox(Path(directory) / "schedule.db"), timeout=.02)
        schedule_calls = []
        async def scheduled_executor(action): schedule_calls.append(action["action_id"]); return {"status": "queued", "action_id": action["action_id"]}
        schedule_dispatcher.register("speak", scheduled_executor)
        await schedule_dispatcher.dispatch("schedule-event", {"action_id": "schedule-1", "type": "schedule", "payload": {"due_at": 0, "next_action": {"action_id": "scheduled-action", "type": "speak", "payload": {"text": "scheduled"}}}})
        from cognitive_core.scheduler.heartbeat import HeartbeatScheduler
        scheduler = HeartbeatScheduler(core, interval=60, dispatcher=schedule_dispatcher, autonomous_enabled=False)
        schedule_pending = (await scheduler.run_once())["scheduled_results"][0]
        await schedule_dispatcher.handle_device_action_result("scheduled-action", "completed", "esp32_main", {"output_sent": True})
        schedule_duplicate = await schedule_dispatcher.handle_device_action_result("scheduled-action", "completed", "esp32_main", {"output_sent": True})
        print(json.dumps({"qq_status": qq_result["dispatched"][0]["status"], "napcat_calls": len(napcat_calls), "esp32_status": esp_result["dispatched"][0]["status"], "esp32_calls": len(esp32_calls), "duplicate": duplicate["duplicate"], "retry_status": retry_result.status, "retry_calls": len(retry_calls), "retry_final": old_completed.status, "duplicate_completed": duplicate_completed.status, "schedule_pending": schedule_pending["status"], "schedule_calls": len(schedule_calls), "schedule_duplicate": schedule_duplicate.status, "schedule_active_after_completion": len(schedule_dispatcher.outbox.recover_schedules()), "autonomous_enabled": scheduler.autonomous_enabled}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
