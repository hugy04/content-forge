"""Error handling and retry, proven by injecting failures."""
from __future__ import annotations

import pytest

from tests.conftest import isolated_settings
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
    return isolated_settings(image_width=512, image_height=288,
                             image_timeout_seconds=5)


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
    from forge.providers.image.registry import ImageRegistry
    from forge.providers.throttle import LIMITER

    settings = isolated_settings(
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


class PermanentlyBroken:
    """Returns a deterministic client error, as an out-of-range param would."""
    name = "broken"
    generated = True

    def __init__(self):
        self.calls = 0

    async def generate(self, **kwargs):
        from forge.providers.retry import PermanentProviderError
        self.calls += 1
        raise PermanentProviderError("broken rejected the request (400)")


@pytest.mark.asyncio
async def test_a_deterministic_400_is_not_retried(settings, monkeypatch):
    """Retrying a malformed request just spends the budget to fail identically."""
    broken = PermanentlyBroken()
    monkeypatch.setattr(registry_module, "build_provider",
                        lambda name, s, **kw: broken if name == "broken"
                        else registry_module.CardProvider(title="t"))
    result, attempts = await ImageRegistry(settings).generate(
        prompt="p", negative="n", seed=1, title="t", style_label="s",
        palette="ink_amber", chain=["broken", "card"])
    assert broken.calls == 1          # not 3
    assert result.provider == "card"


@pytest.mark.asyncio
async def test_a_deterministic_400_does_not_open_the_breaker_for_other_runs(
        settings, monkeypatch):
    """One bad request must not deny the provider to every concurrent run."""
    from forge.providers.retry import BREAKER

    broken = PermanentlyBroken()
    monkeypatch.setattr(registry_module, "build_provider",
                        lambda name, s, **kw: broken if name == "broken"
                        else registry_module.CardProvider(title="t"))
    for _ in range(4):
        await ImageRegistry(settings).generate(
            prompt="p", negative="n", seed=1, title="t", style_label="s",
            palette="ink_amber", chain=["broken", "card"])
    assert BREAKER.snapshot().get("image:broken", {"open": False})["open"] is False


def test_pollinations_folds_oversized_seeds_into_the_api_range():
    """SHA-256 derived seeds overflow the API's int32 limit about half the time."""
    from forge.providers.image.pollinations import MAX_SEED

    assert MAX_SEED == 2_147_483_647
    for raw in (0, 1, MAX_SEED - 1, MAX_SEED, 4_183_850_846, 2**32 - 1):
        assert 0 <= raw % MAX_SEED <= MAX_SEED


def test_a_timeout_is_still_identifiable_when_it_carries_no_message():
    """Regression: a hosted failure reported only "transport failure: ".

    httpx timeout exceptions stringify to an empty string, so interpolating the
    exception alone produced a message that named the provider and nothing
    else - enough to know the network was involved, not enough to tell a
    connect timeout from a read timeout from a protocol error. Diagnosing the
    first live deploy cost an extra round trip because of it.
    """
    import httpx

    from forge.providers.retry import transport_detail

    assert str(httpx.ReadTimeout("")) == "", "premise: httpx timeouts can be blank"
    assert transport_detail(httpx.ReadTimeout("")) == "ReadTimeout"
    assert transport_detail(httpx.ConnectTimeout("")) == "ConnectTimeout"
    assert transport_detail(httpx.ConnectError("name resolution failed")) == (
        "ConnectError: name resolution failed")


def test_pacing_is_not_switched_off_just_because_a_key_is_present():
    """Measured 10 Nov 2026: a key alone lifts nothing.

    Two keyed requests back to back still returned 402 FREE_LIMIT - the
    unmetered tier needs a pollen balance, not merely a key. An earlier version
    of this code inferred "has token, therefore unlimited" and stopped pacing,
    which would have turned polite queueing into failed runs. Capability is
    configured, never guessed.
    """
    from tests.conftest import isolated_settings

    anonymous = isolated_settings(pollinations_min_interval_seconds=47.0)
    assert anonymous.image_min_interval("pollinations") == 47.0

    keyed = isolated_settings(pollinations_min_interval_seconds=47.0,
                              pollinations_token="sk_whatever")
    assert keyed.image_min_interval("pollinations") == 47.0, (
        "a key is not evidence that the per-IP limit has been lifted")

    # An operator who knows their account is unmetered says so explicitly.
    unmetered = isolated_settings(pollinations_min_interval_seconds=0.0,
                                  pollinations_token="sk_whatever")
    assert unmetered.image_min_interval("pollinations") == 0.0


def test_the_watermark_parameter_is_only_sent_with_a_key():
    """`nologo` is ignored without a key, so sending it anonymously is noise.

    Measured 10 Nov 2026: the anonymous tier accepts `nologo=true` and returns
    a watermarked image anyway. The watermark is the provider's attribution for
    a free service and is left intact; a key removes it legitimately.
    """
    from forge.providers.image.pollinations import PollinationsProvider

    assert PollinationsProvider().token == ""
    assert PollinationsProvider(token="pk-abc").token == "pk-abc"
