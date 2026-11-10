"""OpenAI gpt-image-1. Present so the registry has a paid, high-quality option."""
from __future__ import annotations

import base64

import httpx

from forge.providers.image.base import ImageResult, to_png, validate_png_or_jpeg
from forge.providers.retry import ProviderError, transport_detail


def _nearest_supported_size(width: int, height: int) -> str:
    return "1536x1024" if width >= height else "1024x1536"


class OpenAIImageProvider:
    name = "openai"
    generated = True

    def __init__(self, api_key: str, model: str = "gpt-image-1"):
        if not api_key:
            raise ValueError("openai provider needs OPENAI_API_KEY")
        self.api_key = api_key
        self.model = model

    async def generate(self, *, prompt: str, negative: str, width: int, height: int,
                       seed: int, timeout: float) -> ImageResult:
        body = {
            "model": self.model,
            "prompt": f"{prompt}\n\nAvoid: {negative}"[:4000],
            "size": _nearest_supported_size(width, height),
            "n": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai transport error: {transport_detail(exc)}") from exc

        if response.status_code != 200:
            raise ProviderError(
                f"openai returned HTTP {response.status_code}: {response.text[:160]!r}"
            )
        try:
            data = base64.b64decode(response.json()["data"][0]["b64_json"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"openai response shape unexpected: {exc}") from exc

        actual_w, actual_h = validate_png_or_jpeg(data, self.name)
        return ImageResult(
            data=to_png(data), provider=self.name, width=actual_w, height=actual_h,
            meta={"model": self.model},
        )
