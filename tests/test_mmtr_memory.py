"""Real Python -> Node -> SQLite integration; no external services required."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mmtr_adapter_under_test", ROOT / "core/memory/mmtr_memory.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
MmtrMemoryAdapter = module.MmtrMemoryAdapter


@unittest.skipUnless(shutil.which("node"), "Node >=22.13 is required")
class MmtrAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {"mmtr": {"database": str(Path(self.tmp.name) / "mmtr.db"), "context_budget": 600}}
        self.adapter = MmtrMemoryAdapter(self.config)
        self.adapter.initialize()

    def tearDown(self):
        self.adapter.close()
        self.tmp.cleanup()

    def test_turn_recall_deduplication_and_reopen(self):
        first = self.adapter.commit_turn("owner", "qq", 1, "一起包饺子", "好呀")
        self.assertEqual(first, self.adapter.commit_turn("owner", "qq", 1, "一起包饺子", "好呀"))
        self.assertIn("包饺子", self.adapter.recall_for_turn("owner", "qq", 2, "包饺子"))
        self.assertEqual(self.adapter.recall_for_turn("other", "qq", 2, "包饺子"), "")
        self.adapter.close()
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir()], ["mmtr.db"])
        self.adapter = MmtrMemoryAdapter(self.config)
        self.assertIsNotNone(self.adapter.call("getMemory", first, {"scope": "owner"}))

    def test_structured_emotions_promise_and_whole_core(self):
        self.adapter.call("setCore", "name", "他的名字是小雨", {"scope": "owner"})
        self.adapter.remember({"scope": "owner", "fact": "一起包饺子", "gist": "包饺子", "affect": "安心", "entities": ["小雨"]})
        promise = self.adapter.call("promise", {"scope": "owner", "text": "今年生日一起过", "due": "2099-01-01", "entity": "小雨"})
        text = self.adapter.recall_for_turn("owner", "qq", 1, "包饺子")
        self.assertIn("他的名字是小雨", text)
        self.assertIn("今年生日一起过", text)
        self.assertTrue(self.adapter.call("keep", promise, {"scope": "owner"}))
        self.assertFalse(self.adapter.call("keep", promise, {"scope": "owner"}))

    def test_threads_share_one_serialized_bridge(self):
        def commit(i):
            return self.adapter.commit_turn("owner", "qq", i, f"一起包饺子{i}", "好呀")
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(commit, range(24)))
        self.assertEqual(len(set(ids)), 24)
        self.assertTrue(self.adapter.health_check())

    def test_sleep_and_restart_preserve_pending_relationship(self):
        first = self.adapter.call("touchSession", "qq", {"scope": "owner", "relationshipDelta": {"warmth": 0.1}, "summary": "谈到做饭", "openLoops": ["下次问什么馅"]})
        self.adapter.close()
        self.adapter = MmtrMemoryAdapter(self.config)
        self.assertEqual(first, self.adapter.call("touchSession", "qq", {"scope": "owner"}))
        self.assertEqual(self.adapter.call("relationship", "user", {"scope": "owner"})["warmth"], 0.5)
        result = self.adapter.finalize_session("owner", "qq")
        self.assertIn("下次问什么馅", result["summary"])
        self.assertGreater(self.adapter.call("relationship", "user", {"scope": "owner"})["warmth"], 0.5)
        self.assertIsNone(self.adapter.finalize_session("owner", "qq"))

    def test_error_recovers_and_missing_runtime_is_explicit(self):
        with self.assertRaises(ValueError):
            self.adapter.call("notAllowed")
        self.assertTrue(self.adapter.degraded)
        self.assertTrue(self.adapter.health_check())
        bad = MmtrMemoryAdapter({"mmtr": {"node": "missing-mmtr-node-runtime", "database": self.config["mmtr"]["database"]}})
        with self.assertRaisesRegex(RuntimeError, "MMTR unavailable"):
            bad.initialize()
        self.assertEqual(bad.recall_for_turn("owner", "qq", 1, "x"), "")
        bad.close()

    def test_tools_need_an_explicit_observation(self):
        self.assertIsNone(self.adapter.commit_tool_result("owner", "qq", 1, "tool", {}, {"secret": "never-store"}))
        memory_id = self.adapter.commit_tool_result("owner", "qq", 1, "tool", {}, {
            "memory_observation": {"scope": "untrusted", "gist": "订好了看展的票", "affect": "期待"},
        })
        self.assertIsNotNone(self.adapter.call("getMemory", memory_id, {"scope": "owner"}))
        self.assertIsNone(self.adapter.call("getMemory", memory_id, {"scope": "untrusted"}))


@unittest.skipUnless(shutil.which("node"), "Node >=22.13 is required")
class MmtrManagerTests(unittest.TestCase):
    def test_manager_and_cognitive_port_use_quiet_context(self):
        from core.memory.manager import MemoryManager
        from cognitive_core.memory import LegacyMemoryPort
        with tempfile.TemporaryDirectory() as directory, patch("core.memory.manager.setup_logging"):
            manager = MemoryManager({
                "memory_backend": "mmtr", "tencent_memory_enabled": True,
                "owner_id": "owner", "selected_module": {"Memory": "local"},
                "Memory": {"local": {"database": str(Path(directory) / "legacy.db")}},
                "mmtr": {"database": str(Path(directory) / "mmtr.db")},
            })
            try:
                self.assertTrue(manager.using_mmtr)
                self.assertFalse(manager.using_tencent)
                manager.observe_text("我的名字是小雨", "qq", "s")
                manager.mmtr_backend.call("setCore", "name", "他的名字是小雨", {"scope": "owner"})
                manager.record_turn("一起包饺子", "好呀", "qq", "s", turn_id=1)
                port = LegacyMemoryPort(manager)
                results = asyncio.run(port.search("包饺子", session_id="s", context_budget=300))
                self.assertEqual(len(results), 1)
                self.assertIn("他的名字是小雨", results[0].text)
                self.assertIn("包饺子", results[0].text)
                self.assertLessEqual(len(results[0].text), 300)
                self.assertTrue(asyncio.run(port.health()))
                manager.mmtr_backend.budget = 1
                self.assertEqual(asyncio.run(port.search("包饺子", session_id="s")), [])
                self.assertTrue(port.degraded)
                manager.mmtr_backend.budget = 1200
                self.assertIn("他的名字是小雨", asyncio.run(port.search("包饺子", session_id="s"))[0].text)
                self.assertFalse(port.degraded)
                self.assertTrue(asyncio.run(port.store("一起去看展", {"session_id": "s", "event_id": "e7"})))
                with manager._connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_messages").fetchone()[0], 0)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
