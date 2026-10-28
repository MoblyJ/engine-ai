"""Execution policy (upgrade.md §4-5).

`read_only` from a profile's config is ENFORCED here — a reviewer profile physically
cannot call a writing tool even if the model asks. Path arguments are contained to the
workspace; worktrees are isolation, not security boundaries, so this is the gate.
"""

from __future__ import annotations

import os


class PolicyViolation(PermissionError):
    pass


class Policy:
    def __init__(self, *, read_only: bool = False, allowed_tools: set[str] | None = None,
                 permitted_paths: list[str] | None = None, root: str | None = None):
        self.read_only = read_only
        self.allowed_tools = allowed_tools
        self.permitted_paths = [os.path.abspath(p) for p in (permitted_paths or [])]
        self.root = os.path.abspath(root) if root else None

    # ---------------------------------------------------------------- checks
    def allows(self, tool: str, registry) -> bool:
        """Whole-tool gate (used to filter the tool schema list shown to the model)."""
        if self.read_only:
            try:
                if not registry.get(tool).read_only:
                    return False
            except KeyError:
                return False
        if self.allowed_tools is not None and tool not in self.allowed_tools:
            return False
        return True

    def check(self, tool: str, args: dict, registry) -> None:
        """Raise PolicyViolation unless this call is permitted."""
        if not self.allows(tool, registry):
            reason = "read-only profile" if self.read_only else "not in the profile's allowed tools"
            raise PolicyViolation(f"tool {tool!r} blocked: {reason}")
        for key, val in args.items():
            if isinstance(val, str) and key in ("path", "root", "cwd", "file", "target"):
                self._check_path(val)

    def _check_path(self, p: str) -> None:
        # relative paths are workspace-relative (a worker saying `src/x.py` means
        # `<workspace>/src/x.py`, not whatever cwd the host process happens to have)
        if self.root and not os.path.isabs(p):
            abs_p = os.path.abspath(os.path.join(self.root, p))
        else:
            abs_p = os.path.abspath(p)
        real_p = os.path.realpath(abs_p)
        scopes = [self.root] if self.root else []
        scopes += self.permitted_paths
        if scopes:
            for scope in scopes:
                try:
                    if os.path.commonpath([scope, real_p]) == os.path.realpath(scope):
                        return
                except ValueError:
                    continue
            raise PolicyViolation(f"path {p!r} is outside the permitted workspace")
        # deny credential stores outright even without a workspace scope
        if "/.engine-ai/" in real_p and "master.key" in real_p:
            raise PolicyViolation("credential store access is denied")
