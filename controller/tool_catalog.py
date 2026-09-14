from __future__ import annotations
from typing import Any

class ToolCatalog:
    """Controller-owned catalog over every configured tool provider."""
    def __init__(self): self.providers: list[tuple[str, Any]] = []
    def register(self, name: str, provider: Any) -> None: self.providers.append((name, provider))
    async def discover(self) -> list[dict[str, Any]]:
        result=[]
        for provider_name, provider in self.providers:
            try:
                tools = await provider.discover() if hasattr(provider, "discover") else provider.get_all_tools()
                if isinstance(tools, dict): tools=[dict(v, name=k) if isinstance(v,dict) else {"name":k} for k,v in tools.items()]
                for raw in tools or []:
                    if isinstance(raw, dict): item=dict(raw)
                    else:
                        item={"name":getattr(raw,"name", ""), "description":getattr(raw,"description", {})}
                    item.setdefault("name", item.get("function",{}).get("name", "")); item.setdefault("provider", provider_name); item.setdefault("enabled", True); result.append(item)
            except Exception: continue
        return result
    async def find(self, name: str) -> dict[str, Any] | None:
        return next((x for x in await self.discover() if x.get("name") == name or x.get("function",{}).get("name") == name), None)
