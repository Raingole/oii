"""Open an approved desktop application or website through the companion app."""

import json
from urllib.parse import urlparse

from plugins_func.register import Action, ActionResponse, ToolType, register_function


ALLOWED_TARGETS = {"browser", "notepad", "calculator", "explorer", "settings"}


@register_function(
    "open_desktop_app",
    {
        "type": "function",
        "function": {
            "name": "open_desktop_app",
            "description": "在已连接的电脑插件上打开指定软件或网页。只能打开 browser、notepad、calculator、explorer、settings；打开网页时必须提供 http 或 https 的 url。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": sorted(ALLOWED_TARGETS),
                        "description": "要打开的目标：browser浏览器、notepad记事本、calculator计算器、explorer文件管理器、settings系统设置。",
                    },
                    "url": {
                        "type": "string",
                        "description": "仅 browser 使用，必须是 http:// 或 https:// 地址。",
                    },
                },
                "required": ["target"],
            },
        },
    },
    type=ToolType.SYSTEM_CTL,
)
async def open_desktop_app(conn, target: str, url: str = "") -> ActionResponse:
    target = str(target or "").strip().lower()
    url = str(url or "").strip()[:2048]
    if target not in ALLOWED_TARGETS:
        return ActionResponse(Action.ERROR, response="不支持打开这个电脑程序")
    if target == "browser":
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ActionResponse(Action.ERROR, response="请提供有效的 http 或 https 网页地址")
    elif url:
        return ActionResponse(Action.ERROR, response="打开电脑软件时不能提供 url")

    http_server = getattr(getattr(conn, "server", None), "http_server", None)
    control = getattr(http_server, "desktop_control", None)
    if control is None:
        return ActionResponse(Action.ERROR, response="电脑控制功能未启用")
    result = await control.open_app(target, url)
    if not result.get("ok"):
        return ActionResponse(Action.ERROR, result=json.dumps(result, ensure_ascii=False), response=result["error"])
    return ActionResponse(
        Action.RESPONSE,
        result=json.dumps(result, ensure_ascii=False),
        response="已向电脑插件发送打开指令",
    )
