from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import Action, CognitiveEvent
from .cognition import appraisal, emotion_delta, meaning
from .models import SelfModel
from .planning import plan
from .storage import StateStore
from .memory import InMemoryAdapter, MemoryAdapter, should_store
from .prompt_builder import build_runtime_prompt
from .llm import DecisionLLM, validate_decision


class CognitiveCore:
    def __init__(self, db_path: str = "data/cognitive/cognitive.db", agent_id: str = "default", memory: MemoryAdapter | None = None, seed_path: str | None = None, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        seed_file = Path(seed_path or Path(__file__).with_name("persona_seed.json"))
        self.seed = json.loads(seed_file.read_text(encoding="utf-8"))
        self.agent_id = agent_id; self.store = StateStore(db_path); self.memory = memory or InMemoryAdapter()
        self.self_model: SelfModel = self.store.load_self(agent_id, self.seed)
        self.llm = None
        self.goals: list[dict[str, Any]] = self.self_model.active_goals
        self.processed_events: set[str] = set()
        self.last_tick: dict[str, Any] = {}
        self.reflections: list[dict[str, Any]] = []
        self.timeline: list[dict[str, Any]] = []
        self.bodies: dict[str, dict[str, Any]] = dict(self.self_model.body)

    async def process_event(self, event: CognitiveEvent) -> dict[str, Any]:
        if event.event_id in self.processed_events or not self.store.claim_event(event.event_id):
            return {"event_id": event.event_id, "duplicate": True, "actions": []}
        self.store.put("events", event.event_id, event.to_dict()); self.processed_events.add(event.event_id)
        identity = {
            "user_id": event.metadata.get("user_id", event.actor_id if event.source != "system" else "global"),
            "channel": event.metadata.get("channel", event.source),
            "session_id": event.session_id,
            "agent_id": self.agent_id,
            "team_id": event.metadata.get("team_id", self.config.get("team_id", "personal")),
        }
        memories = await self.memory.search(str(event.content.get("text", event.type)), top_k=int(self.config.get("memory_top_k", 8)), context_budget=int(self.config.get("memory_context_budget", 4000)), **identity)
        app = appraisal(event, self.goals); delta = emotion_delta(event, app)
        for key, value in delta.items(): setattr(self.self_model.emotion, key, getattr(self.self_model.emotion, key) + value)
        self.self_model.emotion.clamp()
        meaning_result = meaning(event, app)
        actions = plan(event, self.self_model, self.goals)
        if self.llm is not None and self.config.get("llm_enabled", False) and event.source in {"qq", "esp32"}:
            try:
                prompt = build_runtime_prompt(self.self_model.to_dict(), self.self_model.emotion.to_dict(), self.goals, [m.text for m in memories], event.to_dict(), self.config.get("available_tools", []), str(self.config.get("persona_prompt", "")))
                provider = self.llm if hasattr(self.llm, "decide") else DecisionLLM(self.llm.provider if hasattr(self.llm, "provider") else self.llm)
                decision, _summary = await provider.decide(prompt, str(event.content), {"intent": "do_nothing"}, event.metadata.get("trace_id", ""))
                intent=decision.get("intent")
                if intent == "wait": actions = [Action.create("wait", reason=decision.get("reason", "LLM structured decision"))]
                elif intent == "do_nothing": actions = [Action.create("do_nothing", reason=decision.get("reason", "LLM structured decision"))]
                elif intent == "reply" and decision.get("message"):
                    target=event.actor_id; action_type="speak" if event.source=="esp32" else "send_message"
                    actions=[Action.create(action_type,{"text":decision["message"],"platform":event.metadata.get("platform", event.source)},channel=event.source,target=target,risk_level=decision.get("risk_level","low"),reason=decision.get("reason","LLM decision"),requires_controller_approval=decision.get("risk_level") in {"high","critical"})]
                elif intent == "tool_call":
                    if decision.get("tool") and (not self.config.get("available_tools") or decision["tool"] in {x.get("name") for x in self.config.get("available_tools", [])}):
                        risk=decision.get("risk_level","low"); actions=[Action.create("call_mcp",{"tool":decision["tool"],"arguments":decision.get("arguments",{})},risk_level=risk,reason=decision.get("reason","LLM tool decision"),requires_controller_approval=risk in {"high","critical"})]
                    else: actions=[Action.create("do_nothing",reason="LLM requested unavailable tool")]
            except Exception:
                pass
        memory_policy = should_store(event, app)
        if memory_policy == "episodic" or event.type == "action_result":
            scope = {
                "importance": max(app.values()), "event_id": event.event_id,
                "source": event.source, "channel": event.metadata.get("channel", event.source),
                "session_id": event.session_id, "user_id": event.metadata.get("user_id", event.actor_id),
                "team_id": event.metadata.get("team_id", self.config.get("team_id", "personal")),
                "agent_id": self.agent_id, "provenance": {"event_id": event.event_id, "source": event.source},
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
        self.last_tick = {"event": event.to_dict(), "appraisal": app, "meaning": meaning_result, "memory_refs": [m.text for m in memories]}
        return {"event_id": event.event_id, "appraisal": app, "meaning": meaning_result, "memory_refs": [m.text for m in memories], "actions": [a.to_dict() for a in actions]}

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
