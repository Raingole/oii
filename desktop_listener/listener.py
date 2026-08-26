"""Forward Windows WeChat/QQ toast notifications to the Xiaozhi server."""

import argparse
import asyncio
import re
import time
import json
import os
import webbrowser
from urllib.parse import quote, urlparse

import requests
import websockets
from winrt.windows.ui.notifications import NotificationKinds
from winrt.windows.ui.notifications.management import (
    UserNotificationListener,
    UserNotificationListenerAccessStatus,
)


def notification_text(notification) -> str:
    binding = notification.notification.visual.get_binding("ToastGeneric")
    if binding is None:
        return ""
    values = [element.text.strip() for element in binding.get_text_elements() if element.text]
    return "：".join(values)


def is_supported_app(name: str) -> bool:
    name = name.lower()
    return any(value in name for value in ("微信", "wechat", "qq", "tim"))


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def open_desktop_target(target: str, url: str = "") -> None:
    """Open only known Windows targets; never execute a shell command."""
    targets = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "explorer": "explorer.exe",
        "settings": "ms-settings:",
    }
    if target == "browser":
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("网页地址必须使用 http 或 https")
        webbrowser.open(url)
    elif target in targets:
        os.startfile(targets[target])
    else:
        raise ValueError("不支持的电脑程序")


async def listen_commands(server_url: str, token: str, stop_event=None) -> None:
    """Keep a reconnecting WebSocket connection to the server control channel."""
    if not token:
        raise RuntimeError("请配置桌面插件令牌")
    separator = "&" if "?" in server_url else "?"
    url = f"{server_url}{separator}token={quote(token, safe='')}"
    while stop_event is None or not stop_event.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as websocket:
                print("[INFO] 电脑控制监听已连接")
                async for raw_message in websocket:
                    payload = json.loads(raw_message)
                    if payload.get("type") != "desktop_command":
                        continue
                    status = "ok"
                    error = ""
                    try:
                        open_desktop_target(payload.get("target", ""), payload.get("url", ""))
                    except Exception as exc:
                        status, error = "error", str(exc)
                    await websocket.send(json.dumps({
                        "type": "desktop_ack",
                        "command_id": payload.get("command_id"),
                        "status": status,
                        "error": error,
                    }, ensure_ascii=False))
        except Exception as exc:
            if stop_event is not None and stop_event.is_set():
                break
            print(f"[WARN] 电脑控制连接失败，稍后重试: {exc}")
            await asyncio.sleep(3)


async def request_notification_access():
    return await UserNotificationListener.current.request_access_async()


async def listen(args, stop_event=None, request_permission=True) -> None:
    listener = UserNotificationListener.current
    if request_permission:
        access = await listener.request_access_async()
        if access != UserNotificationListenerAccessStatus.ALLOWED:
            raise RuntimeError("未获得Windows通知读取权限，请在系统设置中允许本程序访问通知")

    session = requests.Session()
    # Notification IDs are only unique within the current Windows session.
    # The bounded set is periodically reset during long-running use.
    known_ids = set()
    print("[INFO] 正在监听微信和QQ系统通知")
    while stop_event is None or not stop_event.is_set():
        notifications = await listener.get_notifications_async(NotificationKinds.TOAST)
        for notification in notifications:
            notification_id = notification.id
            if notification_id in known_ids:
                continue
            known_ids.add(notification_id)
            app_name = notification.app_info.display_info.display_name or "未知应用"
            if not is_supported_app(app_name):
                continue
            text = clean_text(notification_text(notification))
            if not text:
                continue
            payload = {
                "text": f"{app_name}收到消息：{text}",
                "source": app_name,
                "notification_id": str(notification_id),
            }
            try:
                response = session.post(
                    args.server_url,
                    json=payload,
                    timeout=10,
                )
                response.raise_for_status()
                response.close()
                print(f"[INFO] 已转发: {payload['text']}")
            except requests.RequestException as exc:
                print(f"[WARN] 转发失败: {exc}")
        if len(known_ids) > 5000:
            known_ids.clear()
        await asyncio.sleep(args.interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="监听微信/QQ通知并转发到小智")
    parser.add_argument("--server-url", required=True, help="例如 http://36.212.7.43:8003/api/notify")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    try:
        asyncio.run(listen(args))
    except KeyboardInterrupt:
        print("\n[INFO] 监听已停止")
    finally:
        time.sleep(0.1)


if __name__ == "__main__":
    main()
