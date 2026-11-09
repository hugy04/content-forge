"""Process-wide minimum-interval pacing for providers with per-IP rate limits.

Retry-with-backoff is the wrong tool for a *known* rate limit. Pollinations'
anonymous tier allows one image every ~45s per IP; retrying three times inside
that window burns the run's latency budget and still fails. Pacing the calls
instead turns a hard failure into a wait the deadline can reason about.

Two inputs set the next allowed time for a key:
  * a static floor from config (what the published limit says), and
  * a server hint - a `Retry-After` header or the seconds quoted in a 429/402
    body - which is authoritative and overrides the floor.

Scope is per process. Across replicas the same job belongs in a shared Redis
token bucket; see docs/DESIGN.md "Scale".
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("forge.throttle")


class TooLongToWait(RuntimeError):
    """Pacing would blow the caller's remaining budget; skip this provider."""

    def __init__(self, key: str, wait: float, allowed: float):
        super().__init__(
            f"{key} is paced for another {wait:.1f}s but only {allowed:.1f}s "
            "of budget remain"
        )
        self.key = key
        self.wait = wait


class RateLimiter:
    def __init__(self) -> None:
        self._next_free: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def pending(self, key: str) -> float:
        return max(0.0, self._next_free.get(key, 0.0) - time.monotonic())

    async def acquire(self, key: str, min_interval: float, max_wait: float) -> float:
        """Block until `key` may be called again. Returns seconds actually waited."""
        if min_interval <= 0:
            return 0.0
        # Held across the sleep so concurrent runs queue rather than stampede.
        async with self._lock(key):
            wait = self.pending(key)
            if wait > max_wait:
                raise TooLongToWait(key, wait, max_wait)
            if wait > 0:
                log.info("pacing %s: waiting %.1fs before the next call", key, wait)
                await asyncio.sleep(wait)
            self._next_free[key] = time.monotonic() + min_interval
            return wait

    def penalise(self, key: str, seconds: float) -> None:
        """Honour a server-supplied cool-off, which beats the configured floor."""
        if seconds <= 0:
            return
        target = time.monotonic() + seconds
        if target > self._next_free.get(key, 0.0):
            self._next_free[key] = target
            log.warning("%s asked for a %.0fs cool-off; honouring it", key, seconds)

    def release(self, key: str) -> None:
        """Clear pacing for a key - used by tests."""
        self._next_free.pop(key, None)

    def snapshot(self) -> dict[str, float]:
        return {k: round(self.pending(k), 1) for k in self._next_free}


LIMITER = RateLimiter()
