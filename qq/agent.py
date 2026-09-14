"""QQ adapter over the shared Agent/Tool/MCP pipeline."""

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.logger import setup_logging
from core.agent_pipeline import AgentPipeline
from core.providers.tools.unified_tool_handler import UnifiedToolHandler
from core.utils.dialogue import Dialogue
from jinja2 import Template
from cognitive_core.client import CognitiveClient
from urllib.error import URLError, HTTPError
from .delivery import cognitive_delivery_confirmed

TAG = __name__


@dataclass
class QQConversation:
    dialogue: Dialogue
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_tool_result: Any = None

@dataclass
class ReplyResult:
    text: str
    handled_by_cognitive: bool
    action_results: list[dict[str, Any]] = field(default_factory=list)
    event_id: str = ""
    trace_id: str = ""


class QQAgentContext:
    """Formal non-ESP context presented to existing Tool executors."""

    def __init__(self, config: dict, llm: Any, logger, loop: asyncio.AbstractEventLoop, controller=None):
        self.config = config
        self.llm = llm
        self.logger = logger
        self.loop = loop
        self.websocket = None
        self.device_id = None
        self.server = controller
        self.memory_manager = getattr(controller, "memory_manager", None)
        self.user_id = self.memory_manager.resolve_owner("qq") if self.memory_manager else "owner"
        self.channel = "qq"
        self.session_id = ""
        self.dialogue = Dialogue()
        self.func_handler = UnifiedToolHandler(self)
        intent_config = config.get("Intent", {}) if isinstance(config.get("Intent", {}), dict) else {}
        selected_intent = config.get("selected_module", {}).get("Intent", "nointent") if isinstance(config.get("selected_module", {}), dict) else "nointent"
        selected_settings = intent_config.get(selected_intent, {}) if isinstance(intent_config.get(selected_intent, {}), dict) else {}
        self.intent_type = str(selected_settings.get("type", selected_intent))
        self.load_function_plugin = self.intent_type in {"function_call", "intent_llm"}
        self.last_tool_result = None
        self.current_user_query = ""
        self.turn_id = 0

    @property
    def device_connection(self):
        """Return the configured or first online ESP for QQ device controls."""
        if self.server is None:
            return None
        connections = getattr(self.server, "connections", {})
        preferred_id = str(self.config.get("qq", {}).get("device_id", "")).strip()
        if preferred_id and preferred_id in connections:
            candidate = connections[preferred_id]
            if getattr(candidate, "mcp_client", None):
                return candidate
        for candidate in connections.values():
            if getattr(candidate, "mcp_client", None):
                return candidate
        return None


