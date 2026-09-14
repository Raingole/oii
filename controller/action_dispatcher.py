from __future__ import annotations
import asyncio, time
import json, uuid
import logging
from typing import Any, Awaitable, Callable
from contracts.results import ActionResult
from .action_outbox import ActionOutbox
from .approval_service import ApprovalService

class ActionDispatcher:
    def __init__(self,outbox:ActionOutbox,approval:ApprovalService|None=None,max_attempts:int=3,timeout:float=15.0):
        self.outbox,self.approval,self.max_attempts,self.timeout=outbox,approval or ApprovalService(),max(1,max_attempts),timeout; self.executors={}; self._locks={}; self._inflight=set(); self.accepting=True; self.event_router=None; self._device_confirmations={}
    def register(self,action_type:str,executor:Callable[[dict[str,Any]],Awaitable[Any]]): self.executors[action_type]=executor
    def stop_accepting(self): self.accepting=False
    async def wait_for_idle(self):
        if self._inflight: await asyncio.gather(*list(self._inflight),return_exceptions=True)
    async def dispatch(self,event_id:str,action:dict[str,Any],approved:bool=False)->ActionResult:
        if not self.accepting:return ActionResult(str(action.get("action_id","")),"cancelled",False,error="dispatcher stopping")
        task=asyncio.current_task()
        if task:self._inflight.add(task)
        try:
            record=self.outbox.put(event_id,action,self.max_attempts); aid=record["action_id"]
            async with self._locks.setdefault(aid,asyncio.Lock()):
                record=self.outbox.get(aid) or record
                if record["status"]=="succeeded":return ActionResult(aid,"succeeded",True,attempts=record["attempts"])
                approval_validated = False
                if self.approval.requires(action):
                    approval_validated = self.approval.consume(action)
                    if approved and self.approval.path is None: approval_validated = True
                if self.approval.requires(action) and not approval_validated:
                    if record["status"] not in {"cancelled","succeeded"}:self.outbox.transition(aid,"cancelled","controller approval required")
                    return ActionResult(aid,"cancelled",False,error="controller approval required",attempts=record["attempts"])
                if action.get("type") in {"do_nothing","wait","observe"}:
                    if record["status"]!="succeeded":self.outbox.transition(aid,"succeeded")
                    return ActionResult(aid,"succeeded",True,result={"accepted":True},attempts=record["attempts"])
                if action.get("type") == "schedule":
                    schedule_id=str(action.get("payload",{}).get("schedule_id") or uuid.uuid4())
                    payload=dict(action.get("payload",{})); payload.setdefault("action", payload.get("next_action"))
                    db=__import__("sqlite3").connect(self.outbox.path); db.execute("INSERT OR IGNORE INTO schedules(schedule_id,action_id,event_id,due_at,payload) VALUES(?,?,?,?,?)",(schedule_id,aid,event_id,float(payload.get("due_at",time.time())),json.dumps(payload))); db.commit(); db.close()
                    self.outbox.transition(aid,"succeeded"); return ActionResult(aid,"succeeded",True,result={"schedule_id":schedule_id},attempts=record["attempts"])
                if action.get("type") == "request_confirmation":
                    if self.approval.path:
                        approval=self.approval.create(action,event_id,str(action.get("payload",{}).get("actor_id","")),str(action.get("payload",{}).get("session_id","")))
                        return ActionResult(aid,"pending",False,result={"approval_id":approval["approval_id"]},error="approval pending",attempts=record["attempts"])
                    self.outbox.transition(aid,"cancelled","approval service unavailable"); return ActionResult(aid,"cancelled",False,error="approval service unavailable",attempts=record["attempts"])
                executor=self.executors.get(action.get("type"))
                if executor is None:
                    if record["status"]!="failed":self.outbox.transition(aid,"failed","no executor registered")
                    return ActionResult(aid,"failed",False,error="no executor registered",attempts=record["attempts"])
                while True:
                    claimed=self.outbox.claim(aid,max(self.timeout*2,30.0))
                    if claimed is None:await asyncio.sleep(.05);continue
                    if claimed["status"]=="failed":return ActionResult(aid,"failed",False,error=claimed["last_error"] or "attempt limit reached",attempts=claimed["attempts"])
                    if claimed["status"]=="succeeded":return ActionResult(aid,"succeeded",True,attempts=claimed["attempts"])
                    try:
                        executor_action = dict(action); executor_action["_approval_validated"] = approval_validated
                        result=await asyncio.wait_for(executor(executor_action),timeout=self.timeout)
                        if isinstance(result,dict) and result.get("status") in {"accepted","queued","started"}:
                            self._device_confirmations.setdefault(aid, asyncio.Event())
                            self.outbox.transition(aid,"retrying","awaiting external completion",retry_at=time.time()+self.timeout)
                            return ActionResult(aid,"pending",False,result=result,error="awaiting external completion",attempts=claimed["attempts"])
                        self.outbox.transition(aid,"succeeded"); return ActionResult(aid,"succeeded",True,result=result,attempts=claimed["attempts"])
                    except asyncio.CancelledError:raise
                    except Exception as exc:
                        current=self.outbox.get(aid); attempts=current["attempts"] if current else claimed["attempts"]
                        if attempts<self.max_attempts:
                            retry=time.time()+min(.1*attempts,1.0); self.outbox.transition(aid,"retrying",str(exc),retry_at=retry); await asyncio.sleep(max(0,retry-time.time())); continue
                        self.outbox.transition(aid,"failed",str(exc)); return ActionResult(aid,"failed",False,error=str(exc),attempts=attempts)
        finally:
            if task:self._inflight.discard(task)
    async def recover(self):
        results=[await self.dispatch(r["event_id"],r["payload"],approved=r["status"]=="approved") for r in self.outbox.recoverable()]
        for schedule in self.outbox.recover_schedules():
            if not schedule.get("inner_action_id"): continue
            inner=self.outbox.get(schedule["inner_action_id"])
            if inner and inner["status"] == "succeeded": self.outbox.finish_schedule(schedule["schedule_id"],"succeeded")
            elif inner and inner["status"] == "failed": self.outbox.finish_schedule(schedule["schedule_id"],"failed",inner.get("last_error") or "inner action failed")
        return results

    async def process_due_retries(self) -> list[ActionResult]:
        results=[]
        for record in self.outbox.claim_due_retries(lease_seconds=max(self.timeout*2,30.0)):
            result = await self.dispatch(record["event_id"],record["payload"],approved=record["status"]=="approved")
            results.append(result)
            # A scheduled inner action can finish directly during a retry,
            # without going through the device-confirmation callback.
            inner = self.outbox.get(result.action_id)
            if inner and inner["status"] == "succeeded":
                self.outbox.update_schedules_for_inner(result.action_id, "succeeded")
            elif inner and inner["status"] == "failed":
                self.outbox.update_schedules_for_inner(result.action_id, "failed", result.error or inner.get("last_error", ""))
        return results

    async def wait_for_device_confirmation(self, action_id: str, timeout: float | None = None) -> ActionResult | None:
        event=self._device_confirmations.setdefault(action_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout or self.timeout)
        except asyncio.TimeoutError:
            return await self.handle_device_action_result(action_id,"timeout",result={"error":"device confirmation timeout"})
        return self._device_confirmations.pop(action_id, None) and self._last_device_results.pop(action_id, None)

    async def process_due_schedules(self) -> list[ActionResult]:
        results=[]
        for schedule in self.outbox.claim_due_schedules():
            payload=schedule["payload"]; next_action=payload.get("action") or payload.get("action_payload")
            try:
                if not next_action: raise ValueError("schedule has no action payload")
                event_id=f"scheduled:{schedule['schedule_id']}"
                if self.event_router is not None:
                    event=self.event_router.build("controller","scheduled_event","self","environment",source_event_id=event_id,content={"schedule_id":schedule["schedule_id"]})
                    await self.event_router.core.process_event(event)
                    result=await self.dispatch(event.event_id,next_action)
                    ok=result.success
                    await self.event_router.core.process_event(self.event_router.build("controller","action_result","self","environment",source_event_id=f"{result.action_id}:{result.status}",content={"action_id":result.action_id,"success":result.success,"status":result.status,"result":result.result,"error":result.error},session_id=event.session_id))
                else:
                    result=await self.dispatch(event_id,next_action)
                    ok=result.success
                if result.status == "pending" or (result.status == "failed" and self.outbox.get(result.action_id) and self.outbox.get(result.action_id)["status"] == "retrying"):
                    self.outbox.set_schedule_inner(schedule["schedule_id"], result.action_id)
                    result.result = {**(result.result if isinstance(result.result,dict) else {}), "schedule_id":schedule["schedule_id"], "schedule_status":"executing", "inner_action_id":result.action_id}
                    results.append(result)
                else:
                    self.outbox.finish_schedule(schedule["schedule_id"],"succeeded" if ok else "failed",result.error); results.append(result)
            except Exception as exc:
                self.outbox.finish_schedule(schedule["schedule_id"],"failed",str(exc)); results.append(ActionResult(schedule["action_id"],"failed",False,error=str(exc)))
        return results

    async def handle_device_action_result(self, action_id: str, status: str, device_id: str = "", result: dict[str, Any] | None = None) -> ActionResult:
        """Consume an ESP32 confirmation without resending while it is queued."""
        result = dict(result or {}); result.setdefault("device_id", device_id); result.setdefault("status", status)
        if not hasattr(self, "_last_device_results"): self._last_device_results={}
        record = self.outbox.get(action_id)
        if record is None:
            logging.getLogger(__name__).warning("device action result for unknown action_id=%s", action_id)
            outcome=ActionResult(action_id, "failed", False, result=result, error="unknown action_id", external_id=result.get("external_id")); self._last_device_results[action_id]=outcome; return outcome
        if record["status"] == "succeeded":
            outcome=ActionResult(action_id, "succeeded", True, result=result, attempts=record["attempts"], external_id=result.get("external_id")); self._last_device_results[action_id]=outcome; self.outbox.update_schedules_for_inner(action_id,"succeeded"); return outcome
        if status in {"queued", "started"}:
            retry_at = time.time() + self.timeout
            if record["status"] in {"executing", "retrying"}: self.outbox.transition(action_id, "retrying", "awaiting device confirmation", retry_at=retry_at)
            outcome=ActionResult(action_id, "pending", False, result=result, error="awaiting device confirmation", attempts=record["attempts"], external_id=result.get("external_id")); self._last_device_results[action_id]=outcome; return outcome
        if status == "completed" and result.get("output_sent") is True:
            if record["status"] in {"executing", "retrying", "pending", "approved"}: self.outbox.transition(action_id, "succeeded")
            outcome=ActionResult(action_id, "succeeded", True, result=result, attempts=record["attempts"], external_id=result.get("external_id")); self._last_device_results[action_id]=outcome; self.outbox.update_schedules_for_inner(action_id,"succeeded"); self._device_confirmations.setdefault(action_id,asyncio.Event()).set(); return outcome
        if status in {"failed", "timeout"}:
            error = str(result.get("error") or status)
            if record["attempts"] < record["max_attempts"]:
                self.outbox.transition(action_id, "retrying", error, retry_at=time.time())
                outcome=ActionResult(action_id, "failed", False, result=result, error=error, attempts=record["attempts"], external_id=result.get("external_id")); self._last_device_results[action_id]=outcome; self.outbox.update_schedules_for_inner(action_id,"failed",error) if record["attempts"] >= record["max_attempts"] else None; self._device_confirmations.setdefault(action_id,asyncio.Event()).set(); return outcome
            self.outbox.transition(action_id, "failed", error)
            outcome=ActionResult(action_id, "failed", False, result=result, error=error, attempts=record["attempts"], external_id=result.get("external_id")); self._last_device_results[action_id]=outcome; self.outbox.update_schedules_for_inner(action_id,"failed",error); self._device_confirmations.setdefault(action_id,asyncio.Event()).set(); return outcome
        return ActionResult(action_id, "pending", False, result=result, error="unknown device action status", attempts=record["attempts"], external_id=result.get("external_id"))
