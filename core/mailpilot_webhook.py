"""MailPilot webhook consumer; IMAP and analysis remain in the mailpilot service."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from collections import OrderedDict
from typing import Any

from aiohttp import web
from config.logger import setup_logging

TAG = __name__


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(payload: dict[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _truncate(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _mask_code(code: str) -> str:
    return code[:2] + "****" + code[-2:] if len(code) > 4 else ("****" if code else "")


def _stable_id(payload: dict[str, Any]) -> str:
    for name in ("message_id", "messageId", "id", "uid", "message_uid", "email_id"):
        value = _text(payload.get(name))
        if value:
            return f"id:{name}:{value}"
    bucket = int(time.time() // 300)
    raw = "\x1f".join((_first(payload, "sender", "from", "source"), _first(payload, "subject", "title"), _first(payload, "verification_code", "verificationCode", "code"), str(bucket)))
    return "fallback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def format_mail_notification(payload: dict[str, Any]) -> tuple[str, bool, dict[str, str]]:
    sender = _first(payload, "sender", "from", "source")
    subject = _first(payload, "subject", "title")
    summary = _first(payload, "summary")
    code = _first(payload, "verification_code", "verificationCode", "code")
    category = _first(payload, "category")
    urgency = _first(payload, "urgency")
    # Compatibility with the unmodified mailpilot generic body.
    body = _text(payload.get("body"))
    for line in body.splitlines():
        if not sender and (line.startswith("发件人:") or line.startswith("🧑 ")):
            sender = line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()
        if not summary and (line.startswith("摘要:") or line.startswith("💬 ")):
            summary = line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()
        if not code and ("验证码:" in line or "🔑" in line):
            code = line.split(":", 1)[-1].strip() if ":" in line else line.split()[-1]
    sender, subject = _truncate(sender or "未知", 120), _truncate(subject or "无主题", 160)
    if code:
        lines = ["【邮箱验证码】", f"来源：{sender}", f"验证码：{_truncate(code, 128)}"]
        purpose = _first(payload, "purpose", "verification_purpose")
        if purpose:
            lines.append(f"用途：{_truncate(purpose, 80)}")
        return "\n".join(lines), True, {"sender": sender, "subject": subject, "category": category, "urgency": urgency, "code": _mask_code(code)}
    lines = ["【新邮件】", f"来源：{sender}", f"标题：{subject}"]
    if summary:
        lines.append(f"摘要：{_truncate(summary, 240)}")
    points = payload.get("key_points", payload.get("keyPoints", []))
    if isinstance(points, list):
        for point in points[:3]:
            point = _truncate(_text(point), 120)
            if point:
                lines.append(f"- {point}")
    return "\n".join(lines), False, {"sender": sender, "subject": subject, "category": category, "urgency": urgency, "code": ""}


class MailPilotWebhookHandler:
    def __init__(self, config: dict[str, Any], qq_service):
        self.logger = setup_logging(config)
        mailpilot = config.get("mailpilot", {})
        self.secret = _text(os.environ.get("MAILPILOT_WEBHOOK_SECRET") or mailpilot.get("webhook_secret"))
        self.target_qq = _text(os.environ.get("MAILPILOT_TARGET_QQ") or mailpilot.get("target_qq") or config.get("qq", {}).get("owner_qq"))
        self.qq_service = qq_service
        self.ttl_seconds = int(mailpilot.get("dedup_ttl_seconds", 900))
        self.max_cache = int(mailpilot.get("dedup_cache_size", 2048))
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._tasks: set[asyncio.Task] = set()

    def _authorized(self, request: web.Request) -> bool:
        if not self.secret:
            return False
        auth = request.headers.get("Authorization", "")
        supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return (hmac.compare_digest(supplied, self.secret) if supplied else False) or (hmac.compare_digest(request.match_info.get("token", ""), self.secret) if request.match_info.get("token") else False) or (hmac.compare_digest(request.query.get("token", ""), self.secret) if request.query.get("token") else False)

    def _already_seen(self, key: str) -> bool:
        now = time.monotonic()
        for old in [k for k, t in self._seen.items() if now - t > self.ttl_seconds]:
            self._seen.pop(old, None)
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = now
        while len(self._seen) > self.max_cache:
            self._seen.popitem(last=False)
        return False

    async def handle(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError, TypeError):
            return web.json_response({"ok": False, "error": "JSON required"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "JSON object required"}, status=400)
        message, is_code, fields = format_mail_notification(payload)
        key = _stable_id(payload)
        self.logger.bind(tag=TAG).info("Received mailpilot webhook: sender={}, subject={}, category={}, verification={}", fields["sender"], fields["subject"], fields["category"], is_code)
        if self._already_seen(key):
            return web.json_response({"ok": True, "duplicate": True})
        if not self.target_qq:
            self._seen.pop(key, None)
            return web.json_response({"ok": False, "error": "target QQ not configured"}, status=503)
        task = asyncio.create_task(self._send(message, fields, is_code))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return web.json_response({"ok": True, "accepted": True}, status=202)

    async def _send(self, message: str, fields: dict[str, str], is_code: bool) -> None:
        try:
            ok = await self.qq_service.send_private_message(self.target_qq, message)
            masked = fields["code"] or "none"
            self.logger.bind(tag=TAG).info("Mailpilot QQ push {}: verification={}, code={}", "succeeded" if ok else "failed", is_code, masked)
        except Exception as exc:
            self.logger.bind(tag=TAG).error("Mailpilot QQ push raised an error: {}", exc)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.logger.bind(tag=TAG).error("Mailpilot delivery task failed: {}", task.exception())
