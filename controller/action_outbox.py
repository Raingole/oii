from __future__ import annotations
import json, sqlite3, time
from pathlib import Path
from typing import Any

class ActionOutbox:
    STATES = {"planned","pending","approved","executing","succeeded","failed","retrying","cancelled"}
    TRANSITIONS = {"planned":{"pending","cancelled"},"pending":{"approved","executing","retrying","failed","cancelled","succeeded"},"approved":{"executing","retrying","failed","cancelled","succeeded"},"executing":{"succeeded","retrying","failed","cancelled","pending"},"retrying":{"executing","succeeded","failed","cancelled"},"failed":{"retrying","executing"},"succeeded":set(),"cancelled":{"approved"}}
    def __init__(self, path: str|Path) -> None:
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); db=sqlite3.connect(self.path,timeout=30)
        try:
            db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA busy_timeout=30000")
            db.execute("CREATE TABLE IF NOT EXISTS action_outbox (action_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 3,retry_at REAL,lease_until REAL,last_error TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            cols={r[1] for r in db.execute("PRAGMA table_info(action_outbox)")}
            if "max_attempts" not in cols: db.execute("ALTER TABLE action_outbox ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3")
            db.execute("CREATE INDEX IF NOT EXISTS idx_action_outbox_status ON action_outbox(status,retry_at)"); db.commit()
            db.execute("CREATE TABLE IF NOT EXISTS schedules (schedule_id TEXT PRIMARY KEY, action_id TEXT NOT NULL, event_id TEXT NOT NULL, due_at REAL NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', UNIQUE(action_id))"); db.commit()
            schedule_cols={r[1] for r in db.execute("PRAGMA table_info(schedules)")}
            if "inner_action_id" not in schedule_cols: db.execute("ALTER TABLE schedules ADD COLUMN inner_action_id TEXT")
            if "last_error" not in schedule_cols: db.execute("ALTER TABLE schedules ADD COLUMN last_error TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_schedules_inner_action ON schedules(inner_action_id,status)"); db.commit()
        finally: db.close()
    @staticmethod
    def _map(row):
        if not row:return None
        return {"action_id":row[0],"event_id":row[1],"payload":json.loads(row[2]),"status":row[3],"attempts":row[4],"max_attempts":row[5],"retry_at":row[6],"lease_until":row[7],"last_error":row[8]}
    def put(self,event_id:str,action:dict[str,Any],max_attempts:int=3):
        db=sqlite3.connect(self.path,timeout=30)
        try:
            row=db.execute("SELECT action_id,event_id,payload,status,attempts,max_attempts,retry_at,lease_until,last_error FROM action_outbox WHERE action_id=?",(str(action["action_id"]),)).fetchone()
            if row: out=self._map(row); out["duplicate"]=True; return out
            m=max(1,int(max_attempts)); payload=json.dumps(action,ensure_ascii=False); db.execute("INSERT INTO action_outbox(action_id,event_id,payload,status,max_attempts) VALUES(?,?,?,?,?)",(str(action["action_id"]),event_id,payload,"pending",m)); db.commit()
            out=self._map((str(action["action_id"]),event_id,payload,"pending",0,m,None,None,"")); out["duplicate"]=False; return out
        finally:db.close()
    def claim(self,action_id:str,lease_seconds:float=30.0):
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT action_id,event_id,payload,status,attempts,max_attempts,retry_at,lease_until,last_error FROM action_outbox WHERE action_id=?",(action_id,)).fetchone()
            if not row: db.execute("ROLLBACK"); return None
            out=self._map(row); now=time.time()
            if out["status"] in {"succeeded","cancelled"}: db.execute("COMMIT"); return out
            if out["status"]=="executing" and out["lease_until"] and out["lease_until"]>now: db.execute("ROLLBACK"); return None
            if out["attempts"]>=out["max_attempts"]:
                db.execute("UPDATE action_outbox SET status='failed',last_error=?,lease_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE action_id=?",(out["last_error"] or "attempt limit reached",action_id)); db.execute("COMMIT"); out["status"]="failed"; return out
            attempt=out["attempts"]+1; lease=now+max(1.0,lease_seconds); db.execute("UPDATE action_outbox SET status='executing',attempts=?,lease_until=?,retry_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE action_id=?",(attempt,lease,action_id)); db.execute("COMMIT"); out.update(status="executing",attempts=attempt,lease_until=lease,retry_at=None); return out
        except Exception: db.execute("ROLLBACK"); raise
        finally:db.close()
    def transition(self,action_id:str,status:str,error:str="",retry_at:float|None=None,lease_until:float|None=None):
        if status not in self.STATES: raise ValueError(f"invalid action status: {status}")
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT status FROM action_outbox WHERE action_id=?",(action_id,)).fetchone()
            if not row: raise KeyError(action_id)
            if status!=row[0] and status not in self.TRANSITIONS.get(row[0],set()): raise ValueError(f"invalid action transition: {row[0]} -> {status}")
            db.execute("UPDATE action_outbox SET status=?,last_error=?,retry_at=?,lease_until=?,updated_at=CURRENT_TIMESTAMP WHERE action_id=?",(status,(error or "")[:500],retry_at,lease_until,action_id)); db.execute("COMMIT")
        except Exception: db.execute("ROLLBACK"); raise
        finally:db.close()
    def get(self,action_id:str):
        db=sqlite3.connect(self.path,timeout=30)
        try:return self._map(db.execute("SELECT action_id,event_id,payload,status,attempts,max_attempts,retry_at,lease_until,last_error FROM action_outbox WHERE action_id=?",(action_id,)).fetchone())
        finally:db.close()
    def recoverable(self):
        now=time.time(); db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE"); db.execute("UPDATE action_outbox SET status='retrying',lease_until=NULL,retry_at=?,last_error=COALESCE(last_error,'execution lease expired'),updated_at=CURRENT_TIMESTAMP WHERE status='executing' AND lease_until IS NOT NULL AND lease_until<=?",(now,now)); rows=db.execute("SELECT action_id,event_id,payload,status,attempts,max_attempts,retry_at,lease_until,last_error FROM action_outbox WHERE status IN ('pending','approved','retrying') AND (retry_at IS NULL OR retry_at<=?)",(now,)).fetchall(); db.execute("COMMIT"); return [self._map(r) for r in rows]
        except Exception:db.execute("ROLLBACK");raise
        finally:db.close()

    def claim_due_schedules(self, now: float | None = None, limit: int = 50):
        now = now or time.time(); db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            rows=db.execute("SELECT schedule_id,action_id,event_id,due_at,payload,status FROM schedules WHERE status='pending' AND due_at<=? ORDER BY due_at LIMIT ?",(now,limit)).fetchall()
            for row in rows: db.execute("UPDATE schedules SET status='executing' WHERE schedule_id=? AND status='pending'",(row[0],))
            db.execute("COMMIT")
            return [{"schedule_id":r[0],"action_id":r[1],"event_id":r[2],"due_at":r[3],"payload":json.loads(r[4]),"status":"executing"} for r in rows]
        except Exception:db.execute("ROLLBACK");raise
        finally:db.close()
    def finish_schedule(self, schedule_id: str, status: str, error: str = "") -> None:
        if status not in {"succeeded","failed","cancelled"}: raise ValueError(status)
        db=sqlite3.connect(self.path,timeout=30); db.execute("UPDATE schedules SET status=?,last_error=? WHERE schedule_id=? AND status IN ('pending','executing')",(status,(error or "")[:500],schedule_id)); db.commit(); db.close()
    def recover_schedules(self) -> list[dict[str, Any]]:
        db=sqlite3.connect(self.path,timeout=30)
        try:
            rows=db.execute("SELECT schedule_id,action_id,event_id,due_at,payload,status,inner_action_id,last_error FROM schedules WHERE status='executing'").fetchall()
            return [{"schedule_id":r[0],"action_id":r[1],"event_id":r[2],"due_at":r[3],"payload":json.loads(r[4]),"status":r[5],"inner_action_id":r[6],"last_error":r[7]} for r in rows]
        finally: db.close()
    def set_schedule_inner(self, schedule_id: str, inner_action_id: str) -> None:
        db=sqlite3.connect(self.path,timeout=30); db.execute("UPDATE schedules SET inner_action_id=? WHERE schedule_id=? AND status='executing'",(inner_action_id,schedule_id)); db.commit(); db.close()
    def update_schedules_for_inner(self, inner_action_id: str, action_status: str, error: str = "") -> None:
        final = "succeeded" if action_status == "succeeded" else "failed" if action_status == "failed" else "executing"
        db=sqlite3.connect(self.path,timeout=30); db.execute("UPDATE schedules SET status=?,last_error=? WHERE inner_action_id=? AND status='executing'",(final,(error or "")[:500],inner_action_id)); db.commit(); db.close()

    def claim_due_retries(self, now: float | None = None, limit: int = 50, lease_seconds: float = 30.0):
        """Claim retry rows atomically; only one scheduler receives each row."""
        now = now or time.time(); db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            rows=db.execute("SELECT action_id,event_id,payload,status,attempts,max_attempts,retry_at,lease_until,last_error FROM action_outbox WHERE status='retrying' AND retry_at<=? AND (lease_until IS NULL OR lease_until<=?) ORDER BY retry_at LIMIT ?",(now,now,limit)).fetchall()
            for row in rows: db.execute("UPDATE action_outbox SET lease_until=?,updated_at=CURRENT_TIMESTAMP WHERE action_id=? AND status='retrying' AND (lease_until IS NULL OR lease_until<=?)",(now+max(1.0,lease_seconds),row[0],now))
            db.execute("COMMIT")
            return [self._map(tuple(list(r[:7])+[now+max(1.0,lease_seconds),r[8]])) for r in rows]
        except Exception:db.execute("ROLLBACK");raise
        finally:db.close()
