import unittest

from qq.delivery import cognitive_delivery_attempted, cognitive_delivery_confirmed
from qq.service import napcat_action_succeeded


class QQDeliveryCompatibilityTests(unittest.TestCase):
    def test_napcat_http_200_retcode_200_is_success(self):
        self.assertTrue(napcat_action_succeeded(200, {"status": "ok", "retcode": 200}))

    def test_napcat_error_envelope_is_not_success(self):
        self.assertFalse(napcat_action_succeeded(200, {"status": "failed", "retcode": 200}))
        self.assertFalse(napcat_action_succeeded(500, {"status": "ok", "retcode": 0}))

    def test_cognitive_action_is_handled_only_after_success(self):
        self.assertFalse(cognitive_delivery_confirmed([{"status": "failed", "success": False}]))
        self.assertFalse(cognitive_delivery_confirmed([{"status": "pending", "success": False}]))
        self.assertTrue(cognitive_delivery_confirmed([{"status": "succeeded", "success": True}]))

    def test_failed_cognitive_delivery_is_not_sent_again_by_gateway(self):
        """The gateway fallback must not duplicate an already attempted send."""
        self.assertTrue(cognitive_delivery_attempted(
            [{"type": "send_message"}],
            [{"status": "failed", "success": False}],
        ))
        self.assertFalse(cognitive_delivery_attempted(
            [{"type": "do_nothing"}],
            [],
        ))


if __name__ == "__main__":
    unittest.main()
