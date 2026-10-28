"""Self-contained backend for the Claude Code integration (no external deps).

This is the local, single-user backend for the Claude Code integration: it gives the MCP
server a repo-aware RAG index (incremental, with source spans), a unified skill registry
(with importers for the three source repos), and an encrypted secrets vault — all in one
SQLite file under ~/.engine-ai. Claude Code itself is the agent loop, so there is no job
queue here.
"""

from __future__ import annotations

import bisect
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time

HOME = os.path.expanduser(os.environ.get("ENGINE_AI_HOME", "~/.engine-ai"))
DIM = 512
TEXT_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".rb", ".md", ".txt",
            ".json", ".toml", ".yaml", ".yml", ".sh", ".c", ".h", ".cpp", ".css", ".html"}
SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", ".venv", "__pycache__", ".next"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS skills(
  name TEXT, version TEXT DEFAULT '1.0.0', format TEXT, source TEXT,
  description TEXT, body TEXT, content_hash TEXT, created_at REAL,
  PRIMARY KEY(name, version));
CREATE TABLE IF NOT EXISTS secrets(
  scope TEXT, name TEXT, token_json TEXT, created_at REAL, PRIMARY KEY(scope, name));
CREATE TABLE IF NOT EXISTS files(
  repo TEXT, path TEXT, hash TEXT, lines INTEGER, updated_at REAL, PRIMARY KEY(repo, path));
CREATE TABLE IF NOT EXISTS rag(
  repo TEXT, path TEXT, chunk INTEGER, start_line INTEGER, end_line INTEGER, text TEXT, vec TEXT);
