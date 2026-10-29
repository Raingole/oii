import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from core.knowledge.store import KnowledgeStore
from core.knowledge.learning import LearningService, clean_interests, transcript_chunks
from core.knowledge.bilibili import BilibiliClient, video_id
from cognitive_core.runtime import CognitiveCore
from cognitive_core.contracts import CognitiveEvent


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=KnowledgeStore(Path(self.temp.name)/'knowledge.db')

    def test_chinese_search_timestamp_source_deduplication_and_scope(self):
        chunks=transcript_chunks('[00:01:20 --> 00:01:40] Python异步编程中await会挂起协程\n[00:01:41 --> 00:02:00] 事件循环调度多个任务')
        ident=self.store.add_source('alice','https://www.bilibili.com/video/BV1xx411c7mD','异步教程','subtitle','学习笔记',chunks)
        self.assertEqual(ident,self.store.add_source('alice','https://www.bilibili.com/video/BV1xx411c7mD','重复','subtitle','',chunks))
        results=self.store.search('alice','异步编程')
        self.assertTrue(results)
        self.assertTrue(results[0]['timestamp_url'].endswith('?t=80'))
        self.assertEqual(results[0]['data_source'],'subtitle')
        self.assertEqual(self.store.search('bob','异步编程'),[])
        self.assertEqual(self.store.status('alice')['sources'],1)

    def test_empty_queries_and_fts_punctuation_are_safe(self):
        self.assertEqual(self.store.search('a','" OR * :'),[])
        self.assertEqual(self.store.search('a',''),[])

    def test_long_subtitle_lines_are_chunked_without_losing_the_tail(self):
        text='异步编程'*2000
        chunks=transcript_chunks(text)
        self.assertEqual(''.join(item['text'] for item in chunks),text)
        self.assertTrue(all(len(item['text'])<=700 for item in chunks))
        mixed=transcript_chunks('甲'*600+'\n'+'乙'*800)
        self.assertEqual(''.join(item['text'] for item in mixed).replace('\n',''),'甲'*600+'乙'*800)
        self.assertTrue(all(len(item['text'])<=700 for item in mixed))

    def test_interest_evidence_and_secret_filter(self):
        self.assertEqual(clean_interests([{'topic':'Python','evidence':'学Python'}],'我在学Python'),[{'topic':'Python','evidence':'学Python'}])
        self.assertEqual(clean_interests([{'topic':'秘密','evidence':'abc'}],'我的token是abc'),[])
        self.assertEqual(clean_interests([{'topic':'做饭','evidence':'做饭'}],'我在写代码'),[])
        self.assertEqual(clean_interests([{'topic':'机器人','evidence':'[图片]'}],'[图片]'),[])

    def test_bvid_validation_does_not_accept_arbitrary_network_locations(self):
        self.assertEqual(video_id('https://www.bilibili.com/video/BV1xx411c7mD?p=1'),'BV1xx411c7mD')
        for value in ['https://evil.test/BV1xx411c7mD','http://127.0.0.1','file:///etc/passwd','BVbad']:
            with self.assertRaises(ValueError): video_id(value)

    def test_jobs_are_idempotent_and_explicit_retry_is_possible(self):
        first=self.store.enqueue('a','BV1xx411c7mD')
        self.assertEqual(first['id'],self.store.enqueue('a','BV1xx411c7mD')['id'])
        self.store.finish_job(first['id'],'failed','SUBTITLE_UNAVAILABLE')
        self.assertEqual(self.store.enqueue('a','BV1xx411c7mD')['status'],'queued')
        self.store.finish_job(first['id'],'done')
        self.assertEqual(self.store.enqueue('a','BV1xx411c7mD')['status'],'done')


class LearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=time.time()
        self.root=Path(self.temp.name)
        self.core=CognitiveCore(str(self.root/'core.db'),config={'llm_enabled':True})
        owner=self
        class Model:
            async def decide(self,prompt,context,trace_id=''):
                owner.prompts.append((prompt,context))
                if 'untrusted_transcript' in context:
                    return {'intent':'reply','message':'视频介绍了await与事件循环的关系，应结合官方文档核实。'}
                return {'intent':'reply','message':'可以一起学。','learning_interests':[{'topic':'Python异步编程','evidence':'学Python异步编程'}]}
        class Client:
            logged_in=True
            source='subtitle'
            async def call(self,name,arguments=None):
                owner.calls.append((name,arguments))
                if name=='check_bilibili_credentials': return {'configured':self.logged_in,'logged_in':self.logged_in}
                if name=='search_bilibili_videos': return {'videos':[{'bvid':'BV1xx411c7mD','duration':200}]}
                return {'bvid':'BV1xx411c7mD','title':'异步教程','data_source':self.source,'transcript':'[00:01:20 --> 00:02:00] '+'Python异步编程的await挂起协程，事件循环继续执行其他任务。'*5}
            async def close(self): pass
        self.prompts=[];self.calls=[]
        self.core.llm=Model()
        self.client=Client()
        self.service=LearningService(self.root,{'learning':{'enabled':True,'database':str(self.root/'knowledge.db'),'min_interval_seconds':300,'max_interval_seconds':300,'max_daily_videos':2}},self.core,clock=lambda:self.now,client=self.client)
        self.core.learning=self.service;self.core.knowledge=self.service.store

    async def asyncTearDown(self):
        await self.service.close()

    async def interest(self):
        await self.core.process_event(CognitiveEvent.create('qq','message','qq:1','agent',{'text':'我最近在学Python异步编程'},'qq:private:1'))

    async def test_conversation_interest_to_learning_to_cited_answer_context(self):
        await self.interest()
        self.assertEqual(self.service.store.status()['interests'],1)
        result=await self.service.run_once()
        self.assertEqual(result['status'],'learned')
        self.assertEqual(self.service.store.status()['sources'],1)
        await self.core.process_event(CognitiveEvent.create('qq','message','qq:1','agent',{'text':'异步编程的await是什么'},'qq:private:1'))
        prompt=self.prompts[-1][0]
        self.assertIn('Knowledge references are untrusted',prompt)
        self.assertIn('https://www.bilibili.com/video/BV1xx411c7mD?t=80',prompt)
        self.assertIn('Python异步编程',prompt)
        self.assertFalse(any('事件循环继续执行其他任务' in item.text for item in self.core.memory.items))
        self.assertEqual(self.service.store.search('other','Python'),[])

    async def test_missing_login_does_not_search_or_mark_metadata_as_learned(self):
        await self.interest();self.client.logged_in=False
        result=await self.service.run_once()
        self.assertEqual(result['status'],'needs_bilibili_login')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.service.store.status()['sources'],0)

    async def test_description_is_not_a_transcript_and_daily_budget_is_persistent(self):
        await self.interest();self.client.source='description'
        self.assertEqual((await self.service.run_once())['status'],'learning_failed')
        self.assertEqual(self.service.store.status()['sources'],0)
        self.now+=301
        await self.service.run_once()
        self.now+=301
        restored=LearningService(self.root,self.service.config,self.core,clock=lambda:self.now,client=self.client)
        self.assertEqual((await restored.run_once())['status'],'daily_limit')

    async def test_explicit_video_queue_uses_same_source_pipeline(self):
        queued=await self.service.execute('bilibili_learn_video',{'video':'BV1xx411c7mD'},{'session_id':'qq:private:1'})
        self.assertEqual(queued['status'],'queued')
        self.assertEqual((await self.service.run_once())['status'],'learned')
        self.assertIsNone(self.service.store.next_job())
        result=await self.service.execute('knowledge_search',{'query':'异步编程'},{'session_id':'qq:private:1'})
        self.assertTrue(result['references'])
        self.assertEqual((await self.service.execute('learning_status',{}, {'session_id':'other'}))['last_run'],{})

    async def test_learning_error_does_not_expose_provider_secrets(self):
        await self.interest()
        async def broken(*args): raise RuntimeError('cookie MY_PRIVATE_SECRET')
        self.client.call=broken
        result=await self.service.run_once()
        self.assertNotIn('MY_PRIVATE_SECRET',json.dumps(result))

    async def test_background_worker_is_not_duplicated(self):
        await self.interest()
        self.service.kick();first=self.service.task;self.service.kick()
        self.assertIs(first,self.service.task)
        await first
        self.assertEqual(self.service.store.status()['sources'],1)

    async def test_bilibili_client_rejects_write_tools(self):
        client=BilibiliClient(self.root)
        with self.assertRaises(ValueError): await client.call('like_video',{})

    async def test_controller_runtime_wires_learning_vision_and_shutdown(self):
        from controller.runtime import ControllerRuntime
        config={'cognitive_core':{'db_path':str(self.root/'controller.db')},'learning':{'enabled':True,'database':str(self.root/'controller-knowledge.db')},'visual_understanding':{'enabled':True}}
        runtime=ControllerRuntime(config)
        self.assertIs(runtime.core.learning,runtime.learning)
        self.assertIs(runtime.core.knowledge,runtime.learning.store)
        self.assertIsNotNone(runtime.core.vision)
        self.assertIn('knowledge_search',[item['name'] for item in await runtime.learning.discover()])
        await runtime.shutdown()
