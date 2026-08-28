"""QQ conversation adapter reusing the existing LLM provider and Dialogue."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from config.logger import setup_logging
from core.utils.dialogue import Dialogue, Message

TAG = __name__


@dataclass
class QQConversation:
    dialogue: Dialogue
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class QQAgent:
    def __init__(self, config: dict, llm: Any):
        self.config = config
        self.llm = llm
        self.logger = setup_logging(config)
        self.sessions: dict[str, QQConversation] = {}
        self.prompt = str(config.get("prompt", ""))

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
        session = self._get_session(session_key)
        async with session.lock:
            session.dialogue.put(Message(role="user", content=text))
            dialogue = session.dialogue.get_llm_dialogue()
            try:
                parts = await asyncio.to_thread(self._collect_response, session_key, dialogue)
            except Exception as exc:
                self.logger.bind(tag=TAG).error(f"QQ Agent failed: session={session_key}, error={exc}")
                session.dialogue.dialogue.pop()
                return "抱歉，我暂时无法处理这条消息。"
            answer = "".join(parts).strip()
            if not answer:
                return "抱歉，我没有生成有效回复。"
            session.dialogue.put(Message(role="assistant", content=answer))
            return answer

    def _collect_response(self, session_key: str, dialogue: list[dict[str, Any]]) -> list[str]:
        if self.llm is None:
            raise RuntimeError("LLM provider is not initialized")
        return [str(part) for part in self.llm.response(session_key, dialogue) if part]

