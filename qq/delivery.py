from __future__ import annotations

from typing import Any


def cognitive_delivery_confirmed(dispatched: list[dict[str, Any]]) -> bool:
    """Return true only when a Controller executor confirmed delivery."""
    return any(
        item.get("status") == "succeeded" and item.get("success") is True
        for item in dispatched
        if isinstance(item, dict)
    )


def cognitive_delivery_attempted(actions: list[dict[str, Any]], dispatched: list[dict[str, Any]]) -> bool:
    """Return true when Controller already attempted an external message delivery."""
    has_delivery_action = any(
        isinstance(action, dict) and action.get("type") in {"send_message", "speak", "display"}
        for action in actions
    )
    return has_delivery_action and bool(dispatched)
