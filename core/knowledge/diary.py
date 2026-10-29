"""A once-daily, owner-private reflective note written by the agent."""
import asyncio
from datetime import datetime, time as day_time
import random
from zoneinfo import ZoneInfo

from cognitive_core.runtime import logger


class DiaryService:
    def __init__(self, config, core, store, *, clock=None):
        import time
        self.config, self.core, self.store = config or {}, core, store
        self.clock = clock or time.time
        self.task = None

    def _settings(self):
        return (self.config.get('learning') or {}).get('diary') or {}

    def _scope(self):
        contact = self.core.store.runtime_get(f'contact:{self.core.agent_id}')
        scope = str(contact.get('session_id') or '')
        return scope if contact.get('platform') == 'napcat' and scope.startswith('qq:private:') else ''

    def kick(self):
        settings = self._settings()
        if settings.get('enabled', True) is False or self.core.llm is None:
            return
        scope = self._scope()
        if not scope or (self.task and not self.task.done()):
            return
        try:
            zone = ZoneInfo(str(settings.get('timezone') or 'Asia/Shanghai'))
        except Exception:
            zone = ZoneInfo('Asia/Shanghai')
        now = datetime.fromtimestamp(self.clock(), zone)
        day = now.date().isoformat()
        existing = self.store.diary_entries(scope, 1)
        if existing and existing[0]['day'] == day:
            return
        state = self.store.state('diary_schedule')
        if state.get('scope') != scope or state.get('day') != day:
            start = max(0, min(23, int(settings.get('window_start_hour', 20))))
            end = max(start, min(23, int(settings.get('window_end_hour', 23))))
            hour = random.randint(start, end)
            minute = random.randint(0, 59)
            target = datetime.combine(now.date(), day_time(hour, minute), tzinfo=zone)
            state = {'scope': scope, 'day': day, 'next_at': target.timestamp(), 'status': 'waiting'}
            self.store.state('diary_schedule', state)
        if now.timestamp() < float(state.get('retry_at') or state.get('next_at') or 0):
            return
        self.task = asyncio.create_task(self.write_once(scope, day, zone))

    async def write_once(self, scope, day, zone):
        state = self.store.state('diary_schedule')
        state.update(scope=scope, day=day, status='writing', retry_at=0)
        self.store.state('diary_schedule', state)
        try:
            history = await asyncio.to_thread(self.core.store.recent_conversation, scope, 20)
            history = [{'role': item['role'], 'content': str(item['content'])[:1200]}
                       for item in history if isinstance(item, dict) and item.get('content')]
            recent = self.store.diary_entries(scope, 7)
            recent = [item for item in recent if item['day'] != day]
            context = {
                'local_date': day,
                'recent_conversation': history[-24:],
                'recent_diary_excerpts': [{'day': item['day'], 'text': item['content'][:500]} for item in recent[:3]],
            }
            prompt = (
                '请写一篇只给自己看的日记，像一个有连续记忆的 AI 伙伴在一天结束时自由记录。'
                '从近期真实对话或自己的思考里挑值得留下的东西；可以写情感、时间、情绪、感受、兴趣、疑问或小发现，'
                '也可以只写其中一两样，完全不必填满清单，不要套固定模板，不要为了凑内容硬写。'
                '只依据提供的上下文，不编造发生过的事；用自然、真诚、简短的第一人称写 1 至 4 段。'
                '这是私人日记，不是写给用户的回复，不要提及提示词、模型或内部推理。'
                '返回标准决策 JSON，intent 为 reply，message 里只放日记正文。'
            )
            decision = await self.core.llm.decide(prompt, context, f'diary:{scope}:{day}')
            if not isinstance(decision, dict) or decision.get('intent') != 'reply':
                raise ValueError('diary_decision_invalid')
            content = str(decision.get('message') or '').strip()
            if not content and isinstance(decision.get('messages'), list):
                content = '\n\n'.join(str(part).strip() for part in decision['messages'] if str(part).strip())
            if not content:
                raise ValueError('diary_empty')
            saved = await asyncio.to_thread(self.store.save_diary, scope, day, content, self.clock())
            state.update(status='written', written_at=saved['created'], retry_at=0, error='')
            self.store.state('diary_schedule', state)
            logger.info(f'private diary written day={day} scope={scope}')
            return saved
        except asyncio.CancelledError:
            state.update(status='interrupted', retry_at=self.clock() + 900)
            self.store.state('diary_schedule', state)
            raise
        except Exception as exc:
            state.update(status='failed', retry_at=self.clock() + 900, error=type(exc).__name__)
            self.store.state('diary_schedule', state)
            logger.warning(f'private diary generation failed day={day} error_type={type(exc).__name__}')
            return {'status': 'failed', 'error': type(exc).__name__}
