from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from cognitive_core.reply_parts import split_reply_parts
from cognitive_core.runtime import CognitiveCore
from cognitive_core.contracts import CognitiveEvent
from controller.action_outbox import ActionOutbox
from controller.action_dispatcher import ActionDispatcher
from controller.event_router import EventRouter


class ReplyPartsTests(unittest.TestCase):
    def test_requested_example_is_two_messages_with_or_without_spacing(self):
        for text in ['修好了？我这边看着能喘气了', '修好了？  我这边看着能喘气了', '修好了？\n\n我这边看着能喘气了']:
            self.assertEqual(split_reply_parts([text]), ['修好了？', '我这边看着能喘气了'])

    def test_short_reply_and_explicit_messages_keep_their_content(self):
        self.assertEqual(split_reply_parts(['好']), ['好'])
        self.assertEqual(split_reply_parts(['修好了？', '我这边看着能喘气了']), ['修好了？', '我这边看着能喘气了'])
        self.assertEqual(split_reply_parts(['一条\n另一条']), ['一条', '另一条'])

    def test_urls_code_commands_lists_and_quotes_keep_their_format(self):
        values = ['https://example.com/a?x=1&y=2', '`value?left:right`', '他说“修好了？我这边能用了”', '```python\nprint("好了？")\n```', 'sudo bash auto-update.sh run', '1. 看日志\n2. 再检查']
        for text in values:
            self.assertEqual(split_reply_parts([text]), [text], text)
        self.assertEqual(split_reply_parts(['看看 https://example.com/a?x=1  然后回我']), ['看看 https://example.com/a?x=1', '然后回我'])

    def test_versions_and_decimal_points_do_not_split(self):
        self.assertEqual(split_reply_parts(['版本是 1.2.3，值是 3.14']), ['版本是 1.2.3，值是 3.14'])

    def test_four_message_cap_preserves_remaining_sentences(self):
        self.assertEqual(split_reply_parts(['一。二。三。四。五。六。']), ['一。', '二。', '三。', '四。\n五。\n六。'])
        self.assertEqual(split_reply_parts(['', '  ']), [])


class ReplyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = CognitiveCore(str(Path(self.temp.name) / 'core.db'), config={'llm_enabled': True})
        class Model:
            async def decide(self, *args):
                return {'intent': 'reply', 'message': '修好了？我这边看着能喘气了'}
        self.core.llm = Model()
        self.dispatcher = ActionDispatcher(ActionOutbox(self.core.store.path))
        self.sent = []
        async def send(action):
            self.sent.append(action)
            return {'status': 'completed', 'output_sent': True}
        self.dispatcher.register('send_message', send)
        self.router = EventRouter(self.core, self.dispatcher)
        self.dispatcher.event_router = self.router
        self.router.pause_between_replies = AsyncMock()

    async def test_plain_message_is_sent_as_two_distinct_actions_in_order(self):
        event = CognitiveEvent.create('qq', 'message', 'qq:7', 'agent', {'text': '服务恢复了'}, 'qq:private:7')
        result = await self.router.route(event)
        self.assertEqual([item['payload']['text'] for item in self.sent], ['修好了？', '我这边看着能喘气了'])
        self.assertEqual(len({item['action_id'] for item in self.sent}), 2)
        self.assertEqual([item['payload']['reply_part'] for item in self.sent], [1, 2])
        self.assertTrue(all(item['payload']['reply_total'] == 2 for item in self.sent))
        self.assertEqual(len(result['dispatched']), 2)
        self.router.pause_between_replies.assert_awaited_once()
        self.assertTrue((await self.router.route(event))['duplicate'])
        self.assertEqual(len(self.sent), 2)
        self.assertIn({'role':'assistant', 'content':'修好了？\n我这边看着能喘气了'}, self.core.store.recent_conversation('qq:private:7'))

    async def test_tool_followup_also_sends_separate_bubbles(self):
        event = CognitiveEvent.create('controller', 'action_result', 'self', 'environment', {'success':True}, 'qq:private:7', metadata={'followup_reply':True, 'reply_target':'qq:7', 'reply_channel':'qq', 'platform':'napcat'})
        result = await self.router.route(event)
        self.assertEqual([item['payload']['text'] for item in self.sent], ['修好了？', '我这边看着能喘气了'])
        self.assertEqual(len(result['dispatched']), 2)
