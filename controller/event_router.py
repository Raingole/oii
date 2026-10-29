from __future__ import annotations

from typing import Any
import asyncio
import random
import re
import time
from cognitive_core.contracts import Action, CognitiveEvent
from cognitive_core.runtime import CognitiveCore
from .action_dispatcher import ActionDispatcher
from contracts.results import ActionResult

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)


def explicit_bilibili_topic(text: str) -> str | None:
    """Extract an explicit topic request; generic requests for a meme remain random."""
    value = str(text or '').strip()
    patterns = (
        r'^(?:我)?(?:想|要|希望|需要)?(?:让你|让他|让它|请你|帮我|你)?(?:现在|立即|马上)?(?:去)?(?:B站|哔哩哔哩)?\s*(?:搜索|搜|查找)(?:一下|下)?(?:并)?(?:学习|学)?(?:关键词|主题|搜索词)?\s*[：:「“\"]?(.+?)\s*[」”\"]?$',
        r'^(?:我)?(?:想|要|希望|需要)?(?:让你|让他|让它|请你|帮我|你)?(?:现在|立即|马上)?(?:去)?(?:B站|哔哩哔哩)?\s*(?:学习|学)(?:一下|下)?(?:关键词|主题|搜索词)?\s*[：:「“\"]?(.+?)\s*[」”\"]?$',
    )
    for pattern in patterns:
        match = re.match(pattern, value, re.I)
        if not match:
            continue
        topic = match.group(1).strip().strip(' \t\r\n：:，,。.!！?？「」“”\"\'')
        topic = re.sub(r'(?:并)?(?:加入|收录|存入)(?:知识库|知识花园)?$', '', topic).strip()
        if topic.lower() in {'梗', '一个梗', '个梗', '新梗', '一个新梗', '随机梗', '热梗'}:
            return None
        if 2 <= len(topic) <= 80:
            return topic
    return None


def immediate_learning_retry(text: str) -> bool:
    value = re.sub(r'[\s，,。.!！?？]+$', '', str(text or '').strip())
    return bool(re.fullmatch(
        r'(?:来|那|好|行|可以|嗯)?(?:现在|立刻|马上)?(?:就)?(?:再)?(?:试试|试一下|试一次|重新试|执行|开始)(?:吧|呀|啊)?',
        value,
    ))


