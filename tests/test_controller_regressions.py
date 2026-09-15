import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from controller.action_outbox import ActionOutbox
from controller.action_dispatcher import ActionDispatcher
from controller.approval_service import ApprovalService
from controller.cognitive_fallback import device_output_succeeded
from controller.tool_catalog import ToolCatalog
from controller.tool_executor import ToolExecutor
from cognitive_core.memory import InMemoryAdapter
from cognitive_core.scheduler.heartbeat import HeartbeatScheduler

class ControllerRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/"state.db"
    def tearDown(self): self.tmp.cleanup()
    async def test_attempts_persist_and_expired_lease_recovers(self):
        ob=ActionOutbox(self.path); action={"action_id":"a1","type":"speak","payload":{"text":"x"}}
        ob.put("e1",action,max_attempts=2); claimed=ob.claim("a1",1); self.assertEqual(claimed["attempts"],1); self.assertEqual(ob.get("a1")["attempts"],1)
        ob.transition("a1","retrying","timeout",retry_at=time.time()-1); self.assertEqual(ob.recoverable()[0]["attempts"],1)
    async def test_retry_never_resets_after_restart(self):
        ob=ActionOutbox(self.path); action={"action_id":"a2","type":"send_message","payload":{}}; ob.put("e2",action,max_attempts=2); ob.claim("a2"); ob.transition("a2","retrying","x",retry_at=time.time()-1)
        d=ActionDispatcher(ActionOutbox(self.path),max_attempts=2,timeout=.01); calls=0
        async def fail(_):
            nonlocal calls; calls+=1; raise RuntimeError("no")
        d.register("send_message",fail); result=await d.recover(); self.assertEqual(calls,1); self.assertEqual(result[0].attempts,2); self.assertEqual(d.outbox.get("a2")["status"],"failed")
    def test_device_fallback_requires_real_output(self):
        base={"actions":[{"action_id":"x","type":"speak"}]}
        self.assertTrue(device_output_succeeded({**base,"dispatched":[{"action_id":"x","status":"succeeded","success":True,"result":{"status":"completed","output_sent":True}}]}))
        self.assertFalse(device_output_succeeded({**base,"dispatched":[{"action_id":"x","status":"succeeded","success":True,"result":{"status":"queued"}}]}))
        for status in ("failed","cancelled"):
            self.assertFalse(device_output_succeeded({**base,"dispatched":[{"action_id":"x","status":status,"success":False}]}))
        self.assertFalse(device_output_succeeded({"actions":[{"type":"do_nothing"}],"dispatched":[]}))
    async def test_tool_catalog_provider_and_unavailable(self):
        class Provider:
            async def discover(self): return [{"name":"mock.weather"}]
            async def execute(self,n,a,c): return {"temperature":20}
        cat=ToolCatalog(); cat.register("server_mcp",Provider()); ex=ToolExecutor(cat)
        self.assertTrue((await ex.execute("mock.weather",{},{}))["success"]); self.assertFalse((await ex.execute("missing",{},{}))["success"])
    def test_approval_payload_binding_and_expiry(self):
        service=ApprovalService(self.path); action={"action_id":"r1","type":"call_mcp","risk_level":"high","payload":{"tool":"delete"}}
        rec=service.create(action,"e1","actor","s1",ttl=1); self.assertTrue(service.approve_request(rec["approval_id"],rec["approval_token"],"admin","actor")); self.assertTrue(service.is_approved(action)); action["payload"]["tool"]="other"; self.assertFalse(service.is_approved(action))

    async def test_memory_user_and_session_isolation(self):
        memory=InMemoryAdapter(); await memory.store("private alice", {"user_id":"alice","session_id":"s1","channel":"qq"}); await memory.store("private bob", {"user_id":"bob","session_id":"s2","channel":"qq"})
        self.assertEqual([x.text for x in await memory.search("private", user_id="alice", session_id="s1", channel="qq")], ["private alice"])
        self.assertEqual(await memory.search("private", user_id="alice", session_id="s2", channel="qq"), [])

    async def test_failed_provider_result_is_not_success(self):
        class Provider:
            async def discover(self): return [{"name":"bad"}]
            async def execute(self,n,a,c): return {"success":False,"error":"denied"}
        cat=ToolCatalog(); cat.register("plugins",Provider()); result=await ToolExecutor(cat).execute("bad",{},{}); self.assertFalse(result["success"]); self.assertEqual(result["error"],"denied")

    async def test_device_confirmation_closes_action_once(self):
        ob=ActionOutbox(self.path); d=ActionDispatcher(ob, max_attempts=2, timeout=.05); action={"action_id":"device-1","type":"speak","payload":{"text":"hello"}}
        async def queued(_): return {"status":"queued","action_id":"device-1"}
        d.register("speak", queued); pending=await d.dispatch("event-1", action); self.assertEqual(pending.status,"pending"); self.assertFalse(pending.success)
        done=await d.handle_device_action_result("device-1","completed","esp32-1", {"output_sent":True}); self.assertTrue(done.success); self.assertEqual(ob.get("device-1")["status"],"succeeded")
        duplicate=await d.handle_device_action_result("device-1","completed","esp32-1", {"output_sent":True}); self.assertTrue(duplicate.success); self.assertEqual(ob.get("device-1")["attempts"],1)
        unknown=await d.handle_device_action_result("missing","completed","esp32-1", {"output_sent":True}); self.assertFalse(unknown.success)

    async def test_due_schedule_executes_once(self):
        ob=ActionOutbox(self.path); d=ActionDispatcher(ob, timeout=.1); calls=[]
        async def send(action): calls.append(action["action_id"]); return {"status":"completed","output_sent":True}
        d.register("send_message",send)
        scheduled={"action_id":"schedule-1","type":"schedule","payload":{"due_at":time.time()-1,"next_action":{"action_id":"inner-1","type":"send_message","payload":{"text":"now"}}}}
        await d.dispatch("event-schedule",scheduled); results=await d.process_due_schedules(); self.assertTrue(results[0].success); self.assertEqual(calls,["inner-1"]); self.assertEqual(len(await d.process_due_schedules()),0)

    async def test_queued_schedule_waits_for_completion(self):
        ob=ActionOutbox(self.path); d=ActionDispatcher(ob, timeout=.05); action={"action_id":"scheduled-esp","type":"schedule","payload":{"due_at":0,"next_action":{"action_id":"inner-esp","type":"speak","payload":{"text":"hello"}}}}
        async def queued(_): return {"status":"queued"}
        d.register("speak",queued); await d.dispatch("schedule-event",action); result=(await d.process_due_schedules())[0]
        self.assertEqual(result.status,"pending"); self.assertEqual(ob.recover_schedules()[0]["status"],"executing")
        await d.handle_device_action_result("inner-esp","completed","esp32", {"output_sent":True})
        self.assertEqual(ob.recover_schedules(),[])

    async def test_scheduled_inner_retry_completion_finishes_schedule(self):
        ob=ActionOutbox(self.path); d=ActionDispatcher(ob,max_attempts=2,timeout=.02); calls=[]
        async def queued_then_complete(action):
            calls.append(action["action_id"])
            return {"status":"queued"} if len(calls)==1 else {"status":"completed","output_sent":True}
        d.register("speak",queued_then_complete)
        await d.dispatch("schedule-retry", {"action_id":"schedule-retry","type":"schedule","payload":{"due_at":0,"next_action":{"action_id":"inner-retry","type":"speak","payload":{}}}})
        pending=(await d.process_due_schedules())[0]
        self.assertEqual(pending.status,"pending")
        await d.handle_device_action_result("inner-retry","timeout",result={})
        result=(await d.process_due_retries())[0]
        self.assertTrue(result.success)
        self.assertEqual(ob.get("inner-retry")["status"],"succeeded")
        self.assertEqual(ob.recover_schedules(),[])
        self.assertEqual(calls,["inner-retry","inner-retry"])

    async def test_approval_approved_action_resumes_and_consumes(self):
        approval=ApprovalService(self.path); ob=ActionOutbox(self.path); d=ActionDispatcher(ob,approval=approval,timeout=.1); calls=[]
        async def execute(_): calls.append(1); return {"status":"completed"}
        d.register("call_mcp",execute); action={"action_id":"risk-1","type":"call_mcp","risk_level":"high","requires_controller_approval":True,"payload":{"tool":"write"}}
        blocked=await d.dispatch("event-risk",action); self.assertEqual(blocked.status,"cancelled"); rec=approval.create(action,"event-risk","actor-1","session-1",ttl=30); self.assertTrue(approval.approve_request(rec["approval_id"],rec["approval_token"],"admin","actor-1")); ob.transition("risk-1","approved"); result=await d.dispatch("event-risk",action,approved=True); self.assertTrue(result.success); self.assertEqual(calls,[1]); self.assertFalse(approval.consume(action))

    async def test_due_retry_is_redispatched_by_scheduler(self):
        ob=ActionOutbox(self.path); d=ActionDispatcher(ob,max_attempts=2,timeout=.02); calls=[]
        async def executor(action):
            calls.append(action["action_id"]); return {"status":"queued"} if len(calls)==1 else {"status":"completed"}
        d.register("speak",executor); action={"action_id":"retry-scheduler","type":"speak","payload":{}}
        await d.dispatch("retry-event",action); await d.handle_device_action_result(action["action_id"],"timeout",result={}); await d.process_due_retries()
        self.assertEqual(calls,["retry-scheduler","retry-scheduler"]); self.assertEqual(ob.get(action["action_id"])["status"],"succeeded")

    async def test_qq_send_timeout_is_at_most_once(self):
        ob=ActionOutbox(self.path); d=ActionDispatcher(ob,max_attempts=3,timeout=.01); calls=[]
        async def unavailable(_):
            calls.append(1)
            raise TimeoutError("OneBot echo timeout")
        d.register("send_message", unavailable)
        result=await d.dispatch("qq-event", {"action_id":"qq-once","type":"send_message","channel":"qq","payload":{"text":"hello"}})
        self.assertEqual(result.status,"failed")
        self.assertEqual(calls,[1])
        self.assertEqual(ob.get("qq-once")["attempts"],1)

    async def test_scheduler_runs_persisted_work_when_autonomy_disabled(self):
        ob=ActionOutbox(self.path); d=ActionDispatcher(ob,timeout=.02); calls=[]
        async def execute(action): calls.append(action["action_id"]); return {"status":"completed"}
        d.register("send_message",execute); await d.dispatch("schedule-event", {"action_id":"schedule-off","type":"schedule","payload":{"due_at":0,"next_action":{"action_id":"scheduled-off-action","type":"send_message","payload":{}}}})
        core=__import__("cognitive_core.runtime",fromlist=["CognitiveCore"]).CognitiveCore(str(self.path)+".core",config={"initiative_threshold":999})
        scheduler=HeartbeatScheduler(core,interval=60,dispatcher=d,autonomous_enabled=False); result=await scheduler.run_once()
        self.assertFalse(result["full_tick"]); self.assertEqual(calls,["scheduled-off-action"])
