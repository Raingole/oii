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

    def topic(self, local, history, last_topic=None, recent_topics=None, interest=None):
        """Choose a diverse thinking angle; let the model derive the actual opener from context."""
        user_lines = [item['content'].strip() for item in history
            if item.get('role') == 'user' and len(item.get('content','').strip()) >= 4
            and item.get('content','').strip() not in {'[图片]','[语音]','[文件]'}]
        candidates = [
            ('fresh_angle', '从最近交流或实际召回的记忆里，换一个尚未展开的角度继续聊'),
            ('shared_reference', '找双方最近对话里的共同梗、具体细节或值得回扣的内容'),
            ('curiosity', '围绕用户真实提过的兴趣，产生一个具体而自然的好奇问题'),
            ('creative_link', '把最近的话题与一条相关记忆做一个新联想，不要复述原话'),
            ('perspective_shift', '从用户最近表达的选择、偏好或观点出发，换个角度自然聊下去'),
            ('playful_callback', '如果双方近期有轻松互动，就自然回扣一下；没有就不要强行玩梗'),
            ('current_context', '结合当前日期和时段，但只有确实相关时才用；不推断用户正在做什么'),
        ]
        progress_words=('我在','我正','准备','还没','最近在','项目','计划','开发','代码','考试','工作','视频','图片','机器人')
        if any(word in line for line in user_lines[-8:] for word in progress_words):
            candidates.append(('check_in', '围绕用户明确提到的项目或近况跟进一个具体进展；不要泛问'))
        if user_lines:
            candidates.append(('recent_thread', '从双方最近聊过的具体内容里自然接话；候选原文：'+self.rng.choice(user_lines)[-180:]))
        if any('?' in line or '？' in line or any(word in line for word in ('怎么','为什么','后来','结果呢','还没','准备')) for line in user_lines[-6:]):
            candidates.append(('open_loop', '检查最近对话中是否真有尚未收尾的问题或事情；只有确实未解决时才接着问'))
        if interest and interest.get('topic'):
            candidates.append(('known_interest', '从用户明确表达的兴趣切入：'+str(interest['topic'])[:60]))
        if getattr(self.core, 'learning', None) is not None:
            candidates.append(('meme_callback', '检查已收录梗里是否有与近期对话自然契合的一条；可以轻松回扣，也可以不用梗'))
        if isinstance(recent_topics,str):
            recent_topics=[recent_topics]
        cooldown=max(1,int(self.config.get('topic_cooldown_count',5)))
        # recent_topics is ordered newest first; older states may still contain
        # only last_topic, so preserve that compatibility during deployment.
        excluded=set((recent_topics or [])[:cooldown]) if isinstance(recent_topics,list) else set()
        if last_topic:
            excluded.add(last_topic)
        eligible=[item for item in candidates if item[0] not in excluded]
        return self.rng.choice(eligible or candidates)

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
        recent_topics=state.get('recent_topics',[])
        if not isinstance(recent_topics,list):
            recent_topics=[state.get('last_topic','')]
        interest=None
        learning=getattr(self.core,'learning',None)
        if learning is not None:
            try:
                interest=learning.store.next_interest(scope=session)
            except Exception:
                interest=None
        topic, clue = self.topic(local, history, state.get('last_topic'), recent_topics, interest)
        # Reserve before inference/delivery. Restart or failure cannot replay
        # a due thought or reset today's budget into a burst of messages.
        recent_topics=[topic,*[item for item in recent_topics if item!=topic]][:max(1,int(self.config.get('topic_cooldown_count',5)))]
        state.update(count=state.get('count', 0) + 1, next_at=now + self.interval(),
            last_topic=topic,recent_topics=recent_topics)
        self.core.store.runtime_set(self.key, state)
        meme_context = ''
        learning = getattr(self.core, 'learning', None)
        if learning is not None:
            try:
                references = learning.store.search(session, '梗词', limit=10, budget=3000,
                    data_source='bilibili_meme_transcript')
                if references:
                    item = self.rng.choice(references)
                    meme_context = ('\n可选的已收录梗资料（只有和上下文自然相关时才用）：' +
                        str({'text': item.get('text'), 'title': item.get('title'),
                             'source': item.get('timestamp_url'), 'video': item.get('url'),
                             'seconds': item.get('start')}))
            except Exception:
                meme_context = ''
        event = CognitiveEvent.create('system', 'proactive_thought', 'self', 'self',
            {'text': f'本地时间 {local:%Y-%m-%d %H:%M}。本轮随机思考角度（只作启发，不是指定话题）：{clue}。请结合最近对话、真实记忆、当前情景和交流语气，自行生成一个有上下文的新想法；也可以判断没有自然话题并保持安静。{meme_context}'},
            session_id=session, metadata={'proactive_thought': True, 'topic': topic,
                'local_time': local.isoformat(), 'platform': 'napcat', 'reply_channel': 'qq',
                'reply_target': contact['target'], 'user_id': contact['actor_id']})
        result = await router.route(event)
        logger.info(f'proactive thought considered event_id={event.event_id} topic={topic} generation={result.get("generation", {}).get("status", "unknown")}')
        return {'status': 'considered', 'topic': topic, 'event_id': event.event_id,
                'dispatched': result.get('dispatched', []), 'generation': result.get('generation', {})}
