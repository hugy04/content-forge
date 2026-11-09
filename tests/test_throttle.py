"""Pacing behaviour for providers with published per-IP rate limits."""
from __future__ import annotations

import asyncio
import time

import pytest

from forge.providers.throttle import RateLimiter, TooLongToWait


@pytest.mark.asyncio
async def test_first_call_is_not_delayed():
    limiter = RateLimiter()
    waited = await limiter.acquire("p", min_interval=5.0, max_wait=60.0)
    assert waited == 0.0


@pytest.mark.asyncio
async def test_second_call_waits_for_the_interval():
    limiter = RateLimiter()
    await limiter.acquire("p", min_interval=0.3, max_wait=60.0)
    start = time.monotonic()
    waited = await limiter.acquire("p", min_interval=0.3, max_wait=60.0)
    elapsed = time.monotonic() - start
    assert waited > 0
    assert elapsed >= 0.25


@pytest.mark.asyncio
async def test_a_wait_longer_than_the_budget_is_refused_rather_than_served():
    """The point of the limiter is to protect the deadline, not to honour it blindly."""
    limiter = RateLimiter()
    await limiter.acquire("p", min_interval=45.0, max_wait=60.0)
    with pytest.raises(TooLongToWait):
        await limiter.acquire("p", min_interval=45.0, max_wait=2.0)


@pytest.mark.asyncio
async def test_a_server_cooloff_overrides_the_configured_floor():
    limiter = RateLimiter()
    limiter.penalise("p", 30.0)
    assert limiter.pending("p") > 25.0
    with pytest.raises(TooLongToWait):
        await limiter.acquire("p", min_interval=1.0, max_wait=5.0)


@pytest.mark.asyncio
async def test_a_shorter_cooloff_does_not_shorten_an_existing_one():
    limiter = RateLimiter()
    limiter.penalise("p", 30.0)
    limiter.penalise("p", 2.0)
    assert limiter.pending("p") > 25.0


@pytest.mark.asyncio
async def test_concurrent_callers_queue_instead_of_stampeding():
    """Two runs starting together must not both fire inside the same window."""
    limiter = RateLimiter()
    fired: list[float] = []

    async def call():
        await limiter.acquire("p", min_interval=0.3, max_wait=10.0)
        fired.append(time.monotonic())

    await asyncio.gather(call(), call(), call())
    fired.sort()
    assert fired[1] - fired[0] >= 0.25
    assert fired[2] - fired[1] >= 0.25


@pytest.mark.asyncio
async def test_zero_interval_providers_are_never_paced():
    limiter = RateLimiter()
    for _ in range(5):
        assert await limiter.acquire("free", min_interval=0.0, max_wait=1.0) == 0.0
