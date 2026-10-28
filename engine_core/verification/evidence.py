"""Evidence manifests (upgrade.md §6) — the replacement for 'readiness 100'.

An EvidenceManifest binds claims to reality: tested SHA, environment, dependency
versions, the exact commands + exit codes, artifacts (by content hash), and which
requirements the run covered. Content-addressed so two runs can be compared.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from dataclasses import dataclass, field, asdict


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class EvidenceManifest:
    subject: str                      # task id / feature name
    commit: str | None = None         # tested SHA — evidence without a SHA is hearsay
    checks: list[dict] = field(default_factory=list)     # [{name, command, exit_code, ...}]
    artifacts: dict = field(default_factory=dict)        # name -> sha256
    requirements_covered: list[str] = field(default_factory=list)
    environment: dict = field(default_factory=lambda: {
        "python": sys.version.split()[0], "platform": platform.platform()})
    created_at: float = field(default_factory=time.time)

    def passed(self) -> bool:
        return bool(self.checks) and all(c.get("exit_code") == 0 for c in self.checks)

    def add_artifact(self, name: str, data: bytes) -> str:
        h = content_hash(data)
        self.artifacts[name] = h
        return h

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def manifest_hash(self) -> str:
        return content_hash(self.to_json().encode())

    @staticmethod
    def from_run(subject: str, run_result: dict) -> "EvidenceManifest":
        return EvidenceManifest(subject=subject, commit=run_result.get("commit"),
                                checks=run_result.get("checks", []))
