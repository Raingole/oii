"""OII-managed Douyin QR login. Emits only state and the QR image, never cookies."""
import asyncio
import base64
import io
import json
import os
import tempfile
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "upstream"))

import httpx
import qrcode
from src.cookies import normalize_cookie_string

HOME = "https://www.douyin.com"
SSO = "https://sso.douyin.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


def emit(status, **extra):
    print(json.dumps({"status": status, **extra}, ensure_ascii=False), flush=True)


def save_cookie(value):
    normalized = normalize_cookie_string(value)
    if not normalized.value:
        return False
    path = Path(os.environ["DOUYIN_COOKIE_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="cookies_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(normalized.value)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return True


async def main():
    headers = {"User-Agent": USER_AGENT, "Referer": HOME,
               "Accept": "application/json, text/plain, */*"}
    async with httpx.AsyncClient(follow_redirects=False, headers=headers, timeout=15) as client:
        try:
            response = await client.get(f"{SSO}/get_qrcode/", params={
                "aid": "6383", "service": HOME, "next": HOME,
            })
            content_type = response.headers.get("content-type", "")
            if "html" in content_type.lower() or response.text.lstrip().startswith("<"):
                emit("failed", error="douyin_qrcode_response_blocked")
                return 2
            data = response.json()
        except Exception as exc:
            emit("failed", error="douyin_qrcode_request_failed", error_type=type(exc).__name__)
            return 2
        if data.get("error_code") != 0:
            emit("failed", error="douyin_qrcode_rejected")
            return 2
        qr_data = data.get("data") or {}
        token = qr_data.get("token")
        qr_url = qr_data.get("qrcode_index_url")
        if not token or not qr_url:
            emit("failed", error="douyin_qrcode_invalid_response")
            return 2
        image = qrcode.make(qr_url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        emit("waiting", image_base64=base64.b64encode(buffer.getvalue()).decode("ascii"))
        started = time.monotonic()
        last_status = "waiting"
        while time.monotonic() - started < 120:
            await asyncio.sleep(2)
            try:
                checked = await client.get(f"{SSO}/check_qrconnect/", params={
                    "token": token, "aid": "6383", "service": HOME,
                })
                result = checked.json().get("data") or {}
            except Exception:
                continue
            status = str(result.get("status") or "")
            if status == "2" and last_status != "scanned":
                last_status = "scanned"
                emit("scanned")
            elif status == "5":
                emit("confirmed")
                cookies = dict(checked.cookies)
                redirect_url = result.get("redirect_url") or ""
                if redirect_url:
                    try:
                        redirected = await client.get(redirect_url)
                        cookies.update(dict(redirected.cookies))
                    except Exception:
                        pass
                try:
                    async with httpx.AsyncClient(follow_redirects=True, headers=headers,
                                                 cookies=cookies, timeout=15) as home_client:
                        home_response = await home_client.get(HOME)
                        cookies.update(dict(home_response.cookies))
                except Exception:
                    pass
                cookie_string = "; ".join(f"{key}={value}" for key, value in cookies.items())
                if "sessionid" not in cookie_string or not save_cookie(cookie_string):
                    emit("failed", error="douyin_qrcode_cookie_save_failed")
                    return 2
                emit("completed")
                return 0
            elif status not in {"", "1", "2"}:
                emit("failed", error="douyin_qrcode_invalid_state")
                return 2
        emit("expired")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        emit("failed", error="douyin_qrcode_internal_error", error_type=type(exc).__name__)
        raise SystemExit(2)
