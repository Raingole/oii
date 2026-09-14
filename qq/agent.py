"""QQ adapter over the shared Agent/Tool/MCP pipeline."""

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.logger import setup_logging
from core.providers.tools.unified_tool_handler import UnifiedToolHandler
from core.utils.dialogue import Dialogue, Message
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
        if self.controller is not None and getattr(self.controller, "event_router", None) is not None:
            try:
                func_handler = getattr(self.context, "func_handler", None)
                if func_handler is not None:
                    func_handler.tool_manager.refresh_tools()
                    raw_tools = func_handler.get_functions()
                    tools = []
                    for tool in raw_tools or []:
                        if not isinstance(tool, dict):
                            continue
                        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
                        name = function.get("name") or tool.get("name")
                        if not name:
                            continue
                        tools.append({
                            "name": str(name),
                            "description": function.get("description") or tool.get("description", ""),
                            "parameters": function.get("parameters") or tool.get("parameters", {}),
                        })
                    self.controller.event_router.core.config["available_tools"] = tools
                    self.logger.bind(tag=TAG).info(f"Cognitive tool catalog refreshed: {len(tools)} tools")
            except Exception as exc:
                self.logger.bind(tag=TAG).warning(f"Cognitive tool catalog refresh failed: {exc}")
        session = self._get_session(session_key)
        conversation_history = []
        for message in session.dialogue.get_llm_dialogue():
            role = str(message.get("role", ""))
            content = message.get("content")
            if role in {"user", "assistant"} and content:
                conversation_history.append({"role": role, "content": str(content)})
        conversation_history = conversation_history[-12:]
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
                    event = self.controller.event_router.build("qq", "message", actor_id, "agent", source_event_id=source_event_id, content={"text": text}, session_id=session_key, metadata={"platform": "napcat", "conversation_history": conversation_history, **(event_metadata or {})})
                    result = await self.controller.event_router.route(event)
                    messages = [a.get("payload", {}).get("text", "") for a in result.get("actions", []) if a.get("type") == "send_message"]
                    dispatched = result.get("dispatched", [])
                    # An action being accepted by the Cognitive Core is not
                    # enough to suppress the legacy reply.  Only a confirmed
                    # succeeded ActionResult means that a reply was really
                    # delivered by a Controller executor.
                    handled = bool(result.get("duplicate")) or cognitive_delivery_confirmed(dispatched)
                    reply_text = messages[0] if messages else ""
                    if handled and not result.get("duplicate"):
                        session.dialogue.put(Message(role="user", content=text))
                        if reply_text:
                            session.dialogue.put(Message(role="assistant", content=reply_text))
                    return ReplyResult(reply_text, handled, dispatched, event.event_id)
                except Exception as exc:
                    self.logger.bind(tag=TAG).error(f"Cognitive router failed; legacy fallback is disabled: {exc}")
                    # Do not fall back to AgentPipeline.  The cognitive path
                    # has already produced/dispatched actions in most failure
                    # cases, so an extra legacy reply could duplicate output.
                    return ReplyResult("", True, [], source_event_id)
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
                    self.logger.bind(tag=TAG).warning(f"Cognitive Core HTTP client unavailable; legacy fallback is disabled: {exc}")
            self.logger.bind(tag=TAG).error("No cognitive event router available; legacy AgentPipeline is disabled")
            return ReplyResult("思维核心当前不可用，请检查 Controller Cognitive Core 状态。", False, [], source_event_id)

    async def reply(self, session_key: str, text: str, source_event_id: str = "", event_metadata: dict[str, Any] | None = None) -> str:
        """Legacy compatibility API; request state remains in the return value."""
        return (await self.reply_result(session_key, text, source_event_id, event_metadata)).text
