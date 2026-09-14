from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    server: str
    risk_level: str = "low"
    enabled: bool = True
    requires_controller_approval: bool = False


class ToolRegistry:
    def __init__(self) -> None: self.tools: dict[str, ToolSpec] = {}; self.handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
    def register(self, spec: ToolSpec, handler: Callable[..., Awaitable[Any]] | None = None) -> None: self.tools[spec.name] = spec; handler and self.handlers.__setitem__(spec.name, handler)
    def discover(self) -> list[dict[str, Any]]: return [vars(t).copy() for t in self.tools.values() if t.enabled]
    async def call(self, name: str, arguments: dict[str, Any], controller_approved: bool = False) -> Any:
        spec = self.tools.get(name)
        if not spec or not spec.enabled: raise ValueError(f"tool unavailable: {name}")
        if (spec.requires_controller_approval or spec.risk_level in {"high", "critical"}) and not controller_approved:
            raise PermissionError(f"controller approval required: {name}")
        handler = self.handlers.get(name)
        if handler is None: raise ValueError(f"no handler for tool: {name}")
        return await handler(arguments)
