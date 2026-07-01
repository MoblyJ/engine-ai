"""Memory pockets — evolving, keyword-tagged context for building apps from prompts.

Design borrowed from HelixDB (graph + vector memory), reduced to Python + SQLite:
- A **pocket** = a labeled memory chunk (HelixDB "node/label") with KEYWORDS defining its
  context, the context TEXT, structured DATA, an embedding, and a lifecycle (created/updated).
- **Recall** is hybrid: keyword overlap (Jaccard) + embedding cosine (HelixDB's vector+BM25
  hybrid), ranked by a combined score — the seed score is kept before any "traversal".
- **Merge lifecycle**: if 2+ pockets share similar keywords, the closest ones are merged so the
  returned context carries BOTH memory + data — this is what makes apps *evolve* across prompts.
- **Edges** link related pockets (RELATES_TO / SUPERSEDES) like HelixDB's typed edges.

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

MERGE_KW = 0.55        # save-time dedup: keyword Jaccard >= this ⇒ merge into existing pocket
MERGE_KW_VEC = 0.30    # …or keyword>=this AND embedding cosine>=MERGE_VEC
MERGE_VEC = 0.82
SIM_KW = 0.20          # recall-time: two hits this similar (share a core keyword) ⇒ combine memory+data

SCHEMA = """
CREATE TABLE IF NOT EXISTS pockets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keywords TEXT NOT NULL, text TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
  category TEXT, vec TEXT NOT NULL, created_at REAL, updated_at REAL, hits INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pocket_edges(
  src INTEGER, dst INTEGER, label TEXT, weight REAL DEFAULT 1.0, created_at REAL);
"""


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
        self.db = sqlite3.connect(db_path or os.path.join(HOME, "memory.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA); self.db.commit()

    # ---------------------------------------------------------------- save
    def save(self, keywords, text: str, data: dict | None = None, category: str | None = None) -> dict:
        kw = _norm(keywords)
        if not kw or not (text or "").strip():
            return {"ok": False, "error": "keywords and text are required"}
        data = data or {}
        vec = embed(" ".join(kw) + " " + text)
        # dedup/merge into the closest existing pocket if very similar
        best, best_j, best_c = None, 0.0, 0.0
        for r in self.db.execute("SELECT * FROM pockets"):
            j = _jaccard(kw, json.loads(r["keywords"]))
            c = _cos(vec, json.loads(r["vec"]))
            if j > best_j or (j == best_j and c > best_c):
                best, best_j, best_c = r, j, c
        if best and (best_j >= MERGE_KW or (best_j >= MERGE_KW_VEC and best_c >= MERGE_VEC)):
            merged_kw = _norm(json.loads(best["keywords"]) + kw)
            merged_text = (best["text"] + "\n---\n" + text).strip()[-8000:]
            merged_data = {**json.loads(best["data"]), **data}
            self.db.execute(
                "UPDATE pockets SET keywords=?, text=?, data=?, vec=?, updated_at=?, hits=hits+1 WHERE id=?",
                (json.dumps(merged_kw), merged_text, json.dumps(merged_data),
                 json.dumps(embed(" ".join(merged_kw) + " " + merged_text)), time.time(), best["id"]))
            self.db.commit()
            return {"ok": True, "id": best["id"], "merged": True, "keywords": merged_kw,
                    "note": "evolved an existing pocket (similar keywords)"}
        cur = self.db.execute(
            "INSERT INTO pockets(keywords,text,data,category,vec,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (json.dumps(kw), text, json.dumps(data), category, json.dumps(vec), time.time(), time.time()))
        self.db.commit()
        # link to related pockets (RELATES_TO) for graph traversal later
        pid = cur.lastrowid
        for r in self.db.execute("SELECT id,keywords FROM pockets WHERE id!=?", (pid,)):
            j = _jaccard(kw, json.loads(r["keywords"]))
            if j >= SIM_KW:
                self.db.execute("INSERT INTO pocket_edges(src,dst,label,weight,created_at) VALUES(?,?,?,?,?)",
                                (pid, r["id"], "RELATES_TO", j, time.time()))
        self.db.commit()
        return {"ok": True, "id": pid, "merged": False, "keywords": kw}

    # ---------------------------------------------------------------- recall
    def recall(self, keywords, k: int = 3) -> dict:
        kw = _norm(keywords)
        qv = embed(" ".join(kw))
        scored = []
        for r in self.db.execute("SELECT * FROM pockets"):
            pk = json.loads(r["keywords"])
            j = _jaccard(kw, pk)
            c = _cos(qv, json.loads(r["vec"]))
            score = 0.6 * j + 0.4 * c
            if score > 0.02:
                scored.append((score, j, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:k]
        if not top:
            return {"pockets": [], "used": "none", "context": ""}
        # bump hit counts
        for _, _, r in top:
            self.db.execute("UPDATE pockets SET hits=hits+1 WHERE id=?", (r["id"],))
        self.db.commit()
        pockets = [{"id": r["id"], "score": round(s, 3), "keywords": json.loads(r["keywords"]),
                    "category": r["category"], "text": r["text"], "data": json.loads(r["data"])}
                   for s, _, r in top]
        # if the top two share keywords → combine BOTH memory + data (evolving context)
        used = "single"
        if len(top) >= 2 and _jaccard(json.loads(top[0][2]["keywords"]), json.loads(top[1][2]["keywords"])) >= SIM_KW:
            used = "merged-closest"
        ctx = self._format(pockets, kw, used)
        return {"pockets": pockets, "used": used, "context": ctx}

    def context(self, keywords, k: int = 3) -> str:
        return self.recall(keywords, k=k)["context"]

    def _format(self, pockets: list[dict], kw: list[str], used: str) -> str:
        if not pockets:
            return ""
        lines = [f"# Evolving memory for [{', '.join(kw)}]  (recall: {used})",
                 "Build on this prior context so the app EVOLVES from earlier prompts — do not start over.\n"]
        for p in pockets:
            lines.append(f"## pocket #{p['id']}  · keywords: {', '.join(p['keywords'])}  · score {p['score']}")
            lines.append(p["text"].strip())
            if p["data"]:
                lines.append("data: " + json.dumps(p["data"]))
            lines.append("")
        return "\n".join(lines)

    # ---------------------------------------------------------------- misc
    def list(self) -> list[dict]:
        return [{"id": r["id"], "keywords": json.loads(r["keywords"]), "category": r["category"],
                 "hits": r["hits"], "updated_at": r["updated_at"],
                 "preview": r["text"][:80].replace("\n", " ")}
                for r in self.db.execute("SELECT * FROM pockets ORDER BY updated_at DESC")]

    def forget(self, pocket_id: int) -> dict:
        self.db.execute("DELETE FROM pockets WHERE id=?", (pocket_id,))
        self.db.execute("DELETE FROM pocket_edges WHERE src=? OR dst=?", (pocket_id, pocket_id))
        self.db.commit()
        return {"ok": True, "deleted": pocket_id}
