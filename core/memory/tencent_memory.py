"""TencentDB Agent Memory adapter using the official MemoryCore HTTP API.

The upstream Python SDK is intentionally not imported into the controller:
this keeps the business code dependent on this adapter contract and avoids
coupling it to SDK package layout. Endpoints and request shapes match the
official v2 API at the pinned upstream commit documented in deploy/memory-core.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from config.logger import setup_logging

from .base import MemoryService
from .models import MemoryIdentity

TAG = __name__


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, default=str)


class TencentMemoryAdapter(MemoryService):
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.logger = setup_logging(self.config)
        self.endpoint = str(self.config.get("tencent_memory_base_url", "http://127.0.0.1:8420")).rstrip("/")
        self.api_key = str(self.config.get("tencent_memory_api_key", ""))
        self.service_id = str(self.config.get("tencent_memory_service_id", "default"))
        self.timeout = float(self.config.get("tencent_memory_timeout", 3))
        self.recall_timeout = float(self.config.get("tencent_memory_recall_timeout", 2))
        self.max_results = int(self.config.get("tencent_memory_max_results", 5))
        self.identity = MemoryIdentity(
            str(self.config.get("tencent_memory_team_id", "personal")),
            str(self.config.get("tencent_memory_agent_id", "central-controller")),
            str(self.config.get("tencent_memory_user_id") or self.config.get("owner_id") or "yin2hao"),
        )
        self._client = requests.Session()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-commit")
        self._initialized = False
        self._available = False
        self._close_lock = threading.Lock()

    def _headers(self, user_id: str | None = None) -> dict[str, str]:
        headers = {"x-tdai-service-id": self.service_id, "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update({
            "x-tdai-team-id": self.identity.team_id,
            "x-tdai-agent-id": self.identity.agent_id,
            "x-tdai-user-id": user_id or self.identity.user_id,
        })
        return headers

    def _post(self, path: str, payload: dict[str, Any], timeout: float | None = None, user_id: str | None = None) -> dict[str, Any]:
        response = self._client.post(
            f"{self.endpoint}{path}", json=payload,
            headers=self._headers(user_id), timeout=timeout or self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("MemoryCore returned a non-object JSON response")
        if data.get("code") not in (None, 0):
            raise RuntimeError(f"MemoryCore code={data.get('code')}: {data.get('message', 'unknown error')}")
        result = data.get("data", data)
        if not isinstance(result, dict):
            raise ValueError("MemoryCore response data is not an object")
        return result

    def initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self.logger.bind(tag=TAG).info(
            f"[Memory] Initializing TencentDB Agent Memory endpoint={self.endpoint}"
        )
        self._available = self.health_check()
        if self._available:
            self.logger.bind(tag=TAG).info("[Memory] Health check passed")
            self.logger.bind(tag=TAG).info("[Memory] Memory system ready")
        else:
            self.logger.bind(tag=TAG).warning(
                f"[Memory] MemoryCore unavailable: endpoint={self.endpoint}; degraded mode enabled"
            )

    def health_check(self) -> bool:
        started = time.perf_counter()
        try:
            response = self._client.get(
                f"{self.endpoint}/health", headers=self._headers(),
                timeout=min(self.timeout, 3),
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            self.logger.bind(tag=TAG).warning(
                f"[Memory] health failed latency={int((time.perf_counter()-started)*1000)}ms: {exc}"
            )
            return False

    def _scope(self, user_id: str) -> dict[str, str]:
        return {
            "team_id": self.identity.team_id,
            "agent_id": self.identity.agent_id,
            "user_id": user_id or self.identity.user_id,
        }

    def recall_for_turn(self, user_id: str, session_id: str, turn_id: int | str, query: str) -> str:
        if not query.strip():
            return ""
        started = time.perf_counter()
        try:
            scope = self._scope(user_id)
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-recall") as pool:
                atomic_future = pool.submit(
                    self._post, "/v2/atomic/search",
                    {**scope, "query": query, "limit": self.max_results},
                    self.recall_timeout,
                )
                conversation_future = pool.submit(
                    self._post, "/v2/conversation/search",
                    {**scope, "query": query, "limit": self.max_results},
                    self.recall_timeout,
                )
                try:
                    atomic_data = atomic_future.result(timeout=self.recall_timeout)
                except Exception:
                    atomic_data = {}
                try:
                    conversation_data = conversation_future.result(timeout=self.recall_timeout)
                except Exception:
                    conversation_data = {}

            atomic_items = atomic_data.get("items", [])
            conversation_items = conversation_data.get("items", conversation_data.get("messages", []))
            if not isinstance(atomic_items, list):
                atomic_items = []
            if not isinstance(conversation_items, list):
                conversation_items = []
            lines = [
                "[Memory Context]",
                "以下内容来自跨渠道长期记忆，仅作参考；当前用户明确请求优先：",
            ]
            seen = set()
            for item in atomic_items[: self.max_results] + conversation_items[: self.max_results]:
                if isinstance(item, dict):
                    content = _as_text(item.get("content") or item.get("memory") or item.get("text"))
                else:
                    content = _as_text(item)
                if content and content not in seen:
                    seen.add(content)
                    lines.append(f"- {content[:500]}")
            context = "\n".join(lines) if len(lines) > 2 else ""
            self.logger.bind(tag=TAG).info(
                f"[Memory][user={user_id}][session={session_id}][turn={turn_id}] recall success count={len(seen)} latency={int((time.perf_counter()-started)*1000)}ms"
            )
            return context
        except Exception as exc:
            self.logger.bind(tag=TAG).warning(
                f"[Memory][user={user_id}][session={session_id}][turn={turn_id}] recall failed: {exc} latency={int((time.perf_counter()-started)*1000)}ms"
            )
            return ""

    def commit_turn(self, user_id: str, session_id: str, turn_id: int | str, user_text: str, assistant_text: str, source: str = "unknown") -> dict[str, Any] | None:
        started = time.perf_counter()
        self.logger.bind(tag=TAG).info(
            f"[Memory][user={user_id}][session={session_id}][turn={turn_id}] commit started"
        )
        try:
            result = self._post(
                "/v2/conversation/add",
                {**self._scope(user_id), "session_id": session_id, "messages": [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": assistant_text},
                ]},
            )
            self.logger.bind(tag=TAG).info(
                f"[Memory][user={user_id}][session={session_id}][turn={turn_id}] commit success latency={int((time.perf_counter()-started)*1000)}ms accepted={len(result.get('accepted_ids', []))}"
            )
            return result
        except Exception as exc:
            self.logger.bind(tag=TAG).error(
                f"[Memory][user={user_id}][session={session_id}][turn={turn_id}] commit failed: {exc} latency={int((time.perf_counter()-started)*1000)}ms"
            )
            return None

    def submit_commit_turn(self, *args: Any, **kwargs: Any) -> None:
        future = self._executor.submit(self.commit_turn, *args, **kwargs)
        future.add_done_callback(self._consume_future)

    def commit_tool_result(self, user_id: str, session_id: str, turn_id: int | str, tool_name: str, tool_arguments: Any, tool_result: Any) -> dict[str, Any] | None:
        started = time.perf_counter()
        try:
            result = self._post(
                "/v2/offload/ingest",
                {"session_id": session_id, "tool_pairs": [{
                    "tool_name": tool_name,
                    "tool_call_id": f"turn-{turn_id}-{tool_name}",
                    "params": tool_arguments if isinstance(tool_arguments, dict) else _as_text(tool_arguments),
                    "result": _as_text(tool_result),
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }], "prompt": _as_text(tool_result)[:1000]},
                user_id=user_id,
            )
            self.logger.bind(tag=TAG).info(
                f"[Memory][user={user_id}][session={session_id}][turn={turn_id}][tool={tool_name}] tool ingest success latency={int((time.perf_counter()-started)*1000)}ms"
            )
            return result
        except Exception as exc:
            self.logger.bind(tag=TAG).warning(
                f"[Memory][user={user_id}][session={session_id}][turn={turn_id}][tool={tool_name}] tool ingest failed: {exc}"
            )
            return None

    def submit_tool_result(self, *args: Any, **kwargs: Any) -> None:
        future = self._executor.submit(self.commit_tool_result, *args, **kwargs)
        future.add_done_callback(self._consume_future)

    def finalize_session(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        try:
            return self._post("/v2/offload/ingest", {"session_id": session_id, "tool_pairs": []})
        except Exception as exc:
            self.logger.bind(tag=TAG).debug(f"[Memory] finalize session skipped: {exc}")
            return None

    @staticmethod
    def _consume_future(future) -> None:
        try:
            future.result()
        except Exception:
            # commit methods already log failures; this prevents unhandled task output.
            pass

    def close(self) -> None:
        with self._close_lock:
            # Flush accepted background commits before process shutdown.
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._client.close()
