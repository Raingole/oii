import json
import unittest

from core.sms_webhook import SmsWebhookHandler


class _Logger:
    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class _Request:
    def __init__(self, payload, token="test-token"):
        self.headers = {"Authorization": f"Bearer {token}"}
        self.payload = payload

    async def json(self):
        return self.payload


class _QQ:
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [True])

    async def send_private_message(self, user_id, message):
        self.calls.append((user_id, message))
        return self.results.pop(0) if self.results else True


def _payload(event_id="evt-1", code="382915"):
    return {
        "event_id": event_id,
        "timestamp": 1788062400000,
        "sender": "106xxxx",
        "body": "【Microsoft】验证码 382915，5分钟内有效",
        "code": code,
    }


class SmsWebhookTests(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, qq=None):
        qq = qq or _QQ()
        config = {
            "sms": {"webhook_token": "test-token", "dedup_ttl_seconds": 86400},
            "qq": {"owner_qq": "2496303940"},
        }
        return SmsWebhookHandler(config, qq, _Logger()), qq

    async def test_valid_event_sends_to_owner(self):
        handler, qq = self.make_handler()
        response = await handler.handle(_Request(_payload()))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), {"ok": True})
        self.assertEqual(qq.calls[0][0], "2496303940")
        self.assertEqual(qq.calls[0][1], "【短信验证码】\n来源：Microsoft\n验证码：382915")

    async def test_bad_token_is_rejected(self):
        handler, qq = self.make_handler()
        response = await handler.handle(_Request(_payload(), token="wrong"))
        self.assertEqual(response.status, 401)
        self.assertFalse(qq.calls)

    async def test_required_fields_are_validated(self):
        handler, _ = self.make_handler()
        for field in ("event_id", "sender"):
            payload = _payload()
            payload.pop(field)
            response = await handler.handle(_Request(payload))
            self.assertEqual(response.status, 400)

    async def test_null_code_is_accepted_without_sending(self):
        handler, qq = self.make_handler()
        response = await handler.handle(_Request(_payload(code=None)))
        self.assertEqual(response.status, 200)
        self.assertFalse(qq.calls)

    async def test_duplicate_event_sends_once(self):
        handler, qq = self.make_handler()
        first = await handler.handle(_Request(_payload()))
        second = await handler.handle(_Request(_payload()))
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertEqual(json.loads(second.text), {"ok": True, "duplicate": True})
        self.assertEqual(len(qq.calls), 1)

    async def test_failed_send_is_retryable_and_not_deduplicated(self):
        qq = _QQ([False, True])
        handler, _ = self.make_handler(qq)
        failed = await handler.handle(_Request(_payload()))
        retried = await handler.handle(_Request(_payload()))
        self.assertEqual(failed.status, 503)
        self.assertEqual(retried.status, 200)
        self.assertEqual(len(qq.calls), 2)


if __name__ == "__main__":
    unittest.main()
