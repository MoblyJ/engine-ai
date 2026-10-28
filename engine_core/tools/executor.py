"""Tool executor with bounded results + effect journal (upgrade.md §4).

- Results are size-bounded before they re-enter model context.
- Every EXECUTED (side-effecting) call is journaled with an idempotency key; after a
  transport failure the worker resumes from the checkpoint and the journal prevents
  repeating a write that already landed.
- Policy violations return a typed error the model can read and route around.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time

from engine_core.tools.policy import Policy, PolicyViolation
from engine_core.tools.registry import Registry, UnknownTool

JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS effects(
  key TEXT PRIMARY KEY, tool TEXT, args_hash TEXT, ok INTEGER,
  result_summary TEXT, created_at REAL);
"""


def effect_key(tool: str, call_id: str) -> str:
    return f"{tool}:{call_id}"


class Executor:
    def __init__(self, registry: Registry, policy: Policy, journal_path: str | None = None,
                 max_result_chars: int = 8000):
        self.registry = registry
        self.policy = policy
        self.max_result_chars = max_result_chars
        self.db: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        if journal_path:
            os.makedirs(os.path.dirname(journal_path), exist_ok=True)
            self.db = sqlite3.connect(journal_path, check_same_thread=False, timeout=15)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(JOURNAL_SCHEMA)
            self.db.commit()

    def _journal_lookup(self, key: str) -> dict | None:
        if not self.db:
            return None
        with self._lock:
            r = self.db.execute("SELECT result_summary, ok FROM effects WHERE key=?", (key,)).fetchone()
        if not r:
            return None
        try:
            return {"ok": bool(r[1]), "summary": json.loads(r[0])}
        except json.JSONDecodeError:
            return {"ok": bool(r[1]), "summary": {"journaled": True}}

    def _journal_write(self, key: str, tool: str, args_hash: str, ok: bool, summary) -> None:
        if not self.db:
            return
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO effects VALUES(?,?,?,?,?,?)",
                            (key, tool, args_hash, int(ok),
                             json.dumps(summary)[:4000], time.time()))

    def execute(self, call: dict, context: dict | None = None) -> dict:
        """Execute one model-requested tool call → {"content": str, "ok": bool}."""
        name = call.get("function", {}).get("name") or call.get("name") or ""
        raw_args = call.get("function", {}).get("arguments") or call.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            return {"content": f"error: arguments for {name!r} are not valid JSON", "ok": False}
        entry = self.registry.get(name)  # UnknownTool → typed error, model can recover
        key = effect_key(name, call.get("id") or "")
        args_hash = hashlib.sha1(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]

        if not entry.read_only and self._journal_lookup(key):
            prior = self._journal_lookup(key)
            return {"content": json.dumps(
                {"ok": prior["ok"], "already_applied": True, "prior_summary": prior["summary"]}),
                "ok": prior["ok"], "replayed": True}
        try:
            self.policy.check(name, args, self.registry)
            try:  # context-aware tools (build tools) get the workspace; MCP handlers don't
                out = entry.fn(args, context or {})
            except TypeError:
                out = entry.fn(args)
            ok = not (isinstance(out, dict) and out.get("ok") is False)
            if not entry.read_only:
                self._journal_write(key, name, args_hash, ok, out)
        except (PolicyViolation, UnknownTool) as e:
            return {"content": f"error: {e}", "ok": False, "blocked": True}
        except Exception as e:  # noqa: BLE001 - operational error, serialized for the model
            if not entry.read_only:
                self._journal_write(key, name, args_hash, False, {"error": str(e)})
            return {"content": f"error: {e!r}", "ok": False}
        text = json.dumps(out, default=str)
        if len(text) > self.max_result_chars:
            text = text[:self.max_result_chars] + f'… (truncated at {self.max_result_chars} chars)'
        return {"content": text, "ok": ok}
