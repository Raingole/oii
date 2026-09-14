import asyncio
import tempfile
import unittest
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from cognitive_core import CognitiveCore, CognitiveEvent, Action
from cognitive_core.memory import InMemoryAdapter, TencentMemoryAdapter, FallbackMemoryPort, should_store
from cognitive_core.structured import parse_structured, validate_appraisal
from cognitive_core.llm import ExistingProviderLLM, LLMDecisionError, validate_decision
from cognitive_core.mcp import ToolRegistry, ToolSpec
from cognitive_core.bridge import ControllerBridge
from cognitive_core.embodiment import BodyRegistry
from controller.action_outbox import ActionOutbox
from controller.action_dispatcher import ActionDispatcher
from controller.event_router import EventRouter
from cognitive_core.scheduler.heartbeat import HeartbeatScheduler
from cognitive_core.api import create_app
from aiohttp.test_utils import TestClient, TestServer


class CognitiveCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = InMemoryAdapter()
        self.core = CognitiveCore(str(Path(self.tmp.name) / "core.db"), memory=self.memory, config={"initiative_threshold": .3})

    async def asyncTearDown(self): self.tmp.cleanup()

    async def test_event_contract_and_duplicate(self):
        event = CognitiveEvent.create("qq", "message", "qq:1", "agent", {"text": "你在干嘛"}, "qq:1")
        result = await self.core.process_event(event)
        self.assertTrue(result["actions"])
        self.assertTrue((await self.core.process_event(event))["duplicate"])
        with self.assertRaises(ValueError): CognitiveEvent.create("bad", "message", "x", "y")

    async def test_self_persists_and_emotion_decay(self):
        event = CognitiveEvent.create("system", "action_result", "self", "environment", {"text": "工具失败"})
        await self.core.process_event(event)
        before = self.core.self_model.emotion.frustration
        self.assertGreater(before, 0)
        self.core.self_model.emotion.decay(.5)
        self.assertLess(self.core.self_model.emotion.frustration, before)
        restored = CognitiveCore(str(Path(self.tmp.name) / "core.db"), memory=self.memory)
        self.assertGreater(restored.self_model.emotion.frustration, 0)

    async def test_memory_policy_and_goal(self):
        low = CognitiveEvent.create("qq", "message", "qq:1", "agent", {"text": "好的"})
        self.assertEqual(should_store(low, {"self_relevance": .9}), "discard")
        self.core.create_goal("检查天气变化", origin="self")
        result = await self.core.run_once({"social_target": "qq:1"})
        self.assertIn(result["full_tick"], {True, False})

    async def test_heartbeat_low_and_high(self):
        low = await self.core.run_once()
        self.assertFalse(low["full_tick"])
        self.core.self_model.emotion.social_desire = 1
        high = await self.core.run_once({"social_target": "qq:1"})
        self.assertTrue(high["full_tick"])
        self.assertEqual(high["actions"][0]["type"], "send_message")

    async def test_action_permission(self):
        with self.assertRaises(ValueError): Action.create("call_mcp", {"tool": "delete"}, risk_level="high")
        action = Action.create("call_mcp", {"tool": "delete"}, risk_level="high", requires_controller_approval=True)
        self.assertTrue(action.requires_controller_approval)

    async def test_structured_output_mcp_and_body_bridge(self):
        parsed = parse_structured("noise {\"self_relevance\": 2, \"goal_relevance\": .2, \"novelty\": .1, \"controllability\": .4, \"certainty\": .8}", validate_appraisal)
        self.assertEqual(parsed["self_relevance"], 1.0)
        registry = ToolRegistry()
        async def fake(args): return {"success": True, "args": args}
        registry.register(ToolSpec("weather", "weather", "mock"), fake)
        self.assertEqual((await registry.call("weather", {"city": "重庆"}))['success'], True)
        body = BodyRegistry(); body.register("esp32_main", capabilities=["hearing", "speaking", "display"])
        self.assertEqual(body.snapshot()[0]["ownership"], "self")
        executed = []
        bridge = ControllerBridge(self.core, lambda action: _record(executed, action))
        result = await bridge.qq_message("1", "你好", "qq:1")
        self.assertTrue(result["executed"])

    async def test_router_dispatches_result_and_persists_idempotency(self):
        outbox = ActionOutbox(Path(self.tmp.name) / "outbox.db")
        dispatcher = ActionDispatcher(outbox, max_attempts=2)
        sent = []
        async def send(action): sent.append(action); return {"sent": True}
        dispatcher.register("send_message", send)
        router = EventRouter(self.core, dispatcher)
        event = router.build("qq", "message", "qq:7", "agent", source_event_id="msg-7", content={"text": "same"}, session_id="qq:7")
        first = await router.route(event)
        second = await router.route(event)
        self.assertEqual(len(sent), 1)
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["dispatched"][0]["status"], "succeeded")
        restarted = CognitiveCore(str(Path(self.tmp.name) / "core.db"), memory=self.memory)
        self.assertTrue((await restarted.process_event(event))["duplicate"])
        second_event = router.build("qq", "message", "qq:7", "agent", source_event_id="msg-8", content={"text": "same"}, session_id="qq:7")
        self.assertFalse((await router.route(second_event)).get("duplicate", False))

    async def test_retry_timeout_and_scheduler(self):
        outbox = ActionOutbox(Path(self.tmp.name) / "retry.db")
        dispatcher = ActionDispatcher(outbox, max_attempts=2, timeout=.01)
        calls = []
        async def flaky(action): calls.append(1); raise RuntimeError("timeout")
        dispatcher.register("call_mcp", flaky)
        action = Action.create("call_mcp", {"tool": "weather"})
        result = await dispatcher.dispatch("event-1", action.to_dict())
        self.assertEqual(result.status, "failed"); self.assertEqual(len(calls), 2)
        scheduler = HeartbeatScheduler(self.core, interval=.1)
        await scheduler.start(); await asyncio.sleep(.02); await scheduler.stop()
        self.assertFalse(scheduler.running)

    async def test_action_result_reflection_timeline_and_body_restore(self):
        event = CognitiveEvent.create("controller", "action_result", "self", "environment", {"success": False, "error": "timeout"}, source_event_id="failed-action")
        result = await self.core.process_event(event)
        self.assertTrue(result["meaning"])
        self.assertTrue(self.core.reflections); self.assertTrue(self.core.timeline)
        body_event = CognitiveEvent.create("esp32", "body_online", "esp32_main", "self", source_event_id="esp32-session-online")
        await self.core.process_event(body_event)
        restarted = CognitiveCore(str(Path(self.tmp.name) / "core.db"), memory=self.memory)
        self.assertTrue(restarted.self_model.body.get("esp32_main", {}).get("online"))

    async def test_tencent_headers_scope_and_fallback(self):
        requests_seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests_seen.append((self.path, {str(k).lower(): v for k, v in self.headers.items()}, json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))))
                body = {"code": 0, "data": {"items": [{"content": "remembered", "score": .9}]}}
                if self.path.endswith("conversation/add"): body = {"code": 0, "data": {"accepted_ids": ["m1"]}}
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(body).encode())
            def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
            def log_message(self, *_): pass
        server = HTTPServer(("127.0.0.1", 0), Handler); server_thread = threading.Thread(target=server.serve_forever, daemon=True); server_thread.start()
        adapter = TencentMemoryAdapter(f"http://127.0.0.1:{server.server_port}", "test-key", service_id="svc", team_id="team", agent_id="agent", user_id="user")
        self.assertTrue(await adapter.health()); self.assertEqual((await adapter.search("remember"))[0].text, "remembered"); self.assertTrue(await adapter.store("x", {"session_id": "s"}))
        self.assertEqual(requests_seen[0][1]["x-tdai-team-id"], "team"); self.assertEqual(requests_seen[0][1]["authorization"], "Bearer test-key")
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)
        fallback = FallbackMemoryPort(TencentMemoryAdapter("http://127.0.0.1:1", "test-key", timeout=.05), InMemoryAdapter()); self.assertEqual(await fallback.search("x"), [])

    async def test_api_token_health_and_event(self):
        self.core.config.update({"api_token": "local-test", "controller_bridge": True, "action_outbox": True})
        client = TestClient(TestServer(create_app(self.core))); await client.start_server()
        try:
            self.assertEqual((await client.get("/state")).status, 401)
            self.assertEqual((await client.get("/state", headers={"Authorization": "Bearer local-test"})).status, 200)
            payload = CognitiveEvent.create("system", "heartbeat", "self", "self").to_dict()
            response = await client.post("/events", json=payload, headers={"Authorization": "Bearer local-test"})
            self.assertEqual(response.status, 200)
        finally: await client.close()


    async def test_existing_provider_llm_returns_validated_decision(self):
        class StaticProvider:
            def __init__(self):
                self.calls = []

            def response_no_stream(self, system_prompt, user_prompt):
                self.calls.append((system_prompt, user_prompt))
                return '```json\n{"intent": "reply", "message": "pong", "risk_level": "low"}\n```'

        provider = StaticProvider()
        decision = await ExistingProviderLLM(provider).decide("system", {"event": "ping"}, "trace-1")
        self.assertEqual(decision["intent"], "reply")
        self.assertEqual(decision["message"], "pong")
        self.assertIn('"event": "ping"', provider.calls[0][1])

    async def test_existing_provider_llm_rejects_invalid_schema(self):
        class InvalidProvider:
            def response_no_stream(self, system_prompt, user_prompt):
                return '{"message": "missing intent"}'

        with self.assertRaises(LLMDecisionError):
            await ExistingProviderLLM(InvalidProvider()).decide("system", {}, "trace-2")

    async def test_cognitive_core_uses_llm_decision(self):
        class FakeLLM:
            async def decide(self, prompt, context, trace_id=""):
                return {
                    "intent": "reply",
                    "message": "LLM 实际回复",
                    "tool": "",
                    "arguments": {},
                    "risk_level": "low",
                    "reason": "test",
                    "goal_id": "",
                }

        self.core.config["llm_enabled"] = True
        self.core.llm = FakeLLM()
        event = CognitiveEvent.create("qq", "message", "qq:1", "agent", {"text": "测试"}, "qq:1")
        result = await self.core.process_event(event)
        self.assertEqual(result["actions"][0]["type"], "send_message")
        self.assertEqual(result["actions"][0]["payload"]["text"], "LLM 实际回复")

    async def test_cognitive_core_llm_failure_keeps_planner_fallback(self):
        class BrokenLLM:
            async def decide(self, prompt, context, trace_id=""):
                raise RuntimeError("provider timeout")

        self.core.config["llm_enabled"] = True
        self.core.llm = BrokenLLM()
        event = CognitiveEvent.create("qq", "message", "qq:2", "agent", {"text": "测试"}, "qq:2")
        result = await self.core.process_event(event)
        self.assertEqual(result["actions"][0]["type"], "send_message")
        self.assertIn("我记下了", result["actions"][0]["payload"]["text"])

    async def test_cognitive_core_llm_tool_call_uses_catalog(self):
        class ToolLLM:
            async def decide(self, prompt, context, trace_id=""):
                return {
                    "intent": "tool_call",
                    "message": "",
                    "tool": "get_weather",
                    "arguments": {"city": "重庆"},
                    "risk_level": "low",
                    "reason": "weather required",
                    "goal_id": "",
                }

        self.core.config["llm_enabled"] = True
        self.core.config["available_tools"] = [{"type": "function", "function": {"name": "get_weather"}}]
        self.core.llm = ToolLLM()
        event = CognitiveEvent.create("qq", "message", "qq:3", "agent", {"text": "天气"}, "qq:3")
        result = await self.core.process_event(event)
        self.assertEqual(result["actions"][0]["type"], "call_mcp")
        self.assertEqual(result["actions"][0]["payload"]["tool"], "get_weather")

    def test_decision_intents_match_prompt_contract(self):
        for intent in ("observe", "request_confirmation", "schedule"):
            self.assertEqual(validate_decision({"intent": intent})["intent"], intent)

    async def test_llm_prompt_includes_conversation_history(self):
        captured = {}

        class CapturingLLM:
            async def decide(self, prompt, context, trace_id=""):
                captured["prompt"] = prompt
                captured["context"] = context
                return {"intent": "reply", "message": "ok", "risk_level": "low"}

        self.core.config["llm_enabled"] = True
        self.core.llm = CapturingLLM()
        event = CognitiveEvent.create(
            "qq",
            "message",
            "qq:1",
            "agent",
            {"text": "继续"},
            "qq:1",
            metadata={"conversation_history": [{"role": "user", "content": "上一句"}, {"role": "assistant", "content": "上一答"}]},
        )
        await self.core.process_event(event)
        self.assertIn("Recent conversation", captured["prompt"])
        self.assertIn("上一句", captured["prompt"])
        self.assertIn("上一答", captured["prompt"])

    async def test_tool_call_gets_followup_reply(self):
        calls = []

        class ToolLLM:
            async def decide(self, prompt, context, trace_id=""):
                event = context.get("event", {})
                if event.get("type") == "action_result":
                    calls.append("followup")
                    return {"intent": "reply", "message": "已保存", "risk_level": "low"}
                calls.append("tool")
                return {
                    "intent": "tool_call",
                    "message": "",
                    "tool": "secret.store",
                    "arguments": {"name": "deepseek_api_key", "value": "sk-test"},
                    "risk_level": "low",
                }

        self.core.config["llm_enabled"] = True
        self.core.config["available_tools"] = [{"name": "secret.store"}]
        self.core.llm = ToolLLM()

        outbox = ActionOutbox(Path(self.tmp.name) / "followup.db")
        dispatcher = ActionDispatcher(outbox)
        sent = []

        async def send_message(action):
            sent.append(action["payload"]["text"])
            return {"sent": True}

        async def call_mcp(action):
            return {"success": True, "result": {"response": "stored"}}

        dispatcher.register("send_message", send_message)
        dispatcher.register("call_mcp", call_mcp)
        router = EventRouter(self.core, dispatcher)
        event = router.build("qq", "message", "qq:9", "agent", source_event_id="followup-1", content={"text": "记住密钥"})
        result = await router.route(event)
        self.assertEqual(calls, ["tool", "followup"])
        self.assertEqual(sent, ["已保存"])
        self.assertEqual(result["dispatched"][-1]["status"], "succeeded")

    async def test_official_actor_id_survives_shared_session_identity(self):
        # Official QQ uses the same memory/session key for a shared bot, but
        # the delivery target must stay the real user_openid supplied by the
        # gateway metadata.
        self.core.config["llm_enabled"] = True

        class TargetLLM:
            async def decide(self, prompt, context, trace_id=""):
                return {
                    "intent": "reply",
                    "message": "ok",
                    "tool": "",
                    "arguments": {},
                    "risk_level": "low",
                    "reason": "test",
                    "goal_id": "",
                }

        self.core.llm = TargetLLM()
        event = CognitiveEvent.create(
            "qq",
            "message",
            "qq:shared-identity",
            "agent",
            {"text": "hi"},
            "qq:private:shared-identity",
            metadata={"platform": "official", "actor_id": "qq:real-openid"},
        )
        result = await self.core.process_event(event)
        self.assertEqual(result["actions"][0]["target"], "qq:real-openid")


async def _record(target, action):
    target.append(action)
    return {"accepted": True, "action_id": action["action_id"]}


if __name__ == "__main__": unittest.main()
