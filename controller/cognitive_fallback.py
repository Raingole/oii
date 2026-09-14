from __future__ import annotations
from typing import Any

def device_output_succeeded(result: dict[str, Any]) -> bool:
    """Only a confirmed physical output suppresses the legacy ESP pipeline."""
    for action in result.get("actions", []):
        if action.get("type") not in {"speak", "display"}: continue
        aid=action.get("action_id")
        for item in result.get("dispatched", []):
            if item.get("action_id") == aid and item.get("status") == "succeeded" and item.get("success"):
                value=item.get("result") or {}
                if isinstance(value,dict) and value.get("status") == "completed" and value.get("output_sent") is True: return True
    return False
