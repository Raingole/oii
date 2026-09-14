from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
import asyncio
import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


@dataclass
class MemoryItem:
    text: str
    score: float = 0.0
    created_at: str = ""
    metadata: dict[str, Any] | None = None


class MemoryAdapter(Protocol):
    async def search(self, query: str, top_k: int = 8, context_budget: int = 4000, **kwargs: Any) -> list[MemoryItem]: ...
    async def store(self, text: str, metadata: dict[str, Any] | None = None) -> bool: ...
    async def update(self, memory_id: str, text: str, metadata: dict[str, Any] | None = None) -> bool: ...
    async def delete(self, memory_id: str) -> bool: ...
    async def health(self) -> bool: ...

MemoryPort = MemoryAdapter


class InMemoryAdapter:
    def __init__(self) -> None:
        self.items: list[MemoryItem] = []

    async def search(self, query: str, top_k: int = 8, context_budget: int = 4000, **kwargs: Any) -> list[MemoryItem]:
        user_id, session_id, channel = kwargs.get("user_id"), kwargs.get("session_id"), kwargs.get("channel")
        candidates = [x for x in self.items if (not user_id or (x.metadata or {}).get("user_id") == user_id)
                      and (not session_id or (x.metadata or {}).get("session_id") == session_id)
                      and (not channel or (x.metadata or {}).get("channel") == channel)]
        terms = set(query.lower().split())
        ranked = sorted(candidates, key=lambda x: (len(terms & set(x.text.lower().split())) + x.score), reverse=True)
        out, used = [], 0
        for item in ranked[:top_k]:
            if used + len(item.text) > context_budget:
                break
            out.append(item); used += len(item.text)
        return out

    async def store(self, text: str, metadata: dict[str, Any] | None = None) -> bool:
        meta = dict(metadata or {}); meta.setdefault("memory_id", f"local:{len(self.items)+1}")
        self.items.append(MemoryItem(text=text, score=float(meta.get("importance", 0)), metadata=meta))
        return True

    async def update(self, memory_id: str, text: str, metadata: dict[str, Any] | None = None) -> bool:
        for item in self.items:
            if (item.metadata or {}).get("memory_id") == memory_id: item.text=text; item.metadata.update(metadata or {}); return True
        return False
    async def delete(self, memory_id: str) -> bool:
        before=len(self.items); self.items[:]=[x for x in self.items if (x.metadata or {}).get("memory_id") != memory_id]; return len(self.items)<before
    async def health(self) -> bool: return True


class TencentMemoryAdapter:
    """Thin adapter over MemoryCore; Cognitive Core never imports its private code."""
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 3.0, service_id: str = "default", team_id: str = "personal", agent_id: str = "central-controller", user_id: str = "gu", api_version: str = "v2") -> None:
        self.base_url, self.api_key, self.timeout = base_url.rstrip("/"), api_key, timeout
        self.service_id, self.team_id, self.agent_id, self.user_id, self.api_version = service_id, team_id, agent_id, user_id, api_version

    def _headers(self, user_id: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "x-tdai-service-id": self.service_id, "x-tdai-team-id": self.team_id, "x-tdai-agent-id": self.agent_id, "x-tdai-user-id": user_id or self.user_id}
        if self.api_key: headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, user_id: str | None = None) -> Any:
        def call() -> Any:
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            headers = self._headers(user_id)
            request = Request(self.base_url + path, data=data, headers=headers, method=method)
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        last=None
        for attempt in range(3):
            try: return await asyncio.to_thread(call)
            except (URLError, HTTPError, TimeoutError, OSError, ValueError) as exc:
                last=exc
                if attempt<2: await asyncio.sleep(0.05*(attempt+1))
        raise last

    async def search(self, query: str, top_k: int = 8, context_budget: int = 4000, **kwargs: Any) -> list[MemoryItem]:
        scope = {"team_id": kwargs.pop("team_id", self.team_id), "agent_id": kwargs.pop("agent_id", self.agent_id), "user_id": kwargs.pop("user_id", self.user_id)}
        data = await self._request("POST", f"/{self.api_version}/atomic/search", {"query": query, "limit": top_k, **scope, **kwargs}, user_id=scope["user_id"])
        data = data.get("data", data) if isinstance(data, dict) else {}
        items = data.get("items", []) if isinstance(data, dict) else []
        result, used = [], 0
        for item in items:
            text = str(item.get("content") or item.get("memory") or item.get("text") or "").strip()
            if text and used + len(text) <= context_budget:
                result.append(MemoryItem(text=text, score=float(item.get("score", 0)), metadata=item)); used += len(text)
        return result

    async def store(self, text: str, metadata: dict[str, Any] | None = None) -> bool:
        meta = metadata or {}
        scope = {"team_id": meta.get("team_id", self.team_id), "agent_id": meta.get("agent_id", self.agent_id), "user_id": meta.get("user_id", self.user_id)}
        await self._request("POST", f"/{self.api_version}/conversation/add", {"session_id": meta.get("session_id", "cognitive-core"), "messages": [{"role": "assistant", "content": text}], **scope, **meta}, user_id=scope["user_id"])
        return True

    async def update(self, memory_id: str, text: str, metadata: dict[str, Any] | None = None) -> bool:
        meta=metadata or {}; scope={"team_id":meta.get("team_id",self.team_id),"agent_id":meta.get("agent_id",self.agent_id),"user_id":meta.get("user_id",self.user_id)}
        await self._request("POST", f"/{self.api_version}/atomic/update", {"id": memory_id, "content": text, **scope, **meta}, user_id=scope["user_id"]); return True

    async def delete(self, memory_id: str) -> bool:
        await self._request("POST", f"/{self.api_version}/atomic/delete", {"message_ids": [memory_id],"team_id":self.team_id,"agent_id":self.agent_id,"user_id":self.user_id}, user_id=self.user_id); return True

    async def health(self) -> bool:
        try:
            await self._request("GET", "/health")
            return True
        except (URLError, HTTPError, TimeoutError, OSError, ValueError):
            return False


