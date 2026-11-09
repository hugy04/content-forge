"""Google Gemini image generation, via the AI Studio REST endpoint."""
from __future__ import annotations

import base64

import httpx

from forge.providers.image.base import ImageResult, to_png, validate_png_or_jpeg
from forge.providers.retry import ProviderError

MODEL = "gemini-2.5-flash-image"


class GeminiImageProvider:
    name = "gemini"
    generated = True

    def __init__(self, api_key: str, model: str = MODEL):
        if not api_key:
            raise ValueError("gemini provider needs GEMINI_API_KEY")
        self.api_key = api_key
        self.model = model

    async def generate(self, *, prompt: str, negative: str, width: int, height: int,
                       seed: int, timeout: float) -> ImageResult:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        body = {
            "contents": [{"parts": [{"text": f"{prompt}\n\nAvoid: {negative}"}]}],
            # Image models must be told to return an image modality.
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url, headers={"x-goog-api-key": self.api_key}, json=body
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"gemini transport error: {exc}") from exc

        if response.status_code != 200:
            raise ProviderError(
                f"gemini returned HTTP {response.status_code}: {response.text[:160]!r}"
            )
        data = None
        try:
            for part in response.json()["candidates"][0]["content"]["parts"]:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    data = base64.b64decode(inline["data"])
                    break
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"gemini response shape unexpected: {exc}") from exc
        if data is None:
            raise ProviderError("gemini returned no inline image data")

        actual_w, actual_h = validate_png_or_jpeg(data, self.name)
        return ImageResult(
            data=to_png(data), provider=self.name, width=actual_w, height=actual_h,
            meta={"model": self.model},
        )
