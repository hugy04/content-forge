"""Pollinations: FLUX text-to-image over a plain GET, no API key.

Chosen as the default because it needs no signup, which keeps the quickstart
honest for anyone cloning the repo. It is a free public endpoint, so it is
wrapped in the same retry/breaker machinery as everything else and is always
followed in the chain by a provider that cannot fail.
"""
from __future__ import annotations

import logging
import urllib.parse

import httpx

from forge.providers.image.base import ImageResult, to_png, validate_png_or_jpeg
from forge.providers.retry import ProviderError

log = logging.getLogger("forge.image.pollinations")

ENDPOINT = "https://image.pollinations.ai/prompt/{prompt}"


class PollinationsProvider:
    name = "pollinations"
    generated = True

    def __init__(self, model: str = "flux"):
        self.model = model

    async def generate(self, *, prompt: str, negative: str, width: int, height: int,
                       seed: int, timeout: float) -> ImageResult:
        url = ENDPOINT.format(prompt=urllib.parse.quote(prompt[:1800], safe=""))
        # Deliberately minimal. `nologo=true` and `safe=false` are tier-gated and
        # return HTTP 402 on the anonymous tier, so sending them makes the provider
        # fail far more often than it succeeds. The free tier stamps a small
        # attribution watermark; that is the provider's branding and is left intact.
        params = {
            "width": width, "height": height, "seed": seed, "model": self.model,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"pollinations transport error: {exc}") from exc

        if response.status_code != 200:
            raise ProviderError(
                f"pollinations returned HTTP {response.status_code}: "
                f"{response.text[:160]!r}"
            )
        content_type = response.headers.get("content-type", "")
        if "image" not in content_type:
            raise ProviderError(
                f"pollinations returned content-type {content_type!r}, not an image"
            )
        data = response.content
        actual_w, actual_h = validate_png_or_jpeg(data, self.name)
        return ImageResult(
            data=to_png(data), provider=self.name, width=actual_w, height=actual_h,
            meta={"model": self.model, "seed": seed, "endpoint": "pollinations.ai"},
        )