class LegacyMemoryPort:
    """Async port over the existing MemoryManager; preserves one memory authority."""
    def __init__(self, manager: Any, user_id: str = "owner"):
        self.manager, self.user_id = manager, user_id; self.degraded=False; self.degraded_since=None; self.failure_reason=""; self.fallback_count=0; self.recovered_at=None
    async def search(self, query: str, top_k: int = 8, context_budget: int = 4000, **kwargs: Any) -> list[MemoryItem]:
        text = await asyncio.to_thread(self.manager.retrieve_prompt, kwargs.get("user_id", self.user_id), kwargs.get("channel", "controller"), kwargs.get("session_id", "cognitive"), query, kwargs.get("turn_id"))
        return [MemoryItem(line[2:].strip()) for line in text.splitlines() if line.startswith("-") and len(line) <= context_budget][:top_k]
    async def store(self, text: str, metadata: dict[str, Any] | None = None) -> bool:
        meta=metadata or {}; channel=meta.get("channel", meta.get("source", "cognitive")); session=meta.get("session_id", "cognitive")
        # record_turn is the existing single write path that persists locally and
        # invokes the configured Tencent Memory commit hook when enabled.
        try:
            await asyncio.to_thread(self.manager.record_turn, text, "", channel, session, meta.get("tool_result"), meta.get("turn_id")); self.degraded=False; return True
        except Exception as exc: self._failed(exc); return False
    async def update(self, memory_id: str, text: str, metadata: dict[str, Any] | None = None) -> bool:
        backend=getattr(self.manager,"tencent_backend",None)
        if backend is None: return False
        try: return bool(await self._backend_call(backend.update,memory_id,text,metadata))
        except Exception as exc:self._failed(exc); return False
    async def delete(self, memory_id: str) -> bool:
        backend=getattr(self.manager,"tencent_backend",None)
        if backend is None:return False
        try:return bool(await self._backend_call(backend.delete,memory_id))
        except Exception as exc:self._failed(exc); return False
    def _failed(self, exc):
        import time
        if not self.degraded:self.degraded_since=time.time()
        self.degraded=True; self.failure_reason=str(exc)[:300]; self.fallback_count+=1
    async def _backend_call(self, method, *args, **kwargs):
        value = await asyncio.to_thread(method, *args, **kwargs)
        if hasattr(value, "__await__"): value = await value
        return value
    async def health(self) -> bool:
        backend=getattr(self.manager,"tencent_backend",None)
        try:
            ok=bool(await self._backend_call(backend.health)) if backend is not None else True
            if ok and self.degraded:self.recovered_at=__import__("time").time()
            self.degraded=not ok; return ok
        except Exception as exc:self._failed(exc); return False


class FallbackMemoryPort:
    def __init__(self, primary: MemoryAdapter, fallback: MemoryAdapter | None = None): self.primary, self.fallback = primary, fallback or InMemoryAdapter(); self.degraded = False
    async def search(self, query: str, top_k: int = 8, context_budget: int = 4000, **kwargs: Any) -> list[MemoryItem]:
        try: self.degraded = False; return await self.primary.search(query, top_k, context_budget, **kwargs)
        except Exception: self.degraded = True; return await self.fallback.search(query, top_k, context_budget, **kwargs)
    async def store(self, text: str, metadata: dict[str, Any] | None = None) -> bool:
        try: self.degraded = False; return await self.primary.store(text, metadata)
        except Exception: self.degraded = True; return await self.fallback.store(text, metadata)
    async def update(self, memory_id: str, text: str, metadata: dict[str, Any] | None = None) -> bool:
        try: return await self.primary.update(memory_id, text, metadata)
        except Exception: self.degraded = True; return await self.fallback.update(memory_id, text, metadata)
    async def delete(self, memory_id: str) -> bool:
        try: return await self.primary.delete(memory_id)
        except Exception: self.degraded = True; return await self.fallback.delete(memory_id)
    async def health(self) -> bool:
        try: return await self.primary.health()
        except Exception: return False


def should_store(event: Any, appraisal: dict[str, float]) -> str:
    text = str(getattr(event, "content", {}).get("text", "")).strip().lower()
    if text in {"嗯", "好的", "知道了", "ok", "收到"} or len(text) < 3:
        return "discard"
    score = max(appraisal.get("self_relevance", 0), appraisal.get("goal_relevance", 0), appraisal.get("relationship_relevance", 0), appraisal.get("novelty", 0))
    if score >= 0.75 or appraisal.get("emotional_salience", 0) >= 0.7:
        return "episodic"
    return "working"
