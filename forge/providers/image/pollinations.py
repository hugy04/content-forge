"""Pollinations: FLUX text-to-image over a plain GET, no API key.

Chosen as the default because it needs no signup, which keeps the quickstart
honest for anyone cloning the repo. It is a free public endpoint, so it is
wrapped in the same retry/breaker machinery as everything else and is always
followed in the chain by a provider that cannot fail.
"""
from __future__ import annotations

import logging
import re
import urllib.parse

import httpx

from forge.providers.image.base import ImageResult, to_png, validate_png_or_jpeg
from forge.providers.retry import (
    PermanentProviderError,
    ProviderError,
    RateLimited,
    transport_detail,
)

log = logging.getLogger("forge.image.pollinations")

ENDPOINT = "https://image.pollinations.ai/prompt/{prompt}"

# The API validates `seed` as a signed 32-bit integer. Run seeds are derived
# from a SHA-256 digest, which overflows that range about half the time, so the
# seed is folded rather than truncated - truncating would bias the low bits and
# quietly reduce visual variety.
MAX_SEED = 2_147_483_647


class PollinationsProvider:
    name = "pollinations"
    generated = True

    def __init__(self, model: str = "flux", token: str = ""):
        self.model = model
        self.token = token

    async def generate(self, *, prompt: str, negative: str, width: int, height: int,
                       seed: int, timeout: float) -> ImageResult:
        url = ENDPOINT.format(prompt=urllib.parse.quote(prompt[:1800], safe=""))
        # Anonymously the service stamps an attribution watermark and allows one
        # image per 45s per IP. `nologo` is accepted but ignored without a key
        # (measured 10 Nov 2026 - it does not 402, it simply has no effect), so
        # the watermark stays: it is the provider's attribution for a service
        # given away free, and cropping it off would be evading the terms rather
        # than meeting them. A key from enter.pollinations.ai lifts both limits
        # legitimately, and is the only reason `nologo` is sent at all.
        params = {
            "width": width, "height": height, "seed": seed % MAX_SEED,
            "model": self.model,
        }
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            params["nologo"] = "true"
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"pollinations transport error: {transport_detail(exc)}") from exc

        if response.status_code in (402, 429):
            # The anonymous tier allows roughly one image per 45s per IP and
            # says so in the body ("Retry after 38s"). Surface that number so
            # the limiter can pace the next call instead of retrying blind.
            raise RateLimited(
                f"pollinations rate limit ({response.status_code}): "
                f"{response.text[:160]!r}",
                retry_after=_retry_after(response),
            )
        if 400 <= response.status_code < 500:
            # Deterministic: our request is wrong. Fail fast and leave the
            # breaker alone so concurrent runs keep their shot at the provider.
            raise PermanentProviderError(
                f"pollinations rejected the request ({response.status_code}): "
                f"{response.text[:200]!r}"
            )
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


def _retry_after(response: httpx.Response) -> float:
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = re.search(r"[Rr]etry after (\d+(?:\.\d+)?)\s*s", response.text[:400])
    if match:
        return float(match.group(1))
    return 0.0
