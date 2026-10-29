"""Serialized stdio adapter for the zero-dependency Node MMTR library.

The host application retains its own dependencies. MMTR itself only needs Node.
No shell, network listener or third-party Python package is used by this adapter.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from typing import Any


class MmtrMemoryAdapter:
    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self.root = Path(__file__).resolve().parents[2]
        settings = config.get("mmtr", {})
        self.node = str(os.environ.get("NODE_BIN") or settings.get("node", "node"))
        self.path = Path(settings.get("database", "data/memory/mmtr.db"))
        if not self.path.is_absolute():
            self.path = self.root / self.path
        self.timeout = float(settings.get("timeout", 10))
        self.budget = int(settings.get("context_budget", 1200))
        self.gap = int(settings.get("session_gap_seconds", 1800)) * 1000
        self._process = None
        self._responses = None
        self._lock = threading.RLock()
        self._sequence = 0
        self.degraded = False
        self.failure_reason = ""

    def _start(self):
        if self._process is not None and self._process.poll() is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "MMTR_SESSION_GAP_MS": str(self.gap)}
        self._process = subprocess.Popen(
            [self.node, str(self.root / "mmtr" / "bridge.mjs"), str(self.path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        responses = queue.Queue()
        self._responses = responses
        process = self._process

        def read_responses():
            try:
                for line in process.stdout:
                    responses.put(line)
            finally:
                responses.put(None)

        threading.Thread(target=read_responses, name="mmtr-stdout", daemon=True).start()

    def call(self, method: str, *args):
        with self._lock:
            try:
                self._start()
                self._sequence += 1
                request_id = self._sequence
                payload = json.dumps({"id": request_id, "method": method, "args": args}, ensure_ascii=False)
                if len(payload.encode("utf-8")) > 1_000_000:
                    raise ValueError("MMTR request exceeds 1MB")
                self._process.stdin.write(payload + "\n")
                self._process.stdin.flush()
                raw = self._responses.get(timeout=self.timeout)
                if raw is None:
                    raise RuntimeError("MMTR bridge exited; Node >=22.13 with node:sqlite is required")
                response = json.loads(raw)
                if response.get("id") != request_id:
                    raise RuntimeError("MMTR bridge response mismatch")
                if not response.get("ok"):
                    raise ValueError(response.get("error", "MMTR request failed"))
                self.degraded, self.failure_reason = False, ""
                return response.get("result")
            except Exception as exc:
                self.degraded, self.failure_reason = True, str(exc)
                self._stop()
                raise

    def initialize(self):
        if not self.health_check():
            raise RuntimeError(f"MMTR unavailable: {self.failure_reason}")

    def health_check(self) -> bool:
        try:
            return bool(self.call("health"))
        except Exception:
            return False

    health = health_check

    def recall_for_turn(self, user_id, session_id, turn_id, query, context_budget=None) -> str:
        try:
            return self.call("recall", str(query), {
                "scope": str(user_id), "stream": str(session_id or "default"),
                "budget": min(self.budget, int(context_budget)) if context_budget is not None else self.budget, "style": "quiet",
            })
        except Exception:
            return ""

    def commit_turn(self, user_id, session_id, turn_id, user_text, assistant_text, source="unknown"):
        # Deterministic fallback extraction: compact text, no invented feelings or
        # automatic promises/core facts. Rich extraction can supply three channels
        # directly through remember(), including entities, themes and calendar anchors.
        fact = f"用户：{user_text}\n助手：{assistant_text}".strip()
        gist = str(user_text).strip()[:160]
        scope = str(user_id)
        stream = str(session_id or "default")
        external = None
        if turn_id is not None and str(turn_id) != "unknown":
            external = "turn:" + hashlib.sha256(f"{stream}\0{turn_id}".encode()).hexdigest()
        item = {"scope": scope, "stream": stream, "fact": fact, "gist": gist, "affect": ""}
        if external:
            item["externalId"] = external
        memory_id = self.call("remember", item)
        self.call("touchSession", stream, {"scope": scope, "summary": gist})
        return memory_id

    def remember(self, experience: dict[str, Any]):
        return self.call("remember", experience)

    def commit_tool_result(self, user_id, session_id, turn_id, tool_name, tool_arguments, tool_result):
        if not isinstance(tool_result, dict):
            return None
        # Tool payloads can contain credentials or giant blobs. Persist only an
        # explicitly supplied, structured memory observation.
        observation = tool_result.get("memory_observation")
        if not isinstance(observation, dict):
            return None
        return self.remember({**observation, "scope": str(user_id), "stream": str(session_id or "default")})

    def finalize_session(self, user_id, session_id):
        return self.call("endSession", str(session_id or "default"), {"scope": str(user_id)})

    def _stop(self):
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=2)
        finally:
            if process.stdout:
                process.stdout.close()

    def close(self):
        # Process shutdown is not a semantic session boundary: an immediate
        # restart must resume the same session until the inactivity gap elapses.
        with self._lock:
            self._stop()
