import asyncio
from functools import partial
from contextlib import closing
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer
from cognitive_core.contracts import CognitiveEvent
from cognitive_core.memory import LegacyMemoryPort
from cognitive_core.observer_api import register_observer
from cognitive_core.runtime import CognitiveCore
from config.memory_policy import apply_memory_policy, selected_memory_backend
from config.observer_access import observer_token
from core.memory.manager import MemoryManager
from ui_server import QuietStaticHandler


ROOT = Path(__file__).resolve().parents[1]


class MemoryPolicyTests(unittest.TestCase):
    def test_mmtr_wins_over_old_tencent_and_share_flags(self):
        settings = {"memory_backend": "tencent", "tencent_memory_enabled": True,
                    "cognitive_core": {"share_legacy_memory": False}}
        self.assertEqual(selected_memory_backend(settings), "mmtr")
        apply_memory_policy(settings)
        self.assertFalse(settings["tencent_memory_enabled"])
        self.assertTrue(settings["cognitive_core"]["share_legacy_memory"])

    def test_preference_must_be_explicitly_disabled_to_restore_tencent(self):
        self.assertEqual(selected_memory_backend({}), "mmtr")
        self.assertEqual(selected_memory_backend({"mmtr": {"preferred": False}, "memory_backend": "tencent"}), "tencent")

    def test_observer_key_persists_privately_without_entering_public_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {}
            first = observer_token(config, directory)
            self.assertEqual(first, observer_token(config, directory))
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(config, {})
            self.assertEqual(observer_token({"mmtr": {"web_token": "explicit"}}, directory), "explicit")


@unittest.skipUnless(shutil.which("node"), "Node >=22.13 required")
class MemoryObserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        with patch("core.memory.manager.setup_logging"):
            self.manager = MemoryManager({"memory_backend": "tencent", "tencent_memory_enabled": True,
                "owner_id": "owner", "selected_module": {"Memory": "local"},
                "Memory": {"local": {"database": str(folder / "legacy.db")}},
                "mmtr": {"database": str(folder / "mmtr.db")}})
        self.port = LegacyMemoryPort(self.manager)
        self.core = CognitiveCore(str(folder / "cognitive.db"), memory=self.port, config={"llm_enabled": True})
        self.core.create_goal("持续关注共同约定")
        self.prompt = ""
        parent = self
        class VisibleLLM:
            async def decide(self, prompt, context, trace_id=""):
                parent.prompt = prompt
                return {"intent": "reply", "message": "记得那次一起做饭。", "risk_level": "low", "reason": "回应用户的回忆请求"}
        self.core.llm = VisibleLLM()
        self.memory_id = self.manager.mmtr_backend.remember({"scope": "owner", "fact": "那天下雨了", "gist": "一起包饺子很合拍", "affect": "安心，亲切", "entities": ["小雨"], "themes": ["做饭"]})
        await self.core.process_event(CognitiveEvent.create("qq", "message", "qq:7", "agent", {"text": "你还记得我们一起包饺子的事吗？"}, session_id="qq:7", event_id="turn:1"))
        app = web.Application()
        register_observer(app, lambda: self.core, {"mmtr": {"web_token": "observer-test-key"}}, root=folder)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.headers = {"Authorization": "Bearer observer-test-key"}

    async def asyncTearDown(self):
        await self.client.close()
        self.manager.close()
        self.temp.cleanup()

    async def test_activity_matches_exact_recall_and_visible_decision(self):
        response = await self.client.get("/api/memory/activity", headers=self.headers)
        result = await response.json()
        self.assertEqual(result["backend"], "mmtr")
        selected = result["selected"]
        self.assertEqual(selected["event_id"], "turn:1")
        self.assertIn("一起包饺子很合拍", selected["context"])
        self.assertIn("一起包饺子很合拍", self.prompt)
        self.assertEqual(selected["memory_nodes"][0]["id"], self.memory_id)
        self.assertTrue(selected["memory_nodes"][0]["provided_to_model"])
        self.assertIn("回应用户的回忆请求", [node["text"] for node in selected["cognition_nodes"]])
        self.assertIn("持续关注共同约定", [node["text"] for node in selected["cognition_nodes"]])
        self.assertNotIn("persona_prompt", json.dumps(result))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_viewing_does_not_recall_reinforce_or_change_rank(self):
        before = self.manager.mmtr_backend.call("getMemory", self.memory_id, {"scope": "owner"})
        for _ in range(5):
            response = await self.client.get("/api/memory/activity", headers=self.headers)
            self.assertEqual(response.status, 200)
        after = self.manager.mmtr_backend.call("getMemory", self.memory_id, {"scope": "owner"})
        self.assertEqual(before["recalls"], 1)
        self.assertEqual(before["recalls"], after["recalls"])
        self.assertEqual(before["last_round"], after["last_round"])

    async def test_private_data_requires_correct_key(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = await self.client.get("/api/memory/activity", headers=headers)
            self.assertEqual(response.status, 401)
            self.assertNotIn("包饺子", await response.text())
        self.assertEqual((await self.client.get("/memory.html")).status, 200)

    async def test_session_selection_and_cross_session_event_are_checked(self):
        await self.core.process_event(CognitiveEvent.create("qq", "message", "qq:8", "agent", {"text": "今天聊一聊散步"}, session_id="qq:8", event_id="turn:2"))
        result = await (await self.client.get("/api/memory/activity?session_id=qq:7", headers=self.headers)).json()
        self.assertEqual(result["selected"]["event_id"], "turn:1")
        self.assertEqual(len(result["sessions"]), 2)
        response = await self.client.get("/api/memory/activity?session_id=qq:8&event_id=turn:1", headers=self.headers)
        self.assertEqual(response.status, 404)

    async def test_activity_survives_restart_and_retention_is_bounded(self):
        restored = CognitiveCore(str(self.core.store.path), memory=self.port)
        self.assertEqual(restored.store.memory_activity()["selected"]["event_id"], "turn:1")
        base = restored.store.memory_activity()["selected"]
        for index in range(130):
            restored.store.save_memory_activity({**base, "event_id": f"bounded:{index}"})
        with closing(sqlite3.connect(restored.store.path)) as db:
            with db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_activity").fetchone()[0], 120)
                db.execute("UPDATE memory_activity SET created_at=datetime('now','-8 days')")
        self.assertEqual(restored.store.memory_activity()["events"], [])

    async def test_observation_failure_does_not_break_a_conversation(self):
        with patch.object(self.core.store, "save_memory_activity", side_effect=OSError("disk unavailable")):
            result = await self.core.process_event(CognitiveEvent.create("qq", "message", "qq:7", "agent", {"text": "继续说说那天"}, session_id="qq:7", event_id="turn:next"))
        self.assertEqual(result["actions"][0]["type"], "send_message")

    async def test_static_ui_proxy_preserves_authorization_and_no_store(self):
        handler = partial(QuietStaticHandler, directory=str(ROOT / "ui"))
        with patch.object(QuietStaticHandler, "backend_port", self.client.server.port):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                async with ClientSession() as session:
                    base = f"http://127.0.0.1:{server.server_port}"
                    async with session.get(base + "/memory") as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("记忆观察台", await response.text())
                    async with session.get(base + "/api/memory/activity", headers=self.headers) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                    async with session.get(base + "/api/memory/activity") as response:
                        self.assertEqual(response.status, 401)
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
            finally:
                await asyncio.to_thread(server.shutdown)
                server.server_close()
                worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
