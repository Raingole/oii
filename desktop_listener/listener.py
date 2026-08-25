"""Forward Windows WeChat/QQ toast notifications to the Xiaozhi server."""

import argparse
import asyncio
import re
import time

import requests
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


async def listen(args, stop_event=None) -> None:
    listener = UserNotificationListener.current
    access = await listener.request_access_async()
    if access != UserNotificationListenerAccessStatus.ALLOWED:
        raise RuntimeError("未获得Windows通知读取权限，请在系统设置中允许本程序访问通知")

    session = requests.Session()
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
            payload = {"device_id": args.device_id, "text": f"{app_name}收到消息：{text}"}
            try:
                response = session.post(
                    args.server_url,
                    json=payload,
                    headers={"X-Notify-Token": args.token},
                    timeout=10,
                )
                response.raise_for_status()
                print(f"[INFO] 已转发: {payload['text']}")
            except requests.RequestException as exc:
                print(f"[WARN] 转发失败: {exc}")
        if len(known_ids) > 5000:
            known_ids.clear()
        await asyncio.sleep(args.interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="监听微信/QQ通知并转发到小智")
    parser.add_argument("--server-url", required=True, help="例如 http://36.212.7.43:8003/api/notify")
    parser.add_argument("--token", required=True, help="服务器 notification_token")
    parser.add_argument("--device-id", required=True, help="开发板 device-id，例如 9c:13:9e:8a:0a:b0")
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
