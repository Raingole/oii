"""Lightweight desktop UI for forwarding QQ/WeChat notifications to Xiaozhi."""

import asyncio
import json
import os
import sys
import threading
from argparse import Namespace
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from listener import listen, UserNotificationListener, UserNotificationListenerAccessStatus


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        config_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "XiaozhiMessageListener"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir
    return Path(__file__).resolve().parent


class ListenerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("小智消息监听")
        self.root.geometry("460x170")
        self.root.resizable(False, False)
        self.stop_event = None
        self.worker = None
        self.config_path = app_dir() / "desktop_listener.json"

        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        self.server_url = self.add_field(frame, 0, "服务器地址", "http://36.212.7.43:8003/api/notify")
        ttk.Label(frame, text="单设备模式").grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        self.status = tk.StringVar(value="未启动")
        ttk.Label(frame, textvariable=self.status).grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 8))
        self.start_button = ttk.Button(frame, text="启动监听", command=self.start)
        self.start_button.grid(row=3, column=0, padx=(0, 8), sticky="ew")
        ttk.Button(frame, text="停止监听", command=self.stop).grid(row=3, column=1, sticky="ew")
        frame.columnconfigure(1, weight=1)
        self.load_config()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def add_field(self, frame, row, label, default):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
        value = tk.StringVar(value=default)
        entry = ttk.Entry(frame, textvariable=value, width=48, show="*" if label == "通知令牌" else "")
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        return value

    def load_config(self):
        try:
            values = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.server_url.set(values.get("server_url", self.server_url.get()))
        except (OSError, ValueError):
            pass

    def save_config(self):
        values = {
            "server_url": self.server_url.get().strip(),
        }
        self.config_path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.server_url.get().strip():
            messagebox.showwarning("配置不完整", "请填写服务器地址")
            return
        try:
            self.save_config()
            # Windows 要求通知权限申请在 UI 线程执行。
            access = asyncio.run(UserNotificationListener.current.request_access_async())
            if access != UserNotificationListenerAccessStatus.ALLOWED:
                messagebox.showwarning("权限未开启", "请在 Windows 设置中允许本程序读取通知")
                return
        except Exception as exc:
            messagebox.showerror("通知权限失败", str(exc))
            return
        self.stop_event = threading.Event()
        args = Namespace(
            server_url=self.server_url.get().strip(),
            interval=1.0,
        )
        self.worker = threading.Thread(target=self.run_listener, args=(args,), daemon=True)
        self.worker.start()
        self.status.set("监听中：微信和QQ系统通知")
        self.start_button.state(["disabled"])

    def run_listener(self, args):
        try:
            asyncio.run(listen(args, self.stop_event, request_permission=False))
        except Exception as exc:
            self.root.after(0, lambda: messagebox.showerror("监听失败", str(exc)))
            self.root.after(0, lambda: self.status.set("启动失败"))
            self.root.after(0, lambda: self.start_button.state(["!disabled"]))

    def stop(self):
        if self.stop_event:
            self.stop_event.set()
        self.status.set("已停止")
        self.start_button.state(["!disabled"])

    def close(self):
        self.stop()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    ListenerApp(window)
    window.mainloop()
