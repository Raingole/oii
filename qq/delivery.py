from __future__ import annotations

from typing import Any


def cognitive_delivery_confirmed(dispatched: list[dict[str, Any]]) -> bool:
    """Return true only when a Controller executor confirmed delivery."""
    return any(
        item.get("status") == "succeeded" and item.get("success") is True
        for item in dispatched
        if isinstance(item, dict)
    )
