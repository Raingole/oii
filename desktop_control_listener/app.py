"""Standalone Windows companion for server-issued desktop open commands."""

import asyncio
import json
import os
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlparse
import tkinter as tk
from tkinter import messagebox, ttk

import websockets


TARGETS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "settings": "ms-settings:",
}


def open_target(target: str, url: str = "", path: str = "") -> None:
    if target == "browser":
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("网页地址必须使用 http 或 https")
        webbrowser.open(url)
        return
    if target not in TARGETS:
        if target != "path":
            raise ValueError("服务器下发了不支持的程序")
        if url or not path:
            raise ValueError("打开本机路径时必须提供 path")
        normalized = os.path.normcase(os.path.abspath(os.path.expandvars(path)))
        windows_root = os.path.normcase(os.environ.get("WINDIR", r"C:\Windows"))
        desktop_root = os.path.normcase(os.path.join(os.path.expanduser("~"), "Desktop"))
        if not os.path.exists(normalized) or not (
            normalized == windows_root or normalized.startswith(windows_root + os.sep)
            or normalized == desktop_root or normalized.startswith(desktop_root + os.sep)
        ):
            raise ValueError("path 只能是已存在的 Windows 或桌面路径")
        os.startfile(normalized)
        return
    if url or path:
        raise ValueError("打开软件时不能带网页地址或路径")
    os.startfile(TARGETS[target])


async def command_loop(endpoint: str, token: str, stop: threading.Event, on_status) -> None:
    separator = "&" if "?" in endpoint else "?"
    connect_url = f"{endpoint}{separator}token={quote(token, safe='')}"
    while not stop.is_set():
        try:
            async with websockets.connect(connect_url, ping_interval=20, ping_timeout=20) as socket:
                on_status("已连接服务器")
                async for raw in socket:
                    if stop.is_set():
                        break
                    message = json.loads(raw)
                    if message.get("type") != "desktop_command":
                        continue
                    status, error = "ok", ""
                    try:
                        open_target(message.get("target", ""), message.get("url", ""), message.get("path", ""))
                    except Exception as exc:
                        status, error = "error", str(exc)
                        on_status(f"执行失败：{error}")
                    else:
                        on_status(f"已打开：{message.get('target', '')}")
                    await socket.send(json.dumps({
                        "type": "desktop_ack",
                        "command_id": message.get("command_id"),
                        "status": status,
                        "error": error,
                    }, ensure_ascii=False))
        except Exception as exc:
            if not stop.is_set():
                on_status(f"连接失败，3秒后重试：{exc}")
                await asyncio.sleep(3)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("小智电脑控制插件")
        self.root.geometry("560x190")
        self.root.resizable(False, False)
        self.stop = threading.Event()
        self.worker = None
        self.config_path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "XiaozhiDesktopControl" / "config.json"

        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        self.endpoint = self.field(frame, 0, "控制WS地址", "ws://127.0.0.1:8003/api/desktop")
        self.token = self.field(frame, 1, "桌面令牌", "")
        self.status = tk.StringVar(value="未启动")
        ttk.Label(frame, textvariable=self.status).grid(row=2, column=0, columnspan=2, sticky="w", pady=8)
        self.start_button = ttk.Button(frame, text="启动监听", command=self.start)
        self.start_button.grid(row=3, column=0, padx=(0, 8), sticky="ew")
        ttk.Button(frame, text="停止监听", command=self.shutdown).grid(row=3, column=1, sticky="ew")
        frame.columnconfigure(1, weight=1)
        self.load()
        root.protocol("WM_DELETE_WINDOW", self.close)

    def field(self, frame, row, label, default):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
        value = tk.StringVar(value=default)
        ttk.Entry(frame, textvariable=value, width=52, show="*" if label == "桌面令牌" else "").grid(row=row, column=1, sticky="ew", pady=5)
        return value

    def load(self):
        try:
            values = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.endpoint.set(values.get("endpoint", self.endpoint.get()))
            self.token.set(values.get("token", self.token.get()))
        except (OSError, ValueError):
            pass

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps({"endpoint": self.endpoint.get().strip(), "token": self.token.get().strip()}, indent=2), encoding="utf-8")

    def set_status(self, text):
        self.root.after(0, self.status.set, text)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        endpoint, token = self.endpoint.get().strip(), self.token.get().strip()
        if not endpoint or not token:
            messagebox.showwarning("配置不完整", "请填写控制WS地址和桌面令牌")
            return
        self.save()
        self.stop.clear()
        self.worker = threading.Thread(target=lambda: asyncio.run(command_loop(endpoint, token, self.stop, self.set_status)), daemon=True)
        self.worker.start()
        self.start_button.state(["disabled"])
        self.status.set("正在连接服务器")

    def shutdown(self):
        self.stop.set()
        self.status.set("已停止")
        self.start_button.state(["!disabled"])

    def close(self):
        self.shutdown()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    App(window)
    window.mainloop()
