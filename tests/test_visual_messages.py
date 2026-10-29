import asyncio
import base64
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from core.knowledge.store import KnowledgeStore
from core.knowledge.vision import VisualUnderstanding, normalize_image, check_image_url, PublicImageResolver, parse_visual_result
from cognitive_core.runtime import CognitiveCore
from cognitive_core.contracts import CognitiveEvent
from qq.models import QQMessage
from qq.event_tasks import EventTasks


def image_bytes(animated=False):
    from PIL import Image
    buffer=io.BytesIO()
    frames=[Image.new('RGB',(24,24),color) for color in ('red','green','blue')]
    if animated: frames[0].save(buffer,format='GIF',save_all=True,append_images=frames[1:],duration=50)
    else: frames[0].save(buffer,format='PNG')
    return buffer.getvalue()


class VisualTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=KnowledgeStore(self.root/'knowledge.db')
        owner=self
        class Provider:
            calls=0
            def response(self,question,encoded):
                self.calls+=1;owner.question=question
                return json.dumps({'kind':'meme','description':'卡通角色皱眉','visible_text':'气死了','expressed_emotions':[{'label':'生气','evidence':'皱眉和文字气死了','confidence':0.8}]},ensure_ascii=False)
        self.provider=Provider();self.vision=VisualUnderstanding({'visual_understanding':{'enabled':True}},self.store,self.provider)
        self.inline='data:image/png;base64,'+base64.b64encode(image_bytes()).decode()

    async def test_qq_array_and_cq_images_are_not_discarded(self):
        message=QQMessage.from_event({'user_id':7,'message':[{'type':'image','data':{'url':'https://example.com/a.gif','file':'hash'}}]})
        self.assertEqual(message.message,'[图片]');self.assertEqual(len(message.images),1)
        cq=QQMessage.from_event({'message':'看看[CQ:image,url=https://example.com/a?x=1&#44;y=2]'})
        self.assertEqual(cq.message,'看看[图片]')
        self.assertEqual(cq.images[0]['url'],'https://example.com/a?x=1,y=2')

    async def test_sticker_expression_is_scoped_cached_and_not_user_mood(self):
        first=(await self.vision.describe_many('a',[{'url':self.inline}]))[0]
        self.assertEqual(first['emotion_scope'],'image_expression_only')
        self.assertEqual(first['expressed_emotions'][0]['label'],'生气')
        self.assertTrue((await self.vision.describe_many('a',[{'url':self.inline}]))[0]['cached'])
        self.assertEqual(self.provider.calls,1)
        await self.vision.describe_many('b',[{'url':self.inline}])
        self.assertEqual(self.provider.calls,2)

    async def test_image_context_reaches_core_without_persisting_image_payload(self):
        core=CognitiveCore(str(self.root/'core.db'),config={'llm_enabled':True})
        core.vision=self.vision
        captured={}
        class Model:
            async def decide(self,prompt,context,trace_id=''):
                captured['prompt']=prompt
                return {'intent':'reply','message':'这张表情包看起来在表达生气。'}
        core.llm=Model()
        event=CognitiveEvent.create('qq','message','qq:7','agent',{'text':'[图片]'},'qq:private:7',metadata={'images':[{'url':self.inline}]})
        await core.process_event(event)
        self.assertIn('image_expression_only',captured['prompt'])
        self.assertIn('卡通角色皱眉',captured['prompt'])
        with core.store.path.open('rb') as dbfile:
            self.assertNotIn(self.inline.encode(),dbfile.read())
        observation=core.store.memory_activity('qq:private:7')['selected']
        self.assertEqual(observation['visual_context'][0]['kind'],'meme')

    async def test_animated_gif_has_bounded_sampled_frames(self):
        encoded,meta=normalize_image(image_bytes(True))
        self.assertTrue(meta['animated']);self.assertEqual(meta['sampled_frames'],3)
        self.assertTrue(base64.b64decode(encoded).startswith(b'\xff\xd8'))

    async def test_unconfigured_provider_returns_honest_unavailability(self):
        result=await VisualUnderstanding({'visual_understanding':{'enabled':True}},self.store).describe_many('a',[{'url':self.inline}])
        self.assertEqual(result[0]['error'],'vision_provider_not_configured')

    async def test_private_addresses_files_and_dns_rebinding_are_rejected(self):
        for url in ['file:///etc/passwd','http://127.0.0.1/a','http://169.254.169.254/latest','http://[::1]/a','https://user:pass@example.com/a']:
            with self.assertRaises(ValueError): check_image_url(url)
        loop=asyncio.get_running_loop()
        with patch.object(loop,'getaddrinfo',AsyncMock(return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])):
            with self.assertRaises(ValueError): await PublicImageResolver().resolve('public-looking.example',443)
        check_image_url('http://127.0.0.1:3000/a',trusted=['127.0.0.1'])

    async def test_onebot_echo_is_processed_while_message_awaits_send(self):
        queue=EventTasks()
        response=asyncio.get_running_loop().create_future()
        sent=asyncio.Event();finished=asyncio.Event()
        async def handler(raw):
            value=json.loads(raw)
            if value.get('echo'):
                response.set_result(True)
            else:
                sent.set()
                await response
                finished.set()
        await queue.feed('{"post_type":"message"}',handler)
        await asyncio.wait_for(sent.wait(),1)
        await queue.feed('{"echo":"send-1"}',handler)
        await asyncio.wait_for(finished.wait(),1)
        await queue.close()

    async def test_bad_vision_json_does_not_become_a_guessed_emotion(self):
        with self.assertRaises(ValueError): parse_visual_result('not JSON')

    async def test_live_onebot_socket_can_ack_get_image_and_consecutive_replies(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        finished=asyncio.Event();errors=[]
        async def socket(request):
            ws=web.WebSocketResponse();await ws.prepare(request)
            queue=EventTasks(errors.append);pending={}
            async def handle(raw):
                payload=json.loads(raw)
                if payload.get('echo'):
                    pending.pop(payload['echo']).set_result(payload)
                    return
                for index,action in enumerate(['get_image','send_private_msg','send_private_msg']):
                    echo='action-'+str(index)
                    pending[echo]=asyncio.get_running_loop().create_future()
                    await ws.send_json({'action':action,'echo':echo,'params':{}})
                    await pending[echo]
                finished.set()
            try:
                async for message in ws:
                    await queue.feed(message.data,handle)
            finally:
                await queue.close()
            return ws
        app=web.Application();app.router.add_get('/onebot',socket)
        client=TestClient(TestServer(app));await client.start_server()
        try:
            ws=await client.ws_connect('/onebot')
            await ws.send_json({'post_type':'message','message':'[图片]'})
            actions=[]
            for _ in range(3):
                action=await ws.receive_json(timeout=2);actions.append(action['action'])
                await ws.send_json({'echo':action['echo'],'status':'ok','retcode':0,'data':{}})
            await asyncio.wait_for(finished.wait(),2)
            self.assertEqual(actions,['get_image','send_private_msg','send_private_msg'])
            self.assertEqual(errors,[])
            await ws.close()
        finally:
            await client.close()
