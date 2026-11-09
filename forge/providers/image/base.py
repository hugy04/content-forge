from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Protocol

from PIL import Image

from forge.providers.retry import ProviderError


@dataclass
class ImageResult:
    data: bytes
    provider: str
    width: int
    height: int
    generated: bool = True          # False for the synthesised fallback card
    meta: dict[str, Any] = field(default_factory=dict)


class ImageProvider(Protocol):
    name: str
    generated: bool

    async def generate(self, *, prompt: str, negative: str, width: int, height: int,
                       seed: int, timeout: float) -> ImageResult: ...


def validate_png_or_jpeg(data: bytes, provider: str) -> tuple[int, int]:
    """Reject HTML error pages and truncated bodies that arrive with a 200."""
    if not data or len(data) < 1024:
        raise ProviderError(f"{provider} returned {len(data)} bytes, too small to be an image")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except Exception as exc:  # noqa: BLE001 - any decode failure is the same signal
        raise ProviderError(f"{provider} returned data Pillow cannot decode: {exc}") from exc


def to_png(data: bytes) -> bytes:
    """Normalise every provider's output to PNG so sinks have one content type."""
    with Image.open(io.BytesIO(data)) as img:
        if img.format == "PNG":
            return data
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
