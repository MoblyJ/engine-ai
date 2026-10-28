"""GitHub + Vercel + mobile-responsiveness integrations for the MCP server.

- git_publish    : create a GitHub repo and push (uses the `gh` CLI; needs `gh auth login`)
- vercel_deploy  : deploy to Vercel and return the live URL (uses `vercel` CLI / token)
- responsive_audit: static mobile-responsiveness check of a frontend (no deps)

Auth note: GitHub/Vercel require their OWN credentials — there is no way to reuse the
Google account connected to Claude. `git_publish`/`vercel_deploy` detect a missing login
and return a clear `needs_auth` payload with the exact command to run, instead of failing.

v0.16 (per the upgrade review): git_publish is git-worktree-aware (a worktree has a `.git`
FILE, not a directory, and shares its config with the parent repo), every subprocess
result is checked, staging/commit failures are reported instead of swallowed, and an
existing `origin` remote is reused (push) instead of always attempting repo creation —
so publishing two apps never clobbers the first one's remote.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess


def _run(cmd, cwd=None, timeout=300, env=None):
    # stdin=DEVNULL so a CLI that waits for input fails fast instead of hanging.
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})})
    return p.returncode, p.stdout.strip(), p.stderr.strip()


# ----------------------------------------------------------------- git helpers
def git_state(path: str) -> dict:
    """Describe `path` as a git working tree. Works for normal repos AND worktrees:
    inside a worktree, --git-dir points at <main>/.git/worktrees/<name> while
    --git-common-dir points at the main .git — they differ only in worktrees."""
    code, out, err = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path, timeout=20)
    if code != 0:
        return {"is_repo": False, "is_worktree": False, "top": None}
    code, top, _ = _run(["git", "rev-parse", "--show-toplevel"], cwd=path, timeout=20)
    _, gitdir, _ = _run(["git", "rev-parse", "--git-dir"], cwd=path, timeout=20)
    _, commondir, _ = _run(["git", "rev-parse", "--git-common-dir"], cwd=path, timeout=20)

    def _abs(p):
        return os.path.normpath(p if p and os.path.isabs(p) else os.path.join(path, p or ""))
    gitdir_abs, commondir_abs = _abs(gitdir), _abs(commondir)
    is_worktree = bool(gitdir and commondir and gitdir_abs != commondir_abs)
    return {"is_repo": True, "is_worktree": is_worktree, "top": top or path,
            "git_dir": gitdir_abs, "common_dir": commondir_abs}


def origin_url(path: str) -> str | None:
    code, out, _ = _run(["git", "remote", "get-url", "origin"], cwd=path, timeout=20)
    return out if code == 0 and out else None


# ----------------------------------------------------------------- GitHub
def gh_status() -> dict:
    if not shutil.which("gh"):
        return {"ok": False, "needs": "gh", "message": "GitHub CLI not installed.",
                "fix": "install gh, then run: gh auth login"}
    code, _, err = _run(["gh", "auth", "status"], timeout=20)
    if code != 0:
        return {"ok": False, "needs_auth": True, "message": "Not logged in to GitHub.",
                "fix": "run:  gh auth login   (choose GitHub.com → HTTPS → login with a browser)"}
    return {"ok": True, "message": "gh authenticated"}


def git_publish(path: str, repo_name: str, private: bool = True) -> dict:
    """Create a GitHub repo named `repo_name` from `path` and push it.

    Idempotent: if `origin` already points at a remote, this pushes to it instead of
    creating a new repo (so re-publishing or publishing from a worktree never steals
    the app's existing remote).
    """
    st = gh_status()
    if not st.get("ok"):
        return st
    if not re.match(r"^[A-Za-z0-9._-]+$", repo_name or ""):
        return {"ok": False, "message": f"invalid repo name {repo_name!r}"}
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return {"ok": False, "message": f"not a directory: {path}"}

    gs = git_state(path)
    if not gs["is_repo"]:
        code, _, err = _run(["git", "init", "-b", "main"], cwd=path)
        if code != 0:
            return {"ok": False, "message": "git init failed", "detail": err}
    # local identity fallback so the commit doesn't fail on a fresh box
    if _run(["git", "config", "user.email"], cwd=path, timeout=20)[1] == "":
        _run(["git", "config", "user.email", "agent@engine-ai.local"], cwd=path)
        _run(["git", "config", "user.name", "engine-ai"], cwd=path)

    # explicit staging — a failure here must not be swallowed into a false success
    code, out, err = _run(["git", "add", "-A"], cwd=path)
    if code != 0:
        return {"ok": False, "message": "git add failed", "detail": err or out}
    code, _, _ = _run(["git", "diff", "--cached", "--quiet"], cwd=path, timeout=20)
    if code != 0:  # something staged → commit it
        code, out, err = _run(["git", "commit", "-m", "initial commit (engine-ai)"], cwd=path)
        if code != 0:
            return {"ok": False, "message": "git commit failed", "detail": err or out}

    existing_origin = origin_url(path)
    if existing_origin:
        # already connected: push, don't re-create
        code, out, err = _run(["git", "push", "-u", "origin", "HEAD"], cwd=path, timeout=180)
        if code != 0:
            return {"ok": False, "message": "git push to existing origin failed",
                    "detail": err or out, "url": existing_origin}
        return {"ok": True, "repo": repo_name, "url": existing_origin, "private": private,
                "reused_remote": True, "worktree": gs["is_worktree"]}

    vis = "--private" if private else "--public"
    code, out, err = _run(["gh", "repo", "create", repo_name, "--source", path,
                           "--push", vis], cwd=path, timeout=180)
    if code != 0 and re.search(r"(?i)already exists", err + out):
        # repo exists on GitHub but isn't our remote yet → attach and push
        owner, _, oerr = _run(["gh", "api", "user", "-q", ".login"], cwd=path, timeout=30)
        if owner:
            full = f"{owner}/{repo_name}"
            code2, _, err2 = _run(["git", "remote", "add", "origin",
                                   f"https://github.com/{full}.git"], cwd=path, timeout=20)
            if code2 == 0:
                code2, _, err2 = _run(["git", "push", "-u", "origin", "HEAD"], cwd=path, timeout=180)
            if code2 == 0:
                return {"ok": True, "repo": repo_name, "url": f"https://github.com/{full}",
                        "private": private, "attached_existing": True, "worktree": gs["is_worktree"]}
        return {"ok": False, "message": "repo already exists on GitHub and could not be attached",
                "detail": err or out}
    if code != 0:
        return {"ok": False, "message": "gh repo create failed", "detail": err or out,
                "hint": "repo name may already exist, or token lacks 'repo' scope"}
    url = None
    m = re.search(r"https://github\.com/[^\s]+", out + "\n" + err)
    if m:
        url = m.group(0)
    if not url:
        code2, out2, _ = _run(["gh", "repo", "view", repo_name, "--json", "url", "-q", ".url"], cwd=path)
        url = out2 if code2 == 0 else f"(created '{repo_name}')"
    return {"ok": True, "repo": repo_name, "url": url, "private": private,
            "worktree": gs["is_worktree"]}


# ----------------------------------------------------------------- Vercel
def vercel_status() -> dict:
    if not shutil.which("vercel") and not shutil.which("npx"):
        return {"ok": False, "needs": "vercel", "message": "Vercel CLI not installed.",
                "fix": "npm i -g vercel  (then: vercel login)"}
    token = os.environ.get("VERCEL_TOKEN")
    if token:
        return {"ok": True, "auth": "token"}
    needs = {"ok": False, "needs_auth": True, "message": "Not logged in to Vercel.",
             "fix": "run:  vercel login   (or set VERCEL_TOKEN / store it with set_secret)"}
    try:
        code, out, _ = _run((["vercel"] if shutil.which("vercel") else ["npx", "vercel"]) + ["whoami"], timeout=20)
    except subprocess.TimeoutExpired:
        return needs
    if code != 0:
        return needs
    return {"ok": True, "auth": "cli", "user": out}


def vercel_deploy(path: str, prod: bool = False, token: str | None = None) -> dict:
    """Deploy `path` to Vercel; return the live URL. Best for static/Next/frontend apps."""
    base = ["vercel"] if shutil.which("vercel") else ["npx", "vercel"]
    token = token or os.environ.get("VERCEL_TOKEN")
    st = vercel_status()
    if not st.get("ok") and not token:
        return st
    path = os.path.abspath(path)
    cmd = base + ["deploy", "--yes", "--cwd", path]
    if prod:
        cmd.append("--prod")
    if token:
        cmd += ["--token", token]
    code, out, err = _run(cmd, timeout=600)
    if code != 0:
        return {"ok": False, "message": "vercel deploy failed", "detail": (err or out)[-1500:],
                "hint": "raw Node servers need a serverless adapter; static/Next apps deploy as-is"}
    m = re.search(r"https://[^\s]+\.vercel\.app", out + "\n" + err)
    return {"ok": True, "url": m.group(0) if m else out.splitlines()[-1] if out else None,
            "prod": prod}


# ----------------------------------------------------------------- mobile responsiveness
_FRONTEND_EXT = {".html", ".htm", ".css", ".scss", ".jsx", ".tsx", ".vue", ".svelte"}
_SKIP = {".git", "node_modules", "dist", "build", ".next", ".venv", "__pycache__"}


def responsive_audit(path: str) -> dict:
    """Static mobile-responsiveness audit of a frontend project.

    Static hints only — it can say "looks responsive", never "verified on a device".
    Pair it with a real browser journey before claiming mobile-readiness.
    """
    path = os.path.abspath(path)
    html, css = [], []
    for dp, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in _FRONTEND_EXT:
                continue
            try:
                txt = open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if ext in (".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte"):
                html.append((f, txt))
            css.append((f, txt))
    if not css:
        return {"applicable": False, "message": "no frontend files found — responsiveness audit N/A"}

    blob_html = "\n".join(t for _, t in html)
    blob_all = "\n".join(t for _, t in css)
    checks = {
        "viewport_meta": bool(re.search(r'<meta[^>]+name=["\']viewport["\'][^>]*width=device-width', blob_html, re.I))
                         or bool(re.search(r"viewport", blob_html, re.I)) and "<meta" not in blob_html.lower(),
        "media_queries": "@media" in blob_all,
        "responsive_units": bool(re.search(r":\s*\d+(\.\d+)?(vw|vh|rem|em|%)\b", blob_all)),
        "flex_or_grid": bool(re.search(r"display\s*:\s*(flex|grid)", blob_all)),
        "max_width_usage": "max-width" in blob_all,
        "responsive_images": ("srcset" in blob_html) or bool(re.search(r"img[^{]*\{[^}]*max-width\s*:\s*100%", blob_all, re.I)),
    }
    findings = []
    if re.search(r"(?<!-)\bwidth\s*:\s*\d{3,}px", blob_all):  # ignore max-width/min-width breakpoints
        findings.append("Fixed large px widths found (e.g. `width: 1200px`) — use %/max-width for fluid layout.")
    if not checks["viewport_meta"]:
        findings.append('Missing `<meta name="viewport" content="width=device-width, initial-scale=1">`.')
    if not checks["media_queries"]:
        findings.append("No `@media` queries — add breakpoints (e.g. max-width: 640px) for small screens.")
    if not checks["responsive_units"]:
        findings.append("No responsive units (%, vw, rem, em) — px-only layouts don't scale on mobile.")
    if not checks["responsive_images"] and "<img" in blob_html.lower():
        findings.append("Images lack `srcset`/`max-width:100%` — they may overflow on small screens.")
    score = round(100 * sum(checks.values()) / len(checks))
    return {"applicable": True, "score": score, "checks": checks, "findings": findings,
            "verdict": "mobile-ready" if score >= 80 and not findings else "needs work",
            "basis": "static analysis — not a browser-verified journey"}
