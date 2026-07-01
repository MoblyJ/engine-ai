"""App sessions — one git WORKTREE per app, resumable with its folder + memory.

Each `/new-app` gets a real git worktree (hard isolation: own branch + own working dir) plus a
session record. `/resume-app` lists sessions with a 2-line summary and reopens one — restoring its
folder path and its evolving memory context.

Central bookkeeping repo: ~/.engine-ai/workspace (worktrees branch from it).
Default app location:      ~/.engine-ai/apps/<slug>   (override with an explicit path).
Session index:             ~/.engine-ai/sessions.db
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time

from engine import HOME
from memory import Memory

WORKSPACE = os.path.join(HOME, "workspace")
APPS_DIR = os.path.join(HOME, "apps")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE, name TEXT, path TEXT, branch TEXT, mode TEXT DEFAULT 'worktree',
  keywords TEXT NOT NULL DEFAULT '[]', summary TEXT DEFAULT '',
  created_at REAL, last_opened REAL);
"""


def _run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "app").lower()).strip("-")
    return s[:40] or "app"


def _rel_time(ts: float | None) -> str:
    if not ts:
        return "never"
    d = max(0, int(time.time() - ts))
    for n, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if d >= n:
            return f"{d // n}{unit} ago"
    return "just now"


def _ensure_workspace() -> bool:
    """Create the central git repo (with one commit so worktrees can branch). Returns git-available."""
    if not shutil.which("git"):
        return False
    if not os.path.isdir(os.path.join(WORKSPACE, ".git")):
        os.makedirs(WORKSPACE, exist_ok=True)
        _run(["git", "init", "-b", "main"], cwd=WORKSPACE)
        _run(["git", "config", "user.email", "engine@engine-ai.local"], cwd=WORKSPACE)
        _run(["git", "config", "user.name", "engine-ai"], cwd=WORKSPACE)
        with open(os.path.join(WORKSPACE, ".engine-ai-workspace"), "w") as f:
            f.write("engine-ai app worktrees branch from here.\n")
        _run(["git", "add", "-A"], cwd=WORKSPACE)
        _run(["git", "commit", "-m", "init engine-ai workspace"], cwd=WORKSPACE)
    return True


class Sessions:
    def __init__(self, db_path: str | None = None):
        os.makedirs(HOME, exist_ok=True)
        self.db = sqlite3.connect(db_path or os.path.join(HOME, "sessions.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA); self.db.commit()

    def _unique_slug(self, base: str) -> str:
        slug, i = base, 2
        while self.db.execute("SELECT 1 FROM sessions WHERE slug=?", (slug,)).fetchone():
            slug = f"{base}-{i}"; i += 1
        return slug

    def create(self, name: str, keywords=None, path: str | None = None) -> dict:
        slug = self._unique_slug(_slug(name))
        abs_path = os.path.abspath(path or os.path.join(APPS_DIR, slug))
        if os.path.exists(abs_path) and os.listdir(abs_path):
            return {"ok": False, "error": f"path not empty: {abs_path}"}
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        branch = f"app/{slug}"
        mode = "worktree"
        if _ensure_workspace():
            code, _, err = _run(["git", "worktree", "add", abs_path, "-b", branch], cwd=WORKSPACE)
            if code != 0:  # fall back to a standalone git repo
                mode = "gitrepo"
                os.makedirs(abs_path, exist_ok=True)
                _run(["git", "init", "-b", branch.replace("app/", "")], cwd=abs_path)
        else:  # no git at all
            mode = "plain"; os.makedirs(abs_path, exist_ok=True)
        kw = keywords or [slug, *_slug(name).split("-")]
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO sessions(slug,name,path,branch,mode,keywords,created_at,last_opened) VALUES(?,?,?,?,?,?,?,?)",
            (slug, name, abs_path, branch, mode, json.dumps(list(dict.fromkeys(kw))), now, now))
        self.db.commit()
        return {"ok": True, "id": cur.lastrowid, "slug": slug, "path": abs_path,
                "branch": branch, "mode": mode, "keywords": kw}

    def update(self, session_id: int, summary: str | None = None, keywords=None) -> dict:
        if summary is not None:
            self.db.execute("UPDATE sessions SET summary=? WHERE id=?", (summary.strip()[:400], session_id))
        if keywords is not None:
            self.db.execute("UPDATE sessions SET keywords=? WHERE id=?",
                            (json.dumps(list(dict.fromkeys(keywords))), session_id))
        self.db.commit()
        return {"ok": True, "id": session_id}

    def list(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM sessions ORDER BY last_opened DESC"):
            kw = json.loads(r["keywords"])
            exists = os.path.isdir(r["path"])
            line1 = f"#{r['id']} · {r['name']} · opened {_rel_time(r['last_opened'])}{'' if exists else ' · ⚠ folder missing'}"
            line2 = (r["summary"] or ("keywords: " + ", ".join(kw)))[:120]
            out.append({"id": r["id"], "slug": r["slug"], "name": r["name"], "path": r["path"],
                        "branch": r["branch"], "mode": r["mode"], "keywords": kw,
                        "exists": exists, "line1": line1, "line2": line2})
        return out

    def resume(self, key) -> dict:
        r = None
        if str(key).isdigit():
            r = self.db.execute("SELECT * FROM sessions WHERE id=?", (int(key),)).fetchone()
        if not r:
            r = self.db.execute("SELECT * FROM sessions WHERE slug=?", (str(key),)).fetchone()
        if not r:
            return {"ok": False, "error": f"no session '{key}'"}
        self.db.execute("UPDATE sessions SET last_opened=? WHERE id=?", (time.time(), r["id"]))
        self.db.commit()
        kw = json.loads(r["keywords"])
        try:
            context = Memory().context(kw)
        except Exception:  # noqa: BLE001
            context = ""
        return {"ok": True, "id": r["id"], "slug": r["slug"], "name": r["name"], "path": r["path"],
                "branch": r["branch"], "mode": r["mode"], "keywords": kw, "summary": r["summary"],
                "exists": os.path.isdir(r["path"]), "memory_context": context}
