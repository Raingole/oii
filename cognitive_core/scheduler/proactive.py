"""Persistent, bounded random conversation starters over the existing router."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import random
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..contracts import CognitiveEvent
from ..runtime import logger


class ProactiveThinking:
    def __init__(self, core, dispatcher, *, clock=time.time, rng=None):
        self.core, self.dispatcher, self.clock = core, dispatcher, clock
        self.rng = rng or random.SystemRandom()
        self.lock = asyncio.Lock()
        self.key = f'proactive:{core.agent_id}'

    @property
    def config(self):
        return self.core.config.get('proactive', {}) or {}

    def local_time(self, now):
        name = self.config.get('timezone', 'Asia/Shanghai')
        try:
            zone = ZoneInfo(name)
        except ZoneInfoNotFoundError:
            if name != 'Asia/Shanghai':
                raise ValueError('Unknown proactive timezone')
            zone = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(now, zone)

    def interval(self):
        minimum = max(60, float(self.config.get('min_interval_seconds', 900)))
        maximum = max(minimum, float(self.config.get('max_interval_seconds', 3600)))
        return self.rng.uniform(minimum, maximum)

    def quiet(self, hour):
        start = int(self.config.get('quiet_start_hour', 0)) % 24
        end = int(self.config.get('quiet_end_hour', 8)) % 24
        if start == end:
            return False
        return start <= hour < end if start < end else hour >= start or hour < end

    def topic(self, local, history, last_topic):
        topics = [('phone', '放松或玩手机的话题'), ('coding', '写代码或最近在忙什么'), ('rest', '休息和睡眠的话题')]
        if 7 <= local.hour <= 9 or 11 <= local.hour <= 14 or 17 <= local.hour <= 20:
            topics.extend([('meal', '当前时段适合聊吃饭'), ('meal', '想到吃饭或喜欢的食物')])
        prior = [item['content'][:160] for item in history if item['role'] == 'user' and len(item['content'].strip()) >= 3]
        if prior:
            topics.append(('history', '同一会话曾提到的原文：' + self.rng.choice(prior)))
        eligible = [item for item in topics if item[0] != last_topic]
        return self.rng.choice(eligible or topics)

    async def run_once(self):
        if not self.config.get('enabled', False) or not self.dispatcher:
            return {'status': 'disabled'}
        router = getattr(self.dispatcher, 'event_router', None)
        if router is None or self.core.llm is None or not self.core.config.get('llm_enabled', False):
            return {'status': 'unavailable'}
        if self.lock.locked():
            return {'status': 'busy'}
        async with self.lock:
            return await self._run(router)

    async def _run(self, router):
        now = self.clock()
        contact = self.core.store.runtime_get(f'contact:{self.core.agent_id}')
        if not contact or contact.get('platform') != 'napcat':
            return {'status': 'no_contact'}
        age = now - contact.get('last_input_at', now)
        if age > max(3600, float(self.config.get('contact_max_age_seconds', 259200))):
            return {'status': 'stale_contact'}
        session = contact['session_id']
        if router.session_busy(session) or age < max(60, float(self.config.get('min_idle_seconds', 300))):
            return {'status': 'conversation_active'}
        local = self.local_time(now)
        state = self.core.store.runtime_get(self.key)
        if state.get('day') != local.date().isoformat():
            state.update(day=local.date().isoformat(), count=0)
        if not state.get('next_at'):
            state['next_at'] = now + self.interval()
            self.core.store.runtime_set(self.key, state)
            logger.info(f'proactive thought scheduled next_at={state["next_at"]}')
            return {'status': 'scheduled', 'next_at': state['next_at']}
        if self.quiet(local.hour):
            if state['next_at'] <= now:
                state['next_at'] = now + self.interval()
                self.core.store.runtime_set(self.key, state)
            return {'status': 'quiet_hours'}
        if state.get('count', 0) >= max(0, int(self.config.get('max_daily_starts', 8))):
            return {'status': 'daily_limit'}
        if now < state['next_at']:
            return {'status': 'waiting', 'next_at': state['next_at']}
        history = self.core.store.recent_conversation(session, 12)
        topic, clue = self.topic(local, history, state.get('last_topic'))
        # Reserve before inference/delivery. Restart or failure cannot replay
        # a due thought or reset today's budget into a burst of messages.
        state.update(count=state.get('count', 0) + 1, next_at=now + self.interval(), last_topic=topic)
        self.core.store.runtime_set(self.key, state)
        event = CognitiveEvent.create('system', 'proactive_thought', 'self', 'self',
            {'text': f'本地时间 {local:%Y-%m-%d %H:%M}。随机联想线索：{clue}。结合真实记忆，决定是否自然地发起聊天。'},
            session_id=session, metadata={'proactive_thought': True, 'topic': topic,
                'local_time': local.isoformat(), 'platform': 'napcat', 'reply_channel': 'qq',
                'reply_target': contact['target'], 'user_id': contact['actor_id']})
        result = await router.route(event)
        logger.info(f'proactive thought considered event_id={event.event_id} topic={topic} generation={result.get("generation", {}).get("status", "unknown")}')
        return {'status': 'considered', 'topic': topic, 'event_id': event.event_id,
                'dispatched': result.get('dispatched', []), 'generation': result.get('generation', {})}
