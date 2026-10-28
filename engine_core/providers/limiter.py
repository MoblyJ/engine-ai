"""Per-account-group rate limiting with atomic slot reservation (upgrade.md §4).

OpenRouter's published free limits at review time were 20 requests/minute and 50/day
(rising after purchased credits) — but those numbers are account facts, not code
constants: `configure()` sets whatever the account's limits page reports today.

Reservation is atomic across threads (a lock guards the window deques), so two workers
racing for the last slot don't both win it. 429 responses call `pause()` with the
server's Retry-After; retries use bounded exponential backoff with jitter.
"""

from __future__ import annotations

import random
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class Limits:
    requests_per_minute: int = 20
    requests_per_day: int = 50


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._limits: dict[str, Limits] = {}
        self._minute: dict[str, deque] = defaultdict(deque)
        self._day: dict[str, deque] = defaultdict(deque)
        self._paused_until: dict[str, float] = {}

    def configure(self, group: str, limits: Limits) -> None:
        with self._lock:
            self._limits[group] = limits

    def limits(self, group: str) -> Limits:
        return self._limits.get(group, Limits())

    def reserve(self, group: str, n: int = 1, now: float | None = None) -> tuple[bool, float]:
        """Atomically reserve n slots for `group`.

        Returns (ok, wait_seconds) — wait is 0 when reserved, else how long to wait
        (quota exhaustion included: wait until the daily window resets).
        """
        now = now if now is not None else time.time()
        with self._lock:
            paused = self._paused_until.get(group, 0.0)
            if paused > now:
                return False, paused - now
            lim = self.limits(group)
            minute, day = self._minute[group], self._day[group]
            horizon_m, horizon_d = now - 60.0, now - 86400.0
            while minute and minute[0] < horizon_m:
                minute.popleft()
            while day and day[0] < horizon_d:
                day.popleft()
            if len(minute) + n > lim.requests_per_minute:
                return False, 60.0 - (now - minute[0])
            if len(day) + n > lim.requests_per_day:
                # daily quota exhausted: this is a PAUSE signal (429 retry-after won't help)
                return False, max(1.0, 86400.0 - (now - day[0]))
            for _ in range(n):
                minute.append(now)
                day.append(now)
            return True, 0.0

    def pause(self, group: str, seconds: float) -> None:
        """Honor Retry-After / operator pause for this group."""
        with self._lock:
            self._paused_until[group] = max(self._paused_until.get(group, 0.0),
                                            time.time() + max(0.0, seconds))

    def paused_for(self, group: str) -> float:
        with self._lock:
            return max(0.0, self._paused_until.get(group, 0.0) - time.time())

    def snapshot(self, group: str) -> dict:
        with self._lock:
            now = time.time()
            lim = self.limits(group)
            return {"group": group, "rpm_limit": lim.requests_per_minute,
                    "rpm_used": len([t for t in self._minute[group] if t > now - 60]),
                    "rpd_limit": lim.requests_per_day,
                    "rpd_used": len([t for t in self._day[group] if t > now - 86400]),
                    "paused_for_s": round(self.paused_for(group), 1)}


def backoff(attempt: int, base: float = 1.0, cap: float = 60.0, jitter: float = 0.25) -> float:
    """Bounded exponential backoff with ±jitter fraction — never exceeds `cap`."""
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    spread = delay * jitter
    return min(cap, max(0.0, delay - spread + random.uniform(0, 2 * spread)))
