"""Durable task state with leases and fencing (upgrade.md §5, §7 phase 3).

SQLite is local-only (never on a network filesystem). Every state transition happens in
a short transaction; leases carry an expiry and a monotonically increasing FENCE token
so a late worker's stale write cannot overwrite a newer attempt — `complete()/fail()`
only apply when the fence matches the lease the worker actually holds.

`check_same_thread=False` is not a concurrency protocol — the module lock + short
transactions are: one writer at a time, per upgrade.md's note on that flag.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid

from engine_core.runtime.task_graph import STATES, TaskSpec

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, project TEXT, spec_json TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT, lease_expires REAL, fence INTEGER NOT NULL DEFAULT 0,
  result_json TEXT, error TEXT, checkpoint_json TEXT,
  created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, task_id TEXT, kind TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
"""

DEFAULT_LEASE_SECONDS = 600.0
MAX_ATTEMPTS = 3


class StateError(RuntimeError):
    pass


class StateStore:
    def __init__(self, db_path: str):
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- tasks
    def upsert(self, spec: TaskSpec) -> None:
        now = time.time()
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO tasks(id,project,spec_json,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET spec_json=excluded.spec_json, project=excluded.project, "
                "updated_at=excluded.updated_at",
                (spec.id, spec.project, json.dumps(spec.to_dict()), now, now))

    def get(self, task_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["spec"] = TaskSpec.from_dict(json.loads(d.pop("spec_json")))
        for k in ("result", "checkpoint"):
            if d.get(f"{k}_json"):
                d[k] = json.loads(d[f"{k}_json"])
            d.pop(f"{k}_json", None)
        return d

    def all(self) -> list[dict]:
        return [self.get(r["id"]) for r in self.db.execute("SELECT id FROM tasks ORDER BY id")]

    def _set(self, task_id: str, state: str, *, error: str | None = None, **cols) -> None:
        sets = ", ".join([f"state=?", "error=?", "updated_at=?"] + [f"{k}=?" for k in cols])
        vals = [state, error, time.time()] + list(cols.values())
        with self._lock, self.db:
            cur = self.db.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*vals, task_id))
            if cur.rowcount == 0:
                raise StateError(f"no task {task_id!r}")
            self.db.execute("INSERT INTO events(ts,task_id,kind,payload) VALUES(?,?,?,?)",
                            (time.time(), task_id, f"state:{state}", json.dumps({"error": error})))

    # ---------------------------------------------------------------- leases
    def claim(self, task_id: str, owner: str | None = None,
              lease_seconds: float = DEFAULT_LEASE_SECONDS) -> dict:
        """Atomically lease a ready task. Returns the assignment incl. its fence."""
        owner = owner or f"worker-{uuid.uuid4().hex[:8]}"
        now = time.time()
        with self._lock, self.db:
            r = self.db.execute("SELECT state, attempts, fence FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not r:
                raise StateError(f"no task {task_id!r}")
            if r["state"] not in ("ready", "pending"):
                raise StateError(f"task {task_id!r} is {r['state']!r}, not claimable")
            fence = r["fence"] + 1
            self.db.execute(
                "UPDATE tasks SET state='leased', lease_owner=?, lease_expires=?, fence=?, "
                "attempts=attempts+1, updated_at=? WHERE id=?",
                (owner, now + lease_seconds, fence, now, task_id))
            self.db.execute("INSERT INTO events(ts,task_id,kind,payload) VALUES(?,?,?,?)",
                            (now, task_id, "leased", json.dumps({"owner": owner, "fence": fence})))
        return {"task_id": task_id, "owner": owner, "fence": fence,
                "lease_expires": now + lease_seconds}

    def heartbeat(self, task_id: str, owner: str, fence: int,
                  lease_seconds: float = DEFAULT_LEASE_SECONDS) -> bool:
        with self._lock, self.db:
            cur = self.db.execute(
                "UPDATE tasks SET state='running', lease_expires=?, updated_at=? "
                "WHERE id=? AND lease_owner=? AND fence=? AND state IN ('leased','running')",
                (time.time() + lease_seconds, time.time(), task_id, owner, fence))
            return cur.rowcount > 0

    def complete(self, task_id: str, fence: int, result: dict | None = None) -> bool:
        """Fenced completion: only the lease holder's fence wins."""
        with self._lock, self.db:
            cur = self.db.execute(
                "UPDATE tasks SET state='complete', result_json=?, lease_owner=NULL, lease_expires=NULL, "
                "updated_at=? WHERE id=? AND fence=? AND state IN ('leased','running','verifying')",
                (json.dumps(result or {}), time.time(), task_id, fence))
            if cur.rowcount:
                self.db.execute("INSERT INTO events(ts,task_id,kind,payload) VALUES(?,?,?,?)",
                                (time.time(), task_id, "complete", json.dumps({"fence": fence})))
                return True
            return False

    def fail(self, task_id: str, fence: int, error: str, *, blocked: bool = False) -> bool:
        state = "blocked" if blocked else "failed"
        with self._lock, self.db:
            row = self.db.execute("SELECT attempts, lease_owner FROM tasks WHERE id=? AND fence=?",
                                  (task_id, fence)).fetchone()
            if not row:
                return False
            retryable = (not blocked) and row["attempts"] < MAX_ATTEMPTS
            self.db.execute(
                "UPDATE tasks SET state=?, error=?, lease_owner=NULL, lease_expires=NULL, updated_at=? "
                "WHERE id=? AND fence=?", ("ready" if retryable else state, error, time.time(), task_id, fence))
            self.db.execute("INSERT INTO events(ts,task_id,kind,payload) VALUES(?,?,?,?)",
                            (time.time(), task_id, f"{'retry' if retryable else state}",
                             json.dumps({"error": error[:500]})))
            return True

    def expire_leases(self, now: float | None = None) -> list[str]:
        """Requeue tasks whose lease lapsed (crashed worker). Attempts still bounded."""
        now = now if now is not None else time.time()
        expired = []
        with self._lock, self.db:
            rows = self.db.execute(
                "SELECT id, attempts FROM tasks WHERE state IN ('leased','running') AND lease_expires < ?",
                (now,)).fetchall()
            for r in rows:
                retryable = r["attempts"] < MAX_ATTEMPTS
                self.db.execute("UPDATE tasks SET state=?, lease_owner=NULL, lease_expires=NULL, "
                                "error=?, updated_at=? WHERE id=?",
                                ("ready" if retryable else "failed", "lease expired", now, r["id"]))
                expired.append(r["id"])
        return expired

    def ready_ids(self, *, exclude_blocked: bool = True) -> list[str]:
        """IDs in a claimable state whose dependencies (per their specs) are complete."""
        rows = self.db.execute("SELECT id, spec_json, state FROM tasks").fetchall()
        state = {r["id"]: r["state"] for r in rows}
        out = []
        for r in rows:
            if r["state"] not in ("pending", "ready"):
                continue
            deps = json.loads(r["spec_json"]).get("dependencies", [])
            if all(state.get(d) == "complete" for d in deps):
                out.append(r["id"])
        return sorted(out)

    def mark_ready(self, task_ids: list[str]) -> None:
        for tid in task_ids:
            self._set(tid, "ready")

    # ---------------------------------------------------------------- events
    def events(self, task_id: str | None = None, limit: int = 100) -> list[dict]:
        if task_id:
            rows = self.db.execute("SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?",
                                   (task_id, limit))
        else:
            rows = self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [{"ts": r["ts"], "task_id": r["task_id"], "kind": r["kind"],
                 "payload": json.loads(r["payload"] or "{}")} for r in rows]

    def checkpoint(self, task_id: str, fence: int, checkpoint: dict) -> bool:
        with self._lock, self.db:
            cur = self.db.execute("UPDATE tasks SET checkpoint_json=?, updated_at=? WHERE id=? AND fence=?",
                                  (json.dumps(checkpoint), time.time(), task_id, fence))
            return cur.rowcount > 0

    def close(self):
        self.db.close()
