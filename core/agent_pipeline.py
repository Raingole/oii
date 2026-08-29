"""Shared text Agent pipeline for channel adapters."""

import asyncio
from typing import Any

from config.logger import setup_logging
from core.utils.dialogue import Message

TAG = __name__


class AgentPipeline:
    """Run LLM turns and the existing ToolManager/MCP execution loop."""

    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging(config)

    async def process(self, context: Any, query: str, session_id: str) -> str:
        memory_manager = getattr(context, "memory_manager", None)
        channel = getattr(context, "channel", "")
        user_id = getattr(context, "user_id", "owner")
        if memory_manager is not None:
            memory_manager.observe_text(query, channel, session_id)
        context.current_user_query = query
        context.dialogue.put(Message(role="user", content=query))
        try:
            answer = await self._turn(context, session_id, int(self.config.get("tool_call_max_depth", 5)))
        except Exception as exc:
            self.logger.bind(tag=TAG).error(f"Agent pipeline failed: {exc}")
            context.dialogue.dialogue.pop()
            return "抱歉，我暂时无法处理这条消息。"
        answer = answer.strip()
        if answer:
            context.dialogue.put(Message(role="assistant", content=answer))
            if memory_manager is not None:
                memory_manager.record_turn(
                    query,
                    answer,
                    channel,
                    session_id,
                    getattr(context, "last_tool_result", None),
                )
            return answer
        return "抱歉，我没有生成有效回复。"

    async def _turn(self, context: Any, session_id: str, remaining: int) -> str:
        functions = self._get_functions(context)
        dialogue = context.dialogue.get_llm_dialogue()
        memory_manager = getattr(context, "memory_manager", None)
        if memory_manager is not None:
            memory_prompt = memory_manager.retrieve_prompt(
                getattr(context, "user_id", "owner"),
                getattr(context, "channel", ""),
                session_id,
                getattr(context, "current_user_query", ""),
            )
            if memory_prompt:
                if dialogue and isinstance(dialogue[0], dict) and dialogue[0].get("role") == "system":
                    dialogue[0]["content"] = f"{dialogue[0].get('content', '')}\n\n{memory_prompt}"
                else:
                    dialogue = [{"role": "system", "content": memory_prompt}] + dialogue
        if functions:
            text, calls = await asyncio.to_thread(
                self._collect_function_response, context.llm, session_id, dialogue, functions
            )
        else:
            text = "".join(await asyncio.to_thread(self._collect_response, context.llm, session_id, dialogue))
            calls = []
        if not calls:
            return text
        if remaining <= 0:
            return text or "抱歉，工具调用次数已达到上限。"

        assistant_calls = []
        results = []
        for call in calls:
            call_id = call["id"]
            assistant_calls.append({
                "id": call_id, "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]},
            })
            result = await context.func_handler.handle_llm_function_call(context, {
                "name": call["name"], "arguments": call["arguments"], "id": call_id,
            })
            results.append((call_id, result))
        context.dialogue.put(Message(role="assistant", tool_calls=assistant_calls))
        for call_id, result in results:
            content = getattr(result, "result", None) or getattr(result, "response", None) or ""
            context.dialogue.put(Message(role="tool", tool_call_id=call_id, content=str(content)))
        return await self._turn(context, session_id, remaining - 1)

    def _get_functions(self, context: Any) -> list[dict]:
        if not getattr(context, "func_handler", None):
            return []
        intent_config = self.config.get("Intent", {}).get(
            self.config.get("selected_module", {}).get("Intent", ""), {}
        )
        if intent_config.get("type", "") not in {"function_call", ""}:
            return []
        try:
            return list(context.func_handler.get_functions())
        except Exception as exc:
            self.logger.bind(tag=TAG).warning(f"Unable to load Agent tools: {exc}")
            return []

    @staticmethod
    def _collect_response(llm: Any, session_id: str, dialogue: list[dict]) -> list[str]:
        if llm is None:
            raise RuntimeError("LLM provider is not initialized")
        return [str(part) for part in llm.response(session_id, dialogue) if part]

    @classmethod
    def _collect_function_response(cls, llm: Any, session_id: str, dialogue: list[dict], functions: list[dict]):
        if llm is None:
            raise RuntimeError("LLM provider is not initialized")
        text_parts, calls = [], {}
        for content, deltas in llm.response_with_functions(session_id, dialogue, functions=functions):
            if content:
                text_parts.append(str(content))
            for delta in deltas or []:
                index = int(cls._field(delta, "index", 0) or 0)
                call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                call["id"] += str(cls._field(delta, "id", "") or "")
                function = cls._field(delta, "function", None)
                call["name"] += str(cls._field(function, "name", "") or "")
                call["arguments"] += str(cls._field(function, "arguments", "") or "")
        normalized = []
        for index, call in sorted(calls.items()):
            if call["name"]:
                normalized.append({"id": call["id"] or f"call-{index}", "name": call["name"], "arguments": call["arguments"] or "{}"})
        return "".join(text_parts), normalized

    @staticmethod
    def _field(value: Any, name: str, default: Any) -> Any:
        return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)
