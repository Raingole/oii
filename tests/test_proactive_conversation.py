import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from cognitive_core.contracts import CognitiveEvent
from cognitive_core.llm import validate_decision
from cognitive_core.memory import InMemoryAdapter
from cognitive_core.runtime import CognitiveCore
from cognitive_core.scheduler.heartbeat import HeartbeatScheduler
from cognitive_core.scheduler.proactive import ProactiveThinking
from controller.action_dispatcher import ActionDispatcher
from controller.action_outbox import ActionOutbox
from controller.event_router import EventRouter


class ProactiveConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 29, 12, tzinfo=timezone(timedelta(hours=8))).timestamp()
        self.memory = InMemoryAdapter()
        self.core = CognitiveCore(str(Path(self.temp.name) / 'core.db'), memory=self.memory, config={
            'llm_enabled': True, 'reply_delivery': {'min_gap_seconds': 0, 'max_gap_seconds': 0},
            'proactive': {'enabled': True, 'min_interval_seconds': 60, 'max_interval_seconds': 60,
                          'min_idle_seconds': 60, 'max_daily_starts': 2}})
        owner = self
        class Model:
            async def decide(self, prompt, context, trace_id=''):
                owner.prompts.append((prompt, context))
                return {'intent': 'reply', 'messages': ['刚想起你之前说的事。', '后来怎么样了？']}
        self.prompts = []
        self.core.llm = Model()
        self.sent = []
        self.dispatcher = ActionDispatcher(ActionOutbox(self.core.store.path))
        async def send(action):
            self.sent.append(action)
            return {'status': 'completed'}
        self.dispatcher.register('send_message', send)
        self.router = EventRouter(self.core, self.dispatcher)
        self.dispatcher.event_router = self.router
        self.thinking = ProactiveThinking(self.core, self.dispatcher, clock=lambda: self.now, rng=random.Random(7))
        event = CognitiveEvent.create('qq', 'message', 'qq:123', 'agent', {'text': '昨天在写一个饺子推荐程序'}, 'qq:private:123')
        with patch('cognitive_core.runtime.time.time', return_value=self.now - 120):
            await self.core.process_event(event)

    async def due(self):
        self.core.store.runtime_set(self.thinking.key, {'day': self.thinking.local_time(self.now).date().isoformat(), 'next_at': self.now - 1, 'count': 0})
        return await self.thinking.run_once()

    async def test_multiple_replies_are_delivered_once_and_history_is_complete(self):
        event = CognitiveEvent.create('qq', 'message', 'qq:123', 'agent', {'text': '后来呢'}, 'qq:private:123')
        result = await self.router.route(event)
        self.assertEqual([item['payload']['text'] for item in self.sent], ['刚想起你之前说的事。', '后来怎么样了？'])
        self.assertEqual([item['payload']['reply_part'] for item in self.sent], [1, 2])
        self.assertEqual(len(result['dispatched']), 2)
        history = self.core.store.recent_conversation(event.session_id)
        self.assertIn({'role': 'assistant', 'content': '刚想起你之前说的事。\n后来怎么样了？'}, history)
        await self.router.route(event)
        self.assertEqual(len(self.sent), 2)

    async def test_failed_first_message_stops_remaining_parts(self):
        async def fail(action):
            self.sent.append(action)
            raise TimeoutError('send uncertain')
        self.dispatcher.register('send_message', fail)
        event = CognitiveEvent.create('qq', 'message', 'qq:123', 'agent', {'text': '测试失败'}, 'qq:private:123')
        result = await self.router.route(event)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(result['dispatched'][0]['status'], 'failed')

    async def test_voice_and_official_transport_keep_all_parts_together(self):
        for source, metadata in [('esp32', {}), ('qq', {'platform': 'official'})]:
            event = CognitiveEvent.create(source, 'message', 'user', 'agent', {'text': '测试完整回复'}, 'test:transport', metadata=metadata)
            result = await self.core.process_event(event)
            self.assertEqual(len(result['actions']), 1)
            self.assertEqual(result['actions'][0]['payload']['text'], '刚想起你之前说的事。\n后来怎么样了？')

    async def test_single_item_messages_array_is_a_normal_reply(self):
        event = CognitiveEvent.create('qq', 'message', 'qq:123', 'agent', {'text': '单条格式'}, 'qq:private:123')
        action = self.core._actions_from_decision(validate_decision({'intent': 'reply', 'messages': ['只回一条']}), event)
        self.assertEqual(action[0].payload['text'], '只回一条')

    async def test_day_rollover_resets_daily_cap_without_resetting_contact(self):
        self.core.config['proactive'].update(contact_max_age_seconds=7 * 86400)
        await self.due()
        self.now += 61
        await self.thinking.run_once()
        self.assertEqual((await self.thinking.run_once())['status'], 'daily_limit')
        self.now += 86400
        self.assertEqual((await self.thinking.run_once())['status'], 'considered')
        self.assertEqual(self.core.store.runtime_get(self.thinking.key)['count'], 1)

    async def test_no_contact_and_official_messages_do_not_create_background_route(self):
        self.core.store.runtime_set('contact:default', {})
        self.assertEqual((await self.thinking.run_once())['status'], 'no_contact')
        event = CognitiveEvent.create('qq', 'message', 'qq:official', 'agent', {'text': '官方入口'}, 'qq:private:official', metadata={'platform': 'official'})
        await self.core.process_event(event)
        self.assertEqual((await self.thinking.run_once())['status'], 'no_contact')

    async def test_due_thought_uses_existing_route_memory_and_observer(self):
        stored = len(self.memory.items)
        contact = self.core.store.runtime_get('contact:default')
        result = await self.due()
        self.assertEqual(result['status'], 'considered')
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(item['target'] == 'qq:123' for item in self.sent))
        self.assertEqual(self.core.store.runtime_get('contact:default'), contact)
        self.assertEqual(len(self.memory.items), stored)
        selected = self.core.store.memory_activity('qq:private:123')['selected']
        self.assertTrue(selected['proactive'])
        self.assertIn(selected['focus'], {'吃饭', '休息睡眠', '玩手机', '写代码', '历史对话'})
        self.assertIn('由本地时间与会话线索触发的主动联想', [item['text'] for item in selected['cognition_nodes']])
        self.assertIn('后来怎么样了', selected['reply'])
        self.assertEqual(self.prompts[-1][1]['event']['source'], 'system')
        self.assertIn('昨天在写一个饺子推荐程序', self.prompts[-1][0])
        self.assertIn('刚想起你之前说的事。\n后来怎么样了？', self.core.store.recent_conversation('qq:private:123')[-1]['content'])

    async def test_next_random_time_survives_restart_and_daily_cap(self):
        scheduled = await self.thinking.run_once()
        self.assertEqual(scheduled['status'], 'scheduled')
        restored = CognitiveCore(str(self.core.store.path), memory=self.memory, config=self.core.config)
        restored.llm = self.core.llm
        restored_dispatcher = ActionDispatcher(ActionOutbox(restored.store.path))
        restored_dispatcher.register('send_message', self.dispatcher.executors['send_message'])
        restored_dispatcher.event_router = EventRouter(restored, restored_dispatcher)
        other = ProactiveThinking(restored, restored_dispatcher, clock=lambda: self.now)
        self.assertEqual((await other.run_once())['status'], 'waiting')
        self.now = scheduled['next_at'] + 1
        self.assertEqual((await other.run_once())['status'], 'considered')
        self.now += 61
        self.assertEqual((await other.run_once())['status'], 'considered')
        self.now += 61
        self.assertEqual((await other.run_once())['status'], 'daily_limit')
        self.assertEqual(len(self.sent), 4)

    async def test_idle_quiet_stale_contact_and_missing_model(self):
        contact = self.core.store.runtime_get('contact:default')
        contact['last_input_at'] = self.now
        self.core.store.runtime_set('contact:default', contact)
        self.assertEqual((await self.thinking.run_once())['status'], 'conversation_active')
        contact['last_input_at'] -= 120
        self.core.store.runtime_set('contact:default', contact)
        self.core.config['proactive'].update(quiet_start_hour=11, quiet_end_hour=13)
        self.core.store.runtime_set(self.thinking.key, {'next_at': self.now - 1})
        self.assertEqual((await self.thinking.run_once())['status'], 'quiet_hours')
        self.core.config['proactive'].update(quiet_start_hour=0, quiet_end_hour=8)
        self.now += 4 * 86400
        self.assertEqual((await self.thinking.run_once())['status'], 'stale_contact')
        self.core.llm = None
        self.assertEqual((await self.thinking.run_once())['status'], 'unavailable')
        self.assertEqual(self.sent, [])

    async def test_active_router_and_concurrent_ticks_cannot_duplicate(self):
        self.router._session_locks['qq:private:123'] = asyncio.Lock()
        async with self.router._session_locks['qq:private:123']:
            self.assertEqual((await self.thinking.run_once())['status'], 'conversation_active')
        self.core.store.runtime_set(self.thinking.key, {'next_at': self.now - 1})
        results = await asyncio.gather(self.thinking.run_once(), self.thinking.run_once())
        self.assertEqual(sum(item['status'] == 'considered' for item in results), 1)
        self.assertEqual(len(self.sent), 2)

    async def test_background_failures_and_tool_requests_do_not_send(self):
        class Broken:
            async def decide(self, *args):
                raise TimeoutError('offline')
        self.core.llm = Broken()
        await self.due()
        self.assertEqual(self.sent, [])
        class Tool:
            async def decide(self, *args):
                return {'intent': 'tool_call', 'tool': 'anything'}
        self.core.llm = Tool()
        await self.due()
        self.assertEqual(self.sent, [])

    async def test_scheduler_runs_proactive_with_legacy_autonomy_off(self):
        scheduler = HeartbeatScheduler(self.core, dispatcher=self.dispatcher, autonomous_enabled=False)
        scheduler.proactive = self.thinking
        await scheduler.run_once()
        self.now += 61
        result = await scheduler.run_once()
        self.assertFalse(result['full_tick'])
        self.assertEqual(result['proactive']['status'], 'considered')

    def test_messages_validation_and_legacy_single_reply(self):
        self.assertEqual(validate_decision({'intent': 'reply', 'message': '旧格式'})['messages'], [])
        for messages in ('text', [''], ['ok', {}], ['x'] * 5):
            with self.assertRaises(ValueError):
                validate_decision({'intent': 'reply', 'messages': messages})

    def test_time_aware_topics_and_session_history_selection(self):
        local = self.thinking.local_time(self.now)
        topics = {self.thinking.topic(local, [{'role': 'user', 'content': '原先的代码话题'}], '')[0] for _ in range(100)}
        self.assertIn('meal', topics)
        self.assertIn('history', topics)
        self.assertFalse(self.thinking.quiet(12))
        self.assertTrue(self.thinking.quiet(1))
