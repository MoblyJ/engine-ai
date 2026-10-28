"""The single serialized integrator (upgrade.md §5).

Only ONE integrator updates the integration branch, enforced with an OS file lock:
parallel workers may build in parallel, but combining is serialized. Merges happen in
topological order; a conflict BLOCKS with an explicit resolution task (no ours/theirs
auto-picks); the verifier re-runs after every merge because branch-local success is not
proof of combined compatibility.
"""

from __future__ import annotations

import fcntl
import os

from engine_core.workspaces.git import WorkspaceManager, WorkspaceError, _git


class IntegrationBlocked(RuntimeError):
    """A merge conflict — requires an explicit resolution task, not auto-resolution."""

    def __init__(self, message: str, conflicts: list[str]):
        super().__init__(message)
        self.conflicts = conflicts


class Integrator:
    def __init__(self, repo_root: str, verifier=None, integration_branch: str = "engine/integration"):
        self.repo = os.path.abspath(repo_root)
        self.git = WorkspaceManager(repo_root)
        self.verifier = verifier  # callable(workspace) -> {"passed": bool, ...evidence}
        self.integration_branch = integration_branch
        self._lock_path = os.path.join(self.repo, ".git", "engine-integrator.lock")

    # ---------------------------------------------------------------- locking
    def __enter__(self):
        os.makedirs(os.path.dirname(self._lock_path), exist_ok=True)
        self._lock_fd = open(self._lock_path, "w")
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX)  # one integrator at a time, machine-wide
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        self._lock_fd.close()
        return False

    # ---------------------------------------------------------------- merging
    def _ensure_branch(self) -> None:
        code, current, _ = _git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")
        if current != self.integration_branch:
            code, _, err = _git(self.repo, "checkout", "-B", self.integration_branch)
            if code != 0:
                raise WorkspaceError(f"cannot create integration branch: {err}")

    def integrate(self, task_branches: list[str]) -> dict:
        """Merge task branches in order; verify after each. Conflicts raise IntegrationBlocked."""
        self._ensure_branch()
        merged, evidence = [], []
        for branch in task_branches:
            # recheck the base BEFORE each merge: earlier merges may have moved under us
            code, out, err = _git(self.repo, "merge", "--no-ff", "--no-edit", branch)
            if code != 0:
                _git(self.repo, "merge", "--abort")
                conflicts = self._conflicts(err + out)
                raise IntegrationBlocked(
                    f"merge of {branch!r} conflicts — needs an explicit resolution task", conflicts)
            if self.verifier is not None:
                result = self.verifier(self.repo)
                evidence.append({"branch": branch, "verification": result})
                if not result.get("passed"):
                    # the merge already committed — undo it with a revert, keep history.
                    # HEAD is a --no-ff MERGE commit: reverting needs -m 1 (vs mainline).
                    code2, _, revert_err = _git(self.repo, "revert", "--no-edit", "-m", "1", "HEAD")
                    return {"ok": False, "merged": merged, "reverted": branch,
                            "reason": "post-merge verification failed",
                            "revert_ok": code2 == 0, "detail": revert_err[-500:], "evidence": evidence}
            merged.append(branch)
        sha = self.git.current_sha(self.repo)
        return {"ok": True, "merged": merged, "integration_branch": self.integration_branch,
                "head": sha, "evidence": evidence}

    @staticmethod
    def _conflicts(output: str) -> list[str]:
        return [line.strip() for line in output.splitlines()
                if line.startswith(("CONFLICT ", "Auto-merging ")) and "CONFLICT" in line]
