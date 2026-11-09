"""Cloudflare Workers AI (FLUX-1-schnell). Needs an account id + API token."""
from __future__ import annotations

import base64

import httpx

from forge.providers.image.base import ImageResult, to_png, validate_png_or_jpeg
from forge.providers.retry import ProviderError

MODEL = "@cf/black-forest-labs/flux-1-schnell"


class CloudflareProvider:
    name = "cloudflare"
    generated = True

    def __init__(self, account_id: str, api_token: str):
        if not account_id or not api_token:
            raise ValueError("cloudflare provider needs CLOUDFLARE_ACCOUNT_ID and _API_TOKEN")
        self.account_id = account_id
        self.api_token = api_token

    async def generate(self, *, prompt: str, negative: str, width: int, height: int,
                       seed: int, timeout: float) -> ImageResult:
        url = (f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
               f"/ai/run/{MODEL}")
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.api_token}"},
                    json={"prompt": prompt[:2000], "seed": seed},
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"cloudflare transport error: {exc}") from exc

        if response.status_code != 200:
            raise ProviderError(
                f"cloudflare returned HTTP {response.status_code}: {response.text[:160]!r}"
            )
        try:
            payload = response.json()
            b64 = payload["result"]["image"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(f"cloudflare response shape unexpected: {exc}") from exc

        data = base64.b64decode(b64)
        actual_w, actual_h = validate_png_or_jpeg(data, self.name)
        return ImageResult(
            data=to_png(data), provider=self.name, width=actual_w, height=actual_h,
            meta={"model": MODEL, "seed": seed},
        )
