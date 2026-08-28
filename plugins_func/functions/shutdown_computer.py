"""Two-step, confirmation-gated computer shutdown command."""

from plugins_func.register import Action, ActionResponse, ToolType, register_function


DESCRIPTION = {
    "type": "function",
    "function": {
        "name": "shutdown_computer",
        "description": "关机操作必须二次确认。用户只说关机时询问确认；只有用户明确说‘确认关机’并将 confirm 填为‘确认关机’时才执行。",
        "parameters": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "string",
                    "enum": ["确认关机"],
                    "description": "用户必须明确说出的确认词",
                }
            },
            "required": ["confirm"],
        },
    },
}


@register_function("shutdown_computer", DESCRIPTION, type=ToolType.SYSTEM_CTL)
async def shutdown_computer(conn, confirm: str = "") -> ActionResponse:
    latest_user_text = ""
    for item in reversed(getattr(getattr(conn, "dialogue", None), "dialogue", [])):
        if getattr(item, "role", None) == "user":
            latest_user_text = str(getattr(item, "content", "")).strip()
            break
    if str(confirm).strip() != "确认关机" or latest_user_text not in {"确认关机", "确认关机。"}:
        return ActionResponse(Action.RESPONSE, response="关机操作需要确认，请回复确认关机")
    server = getattr(conn, "server", None)
    control = getattr(server, "desktop_control", None)
    if control is None:
        return ActionResponse(Action.ERROR, response="电脑控制功能未连接")
    result = await control.shutdown()
    if not result.get("ok"):
        return ActionResponse(Action.ERROR, response=result.get("error", "关机失败"))
    return ActionResponse(Action.RESPONSE, result=result, response="已确认关机，电脑即将关机")
