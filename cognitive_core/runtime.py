from __future__ import annotations

import json
import time
import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

from .contracts import Action, CognitiveEvent
from .cognition import appraisal, emotion_delta, meaning
from .models import SelfModel
from .planning import plan
from .storage import StateStore
from .memory import InMemoryAdapter, MemoryAdapter, should_store
from .prompt_builder import build_runtime_prompt
from .llm import ExistingProviderLLM, validate_decision, LLMDecisionError
from .observability import build_activity
from .reply_parts import split_reply_parts


class CognitiveCore:
    def __init__(self, db_path: str = "data/cognitive/cognitive.db", agent_id: str = "default", memory: MemoryAdapter | None = None, seed_path: str | None = None, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        seed_file = Path(seed_path or Path(__file__).with_name("persona_seed.json"))
        self.seed = json.loads(seed_file.read_text(encoding="utf-8"))
        self.agent_id = agent_id; self.store = StateStore(db_path); self.memory = memory or InMemoryAdapter()
        self.self_model: SelfModel = self.store.load_self(agent_id, self.seed)
        self.llm = None
        self.learning = self.knowledge = self.vision = None
        self.goals: list[dict[str, Any]] = self.self_model.active_goals
        self.processed_events: set[str] = set()
        self.last_tick: dict[str, Any] = {}
        self.reflections: list[dict[str, Any]] = []
        self.timeline: list[dict[str, Any]] = []
        self.bodies: dict[str, dict[str, Any]] = dict(self.self_model.body)

    async def process_event(self, event: CognitiveEvent) -> dict[str, Any]:
        if event.event_id in self.processed_events or not self.store.claim_event(event.event_id):
            return {"event_id": event.event_id, "duplicate": True, "actions": []}
        self.processed_events.add(event.event_id)
        images = event.content.get('images') or event.metadata.get('images') or []
        if isinstance(images,list) and images:
            visual = await self.vision.describe_many(event.session_id,images) if self.vision else [{'status':'unavailable','error':'vision_provider_not_configured'}]
            metadata = {key:value for key,value in event.metadata.items() if key!='images'}
            content = {key:value for key,value in event.content.items() if key!='images'}
            event = replace(event,content={**content,'visual_context':visual,'image_count':min(3,len(images))},metadata=metadata)
        self.store.put("events", event.event_id, event.to_dict())
        proactive = bool(event.metadata.get('proactive_thought'))
        if event.source == 'qq' and event.type == 'message' and event.content.get('text') and event.session_id.startswith('qq:private:') and event.metadata.get('platform', 'napcat') == 'napcat':
            self.store.runtime_set(f'contact:{self.agent_id}', {
                'session_id': event.session_id, 'actor_id': event.actor_id,
                'target': self._reply_target(event), 'platform': 'napcat',
                'last_input_at': time.time(), 'event_id': event.event_id,
            })
        identity = {
            "user_id": event.metadata.get("user_id", event.actor_id if event.source != "system" else "global"),
            "channel": event.metadata.get("channel", event.source),
            "session_id": event.session_id,
            "agent_id": self.agent_id,
            "team_id": event.metadata.get("team_id", self.config.get("team_id", "personal")),
        }
        memories = await self.memory.search(str(event.content.get("text", event.type)), top_k=int(self.config.get("memory_top_k", 8)), context_budget=int(self.config.get("memory_context_budget", 4000)), **identity)
        references = []
        if self.knowledge:
            try:
                references = await asyncio.to_thread(self.knowledge.search,event.session_id,str(event.content.get('text','')))
            except Exception:
                logger.warning(f'knowledge retrieval failed event_id={event.event_id}')
        # Conversation context is short-lived working state, not long-term
        # memory.  Rebuild it from SQLite so restart/send-failure does not
        # erase the meaning of the previous turn.
        stored_history = self.store.recent_conversation(
            event.session_id,
            int(self.config.get("conversation_window", 12)),
        )
        supplied_history = event.metadata.get("conversation_history", [])
        history: list[dict[str, str]] = []
        for item in [*stored_history, *(supplied_history if isinstance(supplied_history, list) else [])]:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"} or not item.get("content"):
                continue
            normalized = {"role": str(item["role"]), "content": str(item["content"])}
            if normalized not in history:
                history.append(normalized)
        history = history[-int(self.config.get("conversation_window", 12)) * 2:]
        goals_for_turn = json.loads(json.dumps(self.goals))
        app = appraisal(event, goals_for_turn); delta = emotion_delta(event, app)
        for key, value in delta.items(): setattr(self.self_model.emotion, key, getattr(self.self_model.emotion, key) + value)
        self.self_model.emotion.clamp()
        meaning_result = meaning(event, app)
        actions = plan(event, self.self_model, goals_for_turn)
        trace_id = str(event.metadata.get("trace_id") or event.event_id)
        decision = None
        llm_used = False
        generation = {"status": "not_requested", "attempts": 0}
        conversational = event.source in {"qq", "esp32"} or event.metadata.get("followup_reply") or proactive
        if conversational:
            generation["status"] = "unavailable" if self.config.get("llm_enabled", False) else "disabled"
        if self.llm is not None and self.config.get("llm_enabled", False) and conversational:
            try:
                prompt = build_runtime_prompt(
                    self.self_model.to_dict(),
                    self.self_model.emotion.to_dict(),
                    goals_for_turn,
                    [m.text for m in memories],
                    event.to_dict(),
                    [] if proactive else self.config.get("available_tools", []),
                    str(self.config.get("persona_prompt", "")),
                    history,
                    references,
                )
                llm = self.llm
                if not hasattr(llm, "decide"):
                    llm = ExistingProviderLLM(llm, timeout=float(self.config.get("llm_timeout", 60)))
                # The full state is already present once in the system
                # prompt.  Sending it again as JSON user content doubled
                # input tokens and made prefix-cache reuse less effective.
                context = {
                    "event": event.to_dict(),
                    "trace_id": trace_id,
                    "session_id": event.session_id,
                }
                # Retry inference only, before any action is persisted or sent.
                # A failed attempt cannot execute tools or duplicate delivery.
                for attempt in range(1, 3):
                    generation["attempts"] = attempt
                    try:
                        candidate = validate_decision(await llm.decide(prompt, context, trace_id))
                        if candidate["intent"] == "reply" and not (candidate["messages"] or candidate["message"].strip()):
                            raise LLMDecisionError("empty_reply")
                        candidate_actions = self._actions_from_decision(candidate, event)
                    except Exception as exc:
                        if attempt == 2:
                            raise
                        logger.warning(f"cognitive_llm_retry error_type={type(exc).__name__} trace_id={trace_id}")
                    else:
                        decision, actions, llm_used = candidate, candidate_actions, True
                        break
                generation["status"] = "retry_succeeded" if generation["attempts"] > 1 else "succeeded"
                logger.info(f"llm decision generated trace_id={trace_id} intent={decision.get('intent', '')}")
            except Exception as exc:
                generation.update(status="failed", error_type=type(exc).__name__)
                decision = None
                actions = [Action.create('do_nothing', reason='proactive generation failed')] if proactive else self._actions_from_decision({"intent": "reply", "message": "这次没能生成回复，请稍后再试。", "reason": "reply generation failed"}, event)
                logger.warning(f"cognitive_llm_fallback reason={exc} trace_id={trace_id}")
        if self.learning and llm_used:
            try:
                self.learning.observe(event,decision)
            except Exception:
                logger.warning(f'learning interest capture failed event_id={event.event_id}')
        memory_policy = 'discard' if proactive else should_store(event, app)
        # Technical action_result events are evidence for the action log, not
        # user dialogue. Do not manufacture "用户：action_result" memories.
        if not (proactive or event.metadata.get('proactive_parent')) and memory_policy == "episodic" and event.type != 'action_result':
            scope = {
                "importance": max(app.values()), "event_id": event.event_id,
                "source": event.source, "channel": event.metadata.get("channel", event.source),
                "session_id": event.session_id, "user_id": event.metadata.get("user_id", event.actor_id),
                "team_id": event.metadata.get("team_id", self.config.get("team_id", "personal")),
                "agent_id": self.agent_id, "provenance": {"event_id": event.event_id, "source": event.source},
                "memory_observation": ({**(decision or {}).get('memory_observation', {}),
                    "visual_context": event.content.get('visual_context', [])} if llm_used else {}),
                "assistant_text": '\n'.join(str(action.payload.get('text', '')) for action in actions if action.type in {'send_message', 'speak'}) if llm_used else '',
            }
            await self.memory.store(meaning_result["event_summary"], scope)
        if max(app.values()) >= 0.75 or event.type == "action_result":
            timeline = {"id": event.event_id, "title": meaning_result["event_summary"], "kind": event.type, "evidence": [event.event_id]}
            self.timeline.append(timeline); self.store.put("timeline_events", event.event_id, timeline)
        if event.type == "action_result" and app.get("pleasantness", 1) < 0.4:
            reflection = {"id": event.event_id, "lesson": "失败结果需要验证", "future_strategy": "关键工具结果执行观察或重试", "confidence": 0.75}
            reflection["lesson"] = "验证失败结果并记录原因"
            reflection["future_strategy"] = "关键工具结果需要观察或重试"
            self.reflections.append(reflection); self.store.put("reflections", event.event_id, reflection)
        if event.source == "esp32" and event.type in {"body_online", "body_offline"}:
            body_id = event.actor_id; self.bodies.setdefault(body_id, {"id": body_id, "ownership": "self", "capabilities": []})["online"] = event.type == "body_online"
            self.self_model.body = self.bodies
        self.self_model.active_goals = self.goals; self.store.save_self(self.agent_id, self.self_model)
        for action in actions: self.store.put("actions", action.action_id, action.to_dict())
        if proactive or event.metadata.get('followup_reply') or (event.source in {"qq", "esp32", "sms", "email"} and event.type in {"message", "speech", "notification"}):
            assistant_text = '\n'.join(
                    str(action.payload.get("text", ""))
                    for action in actions
                    if action.type in {"send_message", "speak"} and action.payload.get("text")
            )
            self.store.save_conversation_turn(
                event.event_id,
                event.session_id,
                str(identity["user_id"]),
                str(identity["channel"]),
                '' if proactive or event.metadata.get('followup_reply') else str(event.content.get("text", "")),
                assistant_text,
            )
        if proactive or (event.source in {"qq", "esp32", "sms", "email"} and event.content.get("text")) or event.metadata.get("followup_reply"):
            manager = getattr(self.memory, "manager", None)
            backend = getattr(manager, "backend_name", "adapter")
            try:
                activity = build_activity(event, memories, meaning_result, goals_for_turn, actions, decision, llm_used, backend)
                activity["generation"] = generation
                activity['knowledge_refs'] = references
                activity['visual_context'] = event.content.get('visual_context',[])
                self.store.save_memory_activity(activity)
            except Exception:
                logger.warning(f"memory observer capture failed event_id={event.event_id}")
        self.last_tick = {"event": event.to_dict(), "appraisal": app, "meaning": meaning_result, "memory_refs": [m.text for m in memories]}
        return {"event_id": event.event_id, "appraisal": app, "meaning": meaning_result, "memory_refs": [m.text for m in memories], "actions": [a.to_dict() for a in actions], "generation": generation}

    @staticmethod
    def _reply_target(event: CognitiveEvent) -> str:
        return str(event.metadata.get("reply_target") or event.metadata.get("actor_id") or event.actor_id)

    @staticmethod
    def _tool_name(item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        direct = item.get("name")
        if direct:
            return str(direct)
        function = item.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        return ""

    def _actions_from_decision(self, decision: dict[str, Any], event: CognitiveEvent) -> list[Action]:
        intent = decision.get("intent")
        if event.metadata.get('proactive_thought') and intent not in {'reply', 'wait', 'observe', 'do_nothing'}:
            return [Action.create('do_nothing', reason='proactive thought cannot execute tools or schedules')]
        risk = str(decision.get("risk_level", "low") or "low")
        requires_approval = risk in {"high", "critical"}
        reason = str(decision.get("reason", "") or "LLM structured decision")
        if intent == "wait":
            return [Action.create("wait", reason=reason)]
        if intent == "do_nothing":
            return [Action.create("do_nothing", reason=reason)]
        if intent == "observe":
            return [Action.create("observe", reason=reason)]
        if intent in {"request_confirmation", "ask_confirmation"}:
            return [Action.create(
                "request_confirmation",
                {"text": str(decision.get("message", "") or "")},
                channel=event.source,
                target=self._reply_target(event),
                risk_level=risk,
                reason=reason,
                requires_controller_approval=requires_approval,
            )]
        if intent == "reply":
            parts = decision.get('messages') or [str(decision.get('message', '') or '').strip()]
            # Voice has one active utterance; official QQ has its own reply
            # sequencing limits. Preserve all text in one transport message.
            if event.source == 'esp32' or event.metadata.get('platform') == 'official':
                parts = ['\n'.join(parts)]
            else:
                parts = split_reply_parts(parts)
            if not parts or not any(part.strip() for part in parts):
                return [Action.create("do_nothing", reason="LLM reply missing message")]
            actions = []
            for index, message in enumerate(parts):
                voice = event.source == 'esp32'
                payload = {'text': message} if voice else {
                    'text': message, 'platform': event.metadata.get('platform', event.source),
                    'source_message_id': event.metadata.get('source_message_id', event.source_event_id)}
                if len(parts) > 1:
                    payload.update(reply_part=index + 1, reply_total=len(parts))
                actions.append(Action.create('speak' if voice else 'send_message', payload,
                    channel='esp32' if voice else str(event.metadata.get('reply_channel') or 'qq'),
                    target=event.actor_id if voice else self._reply_target(event),
                    risk_level=risk, reason=reason, requires_controller_approval=requires_approval))
            image_reply=decision.get('image_reply') or {}
            image_query=str(image_reply.get('query','') or '').strip()
            if (image_query and event.source=='qq' and event.metadata.get('platform')=='napcat'
                    and event.session_id.startswith('qq:private:')
                    and 'qq_send_relevant_image' in {self._tool_name(item) for item in self.config.get('available_tools',[]) or []}):
                actions.append(Action.create('call_mcp',
                    {'tool':'qq_send_relevant_image','arguments':{'query':image_query,
                     'caption':str(image_reply.get('caption','') or '')[:240]},
                     'session_id':event.session_id,'reply_target':self._reply_target(event),
                     'reply_channel':event.metadata.get('reply_channel') or 'qq','platform':'napcat',
                     'silent_followup':True},
                    target=self._reply_target(event),channel=str(event.metadata.get('reply_channel') or 'qq'),
                    risk_level='low',reason='按当前对话情绪匹配本地图片',requires_controller_approval=False))
            return actions
        if intent == "schedule":
            arguments = decision.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}
            arguments.setdefault("next_action", {"type": "do_nothing"})
            return [Action.create(
                "schedule",
                arguments,
                risk_level=risk,
                reason=reason,
                requires_controller_approval=requires_approval,
            )]
        if intent == "tool_call":
            tool = str(decision.get("tool", "") or "").strip()
            available_tools = self.config.get("available_tools", []) or []
            allowed = {self._tool_name(item) for item in available_tools}
            if not tool or (available_tools and tool not in allowed):
                return [Action.create("do_nothing", reason="LLM requested unavailable tool")]
            arguments = decision.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}
            return [Action.create(
                "call_mcp",
                {"tool": tool, "arguments": arguments, "session_id": event.session_id,
                 "reply_target": self._reply_target(event),
                 "reply_channel": event.metadata.get("reply_channel") or event.source,
                 "platform": event.metadata.get("platform", event.source)},
                target=self._reply_target(event),
                channel=str(event.metadata.get("reply_channel") or event.source),
                risk_level=risk,
                reason=reason,
                requires_controller_approval=requires_approval,
            )]
        return [Action.create("do_nothing", reason="unsupported LLM decision")]

    async def run_once(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        emotion = self.self_model.emotion
        score = (emotion.social_desire * 0.35 + emotion.curiosity * 0.15 + emotion.frustration * 0.2 + sum(0.3 for g in self.goals if g.get("status") == "active"))
        event = CognitiveEvent.create("system", "heartbeat", "self", "self", metadata=metadata or {"initiative_score": score})
        if score < float(self.config.get("initiative_threshold", 0.75)):
            self.self_model.emotion.decay(float(self.config.get("emotion_decay_rate", 0.05))); self.store.save_self(self.agent_id, self.self_model)
            return {"initiative_score": score, "full_tick": False, "actions": [Action.create("do_nothing", reason="low initiative").to_dict()]}
        result = await self.process_event(event); result.update({"initiative_score": score, "full_tick": True}); return result

    def create_goal(self, title: str, description: str = "", origin: str = "user", priority: float = 0.5, deadline: str | None = None, parent_goal: str | None = None) -> dict[str, Any]:
        import uuid
        goal = {"id": str(uuid.uuid4()), "title": title, "description": description, "origin": origin, "priority": priority, "status": "active", "progress": 0.0, "deadline": deadline, "parent_goal": parent_goal}
        self.goals.append(goal); self.self_model.active_goals = self.goals; self.store.save_self(self.agent_id, self.self_model); self.store.put("goals", goal["id"], goal); return goal

    def snapshot(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "self": self.self_model.to_dict(), "goals": self.goals, "last_tick": self.last_tick, "reflections": self.reflections, "timeline": self.timeline, "bodies": self.bodies}
