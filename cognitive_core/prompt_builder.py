from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def _compact(value: Any) -> str:
    """Serialize runtime sections deterministically and without Python repr noise."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def build_runtime_prompt(self_snapshot: dict[str, Any], emotion: dict[str, Any], goals: list[dict[str, Any]], memories: list[str], event: dict[str, Any], tools: list[dict[str, Any]] | None = None, persona_prompt: str = "", conversation_history: list[dict[str, Any]] | None = None) -> str:
    protocol = Path(__file__).with_name("prompt").joinpath("core_prompt.md").read_text(encoding="utf-8")
    persona = persona_prompt.strip() or "[Persona slot is empty: follow the structured Self Model.]"
    metadata = event.get("metadata") or {}
    history = conversation_history if conversation_history is not None else metadata.get("conversation_history", [])
    followup = bool(metadata.get("followup_reply"))
    # Keep the immutable protocol/persona at the beginning so providers that
    # support prefix caching can reuse it.  Runtime state is deliberately
    # compact and appears only once in the system prompt; callers must not
    # duplicate the same state in the user context.
    return "\n\n".join([
        protocol,
        f"Persona customization: {persona}",
        "Runtime state (changes per turn):",
        f"Self: {_compact(self_snapshot)}",
        f"Emotion: {_compact(emotion)}",
        f"Goals: {_compact(goals)}",
        f"Activated memories: {_compact(memories)}",
        f"Recent conversation: {_compact(history)}",
        f"Tool follow-up reply required: {str(followup).lower()}",
        f"Event: {_compact(event)}",
        f"Tools: {_compact(tools or [])}",
    ])
