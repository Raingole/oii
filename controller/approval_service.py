from __future__ import annotations
import hashlib, json, secrets, sqlite3, time, uuid
from pathlib import Path

class ApprovalService:
    def __init__(self,path: str|Path|None=None, allowed: set[str]|None=None):
        if isinstance(path, set) and allowed is None:
            allowed, path = path, None
        self.allowed=allowed or set(); self.path=Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True,exist_ok=True); db=sqlite3.connect(self.path); db.execute("CREATE TABLE IF NOT EXISTS approvals (approval_id TEXT PRIMARY KEY,action_id TEXT,event_id TEXT,actor_id TEXT,session_id TEXT,risk_level TEXT,payload_hash TEXT NOT NULL,expires_at REAL NOT NULL,approval_token TEXT NOT NULL,approved_by TEXT,status TEXT NOT NULL DEFAULT 'pending',used_at REAL,created_at REAL NOT NULL)"); db.commit(); db.close()
    @staticmethod
    def requires(action:dict)->bool:return bool(action.get("requires_controller_approval") or action.get("risk_level") in {"high","critical"})
    @staticmethod
    def _hash(action):return hashlib.sha256(json.dumps(action,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    def create(self,action,event_id:str,actor_id:str,session_id:str,ttl:float=300.0)->dict:
        if not self.path: raise RuntimeError("approval persistence is not configured")
        rec={"approval_id":str(uuid.uuid4()),"action_id":action["action_id"],"event_id":event_id,"actor_id":actor_id,"session_id":session_id,"risk_level":action.get("risk_level","high"),"payload_hash":self._hash(action),"expires_at":time.time()+ttl,"approval_token":secrets.token_urlsafe(24),"approved_by":"","status":"pending","created_at":time.time()}
        db=sqlite3.connect(self.path); db.execute("INSERT INTO approvals(approval_id,action_id,event_id,actor_id,session_id,risk_level,payload_hash,expires_at,approval_token,approved_by,status,created_at) VALUES(:approval_id,:action_id,:event_id,:actor_id,:session_id,:risk_level,:payload_hash,:expires_at,:approval_token,:approved_by,:status,:created_at)",rec); db.commit(); db.close(); return rec
    def approve_request(self,approval_id:str,token:str,approved_by:str,actor_id:str|None=None)->bool:
        if not self.path:return False
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT approval_token,expires_at,status,actor_id FROM approvals WHERE approval_id=?",(approval_id,)).fetchone()
            ok=bool(row and secrets.compare_digest(row[0],token) and row[1]>time.time() and row[2]=="pending" and (actor_id is None or actor_id==row[3]))
            if ok:db.execute("UPDATE approvals SET status='approved',approved_by=? WHERE approval_id=?",(approved_by,approval_id))
            db.execute("COMMIT"); return ok
        except Exception:db.execute("ROLLBACK");raise
        finally:db.close()
    def is_approved(self,action:dict)->bool:
        if action.get("action_id") in self.allowed:return True
        if not self.path:return False
        db=sqlite3.connect(self.path); row=db.execute("SELECT status,expires_at,payload_hash FROM approvals WHERE action_id=? ORDER BY created_at DESC LIMIT 1",(action.get("action_id"),)).fetchone(); db.close()
        return bool(row and row[0]=="approved" and row[1]>time.time() and row[2]==self._hash(action))
    def consume(self, action: dict) -> bool:
        """Atomically spend one approval, binding it to the exact payload."""
        if action.get("action_id") in self.allowed: return True
        if not self.path: return False
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT approval_id,expires_at,status,payload_hash FROM approvals WHERE action_id=? ORDER BY created_at DESC LIMIT 1",(action.get("action_id"),)).fetchone()
            ok=bool(row and row[2]=="approved" and row[1]>time.time() and row[3]==self._hash(action))
            if ok: db.execute("UPDATE approvals SET status='used',used_at=? WHERE approval_id=? AND status='approved'",(time.time(),row[0]))
            db.execute("COMMIT"); return ok
        except Exception: db.execute("ROLLBACK"); raise
        finally: db.close()
    def get(self,approval_id:str):
        if not self.path:return None
        db=sqlite3.connect(self.path); row=db.execute("SELECT approval_id,action_id,event_id,actor_id,session_id,risk_level,payload_hash,expires_at,approval_token,approved_by,status,used_at,created_at FROM approvals WHERE approval_id=?",(approval_id,)).fetchone(); db.close()
        if not row:return None
        keys="approval_id action_id event_id actor_id session_id risk_level payload_hash expires_at approval_token approved_by status used_at created_at".split(); return dict(zip(keys,row))
