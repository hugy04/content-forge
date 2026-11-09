"""Retry policy and circuit breaker shared by every outbound provider call.

Retries alone are not enough: when a provider is genuinely down, retrying each
request wastes the run's latency budget before the fallback provider is ever
tried. The breaker records failures per provider and, once tripped, makes
subsequent calls fail instantly so the chain moves on.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger("forge.retry")


class ProviderError(RuntimeError):
    """A provider failed in a way worth retrying or falling back from."""


class CircuitOpen(ProviderError):
    """The breaker is open; the call was not attempted."""


class RateLimited(ProviderError):
    """The provider refused because of a rate limit and told us how long to wait.

    Distinct from a generic ProviderError because the correct response is to
    pace future calls (see forge.providers.throttle), not to retry immediately.
    """

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class _Circuit:
    failures: int = 0
    opened_at: float | None = None


@dataclass
class CircuitBreaker:
    """Per-key breaker. Trips after `threshold` consecutive failures."""
    threshold: int = 3
    reset_after: float = 60.0
    _state: dict[str, _Circuit] = field(default_factory=dict)

    def _get(self, key: str) -> _Circuit:
        return self._state.setdefault(key, _Circuit())

    def check(self, key: str) -> None:
        circuit = self._get(key)
        if circuit.opened_at is None:
            return
        if time.monotonic() - circuit.opened_at >= self.reset_after:
            # Half-open: allow one trial call through.
            circuit.opened_at = None
            circuit.failures = 0
            log.info("circuit for %s half-open, allowing a trial call", key)
            return
        raise CircuitOpen(f"circuit open for {key}")

    def record_success(self, key: str) -> None:
        self._state[key] = _Circuit()

    def record_failure(self, key: str) -> None:
        circuit = self._get(key)
        circuit.failures += 1
        if circuit.failures >= self.threshold and circuit.opened_at is None:
            circuit.opened_at = time.monotonic()
            log.warning("circuit opened for %s after %d failures", key, circuit.failures)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            key: {"failures": c.failures, "open": c.opened_at is not None}
            for key, c in self._state.items()
        }


BREAKER = CircuitBreaker()


def retrying(attempts: int = 3, initial: float = 1.0, maximum: float = 12.0) -> AsyncRetrying:
    """Exponential backoff with jitter, for transient provider failures."""
    return AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=initial, max=maximum),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
