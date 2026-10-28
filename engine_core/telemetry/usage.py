"""Usage/cost journal — every model call, per task (upgrade.md §6).

Measures total input/output/reasoning/cached tokens, calls, retries, latency, cost —
the numbers the benchmark comparison needs (target: ≥30% lower median input tokens per
accepted task without pass-rate regressions). Credentials never appear here.
"""

from __future__ import annotations

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, task_id TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
  cached_tokens INTEGER, cost_usd REAL, latency_s REAL);
CREATE INDEX IF NOT EXISTS idx_usage_task ON usage(task_id);
"""


class UsageJournal:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def record(self, task_id: str, model: str, usage, latency_s: float = 0.0) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO usage(ts,task_id,model,input_tokens,output_tokens,reasoning_tokens,"
                "cached_tokens,cost_usd,latency_s) VALUES(?,?,?,?,?,?,?,?,?)",
                (time.time(), task_id, model, getattr(usage, "input_tokens", 0),
                 getattr(usage, "output_tokens", 0), getattr(usage, "reasoning_tokens", 0),
                 getattr(usage, "cached_tokens", 0), getattr(usage, "cost_usd", 0.0), latency_s))

    def totals(self, task_id: str | None = None) -> dict:
        q = ("SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o, "
             "COALESCE(SUM(reasoning_tokens),0) r, COALESCE(SUM(cached_tokens),0) c, "
             "COALESCE(SUM(cost_usd),0.0) usd, COALESCE(AVG(latency_s),0.0) lat FROM usage")
        args: tuple = ()
        if task_id:
            q += " WHERE task_id=?"
            args = (task_id,)
        row = self.db.execute(q, args).fetchone()
        return {"calls": row[0], "input_tokens": row[1], "output_tokens": row[2],
                "reasoning_tokens": row[3], "cached_tokens": row[4],
                "cost_usd": round(row[5], 6), "avg_latency_s": round(row[6], 3)}

    def per_task(self) -> dict[str, dict]:
        out = {}
        for r in self.db.execute("SELECT DISTINCT task_id FROM usage"):
            out[r[0]] = self.totals(r[0])
        return out

    def close(self):
        self.db.close()
