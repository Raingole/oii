"""QQ adapter over the shared Agent/Tool/MCP pipeline."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from config.logger import setup_logging
from core.agent_pipeline import AgentPipeline
from core.providers.tools.unified_tool_handler import UnifiedToolHandler
from core.utils.dialogue import Dialogue

TAG = __name__


@dataclass
class QQConversation:
    dialogue: Dialogue
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_tool_result: Any = None


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
        self.prompt = str(config.get("prompt", ""))
        self.context: QQAgentContext | None = None
        self.pipeline = AgentPipeline(config)
        self.controller = controller

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

    async def reply(self, session_key: str, text: str) -> str:
        await self.start()
        session = self._get_session(session_key)
        async with session.lock:
            self.context.dialogue = session.dialogue
            self.context.session_id = session_key
            self.context.turn_id += 1
            external_id = session_key.rsplit(":", 1)[-1] if session_key else ""
            if self.context.memory_manager:
                self.context.user_id = self.context.memory_manager.resolve_owner("qq", external_id)
            self.context.last_tool_result = session.last_tool_result
            # The ESP may connect after the QQ agent; refresh device tools for
            # every QQ turn so the current MCP tool list is visible.
            self.context.func_handler.tool_manager.refresh_tools()
            answer = await self.pipeline.process(self.context, text, session_key)
            session.last_tool_result = self.context.last_tool_result
            return answer
