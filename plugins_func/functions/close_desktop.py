"""Desktop command to gracefully close visible user applications."""

from plugins_func.register import Action, ActionResponse, ToolType, register_function


DESCRIPTION = {
    "type": "function",
    "function": {
        "name": "close_desktop",
        "description": "关闭当前电脑上打开的应用程序。仅当用户明确说关闭桌面、关闭所有程序或清理桌面时调用。不会关闭系统关键进程。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


@register_function("close_desktop", DESCRIPTION, type=ToolType.SYSTEM_CTL)
async def close_desktop(conn) -> ActionResponse:
    server = getattr(conn, "server", None)
    control = getattr(server, "desktop_control", None)
    if control is None:
        return ActionResponse(Action.ERROR, response="电脑控制功能未连接")
    result = await control.close_all_programs()
    if not result.get("ok"):
        return ActionResponse(Action.ERROR, response=result.get("error", "关闭桌面失败"))
    return ActionResponse(Action.RESPONSE, result=result, response="已请求关闭电脑上的应用程序")

