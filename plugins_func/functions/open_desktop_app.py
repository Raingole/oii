"""Open an approved desktop application or website through the companion app."""

import json
from urllib.parse import urlparse

from plugins_func.register import Action, ActionResponse, ToolType, register_function


ALLOWED_TARGETS = {"browser", "notepad", "calculator", "explorer", "settings", "application", "path"}


@register_function(
    "open_desktop_app",
    {
        "type": "function",
        "function": {
            "name": "open_desktop_app",
            "description": "控制已连接的电脑。只有用户明确说‘打开浏览器’或‘用浏览器打开’时才使用 browser；普通‘打开某某’必须使用 application，让电脑插件从开始菜单、桌面快捷方式和桌面文件夹模糊搜索并打开。打开网页时必须提供 http 或 https 的 url。禁止猜测或执行命令行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": sorted(ALLOWED_TARGETS),
                        "description": "目标：browser浏览器（仅明确提到浏览器时）、notepad记事本、calculator计算器、explorer文件管理器、settings系统设置、application按名称搜索应用/快捷方式/桌面文件夹、path本机 Windows 或桌面路径。",
                    },
                    "url": {
                        "type": "string",
                        "description": "仅 browser 使用，必须是 http:// 或 https:// 地址。",
                    },
                    "path": {
                        "type": "string",
                        "description": "仅 path 使用。Windows 或当前用户桌面中的已存在文件/文件夹完整路径。",
                    },
                    "name": {
                        "type": "string",
                        "description": "仅 application 使用。用户想打开的应用、快捷方式或桌面文件夹名称，例如 GPT、ChatGPT、网易云。",
                    },
                },
                "required": ["target"],
            },
        },
    },
    type=ToolType.SYSTEM_CTL,
)
async def open_desktop_app(conn, target: str, url: str = "", path: str = "", name: str = "") -> ActionResponse:
    target = str(target or "").strip().lower()
    url = str(url or "").strip()[:2048]
    path = str(path or "").strip()[:2048]
    name = str(name or "").strip()[:128]
    # Some models put the spoken app name in target instead of name. Treat
    # that form as an application lookup rather than rejecting it.
    if target not in ALLOWED_TARGETS and not url and not path:
        name = name or target
        target = "application"
    if target not in ALLOWED_TARGETS:
        return ActionResponse(Action.ERROR, response="不支持打开这个电脑程序")
    if target == "browser":
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ActionResponse(Action.ERROR, response="请提供有效的 http 或 https 网页地址")
    elif target == "path":
        if url or not path:
            return ActionResponse(Action.ERROR, response="打开本机路径时必须提供 path，不能提供 url")
        normalized = os.path.normcase(os.path.abspath(os.path.expandvars(path)))
        windows_root = os.path.normcase(os.environ.get("WINDIR", r"C:\Windows"))
        desktop_root = os.path.normcase(os.path.join(os.path.expanduser("~"), "Desktop"))
        if not os.path.exists(normalized) or not (
            normalized == windows_root or normalized.startswith(windows_root + os.sep)
            or normalized == desktop_root or normalized.startswith(desktop_root + os.sep)
        ):
            return ActionResponse(Action.ERROR, response="path 只能是已存在的 Windows 或桌面路径")
    elif target == "application":
        if url or path or not name:
            return ActionResponse(Action.ERROR, response="模糊打开应用时必须提供 name，不能提供 url 或 path")
    elif url or path:
        return ActionResponse(Action.ERROR, response="打开电脑软件时不能提供 url")

    server = getattr(conn, "server", None)
    control = getattr(server, "desktop_control", None)
    if control is None:
        http_server = getattr(server, "http_server", None)
        control = getattr(http_server, "desktop_control", None)
    if control is None:
        return ActionResponse(Action.ERROR, response="电脑控制功能未启用")
    result = await control.open_app(target, url, path, name)
    if not result.get("ok"):
        return ActionResponse(Action.ERROR, result=json.dumps(result, ensure_ascii=False), response=result["error"])
    return ActionResponse(
        Action.RESPONSE,
        result=json.dumps(result, ensure_ascii=False),
        response="已向电脑插件发送打开指令",
    )
