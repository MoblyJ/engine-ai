"""Verification command runner (upgrade.md §6).

Runs a task's verification commands against the EXACT working-tree revision (the caller
captures the SHA), records exit codes/durations/log tails, and refuses to call something
passed that never ran. Cacheability: results key on (commit, commands, environment) — a
stale cached success never authorizes merging (that key lives in the caller).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field


@dataclass
class CheckResult:
    name: str
    command: list[str]
    exit_code: int | None
    duration_s: float
    output_tail: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict:
        return asdict(self)


class VerificationError(RuntimeError):
    pass


@dataclass
class Verifier:
    timeout_per_command: int = 300
    max_build_processes: int = 1

    def _resolve(self, workspace: str, argv: list[str]) -> list[str]:
        """Interpret planner-specified commands: a bare relative `script.py` (etc.) means
        run it with its interpreter — models routinely omit the prefix, and a
        PermissionError there would fail a WORKING implementation."""
        if len(argv) and not os.path.isabs(argv[0]) and "/" not in argv[0] and " " not in argv[0]:
            candidate = os.path.join(workspace, argv[0])
            if os.path.isfile(candidate) and not os.access(candidate, os.X_OK):
                if argv[0].endswith(".py"):
                    return [sys.executable or "python3", *argv]
                if argv[0].endswith((".sh", ".bash")):
                    return ["bash", *argv]
        return argv

    def run_checks(self, workspace: str, commands: list[list[str]] | dict,
                   commit: str | None = None) -> dict:
        """Execute commands in `workspace`; returns an evidence dict bound to `commit`."""
        workspace = os.path.abspath(workspace)
        if not os.path.isdir(workspace):
            raise VerificationError(f"not a directory: {workspace}")
        if isinstance(commands, dict):  # {"name": [...argv]}
            commands = [argv if isinstance(argv, list) else [str(argv)] for argv in commands.values()]
        results = []
        for raw in commands:
            argv = self._resolve(workspace, raw)
            name = " ".join(argv[:2])[:80]
            t0 = time.time()
            try:
                p = subprocess.run(argv, cwd=workspace, capture_output=True, text=True,
                                   timeout=self.timeout_per_command, stdin=subprocess.DEVNULL)
                results.append(CheckResult(name, argv, p.returncode,
                                           round(time.time() - t0, 2), (p.stdout + p.stderr)[-2000:]))
            except subprocess.TimeoutExpired:
                results.append(CheckResult(name, argv, None, self.timeout_per_command,
                                           f"timed out after {self.timeout_per_command}s"))
            except OSError as e:  # not installed / not executable — a failed check, not a crash
                results.append(CheckResult(name, argv, None, round(time.time() - t0, 2),
                                           f"{argv[0]}: cannot execute ({e.__class__.__name__})"))
        return {"commit": commit, "workspace": workspace,
                "checks": [r.to_dict() for r in results],
                "passed": bool(results) and all(r.passed for r in results)}
