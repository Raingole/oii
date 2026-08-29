"""Stable identity mapping shared by ESP and QQ."""

from __future__ import annotations

import os
from typing import Any

from .models import MemoryIdentity


class IdentityResolver:
    """Map channel-specific external IDs to one controller user identity."""

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self.team_id = os.getenv(
            "TENCENT_MEMORY_TEAM_ID",
            str(config.get("tencent_memory_team_id", "personal")),
        )
        self.agent_id = os.getenv(
            "TENCENT_MEMORY_AGENT_ID",
            str(config.get("tencent_memory_agent_id", "central-controller")),
        )
        self.user_id = os.getenv(
            "TENCENT_MEMORY_USER_ID",
            str(config.get("tencent_memory_user_id") or config.get("owner_id") or "yin2hao"),
        )

    def resolve(self, channel: str = "", external_id: str = "") -> MemoryIdentity:
        # Channel/external_id are intentionally audit metadata only. They must
        # never become the TencentDB user_id, otherwise QQ and ESP memories split.
        return MemoryIdentity(self.team_id, self.agent_id, self.user_id)

