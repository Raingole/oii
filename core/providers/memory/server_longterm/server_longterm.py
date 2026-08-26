"""Persistent server-side conversation memory.

This provider deliberately uses SQLite from the Python standard library. It
keeps the complete conversation transcript and a small explicit-facts table,
so a cloud VM/container only needs a persistent ``data`` volume.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..base import MemoryProviderBase, logger
from config.config_loader import get_project_dir

TAG = __name__


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _content(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and "content" in payload:
            return str(payload["content"]).strip()
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return text


class MemoryProvider(MemoryProviderBase):
    def __init__(self, config, summary_memory=None):
        super().__init__(config)
        self.config = config or {}
        configured = self.config.get("database", "data/memory/memory.db")
        self.database_path = Path(configured)
        if not self.database_path.is_absolute():
            self.database_path = Path(get_project_dir()) / self.database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_recalled_messages = max(1, int(self.config.get("max_recalled_messages", 12)))
        self._lock = threading.RLock()
        self._initialize_database()
        logger.bind(tag=TAG).info("服务器长期记忆已启用：%s", self.database_path)

    def init_memory(self, role_id, llm, **kwargs):
        super().init_memory(role_id, llm, **kwargs)

    def _connect(self):
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize_database(self):
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT PRIMARY KEY,
                    role_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    message_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, message_index)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_role_time
                    ON messages(role_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_session_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(role_id, content)
                );
                CREATE INDEX IF NOT EXISTS idx_facts_role_time
                    ON facts(role_id, created_at DESC);
                """
            )

    def _role_id(self) -> str:
        return str(self.role_id or "default")

    def _save(self, msgs, session_id=None):
        role_id = self._role_id()
        session_id = str(session_id or "unknown-session")
        now = _now()
        rows = []
        facts = []
        for index, message in enumerate(msgs or []):
            role = str(getattr(message, "role", "") or "")
            content = _content(getattr(message, "content", ""))
            if not content or role == "system":
                continue
            rows.append((session_id, role_id, index, role, content, now))
            if role == "user" and re.search(r"(?:记住|记一下|请记住|以后都|我的(?:名字|称呼|位置|喜好))", content):
                facts.append(content)

        if not rows:
            return 0
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO conversations(session_id, role_id, started_at, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET updated_at=excluded.updated_at",
                (session_id, role_id, now, now),
            )
            db.executemany(
                "INSERT OR IGNORE INTO messages(session_id, role_id, message_index, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            db.executemany(
                "INSERT OR IGNORE INTO facts(role_id, content, source_session_id, created_at) VALUES (?, ?, ?, ?)",
                [(role_id, fact, session_id, now) for fact in facts],
            )
            return len(rows)

    def _query(self, query: str) -> str:
        role_id = self._role_id()
        query = _content(query)
        with self._lock, self._connect() as db:
            fact_rows = db.execute(
                "SELECT content, created_at FROM facts WHERE role_id=? ORDER BY created_at DESC LIMIT 20",
                (role_id,),
            ).fetchall()
            recent_rows = db.execute(
                "SELECT role, content, created_at FROM messages WHERE role_id=? ORDER BY id DESC LIMIT ?",
                (role_id, self.max_recalled_messages),
            ).fetchall()

            terms = [term for term in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", query)][:8]
            relevant = []
            if terms:
                where = " OR ".join("content LIKE ?" for _ in terms)
                relevant = db.execute(
                    f"SELECT role, content, created_at FROM messages WHERE role_id=? AND ({where}) ORDER BY id DESC LIMIT ?",
                    (role_id, *(f"%{term}%" for term in terms), self.max_recalled_messages),
                ).fetchall()

        seen = set()
        lines = ["长期记忆（服务器持久化）："]
        for row in fact_rows:
            key = ("fact", row["content"])
            if key not in seen:
                seen.add(key)
                lines.append(f"- 重要事实：{row['content']}")
        for row in list(relevant) + list(recent_rows):
            key = (row["role"], row["content"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {row['role']}: {row['content']}")
        return "\n".join(lines) if len(lines) > 1 else ""

    async def save_memory(self, msgs, session_id=None):
        try:
            count = await asyncio.to_thread(self._save, msgs, session_id)
            logger.bind(tag=TAG).debug("长期记忆保存完成：%s 条消息，session=%s", count, session_id)
        except Exception as exc:
            logger.bind(tag=TAG).error("长期记忆保存失败：%s", exc)
        return None

    async def query_memory(self, query: str) -> str:
        try:
            return await asyncio.to_thread(self._query, query)
        except Exception as exc:
            logger.bind(tag=TAG).error("长期记忆查询失败：%s", exc)
            return ""
