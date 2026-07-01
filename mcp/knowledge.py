"""Knowledge swarm — ingest engineering repos into a domain-tagged retrieval store.

How the domain-expert agents "master" a field: by RETRIEVAL, not training. We clone curated repos
(roadmaps, system design, ML, LLM, build-your-own-x, …), chunk their docs+code, and index them in
**SQLite FTS5** (fast BM25 keyword search) tagged with a DOMAIN. A domain agent answers by
`knowledge_search(query, domain=...)` grounded in that store, plus its evolving memory.

FTS5 (not per-chunk vectors) keeps the store small and fast at 100k+ chunks. Memory pockets keep
their embeddings (small set). `context_pack` = prior MEMORY + retrieved DOMAIN KNOWLEDGE in one blob.

Store: ~/.engine-ai/knowledge.db   ·   clones: ~/.engine-ai/sources/<repo>
"""

from __future__ import annotations

import os
import re
import subprocess
import sqlite3
import time

from engine import HOME
from memory import Memory

SOURCES = os.path.join(HOME, "sources")
TEXT_EXT = {".md", ".mdx", ".rst", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
            ".java", ".rb", ".yaml", ".yml", ".toml", ".tf", ".sh", ".sql", ".c", ".h", ".cpp",
            ".mmd", ".puml", ".proto"}
SKIP_DIRS = {".git", "node_modules", "dist", "build", ".venv", "__pycache__", ".next", "vendor",
             "translations", "translated", "i18n", "locale", "locales", "lang", "langs", "assets",
             "images", "img", "media", ".github"}
