"""App sessions — one git WORKTREE per app, resumable with its folder + memory.

Each `/new-app` gets a real git worktree (hard isolation: own branch + own working dir) plus a
session record. `/resume-app` lists sessions with a 2-line summary and reopens one — restoring its
folder path and its project-scoped memory context.

v0.16 (per the upgrade review):
- The branch recorded is the ACTUAL branch git created (validated with rev-parse), never
  the branch we asked for — the old fallback could record a name that doesn't exist.
- Path containment uses realpath + commonpath (no `startswith` prefix tricks).
- The session records the base SHA it branched from, and drift (branch/folder changed
  since) is reported explicitly on resume instead of silently trusted.

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
  base_sha TEXT, created_at REAL, last_opened REAL);
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


def _head_sha(cwd: str) -> str | None:
    code, out, _ = _run(["git", "rev-parse", "HEAD"], cwd=cwd)
    return out if code == 0 else None


def _actual_branch(cwd: str) -> str | None:
    code, out, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    return out if code == 0 else None


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


def _contains(parent: str, child: str) -> bool:
    """True if `child` is inside `parent` — realpath + commonpath, separator-safe."""
    try:
        return os.path.commonpath([os.path.realpath(parent), os.path.realpath(child)]) == \
            os.path.realpath(parent)
    except ValueError:  # different drives / mixing absolute and relative
        return False


class Sessions:
    def __init__(self, db_path: str | None = None):
        os.makedirs(HOME, exist_ok=True)
        self.db = sqlite3.connect(db_path or os.path.join(HOME, "sessions.db"),
                                  check_same_thread=False, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")   # concurrent readers must not
        self.db.execute("PRAGMA busy_timeout=5000")  # hit "database is locked"
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self):
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(sessions)")}
        if cols and "base_sha" not in cols:
            self.db.execute("ALTER TABLE sessions ADD COLUMN base_sha TEXT")

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
        warnings: list[str] = []
        base_sha = None
        if _ensure_workspace():
            base_sha = _head_sha(WORKSPACE)
            code, _, err = _run(["git", "worktree", "add", abs_path, "-b", branch], cwd=WORKSPACE)
            if code != 0:  # fall back to a standalone git repo
                mode = "gitrepo"
                os.makedirs(abs_path, exist_ok=True)
                short_branch = branch.replace("app/", "")
                _run(["git", "init", "-b", short_branch], cwd=abs_path)
            # record the branch git ACTUALLY created — never trust the requested name
            actual = _actual_branch(abs_path)
            if actual:
                if actual != branch:
                    warnings.append(f"git created branch {actual!r} instead of {branch!r}")
                    branch = actual
            else:
                warnings.append("could not validate the branch (not a git repo?)")
        else:  # no git at all
            mode = "plain"; os.makedirs(abs_path, exist_ok=True)
            warnings.append("git not available — plain folder, NO isolation")
        kw = keywords or [slug, *_slug(name).split("-")]
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO sessions(slug,name,path,branch,mode,keywords,summary,base_sha,created_at,last_opened) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (slug, name, abs_path, branch, mode, json.dumps(list(dict.fromkeys(kw))), "",
             base_sha, now, now))
        self.db.commit()
        out = {"ok": True, "id": cur.lastrowid, "slug": slug, "path": abs_path,
               "branch": branch, "mode": mode, "keywords": kw, "base_sha": base_sha}
        if warnings:
            out["warnings"] = warnings
        return out

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

    def find(self, path: str) -> dict:
        """Which app session does this working dir belong to? (longest matching worktree path.)"""
        target = os.path.abspath(path or ".")
        best = None
        for r in self.db.execute("SELECT * FROM sessions"):
            if _contains(r["path"], target):
                if best is None or len(r["path"]) > len(best["path"]):
                    best = r
        if not best:
            return {"ok": False, "error": "not inside a known app session", "path": target}
        return {"ok": True, "id": best["id"], "slug": best["slug"], "name": best["name"],
                "path": best["path"], "branch": best["branch"], "mode": best["mode"],
                "keywords": json.loads(best["keywords"]), "summary": best["summary"]}

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
        out = {"ok": True, "id": r["id"], "slug": r["slug"], "name": r["name"], "path": r["path"],
               "branch": r["branch"], "mode": r["mode"], "keywords": kw, "summary": r["summary"],
               "exists": os.path.isdir(r["path"]), "memory_context": context}
        # explicit drift check: report what actually is on disk, don't trust the record
        if out["exists"]:
            actual = _actual_branch(r["path"])
            if actual is None:
                out["drift"] = "folder exists but is not a git repo anymore"
            elif actual != r["branch"]:
                out["drift"] = f"branch is now {actual!r} (session recorded {r['branch']!r})"
        else:
            out["drift"] = "folder missing"
        return out