"""

# ----------------------------------------------------------- crypto (vault)
# v2 (default): AES-256-GCM from the maintained `cryptography` package.
# v1 (legacy, dev-grade SHA-keystream XOR + HMAC): kept ONLY so existing databases can
# still be read; every new write upgrades to v2 whenever `cryptography` is importable.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    _HAS_AESGCM = True
except Exception:  # pragma: no cover - depends on the host env
    _HAS_AESGCM = False


def vault_backend() -> str:
    """Report which AEAD backend the vault uses (for doctor / audits)."""
    if _HAS_AESGCM:
        return "aes-256-gcm"
    return "legacy-sha-keystream+hmac (dev-grade; `pip install cryptography` to upgrade)"


def _master_key() -> bytes:
    env = os.environ.get("ENGINE_AI_MASTER_KEY")
    if env:
        return env.encode()
    os.makedirs(HOME, exist_ok=True)
    p = os.path.join(HOME, "master.key")
    if os.path.exists(p):
        return open(p, "rb").read()
    k = secrets.token_bytes(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, k); os.close(fd)
    return k


def _derive(mk: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(mk, salt=salt, n=2**14, r=8, p=1, dklen=32)


def _ks(key, nonce, n):
    out = bytearray(); c = 0
    while len(out) < n:
        out += hashlib.sha256(key + nonce + c.to_bytes(4, "big")).digest(); c += 1
    return bytes(out[:n])


def _enc(pt: bytes) -> dict:
    mk = _master_key(); salt = secrets.token_bytes(16)
    key = _derive(mk, salt)
    if _HAS_AESGCM:
        nonce = secrets.token_bytes(12)  # GCM standard nonce size
        ct = AESGCM(key).encrypt(nonce, pt, None)  # appends the 16-byte tag
        return {"v": 2, "alg": "aes-256-gcm", "salt": salt.hex(), "nonce": nonce.hex(), "ct": ct.hex()}
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(pt, _ks(key, nonce, len(pt))))
    tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()
    return {"v": 1, "alg": "legacy-sha-keystream+hmac", "salt": salt.hex(),
            "nonce": nonce.hex(), "ct": ct.hex(), "tag": tag.hex()}


def _dec(tok: dict) -> bytes:
    mk = _master_key()
    salt = bytes.fromhex(tok["salt"])
    key = _derive(mk, salt)
    if tok.get("v") == 2 or tok.get("alg") == "aes-256-gcm":
        return AESGCM(key).decrypt(bytes.fromhex(tok["nonce"]), bytes.fromhex(tok["ct"]), None)
    nonce = bytes.fromhex(tok["nonce"]); ct = bytes.fromhex(tok["ct"])
    if not hmac.compare_digest(hmac.new(key, nonce + ct, hashlib.sha256).digest(), bytes.fromhex(tok["tag"])):
        raise ValueError("vault: auth failed")
    return bytes(a ^ b for a, b in zip(ct, _ks(key, nonce, len(ct))))


# ----------------------------------------------------------- embeddings (hashing trick)
def embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,}", text.lower()):
        v[int.from_bytes(hashlib.md5(t.encode()).digest()[:4], "big") % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


# ----------------------------------------------------------- frontmatter parser
def parse_frontmatter(text: str):
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm, raw, body = {}, m.group(1), m.group(2)
    lines = raw.split("\n"); i = 0
    while i < len(lines):
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
        if not km:
            i += 1; continue
        k, val = km.group(1), km.group(2).strip()
        if val in ("|", "|-", ">", ">-"):
            blk = []; i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                blk.append(lines[i].lstrip()); i += 1
            fm[k] = "\n".join(blk).strip(); continue
        fm[k] = val.strip("'\""); i += 1
    return fm, body.strip()


class Engine:
    def __init__(self, db_path: str | None = None):
        os.makedirs(HOME, exist_ok=True)
        self.db = sqlite3.connect(db_path or os.path.join(HOME, "forge.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self):
        """Drop pre-v0.16 rag tables that lack the span columns (the index is a
        disposable cache — it is rebuilt incrementally on the next index_repo)."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(rag)")}
        if cols and "start_line" not in cols:
            self.db.execute("DROP TABLE rag")
            self.db.execute("CREATE TABLE rag(repo TEXT, path TEXT, chunk INTEGER, "
                            "start_line INTEGER, end_line INTEGER, text TEXT, vec TEXT)")

    # ---- skills ----
    def register_skill(self, name, description, body, *, fmt="native", source="bundled", version="1.0.0"):
        if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", name or ""):
            return False, f"bad name {name!r}"
        if not description.strip() or not body.strip():
            return False, "empty description/body"
        h = hashlib.sha256((name + body).encode()).hexdigest()[:16]
        self.db.execute("INSERT INTO skills VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(name,version) DO UPDATE SET description=excluded.description, body=excluded.body, "
                        "format=excluded.format, source=excluded.source",
                        (name, version, fmt, source, description.strip(), body, h, time.time()))
        self.db.commit(); return True, ""

    def list_skills(self, query: str = ""):
        if query:
            q = f"%{query.lower()}%"
            rows = self.db.execute("SELECT name,version,format,source,description FROM skills "
                                   "WHERE lower(name) LIKE ? OR lower(description) LIKE ? ORDER BY name", (q, q)).fetchall()
        else:
            rows = self.db.execute("SELECT name,version,format,source,description FROM skills ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_skill(self, name):
        r = self.db.execute("SELECT * FROM skills WHERE name=? ORDER BY version DESC LIMIT 1", (name,)).fetchone()
        return dict(r) if r else None

    def import_repo_skills(self, repo_root: str):
        """Auto-detect the three layouts and ingest SKILL.md files."""
        plans = [("skills", "agent-skills"), ("", "gstack"), (".omp/skills", "omp")]
        imported = 0
        for sub, fmt in plans:
            base = os.path.join(repo_root, sub) if sub else repo_root
            if not os.path.isdir(base):
                continue
            for entry in sorted(os.listdir(base)):
                f = os.path.join(base, entry, "SKILL.md")
                if not os.path.isfile(f):
                    continue
                try:
                    fm, body = parse_frontmatter(open(f, encoding="utf-8").read())
                    desc = fm.get("description", "")
                    if isinstance(desc, list):
                        desc = " ".join(desc)
                    ok, _ = self.register_skill(fm.get("name", entry), desc, body or "(empty)",
                                                fmt=fmt, source=os.path.basename(repo_root))
                    imported += int(ok)
                except Exception:  # noqa: BLE001
                    pass
        return imported

    # ---- secrets ----
    def set_secret(self, name, value, scope="global"):
        self.db.execute("INSERT INTO secrets VALUES(?,?,?,?) ON CONFLICT(scope,name) DO UPDATE SET token_json=excluded.token_json",
                        (scope, name, json.dumps(_enc(value.encode())), time.time()))
        self.db.commit()

    def get_secret(self, name, scope="global"):
        r = self.db.execute("SELECT token_json FROM secrets WHERE scope=? AND name=?", (scope, name)).fetchone()
        return _dec(json.loads(r["token_json"])).decode() if r else None

    def list_secrets(self, scope="global"):
        return [r["name"] for r in self.db.execute("SELECT name FROM secrets WHERE scope=? ORDER BY name", (scope,))]

    # ---- rag (incremental: only re-embed files whose content hash changed) ----
    @staticmethod
    def _file_chunks(txt: str):
        """Yield (chunk_index, char_offset, start_line, end_line, text)."""
        nl = [i for i, c in enumerate(txt) if c == "\n"]
        line_at = lambda off: bisect.bisect_left(nl, off) + 1  # noqa: E731
        for ci in range(0, max(1, len(txt)), 1300):
            chunk = txt[ci:ci + 1500]
            yield ci // 1300, ci, line_at(ci), line_at(min(ci + len(chunk), len(txt))), chunk

    def index_repo(self, repo: str, max_files=4000):
        repo = os.path.abspath(repo)
        existing = {r["path"]: r["hash"] for r in self.db.execute("SELECT path,hash FROM files WHERE repo=?", (repo,))}
        on_disk: dict[str, str] = {}
        added = updated = removed = 0
        for dp, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in files:
                if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                    continue
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, repo)
                try:
                    if os.path.getsize(full) > 1_000_000:
                        continue
                    raw = open(full, "rb").read()
                except OSError:
                    continue
                h = hashlib.sha1(raw).hexdigest()
                on_disk[rel] = h
                if existing.get(rel) == h:
                    continue  # unchanged since the last index — skip re-embedding
                txt = raw.decode("utf-8", errors="ignore")
                self.db.execute("DELETE FROM rag WHERE repo=? AND path=?", (repo, rel))
                for idx, _off, sl, el, chunk in self._file_chunks(txt):
                    self.db.execute("INSERT INTO rag VALUES(?,?,?,?,?,?,?)",
                                    (repo, rel, idx, sl, el, chunk, json.dumps(embed(chunk))))
                self.db.execute("INSERT INTO files(repo,path,hash,lines,updated_at) VALUES(?,?,?,?,?) "
                                "ON CONFLICT(repo,path) DO UPDATE SET hash=excluded.hash, lines=excluded.lines, "
                                "updated_at=excluded.updated_at",
                                (repo, rel, h, txt.count("\n") + 1, time.time()))
                if rel in existing:
                    updated += 1
                else:
                    added += 1
                if len(on_disk) >= max_files:
                    break
            if len(on_disk) >= max_files:
                break
        for rel in set(existing) - set(on_disk):  # files deleted since the last index
            self.db.execute("DELETE FROM rag WHERE repo=? AND path=?", (repo, rel))
            self.db.execute("DELETE FROM files WHERE repo=? AND path=?", (repo, rel))
            removed += 1
        self.db.commit()
        nf = self.db.execute("SELECT COUNT(DISTINCT path) c FROM rag WHERE repo=?", (repo,)).fetchone()["c"]
        nc = self.db.execute("SELECT COUNT(*) c FROM rag WHERE repo=?", (repo,)).fetchone()["c"]
        return {"files": nf, "chunks": nc, "added": added, "updated": updated,
                "removed": removed, "unchanged": len(on_disk) - added - updated}

    def search_repo(self, repo: str, query: str, k=5):
        repo = os.path.abspath(repo)
        qv = embed(query); scored = []
        for r in self.db.execute(
                "SELECT path,chunk,start_line,end_line,text,vec FROM rag WHERE repo=?", (repo,)):
            s = _cos(qv, json.loads(r["vec"]))
            if s > 0:
                scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"path": r["path"], "score": round(s, 4), "start_line": r["start_line"],
                 "end_line": r["end_line"], "snippet": r["text"][:400].replace("\n", " ")}
                for s, r in scored[:k]]
