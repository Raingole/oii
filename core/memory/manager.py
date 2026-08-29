"""Unified single-user memory manager.

The project currently uses SQLite, so this module adds the requested
structured layers and a small local vector index without introducing a new
database service.  The vector index is only a candidate retriever; structured
records and precedence rules remain authoritative.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.config_loader import get_project_dir
from config.logger import setup_logging

from .vault import SecretVault
from .identity import IdentityResolver
from .tencent_memory import TencentMemoryAdapter

TAG = __name__


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _parse(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value if value is not None else default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default if default is not None else value


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


class MemoryManager:
    """Owner-scoped structured memory shared by ESP and QQ adapters."""

    def __init__(self, config: dict):
        self.config = config or {}
        configured_backend = os.getenv("MEMORY_BACKEND", self.config.get("memory_backend", "legacy"))
        memory_enabled = os.getenv("TENCENT_MEMORY_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
        self.backend_name = ("tencent" if memory_enabled else str(configured_backend)).strip().lower()
        selected = self.config.get("selected_module", {}).get("Memory", "")
        memory_config = self.config.get("Memory", {}).get(selected, {})
        memory_config = memory_config if isinstance(memory_config, dict) else {}
        self.owner_id = str(
            self.config.get("owner_id")
            or memory_config.get("owner_id")
            or "owner"
        )
        configured = memory_config.get("database", "data/memory/memory.db")
        self.database_path = Path(configured)
        if not self.database_path.is_absolute():
            self.database_path = Path(get_project_dir()) / self.database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.vector_dimensions = 96
        self._lock = threading.RLock()
        self.logger = setup_logging(config)
        self.identity_resolver = IdentityResolver(self.config)
        self.tencent_backend = None
        self._last_tencent_context = {}
        if self.backend_name == "tencent":
            self.tencent_backend = TencentMemoryAdapter(self.config)
            self.tencent_backend.initialize()
        self.vault = SecretVault(self.database_path.parent / ".vault.key")
        self._initialize_database()
        self.logger.bind(tag=TAG).info(
            f"记忆后端已启用: backend={self.backend_name}, owner={self.owner_id}, database={self.database_path}"
        )

    @property
    def using_tencent(self) -> bool:
        return self.tencent_backend is not None

    def _connect(self):
        db = sqlite3.connect(str(self.database_path), timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 30000")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def _initialize_database(self):
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, channel TEXT NOT NULL,
                    session_id TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_messages_owner
                    ON memory_messages(user_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS memory_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, subject TEXT NOT NULL,
                    predicate TEXT NOT NULL, object TEXT NOT NULL,
                    confidence REAL NOT NULL, source_type TEXT NOT NULL,
                    source_channel TEXT, source_session TEXT,
                    status TEXT NOT NULL DEFAULT 'active', supersedes INTEGER,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_facts_owner
                    ON memory_facts(user_id, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS memory_defaults (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, domain TEXT NOT NULL,
                    scope TEXT NOT NULL, key TEXT NOT NULL,
                    value_json TEXT NOT NULL, unit TEXT, conditions_json TEXT,
                    priority INTEGER NOT NULL DEFAULT 0, confidence REAL NOT NULL,
                    source_type TEXT NOT NULL, source_channel TEXT,
                    source_session TEXT, status TEXT NOT NULL DEFAULT 'active',
                    supersedes INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_defaults_owner
                    ON memory_defaults(user_id, domain, scope, key, status);
                CREATE TABLE IF NOT EXISTS memory_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, domain TEXT NOT NULL,
                    context_json TEXT NOT NULL, target TEXT NOT NULL,
                    score REAL NOT NULL, confidence REAL NOT NULL,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    positive_evidence INTEGER NOT NULL DEFAULT 0,
                    negative_evidence INTEGER NOT NULL DEFAULT 0,
                    source_channel TEXT, source_session TEXT,
                    status TEXT NOT NULL DEFAULT 'active', updated_at TEXT NOT NULL,
                    UNIQUE(user_id, domain, context_json, target)
                );
                CREATE TABLE IF NOT EXISTS memory_constraints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, domain TEXT NOT NULL,
                    target TEXT NOT NULL, rule TEXT NOT NULL,
                    scope_json TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 100,
                    valid_from TEXT, valid_until TEXT, source_type TEXT NOT NULL,
                    source_channel TEXT, source_session TEXT,
                    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, summary TEXT NOT NULL,
                    structured_data_json TEXT, source_channel TEXT,
                    source_session TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, entity_type TEXT NOT NULL,
                    name TEXT NOT NULL, aliases_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    UNIQUE(user_id, entity_type, name)
                );
                CREATE TABLE IF NOT EXISTS memory_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, source_entity_id INTEGER NOT NULL,
                    predicate TEXT NOT NULL, target_entity_id INTEGER NOT NULL,
                    weight REAL NOT NULL, confidence REAL NOT NULL,
                    source_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    UNIQUE(user_id, source_entity_id, predicate, target_entity_id)
                );
                CREATE TABLE IF NOT EXISTS memory_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, artifact_type TEXT NOT NULL,
                    data_json TEXT NOT NULL, source_channel TEXT,
                    source_session TEXT, created_at TEXT NOT NULL, expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memory_artifacts_owner
                    ON memory_artifacts(user_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS memory_vectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, memory_type TEXT NOT NULL,
                    ref_table TEXT NOT NULL, ref_id INTEGER NOT NULL,
                    content TEXT NOT NULL, embedding_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_vectors_owner
                    ON memory_vectors(user_id, memory_type, created_at DESC);
                CREATE TABLE IF NOT EXISTS memory_secrets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, name TEXT NOT NULL,
                    ciphertext TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    UNIQUE(user_id, name)
                );
                CREATE TABLE IF NOT EXISTS memory_identities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, channel TEXT NOT NULL,
                    external_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(channel, external_id)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_identities_owner
                    ON memory_identities(user_id, channel, external_id);
                """
            )

    def resolve_owner(self, channel: str = "", external_id: str = "") -> str:
        """Single-user identity resolver; external IDs are audit metadata only."""
        if self.using_tencent:
            return self.identity_resolver.resolve(channel, external_id).user_id
        if channel and external_id:
            now = _now()
            with self._lock, self._connect() as db:
                db.execute(
                    "INSERT INTO memory_identities(user_id,channel,external_id,created_at,updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(channel,external_id) DO UPDATE SET user_id=excluded.user_id,updated_at=excluded.updated_at",
                    (self.owner_id, _text(channel), _text(external_id), now, now),
                )
        return self.owner_id

    def _tokens(self, text: str) -> list[str]:
        return re.findall(r"[\u4e00-\u9fff]{1,}|[A-Za-z0-9_]{2,}", _text(text).lower())

    def _embedding(self, text: str) -> list[float]:
        vector = [0.0] * self.vector_dimensions
        for token in self._tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.vector_dimensions
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(item * item for item in vector)) or 1.0
        return [round(item / norm, 6) for item in vector]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right))

    def _index(self, db, memory_type: str, table: str, ref_id: int, content: str):
        if not content:
            return
        db.execute(
            "INSERT INTO memory_vectors(user_id,memory_type,ref_table,ref_id,content,embedding_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (self.owner_id, memory_type, table, ref_id, content, _json(self._embedding(content)), _now()),
        )

    def _legacy_domain(self, query: str) -> str:
        text = _text(query)
        if any(word in text for word in ("空调", "温度", "暖风", "制冷", "风速")):
            return "device"
        if any(word in text for word in ("吃", "餐厅", "午餐", "晚餐", "炒饭", "辣")):
            return "food"
        if any(word in text for word in ("QQ", "消息", "发给", "发送")):
            return "communication"
        return "general"

    def _time_period(self) -> str:
        hour = datetime.now().hour
        if hour >= 22 or hour < 6:
            return "night"
        if 11 <= hour < 14:
            return "lunch"
        if 17 <= hour < 21:
            return "dinner"
        return "day"

    def _conditions_match(self, conditions: dict, context: dict | None = None) -> bool:
        if not conditions:
            return True
        period = conditions.get("time_period")
        context = context or {}
        return not period or period == context.get("time_period", self._time_period())

    def _domain(self, query: str) -> str:
        text = _text(query)
        if any(word in text for word in ("\u7a7a\u8c03", "\u6e29\u5ea6", "\u6696\u98ce", "\u5236\u51b7", "\u98ce\u901f")):
            return "device"
        if any(word in text for word in ("\u5403", "\u9910\u5385", "\u5348\u9910", "\u665a\u9910", "\u7092\u996d", "\u83dc")):
            return "food"
        if any(word in text for word in ("QQ", "\u6d88\u606f", "\u53d1\u7ed9", "\u53d1\u5230")):
            return "communication"
        return "general"

    def retrieve(self, user_id: str, channel: str, session_id: str, query: str) -> dict[str, Any]:
        """Retrieve structured candidates and vector-assisted candidates."""
        query = _text(query)
        if not query:
            return {}
        domain = self._domain(query)
        terms = self._tokens(query)[:10]
        query_context = {"time_period": "night"} if any(word in query for word in ("\u665a\u4e0a", "\u591c\u95f4")) else {"time_period": self._time_period()}
        with self._lock, self._connect() as db:
            facts = db.execute(
                "SELECT * FROM memory_facts WHERE user_id=? AND status='active' ORDER BY confidence DESC,updated_at DESC LIMIT 50",
                (self.owner_id,),
            ).fetchall()
            defaults = db.execute(
                "SELECT * FROM memory_defaults WHERE user_id=? AND status='active' AND (domain=? OR domain='general') ORDER BY CASE WHEN conditions_json='{}' THEN 0 ELSE 1 END DESC, priority DESC,updated_at DESC",
                (self.owner_id, domain),
            ).fetchall()
            constraints = db.execute(
                "SELECT * FROM memory_constraints WHERE user_id=? AND status='active' AND (valid_until IS NULL OR valid_until>?) ORDER BY priority DESC,created_at DESC LIMIT 30",
                (self.owner_id, _now()),
            ).fetchall()
            preferences = db.execute(
                "SELECT * FROM memory_preferences WHERE user_id=? AND status='active' AND (domain=? OR domain='general') ORDER BY score DESC,confidence DESC LIMIT 30",
                (self.owner_id, domain),
            ).fetchall()
            relations = db.execute(
                "SELECT r.*, se.name AS source_name, te.name AS target_name FROM memory_relations r "
                "JOIN memory_entities se ON se.id=r.source_entity_id JOIN memory_entities te ON te.id=r.target_entity_id "
                "WHERE r.user_id=? AND r.status='active' ORDER BY r.confidence DESC LIMIT 50",
                (self.owner_id,),
            ).fetchall()
            artifacts = db.execute(
                "SELECT * FROM memory_artifacts WHERE user_id=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 5",
                (self.owner_id, _now()),
            ).fetchall()
            vectors = db.execute(
                "SELECT * FROM memory_vectors WHERE user_id=? ORDER BY created_at DESC LIMIT 500",
                (self.owner_id,),
            ).fetchall()

        def relevant(row) -> bool:
            haystack = " ".join(str(row[key]) for key in row.keys()).lower()
            return not terms or any(term.lower() in haystack for term in terms)

        result: dict[str, Any] = {"facts": [], "defaults": [], "constraints": [], "preferences": [], "relations": [], "artifacts": [], "vector": []}
        result["facts"] = [dict(row) for row in facts if relevant(row)][:8]
        result["defaults"] = [
            {**dict(row), "value": _parse(row["value_json"]), "conditions": _parse(row["conditions_json"], {})}
            for row in defaults
            if self._conditions_match(_parse(row["conditions_json"], {}), query_context)
            and (domain == "device" or relevant(row))
        ][:8]
        result["constraints"] = [dict(row) for row in constraints if relevant(row) or domain in {"device", "food"}]
        result["preferences"] = [
            {**dict(row), "context": _parse(row["context_json"], {})}
            for row in preferences
            if relevant(row) or domain == "food"
        ][:8]
        result["relations"] = [dict(row) for row in relations if relevant(row)][:12]
        if any(word in query.lower() for word in ("api", "token", "password", "secret", "\u5bc6\u7801", "\u5bc6\u94a5")):
            result["secret_names"] = self.secret_list_names()
        if any(word in query for word in ("刚才", "这个", "那家", "地址", "链接", "上次")):
            result["artifacts"] = [{**dict(row), "data": _parse(row["data_json"], {})} for row in artifacts]

        if not result["artifacts"] and any(word in query for word in ("\u521a\u624d", "\u8fd9\u4e2a", "\u90a3\u5bb6", "\u5730\u5740", "\u94fe\u63a5", "\u4e0a\u6b21")):
            result["artifacts"] = [{**dict(row), "data": _parse(row["data_json"], {})} for row in artifacts]

        query_vector = self._embedding(query)
        ranked = sorted(
            ((self._cosine(query_vector, _parse(row["embedding_json"], [])), row) for row in vectors),
            key=lambda item: item[0], reverse=True,
        )
        result["vector"] = [
            {"memory_type": row["memory_type"], "content": row["content"], "similarity": round(score, 3)}
            for score, row in ranked[:6] if score > 0.12
        ]
        return {key: value for key, value in result.items() if value}

    def _legacy_retrieve_prompt(self, user_id: str, channel: str, session_id: str, query: str) -> str:
        data = self.retrieve(user_id, channel, session_id, query)
        if not data:
            return ""
        lines = ["以下是与当前请求相关的用户长期记忆，仅作事实和规则参考；当前明确指令优先："]
        for fact in data.get("facts", []):
            lines.append(f"- Fact({fact['source_type']}): {fact['subject']} → {fact['predicate']} → {fact['object']}")
        for item in data.get("defaults", []):
            lines.append(f"- Default(priority={item['priority']}): {item['domain']}/{item['scope']}/{item['key']} = {item['value']} {item.get('unit') or ''}")
        for item in data.get("constraints", []):
            lines.append(f"- Constraint(priority={item['priority']}): {item['domain']} {item['rule']} {item['target']}")
        for item in data.get("preferences", []):
            lines.append(f"- Preference(score={item['score']}, confidence={item['confidence']}): {item['domain']} → {item['target']}")
        for item in data.get("relations", []):
            lines.append(f"- Relation({item['source_type']}): {item['source_name']} -> {item['predicate']} -> {item['target_name']}")
        for item in data.get("artifacts", []):
            lines.append(f"- Recent Artifact({item['artifact_type']}): {_json(item['data'])}")
        for item in data.get("vector", []):
            lines.append(f"- Related memory(similarity={item['similarity']}): {item['content']}")
        if data.get("secret_names"):
            lines.append(f"- Secret names (values are never included): {', '.join(data['secret_names'])}")
        return "\n".join(lines)

    def retrieve_prompt(self, user_id: str, channel: str, session_id: str, query: str, turn_id=None) -> str:
        if self.using_tencent:
            context = self.tencent_backend.recall_for_turn(
                user_id or self.owner_id, session_id, turn_id or "unknown", query
            )
            self._last_tencent_context[(user_id or self.owner_id, _text(query))] = context
            return context
        data = self.retrieve(user_id, channel, session_id, query)
        if not data:
            return ""
        lines = ["Relevant owner memory. Use it as reference only; the current explicit request has priority."]
        for fact in data.get("facts", []):
            lines.append(f"- Fact({fact['source_type']}): {fact['subject']} -> {fact['predicate']} -> {fact['object']}")
        for item in data.get("defaults", []):
            lines.append(f"- Default(priority={item['priority']}): {item['domain']}/{item['scope']}/{item['key']} = {item['value']} {item.get('unit') or ''}")
        for item in data.get("constraints", []):
            lines.append(f"- Constraint(priority={item['priority']}): {item['domain']} {item['rule']} {item['target']}")
        for item in data.get("preferences", []):
            lines.append(f"- Preference(score={item['score']}, confidence={item['confidence']}): {item['domain']} -> {item['target']}")
        for item in data.get("relations", []):
            lines.append(f"- Relation({item['source_type']}): {item['source_name']} -> {item['predicate']} -> {item['target_name']}")
        for item in data.get("artifacts", []):
            lines.append(f"- Recent Artifact({item['artifact_type']}): {_json(item['data'])}")
        for item in data.get("vector", []):
            lines.append(f"- Related memory(similarity={item['similarity']}): {item['content']}")
        if data.get("secret_names"):
            lines.append(f"- Secret names (values are never included): {', '.join(data['secret_names'])}")
        return "\n".join(lines)

    def remember_fact(self, subject: str, predicate: str, obj: str, channel: str, session_id: str, source_type: str = "explicit", confidence: float = 1.0):
        now = _now()
        with self._lock, self._connect() as db:
            old = db.execute(
                "SELECT id FROM memory_facts WHERE user_id=? AND subject=? AND predicate=? AND status='active' ORDER BY id DESC LIMIT 1",
                (self.owner_id, subject, predicate),
            ).fetchone()
            if old:
                db.execute("UPDATE memory_facts SET status='superseded',updated_at=? WHERE id=?", (now, old[0]))
            cursor = db.execute(
                "INSERT INTO memory_facts(user_id,subject,predicate,object,confidence,source_type,source_channel,source_session,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.owner_id, subject, predicate, obj, confidence, source_type, channel, session_id, "active", now, now),
            )
            self._index(db, "fact", "memory_facts", cursor.lastrowid, f"{subject} {predicate} {obj}")

    def resolve_defaults(self, user_id: str, domain: str, scope: str, context: dict | None = None) -> dict[str, Any]:
        context = context or {"time_period": self._time_period()}
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM memory_defaults WHERE user_id=? AND status='active' AND domain=? AND (scope=? OR scope='*') ORDER BY CASE WHEN conditions_json='{}' THEN 0 ELSE 1 END DESC, priority DESC,updated_at DESC",
                (self.owner_id, domain, scope),
            ).fetchall()
        resolved = {}
        for row in rows:
            conditions = _parse(row["conditions_json"], {})
            if self._conditions_match(conditions, context) and row["key"] not in resolved:
                resolved[row["key"]] = _parse(row["value_json"])
        return resolved

    def upsert_default(self, domain: str, scope: str, key: str, value: Any, unit: str, conditions: dict, channel: str, session_id: str, priority: int = 50):
        now = _now()
        condition_json = _json(conditions or {})
        with self._lock, self._connect() as db:
            old = db.execute(
                "SELECT id FROM memory_defaults WHERE user_id=? AND domain=? AND scope=? AND key=? AND conditions_json=? AND status='active' LIMIT 1",
                (self.owner_id, domain, scope, key, condition_json),
            ).fetchone()
            old_id = old[0] if old else None
            if old_id:
                db.execute("UPDATE memory_defaults SET status='superseded',updated_at=? WHERE id=?", (now, old_id))
            cursor = db.execute(
                "INSERT INTO memory_defaults(user_id,domain,scope,key,value_json,unit,conditions_json,priority,confidence,source_type,source_channel,source_session,status,supersedes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.owner_id, domain, scope, key, _json(value), unit, condition_json, priority, 1.0, "explicit", channel, session_id, "active", old_id, now, now),
            )
            self._index(db, "default", "memory_defaults", cursor.lastrowid, f"{domain} {scope} {key} {value} {_json(conditions)}")

    def update_preference(self, domain: str, target: str, context: dict, channel: str, session_id: str, positive: bool = True, explicit: bool = False):
        target = _text(target)
        if not target:
            return
        context_json = _json(context or {})
        delta = 0.25 if explicit else 0.12
        if not positive:
            delta = -0.25 if explicit else -0.12
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM memory_preferences WHERE user_id=? AND domain=? AND context_json=? AND target=?",
                (self.owner_id, domain, context_json, target),
            ).fetchone()
            if row:
                score = max(0.0, min(1.0, float(row["score"]) + delta))
                db.execute(
                    "UPDATE memory_preferences SET score=?,confidence=?,evidence_count=?,positive_evidence=?,negative_evidence=?,source_channel=?,source_session=?,updated_at=? WHERE id=?",
                    (score, min(1.0, float(row["confidence"]) + (0.04 if explicit else 0.01)), row["evidence_count"] + 1, row["positive_evidence"] + int(positive), row["negative_evidence"] + int(not positive), channel, session_id, _now(), row["id"]),
                )
                return
            score = max(0.0, min(1.0, 0.5 + delta))
            cursor = db.execute(
                "INSERT INTO memory_preferences(user_id,domain,context_json,target,score,confidence,evidence_count,positive_evidence,negative_evidence,source_channel,source_session,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.owner_id, domain, context_json, target, score, 1.0 if explicit else 0.65, 1, int(positive), int(not positive), channel, session_id, "active", _now()),
            )
            self._index(db, "preference", "memory_preferences", cursor.lastrowid, f"{domain} {target} {_json(context)}")

    def add_constraint(self, domain: str, target: str, rule: str, channel: str, session_id: str, valid_until: str | None = None, priority: int = 100):
        now = _now()
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "INSERT INTO memory_constraints(user_id,domain,target,rule,scope_json,priority,valid_from,valid_until,source_type,source_channel,source_session,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.owner_id, domain, target, rule, "{}", priority, now, valid_until, "explicit", channel, session_id, "active", now),
            )
            self._index(db, "constraint", "memory_constraints", cursor.lastrowid, f"{domain} {rule} {target}")

    def resolve_constraints(self, domain: str = "", context: dict | None = None) -> list[dict[str, Any]]:
        """Return active non-expired constraints, highest priority first."""
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM memory_constraints WHERE user_id=? AND status='active' AND (valid_until IS NULL OR valid_until>?) "
                "AND (domain=? OR ?='') ORDER BY priority DESC,created_at DESC",
                (self.owner_id, _now(), domain, domain),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_preferences(self, domain: str = "", context: dict | None = None) -> list[dict[str, Any]]:
        """Return owner preferences; callers apply constraints before using them."""
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM memory_preferences WHERE user_id=? AND status='active' AND (domain=? OR ?='') "
                "ORDER BY score DESC,confidence DESC",
                (self.owner_id, domain, domain),
            ).fetchall()
        return [{**dict(row), "context": _parse(row["context_json"], {})} for row in rows]

    def upsert_entity(self, entity_type: str, name: str, aliases: list[str] | None = None, metadata: dict | None = None) -> int:
        """Create or update an owner-scoped entity used by relation lookup."""
        entity_type, name = _text(entity_type), _text(name)
        if not entity_type or not name:
            raise ValueError("entity_type and name are required")
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT id FROM memory_entities WHERE user_id=? AND entity_type=? AND name=?",
                (self.owner_id, entity_type, name),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE memory_entities SET aliases_json=?,metadata_json=?,status='active' WHERE id=?",
                    (_json(aliases or []), _json(metadata or {}), row[0]),
                )
                return int(row[0])
            cursor = db.execute(
                "INSERT INTO memory_entities(user_id,entity_type,name,aliases_json,metadata_json,status) VALUES (?,?,?,?,?,?)",
                (self.owner_id, entity_type, name, _json(aliases or []), _json(metadata or {}), "active"),
            )
            return int(cursor.lastrowid)

    def link_relation(self, source_name: str, predicate: str, target_name: str, entity_type: str = "concept", source_type: str = "explicit", confidence: float = 1.0, weight: float = 1.0) -> bool:
        source_id = self.upsert_entity(entity_type, source_name)
        target_id = self.upsert_entity(entity_type, target_name)
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO memory_relations(user_id,source_entity_id,predicate,target_entity_id,weight,confidence,source_type,status) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id,source_entity_id,predicate,target_entity_id) DO UPDATE SET weight=excluded.weight,confidence=excluded.confidence,source_type=excluded.source_type,status='active'",
                (self.owner_id, source_id, _text(predicate), target_id, weight, confidence, source_type, "active"),
            )
        return True

    def _legacy_apply_defaults(self, tool_name: str, arguments: dict | None, query: str = "") -> dict:
        """Fill only missing well-known device arguments from resolved Defaults."""
        if isinstance(arguments, str):
            result = _parse(arguments, {})
            result = result if isinstance(result, dict) else {}
        else:
            result = dict(arguments or {})
        combined = f"{tool_name} {query}"
        if "空调" not in combined and not any(token in tool_name.lower() for token in ("air_conditioner", "set_temperature")):
            return result
        context = {"time_period": "night"} if any(word in query for word in ("\u665a\u4e0a", "\u591c\u95f4")) else None
        defaults = self.resolve_defaults(self.owner_id, "device", "air_conditioner", context)
        if "temperature" in defaults and "temperature" not in result and "temp" not in result:
            result["temperature"] = defaults["temperature"]
        return result

    def record_artifact(self, artifact_type: str, data: dict, channel: str, session_id: str, expires_at: str | None = None):
        now = _now()
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "INSERT INTO memory_artifacts(user_id,artifact_type,data_json,source_channel,source_session,created_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                (self.owner_id, artifact_type, _json(data), channel, session_id, now, expires_at),
            )
            self._index(db, "artifact", "memory_artifacts", cursor.lastrowid, f"{artifact_type} {_json(data)}")

    def get_recent_artifact(self, artifact_type: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM memory_artifacts WHERE user_id=? AND (expires_at IS NULL OR expires_at>?)"
        args: list[Any] = [self.owner_id, _now()]
        if artifact_type:
            sql += " AND artifact_type=?"
            args.append(artifact_type)
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self._lock, self._connect() as db:
            row = db.execute(sql, args).fetchone()
        return {**dict(row), "data": _parse(row["data_json"], {})} if row else None

    def record_tool_result(self, tool_name: str, value: Any, channel: str, session_id: str, turn_id=None, arguments=None):
        if self.using_tencent:
            self.tencent_backend.submit_tool_result(
                self.resolve_owner(channel, session_id), session_id, turn_id or "unknown", tool_name,
                arguments or {}, value,
            )
            return
        value = _parse(value, value)
        if not isinstance(value, dict):
            return
        recommendation = value.get("recommendation")
        if isinstance(recommendation, dict):
            self.record_artifact("place", recommendation, channel, session_id)
        elif value.get("artifact_type") and isinstance(value.get("data"), dict):
            self.record_artifact(str(value["artifact_type"]), value["data"], channel, session_id)

    def record_episode(self, user_text: str, assistant_text: str, channel: str, session_id: str, tool_result: Any = None):
        summary = f"用户：{_text(user_text)[:500]}\n助手：{_text(assistant_text)[:800]}"
        structured = _parse(tool_result, None)
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "INSERT INTO memory_episodes(user_id,summary,structured_data_json,source_channel,source_session,created_at) VALUES (?,?,?,?,?,?)",
                (self.owner_id, summary, _json(structured) if structured is not None else None, channel, session_id, _now()),
            )
            self._index(db, "episode", "memory_episodes", cursor.lastrowid, summary)

    def record_turn(self, user_text: str, assistant_text: str, channel: str, session_id: str, tool_result: Any = None, turn_id=None):
        if self.using_tencent:
            self.tencent_backend.submit_commit_turn(
                self.resolve_owner(channel, session_id), session_id,
                turn_id or "unknown", _text(user_text), _text(assistant_text),
                channel or "unknown",
            )
            return
        now = _now()
        with self._lock, self._connect() as db:
            db.executemany(
                "INSERT INTO memory_messages(user_id,channel,session_id,role,content,created_at) VALUES (?,?,?,?,?,?)",
                [(self.owner_id, channel, session_id, "user", _text(user_text), now), (self.owner_id, channel, session_id, "assistant", _text(assistant_text), now)],
            )
        self.record_episode(user_text, assistant_text, channel, session_id, tool_result)

    def _legacy_observe_text(self, text: str, channel: str, session_id: str):
        """Fast rule layer for explicit writes; ordinary text is not promoted to facts."""
        text = _text(text).rstrip("。！？.!?")
        if not text:
            return
        secret_match = re.search(r"(?:记住|保存)(?:我的)?(.{1,40}?)(?:密码|token|Token|API(?:\s*Key)?|密钥|SSH)\s*(?:是|为|:|：)\s*(\S+)$", text, re.I)
        if secret_match:
            name = f"{secret_match.group(1).strip()}secret" if secret_match.group(1).strip() else "default_secret"
            self.secret_store(name, secret_match.group(2), {"source_channel": channel, "source_session": session_id})
            return
        number = re.search(r"(\d+(?:\.\d+)?)\s*(?:度|℃|摄氏度)", text)
        if "默认" in text and number and "空调" in text:
            conditions = {"time_period": "night"} if "晚上" in text or "夜间" in text else {}
            self.upsert_default("device", "air_conditioner", "temperature", float(number.group(1)), "celsius", conditions, channel, session_id)
            return
        if any(word in text for word in ("不要再", "不要推荐", "最近不要", "这周不要", "不吃")):
            target = re.sub(r".*?(?:不要再|不要推荐|最近不要|这周不要|不吃)", "", text).strip()
            if target:
                self.add_constraint("food", target, "avoid", channel, session_id, (_now() if "这周" not in text else (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")))
            return
        if "以后" in text and ("优先" in text or "喜欢" in text) and any(word in text for word in ("午餐", "中午", "吃")):
            target = re.sub(r".*?(?:优先|喜欢)", "", text).strip()
            if target:
                target = re.sub(r"(?:给我)?(?:推荐|吃)?", "", target).strip()
                self.update_preference("food", target, {"context": "lunch"}, channel, session_id, True, True)
            return
        if "喜欢" in text and "不喜欢" not in text:
            target = text.split("喜欢", 1)[1].strip()
            if target:
                self.update_preference(self._domain(text), target, {}, channel, session_id, True, True)
            return
        fact = re.search(r"我的(.{1,30}?)(?:是|叫做|叫)(.+)$", text)
        if fact:
            self.remember_fact("owner", fact.group(1).strip(), fact.group(2).strip(), channel, session_id)

    def apply_defaults(self, tool_name: str, arguments: dict | None, query: str = "") -> dict:
        if isinstance(arguments, str):
            result = _parse(arguments, {})
            result = result if isinstance(result, dict) else {}
        else:
            result = dict(arguments or {})
        if self.using_tencent:
            context = self._last_tencent_context.get((self.resolve_owner("", ""), _text(query)), "")
            if "temperature" not in result and "temp" not in result:
                match = re.search(r"(?:空调|默认)[^\n]{0,40}?([12]\d)\s*(?:度|℃)", context)
                if match and 16 <= int(match.group(1)) <= 30:
                    result["temperature"] = int(match.group(1))
            return result
        air = "\u7a7a\u8c03"
        lowered = tool_name.lower()
        if air not in f"{tool_name} {query}" and not any(token in lowered for token in ("air_conditioner", "set_temperature")):
            return result
        context = {"time_period": "night"} if any(word in query for word in ("\u665a\u4e0a", "\u591c\u95f4")) else None
        defaults = self.resolve_defaults(self.owner_id, "device", "air_conditioner", context)
        if "temperature" in defaults and "temperature" not in result and "temp" not in result:
            result["temperature"] = defaults["temperature"]
        return result

    def observe_text(self, text: str, channel: str, session_id: str):
        """Promote only explicit, high-signal statements into long-term memory."""
        if self.using_tencent:
            # TencentDB extracts long-term atoms from committed conversations;
            # do not duplicate the old local fact/profile pipeline.
            return
        text = _text(text).rstrip("\u3002\uff01\uff1f!?\u3002")
        if not text:
            return
        secret_match = re.search(
            r"(?:\u8bb0\u4f4f|\u4fdd\u5b58)(?:\u6211\u7684)?(.{0,40}?)(?:\u5bc6\u7801|token|api(?:\s*key)?|\u5bc6\u94a5|ssh)\s*(?:\u662f|\u4e3a|:|\uff1a)\s*(\S+)$",
            text,
            re.I,
        )
        if secret_match:
            label = secret_match.group(1).strip()
            self.secret_store(label or "default_secret", secret_match.group(2), {"source_channel": channel, "source_session": session_id})
            return
        number = re.search(r"(\d+(?:\.\d+)?)\s*(?:\u5ea6|\u2103|\u6444\u6c0f\u5ea6)", text)
        if "\u9ed8\u8ba4" in text and number and "\u7a7a\u8c03" in text:
            conditions = {"time_period": "night"} if any(word in text for word in ("\u665a\u4e0a", "\u591c\u95f4")) else {}
            self.upsert_default("device", "air_conditioner", "temperature", float(number.group(1)), "celsius", conditions, channel, session_id)
            return
        if any(word in text for word in ("\u4e0d\u8981\u518d", "\u4e0d\u8981\u63a8\u8350", "\u6700\u8fd1\u4e0d\u8981", "\u8fd9\u5468\u4e0d\u8981", "\u4e0d\u5403")):
            target = re.sub(r".*?(?:\u4e0d\u8981\u518d|\u4e0d\u8981\u63a8\u8350|\u6700\u8fd1\u4e0d\u8981|\u8fd9\u5468\u4e0d\u8981|\u4e0d\u5403)", "", text).strip()
            if target:
                until = None if "\u8fd9\u5468" not in text else (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
                self.add_constraint("food", target, "avoid", channel, session_id, until)
            return
        if "\u4ee5\u540e" in text and any(word in text for word in ("\u4f18\u5148", "\u559c\u6b22")) and any(word in text for word in ("\u5348\u9910", "\u4e2d\u5348", "\u5403")):
            target = re.sub(r".*?(?:\u4f18\u5148|\u559c\u6b22)", "", text).strip()
            target = re.sub(r"(?:\u7ed9\u6211)?(?:\u63a8\u8350|\u5403)", "", target).strip()
            if target:
                self.update_preference("food", target, {"context": "lunch"}, channel, session_id, True, True)
            return
        if "\u559c\u6b22" in text and "\u4e0d\u559c\u6b22" not in text:
            target = text.split("\u559c\u6b22", 1)[1].strip()
            if target:
                self.update_preference(self._domain(text), target, {}, channel, session_id, True, True)
            return
        fact = re.search(r"\u6211\u7684(.{1,30}?)(?:\u662f|\u53eb\u505a|\u53eb)(.+)$", text)
        if fact:
            self.remember_fact("owner", fact.group(1).strip(), fact.group(2).strip(), channel, session_id)

    def secret_store(self, name: str, value: str, metadata: dict | None = None):
        name = _text(name)
        if not name or not _text(value):
            return False
        now = _now()
        encrypted = self.vault.encrypt(_text(value))
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO memory_secrets(user_id,name,ciphertext,metadata_json,created_at,updated_at,status) VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET ciphertext=excluded.ciphertext,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at,status='active'",
                (self.owner_id, name, encrypted, _json(metadata or {}), now, now, "active"),
            )
        return True

    def secret_exists(self, name: str) -> bool:
        with self._lock, self._connect() as db:
            return db.execute("SELECT 1 FROM memory_secrets WHERE user_id=? AND name=? AND status='active'", (self.owner_id, _text(name))).fetchone() is not None

    def secret_list_names(self) -> list[str]:
        with self._lock, self._connect() as db:
            return [row[0] for row in db.execute("SELECT name FROM memory_secrets WHERE user_id=? AND status='active' ORDER BY name", (self.owner_id,)).fetchall()]

    def secret_read_for_delivery(self, name: str) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT ciphertext FROM memory_secrets WHERE user_id=? AND name=? AND status='active'", (self.owner_id, _text(name))).fetchone()
        if not row:
            return None
        return self.vault.decrypt(row[0])

    def secret_delete(self, name: str) -> bool:
        with self._lock, self._connect() as db:
            cursor = db.execute("UPDATE memory_secrets SET status='deleted',updated_at=? WHERE user_id=? AND name=? AND status='active'", (_now(), self.owner_id, _text(name)))
        return cursor.rowcount > 0

    def close(self):
        """Flush and release the selected remote backend, if any."""
        if self.tencent_backend is not None:
            self.tencent_backend.close()
