from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from .structured import parse_structured


class LLMError(RuntimeError):
    """Base class for cognitive LLM adapter failures."""


class LLMDecisionError(LLMError):
    """Raised when an LLM response is not a valid structured decision."""


class LLMPort(Protocol):
    async def decide(self, prompt: str, context: dict[str, Any], trace_id: str = "") -> dict[str, Any]: ...


def validate_decision(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("decision must be an object")
    if "intent" not in value:
        raise ValueError("decision.intent is required")
    intent = str(value.get("intent", "")).strip()
    if intent not in {"reply", "tool_call", "wait", "do_nothing", "ask_confirmation"}:
        raise ValueError("invalid decision intent")
    risk = str(value.get("risk_level", "low")).strip()
    if risk not in {"low", "medium", "high", "critical"}:
        raise ValueError("invalid decision risk")
    args = value.get("arguments", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("arguments must be object")
    message = value.get("message", "")
    if message is None:
        message = ""
    tool = value.get("tool", "")
    if tool is None:
        tool = ""
    return {
        "intent": intent,
        "message": str(message),
        "tool": str(tool),
        "arguments": args,
        "risk_level": risk,
        "reason": str(value.get("reason", "") or ""),
        "goal_id": str(value.get("goal_id", "") or ""),
    }


def parse_decision(raw: str) -> dict[str, Any]:
    """Parse and validate one model response without silently accepting invalid output."""
    parsed = parse_structured(str(raw), None, None)
    if not isinstance(parsed, dict) or not parsed:
        raise LLMDecisionError("invalid_json")
    try:
        return validate_decision(parsed)
    except (TypeError, ValueError) as exc:
        raise LLMDecisionError(f"invalid_schema:{exc}") from exc


class ExistingProviderLLM:
    """Adapter for the repository's legacy streaming LLM provider.

    The provider is only allowed to produce text.  Parsing, schema validation
    and action construction happen outside the provider, so a model can never
    execute NapCat, ESP32 or MCP directly.
    """

    def __init__(self, provider: Any, timeout: float | None = None):
        if provider is None:
            raise ValueError("provider is required")
        self.provider = provider
        self.timeout = timeout

    async def _raw_response(self, system: str, user: str) -> str:
        def call() -> str:
            if hasattr(self.provider, "response_no_stream"):
                return str(self.provider.response_no_stream(system, user))
            if hasattr(self.provider, "response"):
                result = self.provider.response(
                    "",
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                return "".join(str(part) for part in result)
            raise LLMError("provider_has_no_response_method")

        if self.timeout and self.timeout > 0:
            return await asyncio.wait_for(asyncio.to_thread(call), timeout=self.timeout)
        return await asyncio.to_thread(call)

    async def complete_structured(self, system: str, user: str, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = await self._raw_response(system, user)
            return parse_structured(raw, validate_decision, fallback)
        except Exception:
            if fallback is not None:
                return dict(fallback)
            raise

    async def decide(self, prompt: str, context: dict[str, Any] | str, trace_id: str = "") -> dict[str, Any]:
        user = context if isinstance(context, str) else json.dumps(context, ensure_ascii=False, default=str)
        raw = await self._raw_response(prompt, user)
        return parse_decision(raw)


class DecisionLLM:
    """Compatibility facade for callers that still pass a raw provider."""

    def __init__(self, llm: Any):
        self.llm = llm

    def _port(self) -> LLMPort:
        if hasattr(self.llm, "decide"):
            return self.llm
        return ExistingProviderLLM(self.llm)

    async def decide(self, prompt: str, context: dict[str, Any] | str, trace_id: str = "") -> dict[str, Any]:
        return await self._port().decide(prompt, context, trace_id)
