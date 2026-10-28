"""Scheduler — bounded concurrency, lease claims, quota-aware pausing (upgrade.md §4-5)."""

from __future__ import annotations

import time

from engine_core.config import Config
from engine_core.runtime.state import StateStore


class QuotaPaused(Exception):
    """Raised by next_assignments() when the account group is paused on quota.
    The runner should checkpoint + stop (or wait), never rotate accounts."""


class Scheduler:
    def __init__(self, store: StateStore, config: Config, limiter=None):
        self.store = store
        self.config = config
        self.limiter = limiter
        self.quota_paused = False

    def _quota_wait(self, group: str) -> float:
        if not self.limiter:
            return 0.0
        return self.limiter.paused_for(group)

    def active_count(self) -> int:
        return sum(1 for t in self.store.all() if t["state"] in ("leased", "running", "verifying"))

    def next_assignments(self, owner: str, profile_group: str = "default",
                         max: int | None = None) -> list[dict]:
        """Claim up to (max_active_workers - active) ready tasks.

        Raises QuotaPaused if the account group is paused — the caller checkpoints and
        resumes later; quota exhaustion is a pause, not an account rotation.
        """
        if self._quota_wait(profile_group) > 0:
            self.quota_paused = True
            raise QuotaPaused(f"group {profile_group!r} paused for "
                              f"{self._quota_wait(profile_group):.0f}s (quota/Retry-After)")
        self.quota_paused = False
        self.store.expire_leases()
        limit = (max if max is not None else self.config.max_active_workers) - self.active_count()
        out = []
        for tid in self.store.ready_ids():
            if len(out) >= max(0, limit):
                break
            try:
                out.append(self.store.claim(tid, owner=owner))
            except Exception:  # noqa: BLE001 - another worker claimed it first
                continue
        return out

    def status(self) -> dict:
        counts = {}
        for t in self.store.all():
            counts[t["state"]] = counts.get(t["state"], 0) + 1
        return {"tasks": counts, "active": self.active_count(),
                "max_active_workers": self.config.max_active_workers,
                "quota_paused": self.quota_paused, "ts": time.time()}
