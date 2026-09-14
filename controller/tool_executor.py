from __future__ import annotations
import asyncio
from typing import Any
from .tool_catalog import ToolCatalog

class ToolExecutor:
    def __init__(self, catalog: ToolCatalog, timeout: float = 15.0, approval=None): self.catalog,self.timeout,self.approval=catalog,timeout,approval
    @staticmethod
    def _jsonable(value: Any) -> Any:
        """Convert legacy ActionResponse/plugin values into JSON-safe data."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): ToolExecutor._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [ToolExecutor._jsonable(item) for item in value]
        action = getattr(value, "action", None)
        if action is not None:
            action_name = getattr(action, "name", None) or str(action)
            return {
                "action": action_name,
                "result": ToolExecutor._jsonable(getattr(value, "result", None)),
                "response": ToolExecutor._jsonable(getattr(value, "response", None)),
            }
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return ToolExecutor._jsonable(to_dict())
        return str(value)

    async def execute(self, tool_name: str, arguments: dict, context: dict) -> dict[str, Any]:
        item=await self.catalog.find(tool_name)
        if not item:return {"tool":tool_name,"success":False,"error":"tool unavailable","provider":""}
        if not item.get("enabled",True):return {"tool":tool_name,"success":False,"error":"tool disabled","provider":item.get("provider","")}
        if (item.get("requires_controller_approval") or item.get("risk_level") in {"high","critical"}) and not context.get("approval_validated"):
            return {"tool":tool_name,"success":False,"error":"controller approval required","provider":item.get("provider","")}
        provider=next((p for n,p in self.catalog.providers if n==item.get("provider")),None)
        if provider is None:return {"tool":tool_name,"success":False,"error":"provider unavailable","provider":item.get("provider","")}
        try:
            if hasattr(provider,"execute"): value=provider.execute(tool_name,arguments,context)
            elif hasattr(provider,"execute_tool"): value=provider.execute_tool(tool_name,arguments)
            else: return {"tool":tool_name,"success":False,"error":"provider has no executor","provider":item.get("provider","")}
            if hasattr(value,"__await__"): value=await asyncio.wait_for(value,timeout=self.timeout)
            # Existing plugin/server adapters return ActionResponse objects.
            action = getattr(value, "action", None)
            if action is not None and getattr(action, "name", "") in {"ERROR", "NOTFOUND"}:
                return {"tool":tool_name,"success":False,"error":str(getattr(value,"response",None) or "provider rejected tool"),"provider":item.get("provider","")}
            if isinstance(value, dict) and value.get("success") is False:
                return {"tool":tool_name,"success":False,"error":str(value.get("error", "provider rejected tool")),"provider":item.get("provider","")}
            return {"tool":tool_name,"success":True,"result":self._jsonable(value),"provider":item.get("provider","")}
        except asyncio.TimeoutError:return {"tool":tool_name,"success":False,"error":"tool timeout","provider":item.get("provider","")}
        except (ValueError,TypeError) as exc:return {"tool":tool_name,"success":False,"error":f"invalid arguments: {exc}","provider":item.get("provider","")}
        except PermissionError:return {"tool":tool_name,"success":False,"error":"permission denied","provider":item.get("provider","")}
        except Exception as exc:return {"tool":tool_name,"success":False,"error":str(exc)[:300],"provider":item.get("provider","")}
