from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import SelfModel


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA busy_timeout=30000")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS self_state (agent_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS goals (goal_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS actions (action_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS reflections (reflection_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS timeline_events (timeline_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS event_inbox (event_id TEXT PRIMARY KEY, status TEXT NOT NULL, received_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS conversation_turns (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                user_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_turns_session
                ON conversation_turns(session_id, created_at);
            """)
            if db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0: db.execute("INSERT INTO schema_version(version) VALUES(1)")
            db.commit()
        finally:
            db.close()

    def load_self(self, agent_id: str, seed: dict[str, Any]) -> SelfModel:
        db = sqlite3.connect(self.path)
        try: row = db.execute("SELECT payload FROM self_state WHERE agent_id=?", (agent_id,)).fetchone()
        finally: db.close()
        if row: return SelfModel.from_dict(json.loads(row[0]))
        traits = {k: {"base": v, "current": v, "confidence": 1.0, "evidence": []} for k, v in seed.get("temperament", {}).items()}
        model = SelfModel(identity=seed.get("identity", {}), personality={k: __import__("cognitive_core.models", fromlist=["PersonalityTrait"]).PersonalityTrait(**v) for k, v in traits.items()}, values=seed.get("values", {}))
        self.save_self(agent_id, model); return model

    def claim_event(self, event_id: str) -> bool:
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA busy_timeout=30000")
            row = db.execute("INSERT OR IGNORE INTO event_inbox(event_id,status) VALUES(?, 'received')", (event_id,))
            db.commit(); return row.rowcount == 1
        finally: db.close()

    def save_self(self, agent_id: str, model: SelfModel) -> None:
        db = sqlite3.connect(self.path)
        try:
            db.execute("INSERT INTO self_state(agent_id,payload) VALUES(?,?) ON CONFLICT(agent_id) DO UPDATE SET payload=excluded.payload,updated_at=CURRENT_TIMESTAMP", (agent_id, json.dumps(model.to_dict(), ensure_ascii=False))); db.commit()
        finally: db.close()

    def put(self, table: str, key: str, payload: dict[str, Any]) -> None:
        key_columns = {"events": "event_id", "goals": "goal_id", "actions": "action_id", "reflections": "reflection_id", "timeline_events": "timeline_id"}
        column = key_columns[table]
        db = sqlite3.connect(self.path)
        try:
            db.execute(f"INSERT OR REPLACE INTO {table}({column},payload) VALUES(?,?)", (key, json.dumps(payload, ensure_ascii=False))); db.commit()
        finally: db.close()

    def has_event(self, event_id: str) -> bool:
        db = sqlite3.connect(self.path)
        try: return db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone() is not None
        finally: db.close()

    def save_conversation_turn(
        self,
        event_id: str,
        session_id: str,
        user_id: str,
        channel: str,
        user_text: str,
        assistant_text: str = "",
    ) -> None:
        if not session_id or not user_text:
            return
        db = sqlite3.connect(self.path)
        try:
            db.execute(
                """INSERT INTO conversation_turns
                   (event_id,session_id,user_id,channel,user_text,assistant_text)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(event_id) DO UPDATE SET
                   assistant_text=excluded.assistant_text""",
                (event_id, session_id, user_id, channel, user_text, assistant_text or ""),
            )
            db.commit()
        finally:
            db.close()

    def recent_conversation(self, session_id: str, limit: int = 12) -> list[dict[str, str]]:
        if not session_id:
            return []
        db = sqlite3.connect(self.path)
        try:
            rows = db.execute(
                """SELECT user_text,assistant_text FROM conversation_turns
                   WHERE session_id=? ORDER BY created_at DESC LIMIT ?""",
                (session_id, max(1, int(limit))),
            ).fetchall()
        finally:
            db.close()
        history: list[dict[str, str]] = []
        for user_text, assistant_text in reversed(rows):
            history.append({"role": "user", "content": user_text})
            if assistant_text:
                history.append({"role": "assistant", "content": assistant_text})
        return history
