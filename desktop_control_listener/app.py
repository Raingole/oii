"""Xiaozhi desktop control companion: tray-first, auto-connecting, no notification listener."""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import quote, urlparse
import webbrowser

import pystray
import websockets
from PIL import Image, ImageDraw

TARGETS = {"notepad": "notepad.exe", "calculator": "calc.exe", "explorer": "explorer.exe", "settings": "ms-settings:"}


def config_dir() -> Path:
    path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "XiaozhiDesktopControl"
    path.mkdir(parents=True, exist_ok=True)
    return path


def defaults() -> dict[str, str]:
    values = {"endpoint": "ws://127.0.0.1:8003/api/desktop", "token": ""}
    paths = [config_dir() / "config.json", Path(os.environ.get("LOCALAPPDATA", Path.home())) / "XiaozhiDesktop" / "config.json"]
    project = Path(sys.executable).resolve().parents[1] if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    paths.append(project / "data" / ".config.yaml")
    for path in paths:
        try:
            if path.suffix == ".json":
                loaded = json.loads(path.read_text(encoding="utf-8"))
                values["endpoint"] = loaded.get("command_url", loaded.get("endpoint", values["endpoint"]))
                values["token"] = loaded.get("token", values["token"])
                if values["token"]:
                    break
            elif path.exists():
                text = path.read_text(encoding="utf-8", errors="replace")
                websocket = re.search(r"^\s{2}websocket\s*:\s*[\"']?([^\"'\r\n]+)", text, re.MULTILINE)
                token = re.search(r"^\s{2}desktop_token\s*:\s*[\"']?([^\"'\r\n]+)", text, re.MULTILINE)
                http_port = re.search(r"^\s{2}http_port\s*:\s*(\d+)", text, re.MULTILINE)
                if websocket:
                    parsed = urlparse(websocket.group(1).strip())
                    values["endpoint"] = f"ws://{parsed.hostname or '127.0.0.1'}:{http_port.group(1) if http_port else '8003'}/api/desktop"
                if token:
                    values["token"] = token.group(1).strip()
        except (OSError, ValueError):
            continue
    return values