class QQAgent:
    def __init__(self, config: dict, llm: Any, controller=None):
        self.config = config
        self.llm = llm
        self.logger = setup_logging(config)
        self.sessions: dict[str, QQConversation] = {}
        self.prompt = self._build_prompt(str(config.get("prompt", "")))
        self.context: QQAgentContext | None = None
        self.pipeline = AgentPipeline(config)
        self.controller = controller
        cc = config.get("cognitive_core", {}) if isinstance(config.get("cognitive_core", {}), dict) else {}
        self.cognitive_client = CognitiveClient(str(cc.get("url", "http://127.0.0.1:8010")), float(cc.get("timeout", 3))) if cc.get("enabled", False) else None

    def _build_prompt(self, base_prompt: str) -> str:
        """Apply the shared project prompt structure without rewriting the user's prompt."""
        template_path = Path(__file__).resolve().parents[1] / "cognitive_core" / "prompt" / "core_prompt.md"
        try:
            template = Template(template_path.read_text(encoding="utf-8"))
            return template.render(
                base_prompt=base_prompt,
                language="中文",
                current_time="",
                today_date="",
                today_weekday="",
                lunar_date="",
                local_address="",
                weather_info="",
                dynamic_context="",
                emoji_enabled=False,
                emojiList=[],
            )
        except (OSError, UnicodeError, ValueError) as exc:
            self.logger.warning(f"QQ prompt template unavailable; using original prompt: {exc}")
            return base_prompt

    async def start(self) -> None:
        if self.context is not None:
            return
        self.context = QQAgentContext(
            self.config, self.llm, self.logger, asyncio.get_running_loop(), self.controller
        )
        if self.prompt:
            self.context.dialogue.update_system_message(self.prompt)
        try:
            await self.context.func_handler._initialize()
            self.logger.bind(tag=TAG).info("QQ Agent shared Tool/MCP pipeline initialized")
        except Exception as exc:
            self.logger.bind(tag=TAG).error(f"QQ Agent Tool/MCP initialization failed: {exc}")

    def _get_session(self, key: str) -> QQConversation:
        session = self.sessions.get(key)
        if session is None:
            dialogue = Dialogue()
            if self.prompt:
                dialogue.update_system_message(self.prompt)
            session = QQConversation(dialogue=dialogue)
            self.sessions[key] = session
        return session

    async def reply_result(self, session_key: str, text: str, source_event_id: str = "", event_metadata: dict[str, Any] | None = None) -> ReplyResult:
        self.logger.bind(tag=TAG).info(f"QQ agent start: session={session_key}, text_length={len(text or '')}")
        await self.start()
        session = self._get_session(session_key)
        async with session.lock:
            if not source_event_id:
                meta = event_metadata or {}
                bucket = meta.get("timestamp_bucket", int(time.time() // 300))
                external = session_key.rsplit(":", 1)[-1] if session_key else "owner"
                normalized = " ".join(str(text or "").split()).casefold()
                source_event_id = "fallback:" + hashlib.sha256(f"qq|{external}|{session_key}|{normalized}|{bucket}".encode()).hexdigest()
            if getattr(self.controller, "event_router", None) is not None:
                try:
                    external_id = session_key.rsplit(":", 1)[-1] if session_key else "owner"
                    actor_id = str((event_metadata or {}).get("actor_id") or f"qq:{external_id}")
                    if not actor_id.startswith("qq:"):
                        actor_id = f"qq:{actor_id}"
                    event = self.controller.event_router.build("qq", "message", actor_id, "agent", source_event_id=source_event_id, content={"text": text}, session_id=session_key, metadata={"platform": "napcat", **(event_metadata or {})})
                    result = await self.controller.event_router.route(event)
                    messages = [a.get("payload", {}).get("text", "") for a in result.get("actions", []) if a.get("type") == "send_message"]
                    dispatched = result.get("dispatched", [])
                    # An action being accepted by the Cognitive Core is not
                    # enough to suppress the legacy reply.  Only a confirmed
                    # succeeded ActionResult means that a reply was really
                    # delivered by a Controller executor.
                    handled = bool(result.get("duplicate")) or cognitive_delivery_confirmed(dispatched)
                    return ReplyResult(messages[0] if messages else "", handled, dispatched, event.event_id)
                except Exception as exc:
                    self.logger.bind(tag=TAG).warning(f"Cognitive router unavailable; falling back to legacy pipeline: {exc}")
            if self.cognitive_client is not None:
                try:
                    external_id = str((event_metadata or {}).get("actor_id") or session_key.rsplit(":", 1)[-1] if session_key else "owner")
                    if external_id.startswith("qq:"):
                        external_id = external_id[3:]
                    event = {
                        "event_id": f"qq:{source_event_id}" if source_event_id else "",
                        "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                        "source": "qq", "type": "message", "actor_id": f"qq:{external_id}", "target_id": "agent",
                        "session_id": session_key, "content": {"text": text}, "metadata": {},
                    }
                    result = await self.cognitive_client.process(event)
                    actions = result.get("actions", [])
                    messages = [a.get("payload", {}).get("text", "") for a in actions if a.get("type") == "send_message"]
                    if messages: return ReplyResult(messages[0], True, [], event.get("event_id", ""))
                except (URLError, HTTPError, TimeoutError, OSError, ValueError) as exc:
                    self.logger.bind(tag=TAG).warning(f"Cognitive Core unavailable; falling back to legacy pipeline: {exc}")
            self.context.dialogue = session.dialogue
            self.context.session_id = session_key
            self.context.turn_id += 1
            external_id = str((event_metadata or {}).get("actor_id") or session_key.rsplit(":", 1)[-1] if session_key else "")
            if external_id.startswith("qq:"):
                external_id = external_id[3:]
            if self.context.memory_manager:
                self.context.user_id = self.context.memory_manager.resolve_owner("qq", external_id)
            self.context.last_tool_result = session.last_tool_result
            # The ESP may connect after the QQ agent; refresh device tools for
            # every QQ turn so the current MCP tool list is visible.
            self.context.func_handler.tool_manager.refresh_tools()
            answer = await self.pipeline.process(self.context, text, session_key)
            session.last_tool_result = self.context.last_tool_result
            self.logger.bind(tag=TAG).info(f"QQ agent finish: session={session_key}, answer_length={len(answer or '')}")
            return ReplyResult(answer, False, [], source_event_id)

    async def reply(self, session_key: str, text: str, source_event_id: str = "", event_metadata: dict[str, Any] | None = None) -> str:
        """Legacy compatibility API; request state remains in the return value."""
        return (await self.reply_result(session_key, text, source_event_id, event_metadata)).text
