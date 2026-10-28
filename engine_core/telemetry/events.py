"""Append-only event log — decisions, transitions, patches, evidence IDs (upgrade.md §2 #9).

Full logs persist OUTSIDE model context; workers hand each other compact handoffs, and
anything reconstructable lives here. Events are never rewritten; corrections are new
events referencing the ones they supersede.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time


class EventLog:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, task_id TEXT, "
                        "kind TEXT, payload TEXT)")
        self.db.commit()

    def append(self, kind: str, payload: dict, task_id: str | None = None) -> int:
        with self.db:
            cur = self.db.execute("INSERT INTO events(ts,task_id,kind,payload) VALUES(?,?,?,?)",
                                  (time.time(), task_id, kind, json.dumps(payload, default=str)))
            return cur.lastrowid

    def query(self, kind: str | None = None, task_id: str | None = None, limit: int = 100) -> list[dict]:
        q, args = "SELECT * FROM events WHERE 1=1", []
        if kind:
            q += " AND kind=?"; args.append(kind)
        if task_id:
            q += " AND task_id=?"; args.append(task_id)
        q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
        return [{"id": r[0], "ts": r[1], "task_id": r[2], "kind": r[3],
                 "payload": json.loads(r[4] or "{}")} for r in self.db.execute(q, args)]

    def close(self):
        self.db.close()
