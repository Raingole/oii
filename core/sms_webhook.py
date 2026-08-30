"""Android SMS verification-code webhook routed through the existing QQService."""

import asyncio
import hmac
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from aiohttp import web

TAG = __name__
_SERVICE_PREFIX = re.compile(r"^\s*[\u3010\[]([^\u3011\]]{1,40})[\u3011\]]")


@dataclass(frozen=True)
class SmsEvent:
    event_id: str
    timestamp: int
    sender: str
    body: str
    code: str | None


def _mask_code(code: str) -> str:
    return "****" if len(code) <= 4 else f"{code[:2]}****{code[-2:]}"


def _source(event: SmsEvent) -> str:
    match = _SERVICE_PREFIX.match(event.body)
    return (match.group(1).strip() if match else event.sender) or event.sender


def format_sms_notification(event: SmsEvent) -> str:
    return "\n".join([
        "\u3010\u77ed\u4fe1\u9a8c\u8bc1\u7801\u3011",
        f"\u6765\u6e90\uff1a{_source(event)}",
        f"\u9a8c\u8bc1\u7801\uff1a{event.code}",
    ])


class SmsWebhookHandler:
    def __init__(self, config: dict, qq_service: Any, logger: Any):
        sms_config = config.get("sms", {})
        self.qq_service = qq_service
        self.logger = logger
        self.token = str(sms_config.get("webhook_token", "") or "")
        self.target_qq = str(
            sms_config.get("target_qq")
            or config.get("qq", {}).get("owner_qq", "")
            or ""
        ).strip()
        self.ttl_seconds = max(60, int(sms_config.get("dedup_ttl_seconds", 86400)))
        self.cache_size = max(100, int(sms_config.get("dedup_cache_size", 5000)))
        self._processed: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    def _authorized(self, request: web.Request) -> bool:
        supplied = request.headers.get("Authorization", "")
        prefix = "Bearer "
        return bool(self.token and supplied.startswith(prefix)) and hmac.compare_digest(
            supplied[len(prefix):], self.token
        )

    def _purge(self, now: float) -> None:
        for key, seen_at in list(self._processed.items()):
            if now - seen_at >= self.ttl_seconds:
                self._processed.pop(key, None)
        while len(self._processed) > self.cache_size:
            self._processed.popitem(last=False)

    @staticmethod
    def _parse(payload: Any) -> SmsEvent:
        if not isinstance(payload, dict):
            raise ValueError("payload")
        event_id, sender = payload.get("event_id"), payload.get("sender")
        body, timestamp, code = payload.get("body"), payload.get("timestamp"), payload.get("code")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id")
        if not isinstance(sender, str) or not sender.strip():
            raise ValueError("sender")
        if not isinstance(body, str):
            raise ValueError("body")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError("timestamp")
        if code is not None and (not isinstance(code, str) or not code.strip()):
            raise ValueError("code")
        return SmsEvent(event_id.strip(), int(timestamp), sender.strip(), body, code.strip() if code else None)

    async def handle(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            event = self._parse(await request.json())
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "invalid_request"}, status=400)

        self.logger.bind(tag=TAG).info(f"SMS event received: sender={event.sender}")
        async with self._lock:
            now = time.monotonic()
            self._purge(now)
            if event.event_id in self._processed:
                self.logger.bind(tag=TAG).info("Duplicate SMS event ignored")
                return web.json_response({"ok": True, "duplicate": True})
            if event.code is None:
                self._processed[event.event_id] = now
                self.logger.bind(tag=TAG).info("SMS event has no verification code; ignored")
                return web.json_response({"ok": True})
            self.logger.bind(tag=TAG).info(f"SMS verification code detected: {_mask_code(event.code)}")
            if not self.target_qq:
                self.logger.bind(tag=TAG).error("SMS notification failed: target QQ is not configured")
                return web.json_response({"ok": False, "error": "delivery_failed"}, status=503)
            try:
                sent = await self.qq_service.send_private_message(
                    self.target_qq, format_sms_notification(event)
                )
            except Exception as exc:
                self.logger.bind(tag=TAG).error(f"SMS notification failed: {exc}")
                return web.json_response({"ok": False, "error": "delivery_failed"}, status=503)
            if not sent:
                self.logger.bind(tag=TAG).error("SMS notification failed: NapCat unavailable")
                return web.json_response({"ok": False, "error": "delivery_failed"}, status=503)
            self._processed[event.event_id] = now
            self._processed.move_to_end(event.event_id)
            self.logger.bind(tag=TAG).info("SMS notification sent to QQ")
            return web.json_response({"ok": True})
