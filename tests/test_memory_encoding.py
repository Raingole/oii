import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.memory_encoding import encode_turn, explicit_affect
from core.memory.manager import MemoryManager
from cognitive_core.runtime import CognitiveCore
from cognitive_core.contracts import CognitiveEvent
from cognitive_core.memory import LegacyMemoryPort
from cognitive_core.llm import validate_decision


class EncodingTests(unittest.TestCase):
    def test_screenshot_preference_is_not_three_copies_of_raw_text(self):
        text = '以后少一点符号吧 然后句子短一点看着舒服'
        item = encode_turn(text)
        self.assertIn(text, item['fact'])
        self.assertEqual(item['gist'], '用户偏好少符号、简短的表达')
        self.assertIn('舒适', item['affect'])
        self.assertIn('看着舒服', item['affect'])
        self.assertNotIn('助手：', item['fact'])

    def test_neutral_question_negation_and_quoted_examples_do_not_create_affect(self):
        for text in ['今天写了代码', '你开心吗', '我开心吗', '我不生气', '我没有不开心', '如果我很开心就去玩', '他说我很难过', '请回复我很开心', '开心是什么', '我觉得开心的人很多']:
            self.assertEqual(encode_turn(text)['affect'], '', text)
        self.assertIn('不快', encode_turn('我不开心')['affect'])
        self.assertIn('难过', encode_turn('今天真的好难过')['affect'])

    def test_model_summaries_are_used_but_emotions_need_current_source(self):
        item = encode_turn('我很安心，今天和小雨包饺子', '好呀', {
            'fact': '用户今天和小雨包饺子', 'gist': '一起做饭让人安心',
            'affect': '开心', 'affect_evidence': '我很开心',
            'entities': ['小雨', '虚构人物'], 'themes': ['一起做饭'], 'scope': 'other'})
        self.assertEqual(item['fact'], '用户今天和小雨包饺子')
        self.assertEqual(item['gist'], '一起做饭让人安心')
        self.assertIn('安心', item['affect'])
        self.assertNotIn('开心', item['affect'])
        self.assertEqual(item['entities'], ['小雨'])
        self.assertNotIn('scope', item)
        self.assertEqual(encode_turn('今天写了代码', '', {'affect':'开心','affect_evidence':'今天写了代码'})['affect'], '')

    def test_malformed_auxiliary_observation_does_not_break_reply(self):
        for value in [None, [], 'bad', {'fact': {}, 'gist': 1, 'entities': {}}]:
            decision = validate_decision({'intent':'reply','message':'继续聊','memory_observation':value})
            self.assertEqual(decision['message'], '继续聊')
            self.assertIsInstance(decision['memory_observation'], dict)


@unittest.skipUnless(shutil.which('node'), 'Node >=22.13 required')
class EncodingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        with patch('core.memory.manager.setup_logging'):
            self.manager = MemoryManager({'owner_id':'owner','selected_module':{'Memory':'local'},'Memory':{'local':{'database':str(folder/'legacy.db')}},'mmtr':{'database':str(folder/'mmtr.db')}})
        self.core = CognitiveCore(str(folder/'core.db'),memory=LegacyMemoryPort(self.manager),config={'llm_enabled':True})
        class Model:
            async def decide(self,*args):
                return {'intent':'reply','messages':['好，少用符号。','句子也会短一点。'], 'memory_observation':{'fact':'用户希望回复少用符号、句子简短','gist':'偏好清爽简短的沟通','affect':'舒适','affect_evidence':'看着舒服','themes':['沟通偏好']}}
        self.core.llm=Model()

    async def asyncTearDown(self):
        self.manager.close()
        self.temp.cleanup()

    async def test_real_conversation_writes_three_distinct_channels_then_recalls_them(self):
        text='以后少一点符号吧 然后句子短一点看着舒服'
        await self.core.process_event(CognitiveEvent.create('qq','message','qq:7','agent',{'text':text},'qq:private:7',event_id='encode:1'))
        memory=self.manager.mmtr_backend.recall_details('owner','qq:private:7',None,'沟通偏好')
        self.assertTrue(memory['items'])
        channels={item['channel']:item for item in memory['items'][0]['channels']}
        self.assertEqual(channels['fact']['content'],'用户希望回复少用符号、句子简短')
        self.assertEqual(channels['gist']['content'],'偏好清爽简短的沟通')
        self.assertIn('看着舒服',channels['affect']['content'])
        for channel in channels.values():
            self.assertIn('baseStrength',channel)
            self.assertIn('recallBoost',channel)
        await self.core.process_event(CognitiveEvent.create('qq','message','qq:7','agent',{'text':'记得沟通偏好吗'},'qq:private:7',event_id='encode:2'))
        record=self.core.store.memory_activity('qq:private:7')['selected']
        node=record['memory_nodes'][0]
        self.assertIsNotNone(node['created_at'])
        self.assertIsNotNone(node['age_days'])
        self.assertTrue(any(item['channel']=='affect' and item['content'] for item in node['channels']))
        self.assertTrue(all(item['base_strength'] is not None for item in node['channels']))

    async def test_tool_feedback_never_becomes_fake_user_dialogue(self):
        await self.core.process_event(CognitiveEvent.create('controller','action_result','self','environment',{'success':True,'result':{'secret':'not-a-conversation'}},'qq:private:7'))
        self.assertEqual(self.manager.mmtr_backend.call('recall','action_result',{'scope':'owner','style':'full'})['items'],[])