class EventRouter:
    """The only controller ingress into cognition and the only action handoff."""
    def __init__(self, core: CognitiveCore, dispatcher: ActionDispatcher):
        self.core, self.dispatcher = core, dispatcher
        self._session_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _dsh_approval_message(prepared: dict[str, Any]) -> str:
        if prepared.get('status')!='approval_required' or not prepared.get('approval_code'):
            return 'DSH 没有建立有效的待确认请求，因此没有运行任务。'
        operation='开始安装' if prepared.get('operation')=='install_runtime' else '执行'
        return (f"DSH 请求已建立，尚未运行。\n任务：{prepared.get('display_task') or prepared.get('task') or '（无任务说明）'}\n"
            f"理由：{prepared.get('rationale') or '主人提出的请求'}\n回复“同意 {prepared['approval_code']}”{operation}，"
            f"或回复“拒绝 {prepared['approval_code']}”取消。确认码 10 分钟内有效。")

    @staticmethod
    def _dsh_preparation_result(action_result) -> dict[str, Any]:
        envelope=action_result.result if isinstance(action_result.result,dict) else {}
        prepared=envelope.get('result') if isinstance(envelope.get('result'),dict) else {}
        if not action_result.success or not envelope.get('success'):
            return {'status':'failed'}
        return prepared

    def session_busy(self, session_id: str) -> bool:
        lock = self._session_locks.get(session_id)
        return bool(lock and lock.locked())

    async def route(self, event: CognitiveEvent) -> dict[str, Any]:
        async with self._session_locks.setdefault(event.session_id or event.event_id, asyncio.Lock()):
            return await self._route(event)

    async def _route(self, event: CognitiveEvent) -> dict[str, Any]:
        logger.info(f"cognitive event received event_id={event.event_id} source={event.source} type={event.type}")
        learning=getattr(self.core,'learning',None)
        meme_request=str(event.content.get('text') or '').strip()
        owner_qq=str(((getattr(learning,'config',{}) or {}).get('qq') or {}).get('owner_qq') or '') if learning else ''
        actor_qq=str(event.actor_id or '')
        if actor_qq.startswith('qq:'):
            actor_qq=actor_qq[3:]
        topic_query=explicit_bilibili_topic(meme_request)
        retry_pending=immediate_learning_retry(meme_request)
        pending_interest=(learning.store.next_interest(event.session_id)
            if learning is not None and retry_pending else None)
        from_pending=not topic_query and isinstance(pending_interest,dict)
        if from_pending:
            topic_query=str(pending_interest.get('topic') or '').strip() or None
        wants_meme=(re.search(r'(?:去|现在|帮我|让你|自己去|你自己去|你去|给我)?\s*(?:上|去)?\s*(?:B站|哔哩哔哩)?\s*(?:找|搜|学|学习|刷)\s*(?:一下|一个|个|点|些)?\s*(?:B站|哔哩哔哩)?\s*(?:网络)?\s*梗',meme_request,re.I)
            or re.search(r'(?:去|现在|帮我|让你|自己去|你自己去|你去).{0,8}(?:学|学习|找|搜).{0,8}梗',meme_request,re.I))
        if (learning is not None and (topic_query or wants_meme or retry_pending) and event.source=='qq' and event.type=='message'
                and event.metadata.get('platform','napcat')=='napcat'
                and str(event.session_id or '').startswith('qq:private:')
                and (not owner_qq or actor_qq==owner_qq)):
            reply_target=str(event.metadata.get('reply_target') or actor_qq)
            self.core.store.runtime_set(f'contact:{self.core.agent_id}', {
                'session_id':event.session_id,'actor_id':event.actor_id,
                'target':reply_target,
                'platform':'napcat','last_input_at':time.time(),'event_id':event.event_id})
            specific=bool(topic_query)
            if retry_pending and not topic_query and not wants_meme:
                reply='我这边没有待立即执行的指定主题；把搜索词发我，我会马上按那个词学习。'
                response=Action.create('send_message',{'session_id':event.session_id,'text':reply,'platform':'napcat'},
                    channel='qq',target=reply_target,
                    reason='说明没有待立即执行的指定学习主题')
                delivered=await self.dispatcher.dispatch(event.event_id,response.to_dict())
                logger.info(f'bilibili learning retry requested event_id={event.event_id} status=no_pending_topic delivered={delivered.success}')
                return {'event_id':event.event_id,'actions':[response.to_dict()],
                    'dispatched':[delivered.to_dict()],'topic_learning_request':'no_pending_topic'}
            action=Action.create('call_mcp',{'tool':'bilibili_learn_topic' if specific else 'meme_learn_now',
                'arguments':{'query':topic_query} if specific else {},
                'session_id':event.session_id,'reply_target':reply_target,
                'reply_channel':event.metadata.get('reply_channel') or 'qq','platform':'napcat'},
                target=reply_target,channel='qq',
                reason='用户明确要求立即学习指定B站主题' if specific else '用户明确要求现在学习一个B站梗')
            started=await self.dispatcher.dispatch(event.event_id,action.to_dict())
            envelope=started.result if isinstance(started.result,dict) else {}
            payload=envelope.get('result') if isinstance(envelope.get('result'),dict) else {}
            if started.success and payload.get('status')=='started':
                verb='重新搜索' if from_pending else '搜索'
                reply=(f'收到，我现在按「{topic_query}」{verb}并开始学习；完成或遇到问题后会告诉你。'
                    if specific else '好，我现在去 B 站找一个有字幕证据的新梗。完成或遇到问题后我会把结果发给你。')
                status='started'
            elif started.success and payload.get('status')=='already_running':
                reply='我已经在处理一个 B 站学习任务了，结束后会把结果发给你。'
                status='already_running'
            else:
                error=str(started.error or payload.get('status') or 'tool_unavailable')
                reply=f'这次没能启动梗学习（{error}）。我没有记录成已学习。'
                status='failed'
            response=Action.create('send_message',{'session_id':event.session_id,'text':reply,'platform':'napcat'},
                channel='qq',target=reply_target,
                reason='回复明确的B站梗学习请求')
            delivered=await self.dispatcher.dispatch(event.event_id,response.to_dict())
            logger.info(f'bilibili learning request handled event_id={event.event_id} kind={"topic" if specific else "meme"} pending_topic={from_pending} status={status} delivered={delivered.success}')
            return {'event_id':event.event_id,'actions':[action.to_dict(),response.to_dict()],
                'dispatched':[started.to_dict(),delivered.to_dict()],
                'topic_learning_request' if specific else 'meme_learning_request':status}
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
        affirmative = re.fullmatch(r'(?:同意|可以|好|行|嗯|继续|去吧|开始|ok|okay|yes)[。.!！ ]*', install_request, re.I)
        if (dsh is not None and affirmative and event.source=='qq' and event.type=='message'
                and event.metadata.get('platform','napcat')=='napcat'
                and str(event.session_id or '').startswith('qq:private:')):
            pending=dsh.pending_request({'session_id':event.session_id,'actor_id':event.actor_id,
                'action':{'target':event.actor_id,'payload':{}}})
            if pending:
                reply=(f"这项任务已有真实待确认请求：\n任务：{pending['task']}\n"
                    f"请回复“同意 {pending['approval_code']}”执行，或回复“拒绝 {pending['approval_code']}”取消。"
                    "单独回复“同意”不会启动 DSH。")
                action=Action.create('send_message',{'session_id':event.session_id,'text':reply,'platform':'napcat'},
                    channel=str(event.metadata.get('reply_channel') or 'qq'),
                    target=str(event.metadata.get('reply_target') or event.actor_id))
                delivery=await self.dispatcher.dispatch(event.event_id,action.to_dict())
                logger.info(f'dsh pending approval code resent event_id={event.event_id} delivered={delivery.success}')
                return {'event_id':event.event_id,'actions':[action.to_dict()],
                    'dispatched':[delivery.to_dict()],'dsh_pending_request':'approval_code_resent'}
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
        dsh_connectivity_request=(
            re.search(r'dsh',install_request,re.I)
            and re.search(r'(?:api|接口|模型|连接|连通|联网|可用)',install_request,re.I)
            and re.search(r'(?:查看|检查|测试|验证|确认|查一下|看看|测一下)',install_request)
        )
        if (dsh is not None and dsh_connectivity_request and event.source=='qq' and event.type=='message'
                and event.metadata.get('platform','napcat')=='napcat'
                and str(event.session_id or '').startswith('qq:private:')):
            status='approval_required'
            if not dsh.enabled:
                reply='DSH 接入目前处于关闭状态，没有发起 API 检查。'
                status='disabled'
            elif not dsh.binary_path():
                reply='DSH 命令行工具尚未安装，无法检查 API 连通性；没有运行任务。请先单独请求安装 DSH 运行时。'
                status='runtime_missing'
            else:
                try:
                    prepared=await dsh.execute('dsh_prepare',{
                        'operation':'run',
                        'task':('通过当前 DSH 配置执行一次 API 连通性自检，只请求模型回复“DSH API 连通”。'
                            '不要调用工具、读取或修改文件、安装插件、更改配置，也不要显示 API Key。'),
                        'rationale':'主人在 QQ 私聊中明确要求检查 DSH API 是否连通。'},
                        {'session_id':event.session_id,'actor_id':event.actor_id,
                         'action':{'target':event.actor_id,'payload':{}}})
                    if prepared.get('status')=='approval_required':
                        reply=(f"DSH API 连通性检查已建立，尚未启动。\n任务：{prepared['display_task']}\n"
                            f"理由：{prepared['rationale']}\n回复“同意 {prepared['approval_code']}”执行，"
                            f"或回复“拒绝 {prepared['approval_code']}”取消。确认码 10 分钟内有效。")
                        status='approval_required'
                    else:
                        reply='暂时无法建立 DSH API 检查请求；没有运行任务。'
                        status=str(prepared.get('status') or 'unavailable')
                except PermissionError:
                    reply='DSH API 检查仅接受已绑定主人的 QQ 私聊；没有运行任务。'
                    status='owner_only'
                except Exception as exc:
                    logger.warning(f'dsh connectivity request preparation failed event_id={event.event_id} error_type={type(exc).__name__}')
                    reply='暂时无法建立 DSH API 检查请求；没有运行任务，请稍后再试。'
                    status='failed'
            action=Action.create('send_message',{'session_id':event.session_id,
                'text':reply,'platform':'napcat'},
                channel=str(event.metadata.get('reply_channel') or 'qq'),
                target=str(event.metadata.get('reply_target') or event.actor_id))
            delivery=await self.dispatcher.dispatch(event.event_id,action.to_dict())
            logger.info(f'dsh connectivity request handled event_id={event.event_id} status={status} delivered={delivery.success}')
            return {'event_id':event.event_id,'actions':[action.to_dict()],
                'dispatched':[delivery.to_dict()],'dsh_connectivity_request':status}
        dsh_question=bool(re.search(r'(?:为什么|怎么|如何|是什么|能不能|能否|可不可以|是否|吗[?？。！!]*$)',install_request))
        dsh_direct_command=bool(
            re.search(r'(?:用|通过|调用|使用|让|叫).{0,16}dsh',install_request,re.I)
            or re.search(r'dsh.{0,24}(?:帮我|来|执行|运行|安装|配置|检查|测试|查看|搜索|编写|修改|创建|分析|整理|抓取|下载|生成)',install_request,re.I)
        )
        if (dsh is not None and dsh_direct_command and not dsh_question
                and event.source=='qq' and event.type=='message'
                and event.metadata.get('platform','napcat')=='napcat'
                and str(event.session_id or '').startswith('qq:private:')):
            status='approval_required'
            if not dsh.enabled:
                prepared={'status':'disabled'}
                reply='DSH 接入目前处于关闭状态，没有创建或运行任务。'
                status='disabled'
            elif not dsh.binary_path():
                reply='DSH 命令行工具尚未安装，没有创建或运行任务。请先单独请求安装 DSH 运行时。'
                status='runtime_missing'
            else:
                try:
                    prepared=await dsh.execute('dsh_prepare',{
                        'operation':'run','task':install_request[:12000],
                        'rationale':'主人在 QQ 私聊中明确要求通过 DSH 执行这项任务。'},
                        {'session_id':event.session_id,'actor_id':event.actor_id,
                         'action':{'target':event.actor_id,'payload':{}}})
                    reply=self._dsh_approval_message(prepared)
                    status=str(prepared.get('status') or 'unavailable')
                except PermissionError:
                    reply='DSH 请求仅接受已绑定主人的 QQ 私聊；没有运行任务。'
                    status='owner_only'
                except Exception as exc:
                    logger.warning(f'dsh direct request preparation failed event_id={event.event_id} error_type={type(exc).__name__}')
                    reply='暂时无法建立 DSH 确认请求；没有运行任务，请稍后再试。'
                    status='failed'
            action=Action.create('send_message',{'session_id':event.session_id,'text':reply,'platform':'napcat'},
                channel=str(event.metadata.get('reply_channel') or 'qq'),
                target=str(event.metadata.get('reply_target') or event.actor_id))
            delivery=await self.dispatcher.dispatch(event.event_id,action.to_dict())
            logger.info(f'dsh direct request handled event_id={event.event_id} status={status} delivered={delivery.success}')
            return {'event_id':event.event_id,'actions':[action.to_dict()],
                'dispatched':[delivery.to_dict()],'dsh_direct_request':status}
        # A short affirmative can accept the *proposal to prepare a request*, but
        # it must never authorize execution. Create the normal one-time code so
        # the owner can review the exact, bounded task before anything runs.
        affirmative = re.fullmatch(r'(?:同意|可以|好|行|嗯|继续|去吧|开始|ok|okay|yes)[。.!！ ]*', install_request, re.I)
        if (dsh is not None and affirmative and event.source=='qq' and event.type=='message'
                and event.metadata.get('platform','napcat')=='napcat'
                and str(event.session_id or '').startswith('qq:private:')):
            history=self.core.store.recent_conversation(event.session_id, limit=6)
            supplied=event.metadata.get('conversation_history', [])
            if isinstance(supplied, list):
                for item in supplied:
                    if isinstance(item, dict) and item.get('role') in {'user','assistant'} and item.get('content'):
                        normalized={'role':str(item['role']),'content':str(item['content'])}
                        if normalized not in history:
                            history.append(normalized)
            previous=next((item.get('content','') for item in reversed(history)
                           if item.get('role')=='assistant' and item.get('content')), '')
            waiting = bool(re.search(r'dsh', previous, re.I)
                           and re.search(r'(?:等你|需要你|请你|让你|你来|点头|同意|确认|批准|回复|我现在就准备|准备.{0,12}(?:请求|操作|调用))', previous))
            if waiting:
                if not dsh.enabled:
                    reply='DSH 接入目前处于关闭状态，没有创建请求。'
                    status='disabled'
                elif not dsh.binary_path():
                    reply='DSH 命令行工具还未安装。请先单独请求安装；这次没有运行任何 DSH 任务。'
                    status='runtime_missing'
                else:
                    try:
                        proposal=re.sub(r'\s+', ' ', previous).strip()[:400]
                        task=('执行上一条助手明确提出、现由主人表示可以继续准备的 DSH 任务；严格限于该提议的目标。'
                              '此请求仍需主人收到本次真实确认码后再次批准，当前不能运行。\n提议原文：'+proposal)
                        prepared=await dsh.execute('dsh_prepare',{
                            'operation':'run','task':task,
                            'rationale':'你刚才同意我为上一条明确提出的 DSH 建立待确认请求；这条“可以”不作为执行授权。'},
                            {'session_id':event.session_id,'actor_id':event.actor_id,
                             'action':{'target':event.actor_id,'payload':{}}})
                        if prepared.get('status')=='approval_required':
                            reply=(f"DSH 请求已建立，尚未运行。\n任务：{prepared['display_task']}\n"
                                f"理由：{prepared['rationale']}\n回复“同意 {prepared['approval_code']}”才会运行，"
                                f"或回复“拒绝 {prepared['approval_code']}”取消。确认码 10 分钟内有效。")
                            status='approval_required'
                        else:
                            reply='暂时无法建立 DSH 确认请求；没有运行任务。'
                            status=str(prepared.get('status') or 'unavailable')
                    except PermissionError:
                        reply='DSH 请求仅接受已绑定主人的 QQ 私聊；没有运行任务。'
                        status='owner_only'
                    except Exception as exc:
                        logger.warning(f'dsh proposal preparation failed event_id={event.event_id} error_type={type(exc).__name__}')
                        reply='暂时无法建立 DSH 确认请求；没有运行任务，请稍后再试。'
                        status='failed'
                action=Action.create('send_message',{'session_id':event.session_id,
                    'text':reply,'platform':'napcat'},
                    channel=str(event.metadata.get('reply_channel') or 'qq'),
                    target=str(event.metadata.get('reply_target') or event.actor_id))
                delivery=await self.dispatcher.dispatch(event.event_id,action.to_dict())
                logger.info(f'dsh proposal handled event_id={event.event_id} status={status} delivered={delivery.success}')
                return {'event_id':event.event_id,'actions':[action.to_dict()],
                    'dispatched':[delivery.to_dict()],'dsh_proposal_request':status}
        result = await self.core.process_event(event)
        if result.get("duplicate"): return result
        actions = result.get("actions", [])
        logger.info(f"action planned event_id={event.event_id} count={len(actions)}")
        dispatched = []
        feedback = []
        for action in list(actions):
            if action.get('payload', {}).get('reply_part', 1) > 1:
                await self.pause_between_replies()
            logger.info(f"action dispatched event_id={event.event_id} action_id={action.get('action_id')} type={action.get('type')}")
            action_result = await self.dispatcher.dispatch(event.event_id, action)
            logger.info(f"action result event_id={event.event_id} action_id={action_result.action_id} status={action_result.status} success={action_result.success}")
            dispatched.append(action_result.to_dict())
            payload = action.get("payload", {}) if isinstance(action.get("payload", {}), dict) else {}
            dsh_prepare_tool=(action.get('type')=='call_mcp' and payload.get('tool')=='dsh_prepare')
            if dsh_prepare_tool:
                prepared=self._dsh_preparation_result(action_result)
                message=self._dsh_approval_message(prepared)
                if prepared.get('status')!='approval_required':
                    message='没能建立有效的 DSH 待确认请求，因此没有运行任务。请在 DSH 交互台查看工具调用结果。'
                reply_action=Action.create('send_message',{'session_id':event.session_id,'text':message,
                    'platform':event.metadata.get('platform','napcat'),
                    'source_message_id':event.metadata.get('source_message_id',event.source_event_id)},
                    channel=str(event.metadata.get('reply_channel') or 'qq'),
                    target=str(event.metadata.get('reply_target') or event.actor_id),
                    reason='确定性展示 dsh_prepare 返回的真实确认码')
                actions.append(reply_action.to_dict())
                reply_result=await self.dispatcher.dispatch(event.event_id,reply_action.to_dict())
                logger.info(f"dsh preparation response handled event_id={event.event_id} status={prepared.get('status','failed')} delivered={reply_result.success}")
                dispatched.append(reply_result.to_dict())
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
                    "followup_reply": action.get("type") == "call_mcp" and action_result.success and not payload.get('silent_followup') and not dsh_prepare_tool,
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
