"""Ordered provider chain with retry, breaker and graceful degradation.

`IMAGE_PROVIDERS=pollinations,cloudflare,card` is tried left to right. Each
provider gets its own retry budget; a provider that raises after its retries is
recorded and the chain moves on. The run only fails if the chain is exhausted,
which cannot happen while `card` is last.
"""
from __future__ import annotations

import logging
from typing import Any

from forge.config import Settings
from forge.providers.image.base import ImageResult
from forge.providers.image.card import CardProvider
from forge.providers.retry import BREAKER, ProviderError, RateLimited, retrying
from forge.providers.throttle import LIMITER, TooLongToWait

log = logging.getLogger("forge.image")


def build_provider(name: str, settings: Settings, *, title: str = "",
                   style_label: str = "", palette: str = "ink_amber"):
    if name == "pollinations":
        from forge.providers.image.pollinations import PollinationsProvider
        return PollinationsProvider(model=settings.pollinations_model)
    if name == "cloudflare":
        from forge.providers.image.cloudflare import CloudflareProvider
        return CloudflareProvider(settings.cloudflare_account_id, settings.cloudflare_api_token)
    if name == "openai":
        from forge.providers.image.openai import OpenAIImageProvider
        return OpenAIImageProvider(settings.openai_api_key)
    if name == "gemini":
        from forge.providers.image.gemini import GeminiImageProvider
        return GeminiImageProvider(settings.gemini_api_key, settings.gemini_image_model)
    if name == "card":
        return CardProvider(title=title, style_label=style_label, palette=palette)
    if name == "fake":
        return CardProvider(title=title or "Fixture image", style_label=style_label,
                            palette=palette)
    raise ValueError(f"unknown image provider {name!r}")


class ImageRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(self, *, prompt: str, negative: str, seed: int, title: str,
                       style_label: str, palette: str,
                       chain: list[str] | None = None,
                       budget: float | None = None) -> tuple[ImageResult, list[dict[str, Any]]]:
        """Walk the chain. `budget` is the seconds the run can still spend here;
        a provider whose pacing needs longer than that is skipped, not waited on."""
        chain = chain or self.settings.image_provider_chain
        if "card" not in chain and "fake" not in chain:
            chain = [*chain, "card"]   # never leave a run without a visual asset
        attempts: list[dict[str, Any]] = []

        for name in chain:
            try:
                provider = build_provider(
                    name, self.settings, title=title, style_label=style_label, palette=palette
                )
            except (ValueError, TypeError) as exc:
                attempts.append({"provider": name, "ok": False,
                                 "error": f"not configured: {exc}"})
                log.info("image provider %s skipped: %s", name, exc)
                continue

            # The local card needs neither retries nor a breaker.
            is_local = name in {"card", "fake"}
            try:
                if is_local:
                    result = await provider.generate(
                        prompt=prompt, negative=negative,
                        width=self.settings.image_width, height=self.settings.image_height,
                        seed=seed, timeout=self.settings.image_timeout_seconds,
                    )
                else:
                    result = await self._with_retry(
                        provider, name, prompt, negative, seed, budget
                    )
            except TooLongToWait as exc:
                attempts.append({"provider": name, "ok": False, "error": str(exc),
                                 "skipped": "paced"})
                log.info("image provider %s skipped: %s", name, exc)
                continue
            except ProviderError as exc:
                attempts.append({"provider": name, "ok": False, "error": str(exc)})
                log.warning("image provider %s exhausted: %s", name, exc)
                continue

            attempts.append({"provider": name, "ok": True, "generated": result.generated})
            return result, attempts

        raise ProviderError(f"every image provider failed: {attempts}")

    async def _with_retry(self, provider, name: str, prompt: str, negative: str,
                          seed: int, budget: float | None = None) -> ImageResult:
        key = f"image:{name}"
        interval = self.settings.image_min_interval(name)
        allowance = budget if budget is not None else float(
            self.settings.image_timeout_seconds)

        async for attempt in retrying(attempts=3, initial=1.5, maximum=10.0):
            with attempt:
                BREAKER.check(key)
                # Pace before the call. TooLongToWait is deliberately not a
                # ProviderError, so it escapes the retry loop and moves the
                # chain on instead of being retried.
                waited = await LIMITER.acquire(key, interval, allowance)
                allowance -= waited
                try:
                    result = await provider.generate(
                        prompt=prompt, negative=negative,
                        width=self.settings.image_width, height=self.settings.image_height,
                        seed=seed, timeout=self.settings.image_timeout_seconds,
                    )
                except RateLimited as exc:
                    # The server knows its own limit better than our config does.
                    LIMITER.penalise(key, exc.retry_after or interval)
                    BREAKER.record_failure(key)
                    raise
                except ProviderError:
                    BREAKER.record_failure(key)
                    raise
                BREAKER.record_success(key)
                return result
        raise ProviderError("unreachable")
