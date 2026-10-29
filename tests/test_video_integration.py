import asyncio
import base64
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.knowledge.store import KnowledgeStore
from core.knowledge.video import VideoService
from controller.action_dispatcher import ActionDispatcher
from controller.action_outbox import ActionOutbox


class VideoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=KnowledgeStore(self.root/'knowledge.db')
        self.service=VideoService(self.root,{'learning':{'enabled':True}},self.store,SimpleNamespace())
        self.context={'session_id':'qq:private:12345','event_id':'qq:1','action':{
            'action_id':'tool-one','payload':{'reply_channel':'qq','platform':'napcat','reply_target':'qq:12345'}}}
        self.addAsyncCleanup(self.service.close)

    def test_route_cannot_be_changed_to_another_user_or_platform(self):
        self.assertEqual(self.service.route(self.context)['target'],'12345')
        bad={**self.context,'session_id':'qq:private:98765'}
        self.assertIsNone(self.service.route(bad))
        bad={**self.context,'action':{'payload':{'reply_channel':'qq','platform':'official','reply_target':'12345'}}}
        self.assertIsNone(self.service.route(bad))

    async def test_artifact_scope_and_arbitrary_paths_rejected(self):
        ident='a'*32
        (self.service.folder('alice',ident)/'sheet.png').write_bytes(b'image')
        self.assertTrue(self.service.artifact('alice',ident).is_file())
        with self.assertRaises(PermissionError): self.service.artifact('bob',ident)
        with self.assertRaises(ValueError): self.service.artifact('alice','../../etc/passwd')

    async def test_background_job_retains_timestamp_evidence_and_sends_once(self):
        calls=[]
        async def bridge(request,timeout):
            calls.append(request)
            (Path(request['directory'])/'sheet.png').write_bytes(b'image')
            return {'title':'异步编程','url':'https://www.bilibili.com/video/BV1xx411c7mD',
                'cells':[{'i':1,'t_start':12,'t_end':20,'text':'事件循环如何调度协程'}], 'coverage':'sampled_frames_only'}
        self.service.bridge=bridge
        dispatcher=ActionDispatcher(ActionOutbox(self.root/'actions.db'))
        send=AsyncMock(return_value={'status':'completed','output_sent':True})
        dispatcher.register('send_message',send)
        self.service.sender=dispatcher.dispatch
        result=await self.service.execute('bilibili_watch_video',{'video':'BV1xx411c7mD'},self.context)
        await self.service.task
        status=await self.service.execute('video_job_status',{'job_id':result['job_id']},self.context)
        self.assertEqual(status['status'],'done')
        self.assertTrue(status['delivery']['success'])
        self.assertEqual(status['coverage'],'sampled_frames_only')
        self.assertEqual(len(calls),1)
        self.assertTrue(self.store.search('qq:private:12345','事件循环'))
        # Same parent action replay doesn't create or send a second video job.
        again=await self.service.execute('bilibili_watch_video',{'video':'BV1xx411c7mD'},self.context)
        self.assertEqual(again['job_id'],result['job_id'])
        await self.service.task
        self.assertEqual(send.await_count,1)
        with self.assertRaises(ValueError):
            await self.service.execute('video_job_status',{'job_id':result['job_id']},{'session_id':'bob'})

    async def test_queue_validates_network_input_and_window(self):
        for args in ({'video':'file:///etc/passwd'},{'video':'BV1xx411c7mD','from_seconds':0,'to_seconds':601},
                     {'video':'BV1xx411c7mD','from_seconds':float('nan')},{'video':'BV1xx411c7mD','cells':300}):
            with self.assertRaises(ValueError):
                await self.service.execute('bilibili_watch_video',args,self.context)

    async def test_failed_job_does_not_claim_to_have_watched(self):
        self.service.bridge=AsyncMock(side_effect=RuntimeError('SECRET SIGNED URL'))
        result=await self.service.execute('bilibili_watch_video',{'video':'BV1xx411c7mD'},self.context)
        await self.service.task
        status=await self.service.execute('video_job_status',{'job_id':result['job_id']},self.context)
        self.assertEqual(status['status'],'failed')
        self.assertNotIn('SECRET',json.dumps(status))

    async def test_cancelled_job_is_requeued(self):
        started=asyncio.Event()
        async def slow(*args,**kwargs): started.set();await asyncio.sleep(100)
        self.service.bridge=slow
        result=await self.service.execute('bilibili_watch_video',{'video':'BV1xx411c7mD'},self.context)
        await started.wait();await self.service.close()
        status=await self.service.execute('video_job_status',{'job_id':result['job_id']},self.context)
        self.assertEqual(status['status'],'queued')

    async def test_send_url_normalizes_image_and_uses_controller_outbox(self):
        from PIL import Image
        buffer=io.BytesIO();Image.new('RGB',(32,32),'blue').save(buffer,format='PNG')
        self.service.sender=AsyncMock(return_value=SimpleNamespace(to_dict=lambda:{'success':True,'status':'succeeded'}))
        with patch('core.knowledge.vision.fetch_image',AsyncMock(return_value=buffer.getvalue())):
            result=await self.service.execute('qq_send_image',{'url':'https://example.com/image.png'},self.context)
        self.assertTrue(result['success'])
        action=self.service.sender.await_args.args[1]
        self.assertEqual(action['type'],'send_message')
        self.assertNotIn('base64',json.dumps(action))
        self.assertEqual(action['target'],'12345')
        self.assertTrue(self.service.artifact(self.context['session_id'],action['payload']['image_artifact']).is_file())

    async def test_ambiguous_image_send_is_never_retried(self):
        dispatcher=ActionDispatcher(ActionOutbox(self.root/'outbox.db'),max_attempts=3)
        executor=AsyncMock(side_effect=TimeoutError('ACK lost'))
        dispatcher.register('send_message',executor)
        action={'action_id':'image-one','type':'send_message','channel':'qq','target':'12345',
            'payload':{'image_artifact':'a'*32,'session_id':'qq:private:12345'}}
        result=await dispatcher.dispatch('e',action)
        self.assertFalse(result.success)
        self.assertEqual(executor.await_count,1)

        await dispatcher.dispatch('e',action)
        self.assertEqual(executor.await_count,1)

    async def test_real_onebot_socket_delivers_image_segments_and_ack(self):
        # Load just the transport methods to avoid booting legacy LLM/plugin
        # imports. Exercise the production send/echo code over a real socket.
        import ast
        import uuid
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from qq.media import image_segments
        source=Path(__file__).resolve().parents[1]/'qq/gateway.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='QQGateway')
        methods=[n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name in {'send_private_message','send_action','_handle_payload'}]
        namespace={'asyncio':asyncio,'json':json,'uuid':uuid,'Optional':__import__('typing').Optional,'TAG':'test'}
        exec(compile(ast.Module(body=methods,type_ignores=[]),str(source),'exec'),namespace)
        logger=SimpleNamespace(info=lambda *x:None,error=lambda *x:None,warning=lambda *x:None)
        logger.bind=lambda **kw:logger
        delivered=asyncio.get_running_loop().create_future()
        async def socket(request):
            ws=web.WebSocketResponse();await ws.prepare(request)
            gateway=SimpleNamespace(websocket=ws,pending_actions={},self_id=None,logger=logger,
                delivery_mode='websocket',http_fallback_enabled=False)
            from types import MethodType
            for name in ('send_action','_handle_payload','send_private_message'):
                setattr(gateway,name,MethodType(namespace[name],gateway))
            task=asyncio.create_task(gateway.send_private_message('12345',image_segments(b'PNG bytes','看看这张')))
            try:
                async for message in ws:
                    await gateway._handle_payload(message.data)
                    delivered.set_result(await task)
            finally:
                if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
            return ws
        app=web.Application();app.router.add_get('/onebot',socket)
        client=TestClient(TestServer(app));await client.start_server()
        try:
            ws=await client.ws_connect('/onebot')
            payload=await ws.receive_json(timeout=2)
            self.assertEqual(payload['action'],'send_private_msg')
            segments=payload['params']['message']
            self.assertIsInstance(segments,list)
            self.assertEqual([x['type'] for x in segments],['text','image'])
            self.assertEqual(base64.b64decode(segments[1]['data']['file'][9:]),b'PNG bytes')
            await ws.send_json({'echo':payload['echo'],'status':'ok','retcode':0})
            self.assertTrue(await asyncio.wait_for(delivered,2))
            await ws.close()
        finally:
            await client.close()


if __name__=='__main__': unittest.main()
