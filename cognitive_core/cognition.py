from __future__ import annotations

from typing import Any


def appraisal(event: Any, goals: list[dict[str, Any]]) -> dict[str, float]:
    text = str(event.content.get("text", ""))
    lower = text.lower()
    goal_relevance = max((0.85 if g.get("status", "active") == "active" and any(w in lower for w in str(g.get("title", "")).lower().split()) else 0.2 for g in goals), default=0.1)
    failure = event.content.get("success") is False or any(word in lower for word in ("失败", "掉线", "错误", "timeout"))
    return {"self_relevance": 0.8 if event.target_id in {"self", "agent"} else 0.4, "goal_relevance": goal_relevance,
            "novelty": 0.7 if event.type in {"heartbeat", "action_result", "speech"} else 0.4,
            "pleasantness": 0.2 if failure else 0.6, "controllability": 0.5, "certainty": 0.8,
            "relationship_relevance": 0.7 if event.source == "qq" else 0.2, "social_relevance": 0.8 if event.source == "qq" else 0.1,
            "emotional_salience": 0.8 if failure else 0.3}


def meaning(event: Any, app: dict[str, float]) -> dict[str, Any]:
    text = str(event.content.get("text", "")).strip()
    return {"event_summary": text or event.type, "meaning": "这件事需要被持续观察" if event.type == "heartbeat" else "收到了一项外部事件",
            "self_implication": "我需要更新当前状态并保持证据边界", "relationship_implication": "关系上下文可能需要更新" if event.source == "qq" else "",
            "goal_implication": "可能影响当前目标" if app["goal_relevance"] > 0.5 else "", "future_implication": "后续根据结果再决定", "confidence": app["certainty"]}


def emotion_delta(event: Any, app: dict[str, float]) -> dict[str, float]:
    if app["pleasantness"] < 0.4: return {"frustration": 0.15, "arousal": 0.1, "valence": -0.1}
    if event.source == "qq": return {"social_desire": -0.05, "joy": 0.03, "valence": 0.03}
    return {"curiosity": 0.03}
