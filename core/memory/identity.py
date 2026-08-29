"""Stable identity mapping shared by ESP and QQ."""

from __future__ import annotations

from typing import Any

from .models import MemoryIdentity


class IdentityResolver:
    """Map channel-specific external IDs to one controller user identity."""

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self.team_id = str(config.get("tencent_memory_team_id", "personal"))
        self.agent_id = str(config.get("tencent_memory_agent_id", "central-controller"))
        self.user_id = str(config.get("tencent_memory_user_id") or config.get("owner_id") or "gu")
        self.channel_users = config.get("tencent_memory_identity", {})

    def resolve(self, channel: str = "", external_id: str = "") -> MemoryIdentity:
        # Explicit channel mappings are still resolved to one stable user. The
        # external IDs never become TencentDB user IDs themselves.
        channel_map = self.channel_users.get(channel, {}) if isinstance(self.channel_users, dict) else {}
        mapped_user = (
            channel_map.get(str(external_id)) or channel_map.get("*")
            if isinstance(channel_map, dict)
            else None
        )
        return MemoryIdentity(self.team_id, self.agent_id, str(mapped_user or self.user_id))
