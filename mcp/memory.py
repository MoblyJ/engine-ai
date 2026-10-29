"""Memory pockets — project-scoped, provenance-tagged, revision-tracked context.

Design (v0.16, per the upgrade review):
- A **pocket** = an immutable record: KEYWORDS defining its context, the context TEXT,
  structured DATA, a KIND (fact | decision | idea | error | note), the PROJECT it belongs
  to, optional SOURCE provenance, and a REVISION chain.
- **Project namespace**: pockets live in a project (default `global`, or
  $ENGINE_AI_PROJECT). Facts from one project are NEVER recalled for another, and are
  never merged just because their keywords overlap.
- **No implicit merges**: `save` always appends a new immutable pocket. Superseding is
  explicit (`supersedes=<id>`): the new pocket links to the old one and the old one is
  retired from recall. The full chain stays retrievable via `get()`.
- **Recall is bounded**: it returns previews (default 600 chars) + data; fetch full text
  on demand with `get(id)` — compaction/decisions stay separable from evidence.

Stored in ~/.engine-ai/memory.db so it persists across Claude Code sessions.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time

from engine import HOME, embed  # reuse the hashing-trick embedder + home dir

_cos = lambda a, b: sum(x * y for x, y in zip(a, b))  # noqa: E731

SIM_KW = 0.20          # two pockets this keyword-similar get a RELATES_TO edge
PREVIEW_CHARS = 600    # recall sends previews; full text comes via get(id)
KINDS = ("fact", "decision", "idea", "error", "note")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pockets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL DEFAULT 'global',
  keywords TEXT NOT NULL, text TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
  category TEXT, kind TEXT NOT NULL DEFAULT 'note', source TEXT,
  revision INTEGER NOT NULL DEFAULT 1, supersedes INTEGER, superseded_by INTEGER,
  vec TEXT NOT NULL, created_at REAL, updated_at REAL, hits INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pocket_edges(
  src INTEGER, dst INTEGER, label TEXT, weight REAL DEFAULT 1.0, created_at REAL);
"""


def default_project() -> str:
    return os.environ.get("ENGINE_AI_PROJECT") or "global"


