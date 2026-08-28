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


class QQAgentContext:
    """Formal non-ESP context presented to existing Tool executors."""

    def __init__(self, config: dict, llm: Any, logger, loop: asyncio.AbstractEventLoop):
        self.config = config
        self.llm = llm
        self.logger = logger
        self.loop = loop
        self.websocket = None
        self.device_id = None
        self.session_id = ""
        self.dialogue = Dialogue()
        self.func_handler = UnifiedToolHandler(self)


class QQAgent:
    def __init__(self, config: dict, llm: Any):
        self.config = config
        self.llm = llm
        self.logger = setup_logging(config)
        self.sessions: dict[str, QQConversation] = {}
        self.prompt = str(config.get("prompt", ""))
        self.context: QQAgentContext | None = None
        self.pipeline = AgentPipeline(config)

    async def start(self) -> None:
        if self.context is not None:
            return
        self.context = QQAgentContext(self.config, self.llm, self.logger, asyncio.get_running_loop())
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
            return await self.pipeline.process(self.context, text, session_key)

