from __future__ import annotations

from typing import Any
from .contracts import Action


def plan(event: Any, self_model: Any, goals: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> list[Action]:
    text = str(event.content.get("text", "")).strip()
    if event.type in {"notification_sent", "action_result", "body_online", "body_offline"}:
        return [Action.create("do_nothing", reason="observation/result event recorded")]
    if event.type == "heartbeat":
        if self_model.emotion.social_desire >= 0.7 and event.metadata.get("social_target"):
            target = event.metadata["social_target"]
            return [Action.create("send_message", {"text": "我刚刚想到你，最近还好吗？", "platform": event.metadata.get("platform", "napcat")}, channel="qq", target=target, reason="social initiative")]
        if any(g.get("status") == "active" and "天气" in g.get("title", "") for g in goals):
            return [Action.create("call_mcp", {"tool": "weather", "arguments": {}}, risk_level="low", reason="unfinished goal")]
        return [Action.create("do_nothing", reason="initiative below action threshold")]
    if event.source == "qq" and text:
        return [Action.create("send_message", {"text": "这次没能生成回复，请稍后再试。", "platform": event.metadata.get("platform", "napcat"), "source_message_id": event.metadata.get("source_message_id", event.source_event_id)}, channel=str(event.metadata.get("reply_channel") or "qq"), target=str(event.metadata.get("reply_target") or event.actor_id), reason="reply generation unavailable")]
    if event.source == "esp32" and text:
        return [Action.create("speak", {"text": "这次没能生成回复，请稍后再试。"}, channel="esp32", target=event.actor_id, reason="reply generation unavailable")]
    return [Action.create("wait", reason="no immediate action")]
