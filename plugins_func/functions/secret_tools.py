"""Encrypted owner Secret Vault tools.

Secret values are deliberately never returned to the LLM.  Delivery is
performed server-side through the already configured QQService.
"""

from __future__ import annotations

import json

from plugins_func.register import Action, ActionResponse, ToolType, register_function


def _manager(conn):
    server = getattr(conn, "server", None)
    return getattr(server, "memory_manager", None)


@register_function(
    "secret.store",
    {
        "type": "function",
        "function": {
            "name": "secret.store",
            "description": "Encrypt and store an owner secret. Never repeat the value in the response.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "A non-sensitive name for the secret."},
                    "value": {"type": "string", "description": "The secret value to encrypt."},
                },
                "required": ["name", "value"],
            },
        },
    },
    type=ToolType.SYSTEM_CTL,
)
async def secret_store(conn, name: str, value: str) -> ActionResponse:
    manager = _manager(conn)
    if manager is None:
        return ActionResponse(Action.ERROR, response="Secret Vault is unavailable")
    if not manager.secret_store(name, value, {"source_channel": getattr(conn, "channel", "")}):
        return ActionResponse(Action.ERROR, response="Secret name or value is empty")
    return ActionResponse(
        Action.RESPONSE,
        result=json.dumps({"success": True, "stored": True, "name": str(name)}, ensure_ascii=False),
        response="Secret encrypted and stored",
    )


@register_function(
    "secret.exists",
    {
        "type": "function",
        "function": {
            "name": "secret.exists",
            "description": "Check whether a named owner secret exists without revealing its value.",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        },
    },
    type=ToolType.SYSTEM_CTL,
)
async def secret_exists(conn, name: str) -> ActionResponse:
    manager = _manager(conn)
    exists = bool(manager and manager.secret_exists(name))
    return ActionResponse(
        Action.RESPONSE,
        result=json.dumps({"success": True, "exists": exists, "name": str(name)}, ensure_ascii=False),
        response="Secret exists" if exists else "Secret does not exist",
    )


@register_function(
    "secret.list_names",
    {
        "type": "function",
        "function": {
            "name": "secret.list_names",
            "description": "List owner secret names only; never return secret values.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    type=ToolType.SYSTEM_CTL,
)
async def secret_list_names(conn) -> ActionResponse:
    manager = _manager(conn)
    names = manager.secret_list_names() if manager else []
    return ActionResponse(
        Action.RESPONSE,
        result=json.dumps({"success": manager is not None, "names": names}, ensure_ascii=False),
        response="Secret names listed",
    )


@register_function(
    "secret.send_to_owner",
    {
        "type": "function",
        "function": {
            "name": "secret.send_to_owner",
            "description": "Decrypt a named secret only on the server and send it to the configured owner QQ. Never expose plaintext to the model.",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        },
    },
    type=ToolType.SYSTEM_CTL,
)
async def secret_send_to_owner(conn, name: str) -> ActionResponse:
    manager = _manager(conn)
    service = getattr(getattr(conn, "server", None), "qq_service", None)
    value = manager.secret_read_for_delivery(name) if manager else None
    if value is None or service is None:
        return ActionResponse(
            Action.ERROR,
            result=json.dumps({"success": False, "delivered": False}, ensure_ascii=False),
            response="Secret delivery failed",
        )
    try:
        sent = await service.send_text_to_owner(value)
        success = bool(sent.get("success"))
    except Exception:
        success = False
    return ActionResponse(
        Action.RESPONSE if success else Action.ERROR,
        result=json.dumps({"success": success, "delivered": success}, ensure_ascii=False),
        response="Secret sent to owner QQ" if success else "Secret delivery failed",
    )


@register_function(
    "secret.delete",
    {
        "type": "function",
        "function": {
            "name": "secret.delete",
            "description": "Soft-delete a named owner secret without revealing its value.",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        },
    },
    type=ToolType.SYSTEM_CTL,
)
async def secret_delete(conn, name: str) -> ActionResponse:
    manager = _manager(conn)
    deleted = bool(manager and manager.secret_delete(name))
    return ActionResponse(
        Action.RESPONSE if deleted else Action.ERROR,
        result=json.dumps({"success": deleted, "deleted": deleted}, ensure_ascii=False),
        response="Secret deleted" if deleted else "Secret not found",
    )
