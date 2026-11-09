"""Error handling and retry, proven by injecting failures."""
from __future__ import annotations

import pytest

from forge.config import Settings
from forge.providers.image import registry as registry_module
from forge.providers.image.base import ImageResult
from forge.providers.image.registry import ImageRegistry
from forge.providers.retry import CircuitBreaker, CircuitOpen, ProviderError


class AlwaysFails:
    name = "boom"
    generated = True

    def __init__(self):
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        raise ProviderError("provider is down")


class AlwaysWorks:
    name = "good"
    generated = True

    def __init__(self):
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        return ImageResult(data=b"x" * 2048, provider=self.name,
                           width=kwargs["width"], height=kwargs["height"])


@pytest.fixture
def settings():
    return Settings(image_width=512, image_height=288, image_timeout_seconds=5)


async def test_chain_falls_through_a_dead_provider_to_a_live_one(settings, monkeypatch):
    dead, live = AlwaysFails(), AlwaysWorks()
    monkeypatch.setattr(registry_module, "build_provider",
                        lambda name, s, **kw: dead if name == "boom" else live)

    result, attempts = await ImageRegistry(settings).generate(
        prompt="p", negative="n", seed=1, title="t", style_label="s",
        palette="ink_amber", chain=["boom", "good"])

    assert result.provider == "good"
    assert attempts[0]["ok"] is False and attempts[1]["ok"] is True
    assert dead.calls == 3, "the dead provider should have exhausted its 3 retries"


async def test_a_run_always_gets_an_image_even_with_every_network_provider_down(settings):
    """The card provider is appended automatically, so the chain cannot be empty."""
    result, attempts = await ImageRegistry(settings).generate(
        prompt="p", negative="n", seed=2, title="Fallback Title",
        style_label="Opinion", palette="cobalt_sand", chain=["cloudflare"])

    assert result.provider == "card"
    assert result.generated is False, "a synthesised card must not claim to be generated"
    assert len(result.data) > 1000


async def test_unconfigured_providers_are_skipped_not_fatal(settings):
    result, attempts = await ImageRegistry(settings).generate(
        prompt="p", negative="n", seed=3, title="t", style_label="s",
        palette="ink_amber", chain=["openai", "gemini", "card"])
    assert result.provider == "card"
    assert [a["provider"] for a in attempts] == ["openai", "gemini", "card"]
    assert all("not configured" in a["error"] for a in attempts[:2])


def test_circuit_breaker_opens_after_the_threshold():
    breaker = CircuitBreaker(threshold=3, reset_after=300)
    for _ in range(3):
        breaker.record_failure("p")
    with pytest.raises(CircuitOpen):
        breaker.check("p")
    assert breaker.snapshot()["p"]["open"] is True


def test_circuit_breaker_resets_after_a_success():
    breaker = CircuitBreaker(threshold=3, reset_after=300)
    breaker.record_failure("p")
    breaker.record_failure("p")
    breaker.record_success("p")
    breaker.check("p")          # must not raise
    assert breaker.snapshot()["p"]["failures"] == 0


def test_circuit_breaker_half_opens_after_the_cooldown():
    breaker = CircuitBreaker(threshold=1, reset_after=0.0)
    breaker.record_failure("p")
    breaker.check("p")          # reset_after elapsed, so a trial call is allowed
    assert breaker.snapshot()["p"]["open"] is False


@pytest.mark.asyncio
async def test_a_paced_provider_is_skipped_so_the_chain_still_delivers(tmp_path):
    """A provider that cannot be called yet must not stall or fail the run."""
    from forge.config import Settings
    from forge.providers.image.registry import ImageRegistry
    from forge.providers.throttle import LIMITER

    settings = Settings(
        image_providers="pollinations,card",
        pollinations_min_interval_seconds=600.0,
        out_dir=tmp_path, db_path=tmp_path / "t.db",
    )
    LIMITER.penalise("image:pollinations", 600.0)
    try:
        registry = ImageRegistry(settings)
        result, attempts = await registry.generate(
            prompt="a test", negative="", seed=1, title="Paced",
            style_label="Article", palette="ink_amber", budget=5.0,
        )
        assert result.provider == "card"
        paced = next(a for a in attempts if a["provider"] == "pollinations")
        assert paced["skipped"] == "paced"
    finally:
        LIMITER.release("image:pollinations")