def normalize(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def application_locations() -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", ""))
    program_data = Path(os.environ.get("PROGRAMDATA", ""))
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    return [appdata / "Microsoft/Windows/Start Menu/Programs", program_data / "Microsoft/Windows/Start Menu/Programs", desktop]


def open_application(name: str) -> None:
    query = normalize(name)
    if not query:
        raise ValueError("应用名称不能为空")
    candidates = []
    for root in application_locations():
        if not root.is_dir():
            continue
        for item in root.rglob("*"):
            if not item.is_file() or item.suffix.casefold() not in {".lnk", ".url", ".exe"}:
                continue
            title = normalize(item.stem)
            score = 1.0 if query == title else (0.85 if query in title else difflib.SequenceMatcher(None, query, title).ratio())
            if score >= 0.5:
                candidates.append((score, str(item)))
    candidates.sort(reverse=True)
    if not candidates:
        raise ValueError(f"未找到与“{name}”匹配的应用")
    os.startfile(candidates[0][1])


def open_target(message: dict) -> None:
    target = message.get("target", "")
    if target == "browser":
        url = message.get("url", "")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("网页地址必须使用 http 或 https")
        webbrowser.open(url)
    elif target == "application":
        open_application(message.get("name", ""))
    elif target == "path":
        path = os.path.normcase(os.path.abspath(os.path.expandvars(message.get("path", ""))))
        roots = [os.path.normcase(os.environ.get("WINDIR", r"C:\Windows")), os.path.normcase(str(Path.home() / "Desktop"))]
        if not os.path.exists(path) or not any(path == root or path.startswith(root + os.sep) for root in roots):
            raise ValueError("本地路径仅允许已存在的 Windows 或桌面路径")
        os.startfile(path)
    elif target in TARGETS:
        os.startfile(TARGETS[target])
    else:
        raise ValueError("不支持的桌面目标")


async def command_loop(endpoint: str, token: str, stop: threading.Event, status) -> None:
    separator = "&" if "?" in endpoint else "?"
    url = f"{endpoint}{separator}token={quote(token, safe='')}"
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as socket:
                status("服务器已连接")
                async for raw in socket:
                    if stop.is_set():
                        return
                    message = json.loads(raw)
                    if message.get("type") != "desktop_command":
                        continue
                    result, error = "ok", ""
                    try:
                        open_target(message)
                        status(f"已执行：{message.get('target', '')}")
                    except Exception as exc:
                        result, error = "error", str(exc)
                        status(f"执行失败：{error}")
                    await socket.send(json.dumps({"type": "desktop_ack", "command_id": message.get("command_id"), "status": result, "error": error}, ensure_ascii=False))
        except Exception as exc:
            if not stop.is_set():
                status(f"连接失败，3秒后重试：{exc}")
                await asyncio.sleep(3)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("小智电脑控制")
        self.root.geometry("570x175")
        self.root.resizable(False, False)
        self.stop_event = threading.Event()
        self.worker = None
        self.tray = None
        self.config_path = config_dir() / "config.json"
        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        self.endpoint = self.field(frame, 0, "控制 WS 地址", "endpoint")
        self.token = self.field(frame, 1, "桌面令牌", "token", True)
        self.status = tk.StringVar(value="准备连接")
        ttk.Label(frame, textvariable=self.status).grid(row=2, column=0, columnspan=2, sticky="w", pady=8)
        self.start_button = ttk.Button(frame, text="重新连接", command=self.start)
        self.start_button.grid(row=3, column=0, padx=(0, 8), sticky="ew")
        ttk.Button(frame, text="停止连接", command=self.stop).grid(row=3, column=1, sticky="ew")
        frame.columnconfigure(1, weight=1)
        self.load()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.create_tray()
        self.root.after(300, self.start_and_hide)

    def field(self, frame, row, label, key, secret=False):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
        value = tk.StringVar()
        ttk.Entry(frame, textvariable=value, width=54, show="*" if secret else "").grid(row=row, column=1, sticky="ew", pady=5)
        setattr(self, key, value)
        return value

    def load(self):
        values = defaults()
        try:
            values.update(json.loads(self.config_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self.endpoint.set(values.get("endpoint", ""))
        self.token.set(values.get("token", ""))

    def save(self):
        self.config_path.write_text(json.dumps({"endpoint": self.endpoint.get().strip(), "token": self.token.get().strip()}, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_status(self, value):
        self.root.after(0, self.status.set, value)

    def start_and_hide(self):
        self.start()
        self.hide()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        endpoint, token = self.endpoint.get().strip(), self.token.get().strip()
        if not endpoint or not token:
            self.show()
            messagebox.showwarning("配置不完整", "未找到服务器地址或桌面令牌，请填写后点击重新连接。")
            return
        self.save()
        self.stop_event.clear()
        self.worker = threading.Thread(target=lambda: asyncio.run(command_loop(endpoint, token, self.stop_event, self.set_status)), daemon=True)
        self.worker.start()
        self.start_button.state(["disabled"])
        self.set_status("正在连接服务器…")

    def stop(self):
        self.stop_event.set()
        self.start_button.state(["!disabled"])
        self.status.set("已停止")

    def create_tray(self):
        image = Image.new("RGBA", (64, 64), (42, 119, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((12, 12, 52, 52), fill="white")
        draw.text((25, 18), "X", fill=(42, 119, 255, 255))
        menu = pystray.Menu(pystray.MenuItem("打开设置", lambda icon, item: self.show()), pystray.MenuItem("重新连接", lambda icon, item: self.start()), pystray.MenuItem("退出", lambda icon, item: self.quit()))
        self.tray = pystray.Icon("xiaozhi-desktop-control", image, "小智电脑控制", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def hide(self):
        self.root.withdraw()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit(self):
        self.stop()
        if self.tray:
            self.tray.stop()
        self.root.after(0, self.root.destroy)


if __name__ == "__main__":
    window = tk.Tk()
    App(window)
    window.mainloop()
