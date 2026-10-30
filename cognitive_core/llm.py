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
    audio_reply=value.get('audio_reply',{})
    if not isinstance(audio_reply,dict):
        audio_reply={}
    audio_type=str(audio_reply.get('type','') or '').strip().lower()
    if audio_type not in {'tts','bilibili'}:
        audio_reply={}
    else:
        audio_reply={
            'type':audio_type,
            'text':str(audio_reply.get('text','') or '')[:300],
            'video':str(audio_reply.get('video','') or '')[:160],
            'from_seconds':audio_reply.get('from_seconds'),
            'to_seconds':audio_reply.get('to_seconds'),
            'caption':str(audio_reply.get('caption','') or '')[:160],
            'instruction':str(audio_reply.get('instruction','') or '')[:120],
        }
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
        "native_tool_markup": bool(value.get("native_tool_markup", False)),
        "risk_level": risk,
        "reason": str(value.get("reason", "") or ""),
        "goal_id": str(value.get("goal_id", "") or ""),
        "image_reply": {'query':str(image_reply.get('query','') or '')[:240],
                        'caption':str(image_reply.get('caption','') or '')[:240]},
        "audio_reply": audio_reply,
    }


def parse_decision(raw: str) -> dict[str, Any]:
    """Parse a model decision, recovering safe conversational text only.

    Tool and other action intents still require a complete, valid structured
    decision. A malformed action response is never inferred or executed.
    """
    text = str(raw).strip()
    compact = re.sub(r"\s+", "", text).replace("｜", "|").lower()
    if re.search(r"<\|+dsml\|+", compact):
        # Some DeepSeek-compatible endpoints return their native tool tokens
        # in content, using full-width or doubled pipes. Normalize only the
        # envelope and accept exactly one fully formed call; dispatch still
        # checks the registered tool and its declared risk level.
        parsed_call = _parse_native_dsml_call(text)
        if parsed_call is None:
            raise LLMDecisionError("unparsed_tool_markup")
        return validate_decision({**parsed_call, "native_tool_markup": True})
    if any(marker in compact for marker in ("<tool_call", "<function_call")):
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


def _parse_native_dsml_call(text: str) -> dict[str, Any] | None:
    """Convert one complete DeepSeek DSML call into the internal decision shape."""
    normalized = text.replace("｜", "|")
    normalized = re.sub(r"\|{2,}", "|", normalized)
    normalized = re.sub(
        r"<\|\s*dsml\s*\|\s*(calls|invoke|parameter)\b([^>]*)>",
        lambda match: f"<dsml-{match.group(1).lower()}{match.group(2)}>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"</\|\s*dsml\s*\|\s*(calls|invoke|parameter)\s*>",
        lambda match: f"</dsml-{match.group(1).lower()}>",
        normalized,
        flags=re.IGNORECASE,
    )
    envelope = re.fullmatch(r"\s*<dsml-calls\s*>(.*?)</dsml-calls>\s*", normalized, re.IGNORECASE | re.DOTALL)
    if not envelope:
        return None
    invokes = list(re.finditer(r'<dsml-invoke\s+name\s*=\s*(["\'])(.*?)\1\s*>(.*?)</dsml-invoke>', envelope.group(1), re.IGNORECASE | re.DOTALL))
    if len(invokes) != 1:
        return None
    invocation = invokes[0]
    tool_name = invocation.group(2).strip()
    body = invocation.group(3)
    arguments: dict[str, Any] = {}
    cursor = 0
    for match in re.finditer(r"<dsml-parameter\b([^>]*)>(.*?)</dsml-parameter>", body, re.IGNORECASE | re.DOTALL):
        if body[cursor:match.start()].strip():
            return None
        attrs, raw_value = match.group(1), match.group(2)
        name_match = re.search(r'\bname\s*=\s*(["\'])(.*?)\1', attrs, re.IGNORECASE)
        if not name_match:
            return None
        name = name_match.group(2).strip()
        if not name or name in arguments or re.search(r"<\|+dsml\|+", raw_value.replace("｜", "|"), re.IGNORECASE):
            return None
        value = raw_value.strip()
        string_match = re.search(r'\bstring\s*=\s*(["\'])(.*?)\1', attrs, re.IGNORECASE)
        if string_match and string_match.group(2).lower() == "true":
            arguments[name] = value
        else:
            try:
                arguments[name] = json.loads(value)
            except (TypeError, ValueError):
                arguments[name] = value
        cursor = match.end()
    if body[cursor:].strip():
        return None
    if not tool_name:
        return None
    return {"intent": "tool_call", "tool": tool_name, "arguments": arguments, "risk_level": "low",
            "reason": "registered low-risk call recovered from DeepSeek DSML"}


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
