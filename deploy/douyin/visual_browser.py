"""Screenshot-and-coordinate Douyin browser worker; no URL/API scraping."""
import asyncio
import base64
import json
import os
import sys

from playwright.async_api import async_playwright

HOME = "https://www.douyin.com/"
WIDTH = 1280
HEIGHT = 800


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


async def read_command():
    line = await asyncio.to_thread(sys.stdin.readline)
    if not line:
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return {}


def load_cookie_pairs(path):
    try:
        raw = open(path, "r", encoding="utf-8").read(128 * 1024)
    except OSError:
        return []
    pairs = []
    for part in raw.replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if name and value and len(name) < 100 and len(value) < 8192:
            pairs.append({"name": name.strip(), "value": value.strip(), "domain": ".douyin.com", "path": "/"})
    return pairs


async def screenshot(page):
    data = await page.screenshot(type="png", full_page=False, animations="disabled", timeout=10_000)
    return {"ok": True, "image_base64": base64.b64encode(data).decode("ascii"), "width": WIDTH, "height": HEIGHT}


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, channel="chromium", args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, locale="zh-CN")
        cookie_path = os.environ.get("DOUYIN_COOKIE_PATH", "")
        cookies = load_cookie_pairs(cookie_path)
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()
        try:
            await page.goto(HOME, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            emit(await screenshot(page))
            while True:
                command = await read_command()
                if command is None or command.get("op") == "close":
                    break
                op = command.get("op")
                if op == "screenshot":
                    pass
                elif op == "click":
                    x, y = float(command.get("x", -1)), float(command.get("y", -1))
                    if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
                        emit({"ok": False, "error": "visual_coordinate_out_of_bounds"})
                        continue
                    await page.mouse.click(x, y)
                    await page.wait_for_timeout(min(5_000, max(300, int(command.get("wait_ms", 900)))))
                elif op == "type":
                    value = str(command.get("text", ""))[:120]
                    if "http://" in value.lower() or "https://" in value.lower():
                        emit({"ok": False, "error": "url_input_forbidden"})
                        continue
                    await page.keyboard.insert_text(value)
                    await page.wait_for_timeout(250)
                elif op == "key":
                    key = str(command.get("key", ""))
                    if key not in {"Enter", "Escape", "PageDown", "PageUp", "ArrowDown", "ArrowUp", "Tab", "Control+A", "Backspace"}:
                        emit({"ok": False, "error": "visual_key_not_allowed"})
                        continue
                    await page.keyboard.press(key)
                    await page.wait_for_timeout(min(5_000, max(300, int(command.get("wait_ms", 900)))))
                elif op == "scroll":
                    x, y = float(command.get("x", WIDTH / 2)), float(command.get("y", HEIGHT / 2))
                    delta = max(-900, min(900, int(command.get("delta_y", 600))))
                    if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
                        emit({"ok": False, "error": "visual_coordinate_out_of_bounds"})
                        continue
                    await page.mouse.move(x, y)
                    await page.mouse.wheel(0, delta)
                    await page.wait_for_timeout(min(5_000, max(700, int(command.get("wait_ms", 1_400)))))
                elif op == "wait":
                    await page.wait_for_timeout(min(10_000, max(250, int(command.get("wait_ms", 1_000)))))
                else:
                    emit({"ok": False, "error": "visual_operation_not_allowed"})
                    continue
                emit(await screenshot(page))
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        emit({"ok": False, "error": "visual_browser_start_failed", "error_type": type(exc).__name__})
        raise SystemExit(2)
