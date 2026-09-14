#!/usr/bin/env python3
"""Watch the persisted Cognitive Core self/state changes after chat traffic.

The script reads the same SQLite database used by CognitiveCore.  It does not
write to the database and does not call the LLM.  Run it while chatting to see
how identity, personality, emotion, goals, actions, timeline and reflections
change.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

TABLE_KEYS = {
    "events": "event_id",
    "goals": "goal_id",
    "actions": "action_id",
    "reflections": "reflection_id",
    "timeline_events": "timeline_id",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_db_path() -> Path:
    return Path(os.environ.get("COGNITIVE_DB_PATH") or (project_root() / "data" / "cognitive" / "cognitive.db"))


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"cognitive database not found: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def load_json(payload: Any) -> Any:
    if payload is None:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    try:
        return json.loads(str(payload))
    except (TypeError, ValueError):
        return str(payload)


def read_self(con: sqlite3.Connection, agent_id: str) -> tuple[dict[str, Any] | None, str]:
    try:
        row = con.execute(
            "SELECT payload, updated_at FROM self_state WHERE agent_id=?",
            (agent_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None, ""
    if row is None:
        return None, ""
    return load_json(row["payload"]), str(row["updated_at"] or "")


def read_table(con: sqlite3.Connection, table: str, key: str, limit: int) -> dict[str, str]:
    try:
        rows = con.execute(
            f"SELECT {key} AS item_key, payload AS item_payload "
            f"FROM {table} ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(row["item_key"]): str(row["item_payload"] or "") for row in rows}


def read_snapshot(con: sqlite3.Connection, agent_id: str, limit: int) -> dict[str, Any]:
    self_payload, self_updated_at = read_self(con, agent_id)
    return {
        "self_payload": self_payload,
        "self_updated_at": self_updated_at,
        "tables": {
            table: read_table(con, table, key, limit)
            for table, key in TABLE_KEYS.items()
        },
    }


def short(value: Any, width: int = 120) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= width else text[: width - 1] + ""

def fmt_value(value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def print_persona(self_data: dict[str, Any] | None, updated_at: str = "") -> None:
    if self_data is None:
        print("[cognitive_watch] self_state is empty for this agent_id")
        return
    identity = self_data.get("identity") or {}
    print("=" * 72)
    print(f"Self State updated_at: {updated_at or 'unknown'}")
    print("Identity:")
    print(f"  name        : {identity.get('name', '')}")
    print(f"  role        : {identity.get('role', '')}")
    if len(identity) > 2:
        print(f"  raw         : {json.dumps(identity, ensure_ascii=False)}")
    print("Personality:")
    personality = self_data.get("personality") or {}
    if not personality:
        print("  <empty>")
    for name, trait in personality.items():
        if isinstance(trait, dict):
            print(
                f"  {name:<12} current={fmt_value(trait.get('current'))} "
                f"base={fmt_value(trait.get('base'))} confidence={fmt_value(trait.get('confidence'))} "
                f"evidence={len(trait.get('evidence') or [])}"
            )
        else:
            print(f"  {name}: {trait}")
    print("Emotion:")
    emotion = self_data.get("emotion") or {}
    if emotion:
        print("  " + ", ".join(f"{k}={fmt_value(v)}" for k, v in emotion.items()))
    else:
        print("  <empty>")
    values = self_data.get("values") or {}
    if values:
        print("Values:")
        print("  " + ", ".join(f"{k}={fmt_value(v)}" for k, v in values.items()))
    goals = self_data.get("active_goals") or []
    print(f"Active goals: {len(goals)}")
    for goal in goals[:8]:
        print(
            f"  - {goal.get('title', '')} "
            f"status={goal.get('status', '')} priority={fmt_value(goal.get('priority', ''))}"
        )
    print("=" * 72)


def print_table_changes(name: str, old: dict[str, str], new: dict[str, str]) -> None:
    old_ids, new_ids = set(old), set(new)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    changed = sorted(
        item_id for item_id in (old_ids & new_ids) if old[item_id] != new[item_id]
    )
    if not (added or removed or changed):
        return
    print(f"\n--- {name} changes ---")
    for item_id in added:
        print(f"[+] {item_id}")
        try:
            print(json.dumps(json.loads(new[item_id]), ensure_ascii=False, indent=2))
        except (TypeError, ValueError):
            print(short(new[item_id], 500))
    for item_id in changed:
        print(f"[~] {item_id}")
        try:
            old_obj = json.loads(old[item_id])
            new_obj = json.loads(new[item_id])
            for line in difflib.unified_diff(
                json.dumps(old_obj, ensure_ascii=False, indent=2, sort_keys=True).splitlines(),
                json.dumps(new_obj, ensure_ascii=False, indent=2, sort_keys=True).splitlines(),
                fromfile="old",
                tofile="new",
                lineterm="",
            ):
                print(line)
        except (TypeError, ValueError):
            print(f"  old={short(old[item_id])}")
            print(f"  new={short(new[item_id])}")
    for item_id in removed:
        print(f"[-] {item_id}")


def print_snapshot(snapshot: dict[str, Any], limit: int) -> None:
    print_persona(snapshot["self_payload"], snapshot["self_updated_at"])
    print(f"Recent rows per table (limit={limit}):")
    for table, rows in snapshot["tables"].items():
        print(f"  {table:<16} {len(rows)} rows")
    print()


def print_delta(old: dict[str, Any], new: dict[str, Any]) -> None:
    print("\n" + "=" * 72)
    print(f"[cognitive_watch] change detected {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if old["self_updated_at"] != new["self_updated_at"] or old["self_payload"] != new["self_payload"]:
        old_text = json.dumps(old["self_payload"], ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        new_text = json.dumps(new["self_payload"], ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        print(f"Self updated_at: {old['self_updated_at']} -> {new['self_updated_at']}")
        for line in difflib.unified_diff(
            old_text,
            new_text,
            fromfile="self:before",
            tofile="self:after",
            lineterm="",
        ):
            print(line)
        print_persona(new["self_payload"], new["self_updated_at"])
    for table in TABLE_KEYS:
        print_table_changes(table, old["tables"].get(table, {}), new["tables"].get(table, {}))


def watch(db_path: Path, agent_id: str, interval: float, limit: int) -> int:
    con = connect(db_path)
    try:
        previous = read_snapshot(con, agent_id, limit)
        print_snapshot(previous, limit)
        print(f"[cognitive_watch] watching {db_path} agent={agent_id} interval={interval}s")
        print("[cognitive_watch] send QQ/ESP32 messages to see changes. Ctrl+C to stop.")
        while True:
            time.sleep(max(0.2, interval))
            try:
                current = read_snapshot(con, agent_id, limit)
            except sqlite3.OperationalError:
                continue
            if current != previous:
                print_delta(previous, current)
                previous = current
    except KeyboardInterrupt:
        print("\n[cognitive_watch] stopped")
        return 0
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Watch CognitiveCore self/persona and recent state changes."
    )
    parser.add_argument(
        "--db",
        default=str(default_db_path()),
        help="cognitive SQLite path (default: %(default)s)",
    )
    parser.add_argument(
        "--agent-id",
        default=os.environ.get("COGNITIVE_AGENT_ID", "central-controller"),
        help="self_state agent_id (default: %(default)s)",
    )
    parser.add_argument("--interval", type=float, default=2.0, help="poll interval seconds")
    parser.add_argument("--limit", type=int, default=20, help="recent rows per table")
    parser.add_argument("--once", action="store_true", help="print current snapshot and exit")
    parser.add_argument("--json", action="store_true", help="print current snapshot as JSON")
    args = parser.parse_args(argv)

    try:
        if args.once or args.json:
            con = connect(Path(args.db))
            try:
                snapshot = read_snapshot(con, args.agent_id, args.limit)
            finally:
                con.close()
            if args.json:
                print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            else:
                print_snapshot(snapshot, args.limit)
            return 0
        return watch(Path(args.db), args.agent_id, args.interval, args.limit)
    except FileNotFoundError as exc:
        print(f"[cognitive_watch] {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"[cognitive_watch] sqlite error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