def _norm(keywords) -> list[str]:
    if isinstance(keywords, str):
        keywords = re.split(r"[,\s]+", keywords)
    seen, out = set(), []
    for k in keywords:
        t = re.sub(r"[^a-z0-9+.-]", "", str(k).lower().strip())
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class Memory:
    def __init__(self, db_path: str | None = None):
        os.makedirs(HOME, exist_ok=True)
        self.db = sqlite3.connect(db_path or os.path.join(HOME, "memory.db"),
                                  check_same_thread=False, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")   # concurrent readers must not
        self.db.execute("PRAGMA busy_timeout=5000")  # hit "database is locked"
        self.db.row_factory = sqlite3.Row
        self._migrate()
        self.db.commit()

    def _migrate(self):
        """Upgrade pre-v0.16 databases in place: add the namespace/provenance columns."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(pockets)")}
        if not cols:
            self.db.executescript(SCHEMA)
            return
        for col, decl in (("project", "TEXT NOT NULL DEFAULT 'global'"),
                          ("kind", "TEXT NOT NULL DEFAULT 'note'"),
                          ("source", "TEXT"),
                          ("revision", "INTEGER NOT NULL DEFAULT 1"),
                          ("supersedes", "INTEGER"),
                          ("superseded_by", "INTEGER")):
            if col not in cols:
                self.db.execute(f"ALTER TABLE pockets ADD COLUMN {col} {decl}")

    # ---------------------------------------------------------------- save
    def save(self, keywords, text: str, data: dict | None = None, category: str | None = None,
             *, project: str | None = None, kind: str = "note", source: str | None = None,
             supersedes: int | None = None) -> dict:
        kw = _norm(keywords)
        if not kw or not (text or "").strip():
            return {"ok": False, "error": "keywords and text are required"}
        if kind not in KINDS:
            return {"ok": False, "error": f"kind must be one of {KINDS}"}
        project = (project or default_project()).strip() or "global"
        data = data or {}

        revision = 1
        if supersedes is not None:
            old = self.db.execute("SELECT id,project,revision FROM pockets WHERE id=?", (supersedes,)).fetchone()
            if not old:
                return {"ok": False, "error": f"supersedes: no pocket #{supersedes}"}
            if old["project"] != project:
                return {"ok": False, "error": "supersedes: pocket belongs to a different project"}
            revision = old["revision"] + 1

        # idempotence: the exact same record already exists → return it, don't duplicate
        for row in self.db.execute(
                "SELECT id,keywords,data,kind,source,superseded_by FROM pockets WHERE project=? AND text=?",
                (project, text.strip())):
            if (json.loads(row["keywords"]) == kw and json.loads(row["data"]) == data
                    and row["kind"] == kind and row["source"] == source and row["superseded_by"] is None):
                return {"ok": True, "id": row["id"], "merged": False, "duplicate": True,
                        "keywords": kw, "project": project, "kind": kind, "revision": 1}

        vec = embed(" ".join(kw) + " " + text)
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO pockets(project,keywords,text,data,category,kind,source,revision,supersedes,"
            "vec,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (project, json.dumps(kw), text.strip(), json.dumps(data), category, kind, source,
             revision, supersedes, json.dumps(vec), now, now))
        pid = cur.lastrowid
        if supersedes is not None:
            self.db.execute("UPDATE pockets SET superseded_by=?, updated_at=? WHERE id=?",
                            (pid, now, supersedes))
        # link to related pockets in the SAME project (RELATES_TO) for graph traversal
        for r in self.db.execute("SELECT id,keywords FROM pockets WHERE project=? AND id!=? AND superseded_by IS NULL",
                                 (project, pid)):
            j = _jaccard(kw, json.loads(r["keywords"]))
            if j >= SIM_KW:
                self.db.execute("INSERT INTO pocket_edges(src,dst,label,weight,created_at) VALUES(?,?,?,?,?)",
                                (pid, r["id"], "RELATES_TO", j, now))
        self.db.commit()
        return {"ok": True, "id": pid, "merged": False, "duplicate": False, "keywords": kw,
                "project": project, "kind": kind, "revision": revision}

    # ---------------------------------------------------------------- recall
    def recall(self, keywords, k: int = 3, *, project: str | None = None,
               preview_chars: int = PREVIEW_CHARS, include_superseded: bool = False) -> dict:
        kw = _norm(keywords)
        project = (project or default_project()).strip() or "global"
        qv = embed(" ".join(kw))
        scored = []
        for r in self.db.execute("SELECT * FROM pockets WHERE project=?", (project,)):
            if r["superseded_by"] is not None and not include_superseded:
                continue  # retired by an explicit supersede — newest revision wins
            j = _jaccard(kw, json.loads(r["keywords"]))
            c = _cos(qv, json.loads(r["vec"]))
            score = 0.6 * j + 0.4 * c
            if score > 0.02:
                scored.append((score, j, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:k]
        if not top:
            return {"pockets": [], "used": "none", "context": "", "project": project}
        for _, _, r in top:
            self.db.execute("UPDATE pockets SET hits=hits+1 WHERE id=?", (r["id"],))
        self.db.commit()
        pockets = []
        for s, j, r in top:
            text = r["text"]
            pockets.append({"id": r["id"], "score": round(s, 3), "keywords": json.loads(r["keywords"]),
                            "category": r["category"], "kind": r["kind"], "revision": r["revision"],
                            "project": r["project"], "source": r["source"],
                            "supersedes": r["supersedes"], "superseded_by": r["superseded_by"],
                            "preview": text[:preview_chars] + ("…" if len(text) > preview_chars else ""),
                            "truncated": len(text) > preview_chars, "data": json.loads(r["data"])})
        used = "single"
        if len(top) >= 2 and _jaccard(json.loads(top[0][2]["keywords"]),
                                      json.loads(top[1][2]["keywords"])) >= SIM_KW:
            used = "merged-closest"
        ctx = self._format(pockets, kw, used)
        return {"pockets": pockets, "used": used, "context": ctx, "project": project}

    def context(self, keywords, k: int = 3, *, project: str | None = None) -> str:
        return self.recall(keywords, k=k, project=project)["context"]

    def _format(self, pockets: list[dict], kw: list[str], used: str) -> str:
        if not pockets:
            return ""
        lines = [f"# Memory for [{', '.join(kw)}]  · project: {pockets[0]['project']}  (recall: {used})",
                 "Verified prior context — build on it, and supersede outdated pockets explicitly.\n"]
        for p in pockets:
            head = (f"## pocket #{p['id']}  · {p['kind']}  · rev {p['revision']}  · "
                    f"keywords: {', '.join(p['keywords'])}  · score {p['score']}")
            lines.append(head)
            lines.append(p["preview"])
            if p["data"]:
                lines.append("data: " + json.dumps(p["data"]))
            if p["truncated"]:
                lines.append(f"(truncated — memory_get({p['id']}) returns the full text)")
            lines.append("")
        return "\n".join(lines)

    # ---------------------------------------------------------------- fetch full record
    def get(self, pocket_id: int) -> dict:
        r = self.db.execute("SELECT * FROM pockets WHERE id=?", (pocket_id,)).fetchone()
        if not r:
            return {"ok": False, "error": f"no pocket #{pocket_id}"}
        chain = []
        cur = r
        while cur["supersedes"]:
            prev = self.db.execute(
                "SELECT id,kind,revision,supersedes,superseded_by,created_at,text FROM pockets WHERE id=?",
                (cur["supersedes"],)).fetchone()
            if not prev:
                break
            chain.append({"id": prev["id"], "kind": prev["kind"], "revision": prev["revision"],
                          "superseded_at": prev["superseded_by"] and cur["id"], "created_at": prev["created_at"],
                          "text": prev["text"]})
            cur = prev
        superseded_by = None
        if r["superseded_by"]:
            nr = self.db.execute("SELECT id,kind,revision FROM pockets WHERE id=?", (r["superseded_by"],)).fetchone()
            superseded_by = dict(nr) if nr else None
        related = [{"id": e["dst"], "label": e["label"], "weight": round(e["weight"], 3)}
                   for e in self.db.execute("SELECT dst,label,weight FROM pocket_edges WHERE src=?", (pocket_id,))]
        return {"ok": True, "id": r["id"], "project": r["project"], "keywords": json.loads(r["keywords"]),
                "kind": r["kind"], "revision": r["revision"], "source": r["source"],
                "category": r["category"], "text": r["text"], "data": json.loads(r["data"]),
                "supersedes_chain": chain, "superseded_by": superseded_by, "related": related,
                "created_at": r["created_at"], "updated_at": r["updated_at"], "hits": r["hits"]}

    # ---------------------------------------------------------------- misc
    def list(self, project: str | None = None) -> list[dict]:
        project = (project or default_project()).strip() or "global"
        return [{"id": r["id"], "keywords": json.loads(r["keywords"]), "category": r["category"],
                 "kind": r["kind"], "project": r["project"], "revision": r["revision"],
                 "retired": r["superseded_by"] is not None,
                 "hits": r["hits"], "updated_at": r["updated_at"],
                 "preview": r["text"][:80].replace("\n", " ")}
                for r in self.db.execute("SELECT * FROM pockets WHERE project=? ORDER BY updated_at DESC",
                                         (project,))]

    def forget(self, pocket_id: int) -> dict:
        self.db.execute("DELETE FROM pockets WHERE id=?", (pocket_id,))
        self.db.execute("DELETE FROM pocket_edges WHERE src=? OR dst=?", (pocket_id, pocket_id))
        self.db.execute("UPDATE pockets SET superseded_by=NULL WHERE superseded_by=?", (pocket_id,))
        self.db.execute("UPDATE pockets SET supersedes=NULL WHERE supersedes=?", (pocket_id,))
        self.db.commit()
        return {"ok": True, "deleted": pocket_id}