STOP = {"and", "or", "not", "near", "the", "a", "an", "to", "of", "in", "for", "is", "on", "with"}
LANGS = {"ja", "zh", "ko", "fr", "de", "es", "pt", "ru", "it", "tr", "vi", "pl", "nl", "ar", "hi",
         "id", "th", "uk", "cs", "ro", "el", "sv", "da", "fi", "no", "hu", "bg", "sr", "hr", "sk",
         "fa", "he", "bn", "ta", "ur", "ms", "cn", "tw", "br", "gr", "jp"}

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge USING fts5(
  text, domain UNINDEXED, repo UNINDEXED, path UNINDEXED, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS sources(
  repo TEXT PRIMARY KEY, url TEXT, domain TEXT, chunks INTEGER, files INTEGER, ingested_at REAL);
"""


def _chunks(t: str, size=1500, overlap=200):
    out, i = [], 0
    while i < len(t):
        out.append(t[i:i + size]); i += size - overlap
    return out or [""]


def _match(query: str) -> str:
    terms = [t for t in re.findall(r"[a-z0-9_]{2,}", (query or "").lower()) if t not in STOP][:12]
    return " OR ".join(f"{t}*" for t in terms)


class Knowledge:
    def __init__(self, db_path: str | None = None):
        os.makedirs(HOME, exist_ok=True)
        os.makedirs(SOURCES, exist_ok=True)
        self.db = sqlite3.connect(db_path or os.path.join(HOME, "knowledge.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA); self.db.commit()

    # ---------------------------------------------------------------- ingest
    def ingest_path(self, path: str, domain: str, repo: str, max_files=800, max_bytes=300_000, max_chunks=8000) -> dict:
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return {"ok": False, "error": f"not a dir: {path}"}
        self.db.execute("DELETE FROM knowledge WHERE repo=?", (repo,))
        nf = nc = 0
        for dp, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            for fn in files:
                if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                    continue
                # skip translated variants like README-ja.md / README.zh-TW.md / index.fr.mdx
                _lm = re.search(r"[.\-_]([a-z]{2})(?:-[a-z]{2})?\.(?:md|mdx|rst)$", fn.lower())
                if _lm and _lm.group(1) in LANGS:
                    continue
                full = os.path.join(dp, fn); rel = os.path.relpath(full, path)
                try:
                    if os.path.getsize(full) > max_bytes:
                        continue
                    txt = open(full, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                for ch in _chunks(txt):
                    if ch.strip():
                        self.db.execute("INSERT INTO knowledge(text,domain,repo,path) VALUES(?,?,?,?)",
                                        (ch, domain, repo, rel))
                        nc += 1
                        if nc >= max_chunks:
                            break
                nf += 1
                if nf >= max_files or nc >= max_chunks:
                    break
            if nf >= max_files or nc >= max_chunks:
                break
        self.db.execute("INSERT INTO sources(repo,url,domain,chunks,files,ingested_at) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(repo) DO UPDATE SET domain=excluded.domain,chunks=excluded.chunks,files=excluded.files,ingested_at=excluded.ingested_at",
                        (repo, "", domain, nc, nf, time.time()))
        self.db.commit()
        try:
            Memory().save([domain, repo, "knowledge"],
                          f"Ingested knowledge repo '{repo}' for domain '{domain}' ({nf} files, {nc} chunks).",
                          {"repo": repo, "domain": domain, "chunks": nc}, category="knowledge")
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "repo": repo, "domain": domain, "files": nf, "chunks": nc}

    def ingest_repo(self, url_or_path: str, domain: str, repo: str | None = None) -> dict:
        if url_or_path.startswith(("http://", "https://", "git@")):
            repo = repo or re.sub(r"\.git$", "", url_or_path.rstrip("/").split("/")[-1])
            dest = os.path.join(SOURCES, repo)
            if not os.path.isdir(os.path.join(dest, ".git")):
                p = subprocess.run(["git", "clone", "--depth", "1", url_or_path, dest],
                                   capture_output=True, text=True, timeout=600)
                if p.returncode != 0:
                    return {"ok": False, "error": "clone failed", "detail": p.stderr[-800:]}
            return self.ingest_path(dest, domain, repo)
        repo = repo or os.path.basename(os.path.abspath(url_or_path))
        return self.ingest_path(url_or_path, domain, repo)

    # ---------------------------------------------------------------- query (FTS5 / BM25)
    def search(self, query: str, domain: str | None = None, k: int = 6) -> list[dict]:
        m = _match(query)
        if not m:
            return []
        sql = ("SELECT domain, repo, path, snippet(knowledge, 0, '', '', ' … ', 18) AS snip, "
               "bm25(knowledge) AS score FROM knowledge WHERE knowledge MATCH ?")
        params = [m]
        if domain:
            sql += " AND domain = ?"; params.append(domain)
        sql += " ORDER BY bm25(knowledge) LIMIT ?"; params.append(k)
        try:
            rows = self.db.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [{"domain": r["domain"], "repo": r["repo"], "path": r["path"],
                 "score": round(-r["score"], 3), "snippet": r["snip"][:400].replace("\n", " ")}
                for r in rows]

    def domains(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT domain, COUNT(*) chunks, COUNT(DISTINCT repo) repos FROM knowledge GROUP BY domain ORDER BY chunks DESC")
        return [dict(r) for r in rows]

    def sources(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT repo,domain,chunks,files FROM sources ORDER BY domain")]

    # ---------------------------------------------------------------- the "perfect context"
    def context_pack(self, keywords, domain: str | None = None, k: int = 5) -> str:
        kws = keywords if isinstance(keywords, list) else re.split(r"[,\s]+", str(keywords))
        try:
            mem = Memory().context(kws)
        except Exception:  # noqa: BLE001
            mem = ""
        hits = self.search(" ".join(kws), domain=domain, k=k)
        parts = ["# Full context pack for: " + ", ".join(kws) + (f"  · domain: {domain}" if domain else "")]
        if mem:
            parts += ["\n## Prior memory (what we've built/decided)", mem]
        if hits:
            parts.append("\n## Domain knowledge (retrieved expertise)")
            for h in hits:
                parts.append(f"- [{h['domain']}/{h['repo']}] {h['path']}: {h['snippet']}")
        if not mem and not hits:
            parts.append("\n(no prior memory or matching knowledge yet — fresh)")
        return "\n".join(parts)
