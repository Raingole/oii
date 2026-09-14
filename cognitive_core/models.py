from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EmotionState:
    valence: float = 0.0
    arousal: float = 0.2
    anger: float = 0.0
    frustration: float = 0.0
    sadness: float = 0.0
    joy: float = 0.0
    curiosity: float = 0.5
    trust: float = 0.5
    confidence: float = 0.5
    social_desire: float = 0.3
    energy: float = 0.8

    def clamp(self) -> "EmotionState":
        for key, value in asdict(self).items():
            setattr(self, key, max(-1.0, min(1.0, value)) if key == "valence" else max(0.0, min(1.0, value)))
        return self

    def decay(self, rate: float = 0.05) -> "EmotionState":
        for key in ("arousal", "anger", "frustration", "sadness", "joy"):
            setattr(self, key, getattr(self, key) * max(0.0, 1.0 - rate))
        return self.clamp()


@dataclass
class PersonalityTrait:
    base: float
    current: float
    confidence: float = 1.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class SelfModel:
    identity: dict[str, Any] = field(default_factory=lambda: {"name": "待配置", "role": "persistent digital agent"})
    personality: dict[str, PersonalityTrait] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)
    beliefs: dict[str, dict[str, Any]] = field(default_factory=dict)
    preferences: dict[str, Any] = field(default_factory=dict)
    relationships: dict[str, dict[str, Any]] = field(default_factory=dict)
    body: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_goals: list[dict[str, Any]] = field(default_factory=list)
    emotion: EmotionState = field(default_factory=EmotionState)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["personality"] = {k: asdict(v) for k, v in self.personality.items()}
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SelfModel":
        personality = {k: PersonalityTrait(**v) for k, v in raw.get("personality", {}).items()}
        emotion = EmotionState(**raw.get("emotion", {}))
        return cls(identity=raw.get("identity", {}), personality=personality, values=raw.get("values", {}),
                   beliefs=raw.get("beliefs", {}), preferences=raw.get("preferences", {}),
                   relationships=raw.get("relationships", {}), body=raw.get("body", {}),
                   active_goals=raw.get("active_goals", []), emotion=emotion)
