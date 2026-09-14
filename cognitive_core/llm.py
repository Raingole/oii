from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol
from .structured import parse_structured


class LLMPort(Protocol):
    async def complete_structured(self, system: str, user: str, fallback: dict[str, Any]) -> dict[str, Any]: ...


class ExistingProviderLLM:
    """Adapter for the repository's provider interface; provider never gets tool execution authority."""
    def __init__(self, provider: Any, validator=None): self.provider, self.validator = provider, validator
    async def complete_structured(self, system: str, user: str, fallback: dict[str, Any]) -> dict[str, Any]:
        def call() -> str:
            return str(self.provider.response_no_stream(system, user))
        raw = await asyncio.to_thread(call)
        return parse_structured(raw, self.validator, fallback)

def validate_decision(value: dict[str, Any]) -> dict[str, Any]:
    intent=str(value.get("intent", "do_nothing"))
    if intent not in {"reply","tool_call","wait","do_nothing","ask_confirmation"}: raise ValueError("invalid decision intent")
    risk=str(value.get("risk_level", "low"))
    if risk not in {"low","medium","high","critical"}: raise ValueError("invalid decision risk")
    args=value.get("arguments", {})
    if not isinstance(args, dict): raise ValueError("arguments must be object")
    return {"intent":intent,"message":str(value.get("message", "")),"tool":str(value.get("tool", "")),"arguments":args,"risk_level":risk,"reason":str(value.get("reason", "")),"goal_id":str(value.get("goal_id", ""))}

class DecisionLLM:
    """Structured decision facade with safe parsing and timing metadata."""
    def __init__(self, provider: Any): self.provider=provider
    async def decide(self, system: str, user: str, fallback: dict[str, Any], trace_id: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
        started=time.perf_counter()
        result=await ExistingProviderLLM(self.provider, validate_decision).complete_structured(system,user,fallback)
        return result, {"trace_id":trace_id,"duration_ms":round((time.perf_counter()-started)*1000,2)}
