"""Git task worktrees (upgrade.md §5).

Task branches/worktrees live in the REAL project repository (not a central scratch repo).
Every subprocess result is CHECKED — a failed worktree add is an explicit isolation
failure, never a silent plain-folder fallback. Worktrees are not security boundaries;
constrained execution is the policy layer's job (tools/policy.py).
"""

from __future__ import annotations

import os
import subprocess


def _git(cwd: str, *args: str, timeout: int = 60) -> tuple[int, str, str]:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


class WorkspaceError(RuntimeError):
    pass


class WorkspaceManager:
    def __init__(self, repo_root: str, worktrees_dir: str | None = None):
        self.repo = os.path.abspath(repo_root)
        # default: sibling of the repo, namespaced BY repo so two repos never collide
        self.worktrees_dir = os.path.abspath(worktrees_dir or os.path.join(
            os.path.dirname(self.repo), ".engine-worktrees", os.path.basename(self.repo)))
        code, _, err = _git(self.repo, "rev-parse", "--is-inside-work-tree", timeout=20)
        if code != 0:
            raise WorkspaceError(f"{self.repo} is not a git repository: {err}")

    # ---------------------------------------------------------------- lifecycle
    def create(self, task_id: str, base: str | None = None) -> dict:
        """Create an isolated worktree+branch for a task. Returns path/branch/base_sha."""
        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in task_id).strip("-")[:60]
        branch = f"engine/{slug}"
        path = os.path.join(self.worktrees_dir, slug)
        if os.path.exists(path):
            raise WorkspaceError(f"worktree path already exists: {path}")
        base_ref = base or "HEAD"
        code, sha, err = _git(self.repo, "rev-parse", base_ref)
        if code != 0:
            raise WorkspaceError(f"cannot resolve base {base_ref!r}: {err}")
        os.makedirs(self.worktrees_dir, exist_ok=True)
        code, out, err = _git(self.repo, "worktree", "add", "-b", branch, path, base_ref)
        if code != 0:
            raise WorkspaceError(f"git worktree add failed: {err}")
        # validate what git actually created (never trust the request)
        code, actual, err = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        if code != 0 or actual != branch:
            self.remove(path)
            raise WorkspaceError(f"worktree branch validation failed (got {actual!r}, wanted {branch!r})")
        return {"task_id": task_id, "path": path, "branch": branch, "base_sha": sha}

    def remove(self, path: str, force: bool = True) -> None:
        _git(self.repo, "worktree", "remove", "--force" if force else "", path)
        _git(self.repo, "worktree", "prune")

    def list(self) -> list[dict]:
        code, out, _ = _git(self.repo, "worktree", "list", "--porcelain")
        if code != 0:
            return []
        entries, current = [], {}
        for line in out.splitlines():
            if line.startswith("worktree "):
                if current:
                    entries.append(current)
                current = {"path": line.split(" ", 1)[1]}
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
        if current:
            entries.append(current)
        return entries

    # ---------------------------------------------------------------- inspection
    def current_sha(self, path: str) -> str:
        code, sha, err = _git(path, "rev-parse", "HEAD")
        if code != 0:
            raise WorkspaceError(err)
        return sha

    def changed_files(self, path: str, base_sha: str | None = None) -> list[str]:
        if base_sha:
            code, out, err = _git(path, "diff", "--name-only", base_sha)
        else:
            code, out, err = _git(path, "status", "--porcelain")
            out = "\n".join(line[3:] for line in out.splitlines())
        if code != 0:
            raise WorkspaceError(err)
        return [f for f in out.splitlines() if f]

    def commit_all(self, path: str, message: str) -> str:
        """Explicit staging + checked commit; returns the new SHA."""
        for args in (["add", "-A"], ["commit", "-m", message]):
            code, out, err = _git(path, *args)
            if code != 0 and "nothing to commit" not in (out + err):
                raise WorkspaceError(f"git {args[0]} failed: {err or out}")
        return self.current_sha(path)
