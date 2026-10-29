from __future__ import annotations

from typing import Any
import asyncio
import random
import re
from cognitive_core.contracts import Action, CognitiveEvent
from cognitive_core.runtime import CognitiveCore
from .action_dispatcher import ActionDispatcher
from contracts.results import ActionResult

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)


class EventRouter:
    """The only controller ingress into cognition and the only action handoff."""
    def __init__(self, core: CognitiveCore, dispatcher: ActionDispatcher):
        self.core, self.dispatcher = core, dispatcher
        self._session_locks: dict[str, asyncio.Lock] = {}

    def session_busy(self, session_id: str) -> bool:
        lock = self._session_locks.get(session_id)
        return bool(lock and lock.locked())

    async def route(self, event: CognitiveEvent) -> dict[str, Any]:
        async with self._session_locks.setdefault(event.session_id or event.event_id, asyncio.Lock()):
            return await self._route(event)

    async def _route(self, event: CognitiveEvent) -> dict[str, Any]:
        logger.info(f"cognitive event received event_id={event.event_id} source={event.source} type={event.type}")
        dsh=getattr(getattr(self.core,'learning',None),'dsh',None)
        if (dsh is not None and event.source=='qq' and event.type=='message'
                and event.metadata.get('platform','napcat')=='napcat'
                and str(event.session_id or '').startswith('qq:private:')):
            confirmation=await dsh.handle_confirmation(event)
            if confirmation is not None:
                action=Action.create('send_message',{'session_id':event.session_id,
                    'text':confirmation['text'],'platform':'napcat'},
                    channel=str(event.metadata.get('reply_channel') or 'qq'),
                    target=str(event.metadata.get('reply_target') or event.actor_id))
                result=await self.dispatcher.dispatch(event.event_id,action.to_dict())
                return {'event_id':event.event_id,'actions':[action.to_dict()],
                    'dispatched':[result.to_dict()],'dsh_confirmation':confirmation['status']}
        install_request=str(event.content.get('text') or '').strip()
        if (dsh is not None and event.source=='qq' and event.type=='message'
                and event.metadata.get('platform','napcat')=='napcat'
                and str(event.session_id or '').startswith('qq:private:')
                and re.fullmatch(r'(?:请\s*)?(?:帮我\s*)?安装\s*(?:deepseek\s*harness|dsh)\s*(?:运行时|cli|命令行工具)?[。.!！]?',install_request,re.I)):
            if not dsh.enabled:
                reply='DSH 接入目前处于关闭状态，没有发起安装。'
                status='disabled'
            elif dsh.binary_path():
                reply='检测到 DSH 命令行工具已安装。请直接告诉我具体 DSH 任务；每次调用都会先单独征求你的确认。'
                status='already_installed'
            else:
                try:
                    prepared=await dsh.execute('dsh_prepare',{
                        'operation':'install_runtime',
                        'task':'安装官方 DSH 命令行工具 @deepseek-ai/dsh。',
                        'rationale':'主人在 QQ 私聊中明确请求安装 DSH 运行时。'},
                        {'session_id':event.session_id,'actor_id':event.actor_id,
                         'action':{'target':event.actor_id,'payload':{}}})
                    if prepared.get('status')=='approval_required':
                        code=prepared['approval_code']
                        reply=(f"DSH 安装请求已建立，尚未安装。\n任务：{prepared['display_task']}\n"
                            f"理由：{prepared['rationale']}\n回复“同意 {code}”开始安装，或“拒绝 {code}”取消。确认码 10 分钟内有效。")
                        status='approval_required'
                    else:
                        reply='暂时无法建立 DSH 安装确认请求；没有执行安装。'
                        status=str(prepared.get('status') or 'unavailable')
                except PermissionError:
                    reply='DSH 安装请求仅接受已绑定主人账号的 QQ 私聊；没有执行安装。'
                    status='owner_only'
                except Exception as exc:
                    logger.warning(f'dsh install request preparation failed event_id={event.event_id} error_type={type(exc).__name__}')
                    reply='暂时无法建立 DSH 安装确认请求；没有执行安装，请稍后再试。'
                    status='failed'
            action=Action.create('send_message',{'session_id':event.session_id,
                'text':reply,'platform':'napcat'},
                channel=str(event.metadata.get('reply_channel') or 'qq'),
                target=str(event.metadata.get('reply_target') or event.actor_id))
            delivery=await self.dispatcher.dispatch(event.event_id,action.to_dict())
            logger.info(f'dsh install request handled event_id={event.event_id} status={status} delivered={delivery.success}')
            return {'event_id':event.event_id,'actions':[action.to_dict()],
                'dispatched':[delivery.to_dict()],'dsh_install_request':status}
        result = await self.core.process_event(event)
        if result.get("duplicate"): return result
        actions = result.get("actions", [])
        logger.info(f"action planned event_id={event.event_id} count={len(actions)}")
        dispatched = []
        feedback = []
        for action in actions:
            if action.get('payload', {}).get('reply_part', 1) > 1:
                await self.pause_between_replies()
            logger.info(f"action dispatched event_id={event.event_id} action_id={action.get('action_id')} type={action.get('type')}")
            action_result = await self.dispatcher.dispatch(event.event_id, action)
            logger.info(f"action result event_id={event.event_id} action_id={action_result.action_id} status={action_result.status} success={action_result.success}")
            dispatched.append(action_result.to_dict())
            payload = action.get("payload", {}) if isinstance(action.get("payload", {}), dict) else {}
            result_event = self.build(
                "controller",
                "action_result",
                "self",
                "environment",
                source_event_id=f"{action_result.action_id}:{action_result.status}",
                content={
                    "action_id": action_result.action_id,
                    "success": action_result.success,
                    "result": action_result.result,
                    "error": action_result.error,
                },
                session_id=event.session_id,
                metadata={
                    "parent_event_id": event.event_id,
                    "action_type": action.get("type"),
                    "reply_target": action.get("target") or event.actor_id,
                    "reply_channel": action.get("channel") or event.source,
                    "platform": event.metadata.get("platform", event.source),
                    "source_message_id": payload.get("source_message_id") or event.source_event_id,
                    "conversation_history": event.metadata.get("conversation_history", []),
                    "followup_reply": action.get("type") == "call_mcp" and action_result.success and not payload.get('silent_followup'),
                    "proactive_parent": bool(event.metadata.get('proactive_thought')),
                },
            )
            feedback_result = await self.core.process_event(result_event)
            feedback.append(feedback_result)
            for followup in feedback_result.get("actions", []):
                if followup.get("type") in {"do_nothing", "wait", "observe"}:
                    continue
                if followup.get('payload', {}).get('reply_part', 1) > 1:
                    await self.pause_between_replies()
                logger.info(f"follow-up action dispatched event_id={result_event.event_id} action_id={followup.get('action_id')} type={followup.get('type')}")
                followup_result = await self.dispatcher.dispatch(result_event.event_id, followup)
                logger.info(f"follow-up action result event_id={result_event.event_id} action_id={followup_result.action_id} status={followup_result.status} success={followup_result.success}")
                dispatched.append(followup_result.to_dict())
                followup_event = self.build(
                    "controller",
                    "action_result",
                    "self",
                    "environment",
                    source_event_id=f"{followup_result.action_id}:{followup_result.status}",
                    content={
                        "action_id": followup_result.action_id,
                        "success": followup_result.success,
                        "result": followup_result.result,
                        "error": followup_result.error,
                    },
                    session_id=result_event.session_id,
                    metadata={"parent_event_id": result_event.event_id, "action_type": followup.get("type")},
                )
                feedback.append(await self.core.process_event(followup_event))
                if followup.get('payload', {}).get('reply_total') and followup_result.status in {'failed', 'cancelled'}:
                    break
            if action.get('payload', {}).get('reply_total') and action_result.status in {'failed', 'cancelled'}:
                break
        result["dispatched"] = dispatched
        result["feedback"] = feedback
        return result

    async def pause_between_replies(self) -> None:
        config = self.core.config.get('reply_delivery', {}) or {}
        minimum = max(0.0, min(5.0, float(config.get('min_gap_seconds', 0.8))))
        maximum = max(minimum, min(5.0, float(config.get('max_gap_seconds', 2.4))))
        await asyncio.sleep(random.uniform(minimum, maximum))

    async def handle_device_action_result(self, action_id: str, status: str, device_id: str = "", result: dict[str, Any] | None = None) -> ActionResult:
        action_result = await self.dispatcher.handle_device_action_result(action_id, status, device_id, result)
        event = self.build("controller", "action_result", "self", "environment", source_event_id=f"{action_id}:{status}", content={"action_id": action_id, "success": action_result.success, "status": action_result.status, "result": action_result.result, "error": action_result.error}, metadata={"device_id": device_id, "action_status": status})
        await self.core.process_event(event)
        return action_result

    async def wait_for_device_confirmation(self, action_id: str, timeout: float | None = None) -> ActionResult | None:
        return await self.dispatcher.wait_for_device_confirmation(action_id, timeout)

    def build(self, source: str, type: str, actor_id: str, target_id: str, source_event_id: str = "", **kwargs: Any) -> CognitiveEvent:
        event_id = f"{source}:{source_event_id}" if source_event_id else None
        return CognitiveEvent.create(source, type, actor_id, target_id, event_id=event_id, source_event_id=source_event_id, **kwargs)
