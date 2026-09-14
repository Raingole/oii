from __future__ import annotations

import json
import re
from typing import Any, Callable


def parse_structured(raw: str, validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse model JSON with one repair attempt and deterministic fallback."""
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        try:
            start, end = str(raw).find("{"), str(raw).rfind("}")
            fragment = str(raw)[start:end + 1] if start >= 0 and end > start else ""
            fragment = re.sub(r":\s*\.(\d+)", r": 0.\1", fragment)
            value = json.loads(fragment) if fragment else None
        except (TypeError, json.JSONDecodeError):
            value = None
    if not isinstance(value, dict): return dict(fallback or {})
    try: return validator(value) if validator else value
    except (KeyError, TypeError, ValueError): return dict(fallback or {})


def validate_appraisal(value: dict[str, Any]) -> dict[str, float]:
    keys = ("self_relevance", "goal_relevance", "novelty", "controllability", "certainty")
    return {key: max(0.0, min(1.0, float(value[key]))) for key in keys}
