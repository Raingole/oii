from __future__ import annotations

from pathlib import Path
from typing import Any


def build_runtime_prompt(self_snapshot: dict[str, Any], emotion: dict[str, Any], goals: list[dict[str, Any]], memories: list[str], event: dict[str, Any], tools: list[dict[str, Any]] | None = None, persona_prompt: str = "", conversation_history: list[dict[str, Any]] | None = None) -> str:
    protocol = Path(__file__).with_name("prompt").joinpath("core_prompt.md").read_text(encoding="utf-8")
    persona = persona_prompt.strip() or "[Persona slot is empty: follow the structured Self Model.]"
    metadata = event.get("metadata") or {}
    history = conversation_history if conversation_history is not None else metadata.get("conversation_history", [])
    followup = bool(metadata.get("followup_reply"))
    return "\n\n".join([protocol, f"Persona customization: {persona}", f"Self: {self_snapshot}", f"Emotion: {emotion}", f"Goals: {goals}", f"Activated memories: {memories}", f"Recent conversation: {history}", f"Tool follow-up reply required: {followup}", f"Event: {event}", f"Tools: {tools or []}"])
