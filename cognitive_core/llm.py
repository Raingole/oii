from __future__ import annotations

import asyncio
import inspect
import json
import re
from typing import Any, Protocol

from .structured import parse_structured
from core.memory_encoding import clean_observation


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
    if intent not in {"reply", "tool_call", "schedule", "request_confirmation", "ask_confirmation", "wait", "observe", "do_nothing"}:
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
    messages = value.get("messages", [])
    if messages is None:
        messages = []
    if not isinstance(messages, list):
        raise ValueError("messages must be an array")
    # DeepSeek occasionally includes an empty item or an extra split bubble.
    # These are presentation defects, not a reason to drop an otherwise valid
    # reply. Never coerce non-text entries into user-facing output.
    messages = [item.strip() for item in messages if isinstance(item, str) and item.strip()][:4]
    tool = value.get("tool", "")
    if tool is None:
        tool = ""
    image_reply=value.get('image_reply',{})
    if not isinstance(image_reply,dict):
        image_reply={}
    return {
        "intent": intent,
        "message": str(message),
        "messages": [item.strip() for item in messages],
        "memory_observation": clean_observation(value.get('memory_observation')),
        "learning_interests": [
            {'topic':item['topic'][:60], 'evidence':item['evidence'][:160]}
            for item in (value.get('learning_interests') if isinstance(value.get('learning_interests'),list) else [])[:2]
            if isinstance(item,dict) and isinstance(item.get('topic'),str) and isinstance(item.get('evidence'),str)
        ],
        "tool": str(tool),
        "arguments": args,
        "risk_level": risk,
        "reason": str(value.get("reason", "") or ""),
        "goal_id": str(value.get("goal_id", "") or ""),
        "image_reply": {'query':str(image_reply.get('query','') or '')[:240],
                        'caption':str(image_reply.get('caption','') or '')[:240]},
    }


def parse_decision(raw: str) -> dict[str, Any]:
    """Parse a model decision, recovering safe conversational text only.

    Tool and other action intents still require a complete, valid structured
    decision. A malformed action response is never inferred or executed.
    """
    text = str(raw).strip()
    compact = re.sub(r"\s+", "", text).replace("｜", "|").lower()
    if any(marker in compact for marker in ("<|dsml|calls", "<|dsml|invoke", "<|dsml|parameter", "<tool_call", "<function_call")):
        # DeepSeek-native tool markup is not the cognitive JSON protocol. It
        # must never be surfaced as chat text or interpreted as an action.
        raise LLMDecisionError("unparsed_tool_markup")
    parsed = parse_structured(text, None, None)
    if not isinstance(parsed, dict) or not parsed:
        # A provider can occasionally answer in plain text despite the JSON
        # protocol. Preserve an ordinary answer as a reply; never expose a
        # truncated JSON/action payload as chat text.
        structured_markers = ('"intent"', "'intent'", '"tool"', '"arguments"', "```json", "{")
        if text and re.search(r'[\w\u3400-\u9fff]', text) and not any(marker in text for marker in structured_markers):
            return validate_decision({"intent": "reply", "message": text, "reason": "plain-text response recovered"})
        raise LLMDecisionError("invalid_json")
    if "intent" not in parsed:
        # Infer reply only when the object contains no executable/action data.
        # The prompt's common envelope fields are permitted only when empty.
        action_data = (
            bool(parsed.get("tool"))
            or bool(parsed.get("arguments"))
            or bool(parsed.get("goal_id"))
            or bool(parsed.get("requires_controller_approval"))
        )
        message = parsed.get("message")
        messages = parsed.get("messages")
        has_reply = (isinstance(message, str) and bool(message.strip())) or (
            isinstance(messages, list) and any(isinstance(item, str) and item.strip() for item in messages)
        )
        if not action_data and has_reply:
            parsed = {
                "intent": "reply",
                "message": message if isinstance(message, str) else "",
                "messages": messages if isinstance(messages, list) else [],
                "reason": "reply recovered from incomplete decision",
            }
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

    async def _raw_response(self, system: str, user: str, session_id: str = "") -> str:
        def call() -> str:
            if hasattr(self.provider, "response"):
                response = self.provider.response
                response_options: dict[str, Any] = {}
                # Cognitive decisions include the full memory/affect envelope.
                # The general chat cap (often 500 tokens) can truncate that JSON
                # before its closing delimiter, especially on tool-rich turns.
                # Raise the cap only for providers that advertise kwargs or an
                # explicit max_tokens parameter; other provider APIs stay intact.
                try:
                    parameters = inspect.signature(response).parameters.values()
                    accepts_max_tokens = any(
                        parameter.name == "max_tokens" or parameter.kind is inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters
                    )
                except (TypeError, ValueError):
                    accepts_max_tokens = False
                if accepts_max_tokens:
                    configured_cap = getattr(self.provider, "max_tokens", None)
                    try:
                        response_options["max_tokens"] = max(1200, int(configured_cap or 0))
                    except (TypeError, ValueError):
                        response_options["max_tokens"] = 1200
                result = response(
                    session_id,
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    **response_options,
                )
                return "".join(str(part) for part in result)
            if hasattr(self.provider, "response_no_stream"):
                return str(self.provider.response_no_stream(system, user))
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
        session_id = ""
        if isinstance(context, dict):
            event = context.get("event")
            event_session_id = event.get("session_id") if isinstance(event, dict) else ""
            session_id = str(context.get("session_id") or event_session_id or "")
        raw = await self._raw_response(prompt, user, session_id=session_id)
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
