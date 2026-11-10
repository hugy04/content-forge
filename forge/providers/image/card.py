"""The provider that cannot fail.

Last link in the fallback chain. It renders a typographic editorial card locally
with Pillow, so a run whose every network image provider is down still produces
a usable visual asset and a reviewable artifact. Runs that land here are marked
`partial` and carry `image.generated = false`, so a synthesised card is never
silently passed off as a generated image.
"""
from __future__ import annotations

import io
import random
import textwrap

from PIL import Image, ImageDraw, ImageFont

from forge.providers.image.base import ImageResult

# Hex values for the taxonomy's palette names, so the card inherits the run's
# visual identity instead of looking like a generic error state.
PALETTE_HEX: dict[str, tuple[str, str, str]] = {
    "cobalt_sand": ("#13306b", "#e8d9bf", "#ff6a3d"),
    "forest_clay": ("#1d3326", "#e5dcc8", "#b5651d"),
    "ink_amber": ("#15161a", "#efeae1", "#f0a830"),
    "slate_mint": ("#39434d", "#f2f4f1", "#3ddc97"),
    "oxblood_stone": ("#4a1420", "#e9e4dc", "#e0829a"),
    "teal_copper": ("#103d40", "#f1ece3", "#c47a4a"),
    "monochrome_pop": ("#101010", "#f5f5f5", "#2b6fff"),
    "dusk_violet": ("#2a2142", "#ece7e2", "#f5b199"),
}

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:          # very old Pillow
        return ImageFont.load_default()


class CardProvider:
    """Deterministic local renderer. `generated=False` by design."""
    name = "card"
    generated = False

    def __init__(self, title: str = "", style_label: str = "", palette: str = "ink_amber"):
        self.title = title
        self.style_label = style_label
        self.palette = palette

    async def generate(self, *, prompt: str, negative: str, width: int, height: int,
                       seed: int, timeout: float) -> ImageResult:
        bg, fg, accent = PALETTE_HEX.get(self.palette, PALETTE_HEX["ink_amber"])
        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)
        rng = random.Random(seed)

        # A faint generative texture so the card reads as designed, not broken.
        for _ in range(90):
            x0 = rng.randint(-100, width)
            y0 = rng.randint(-100, height)
            size = rng.randint(60, 420)
            draw.ellipse([x0, y0, x0 + size, y0 + size], outline=accent, width=1)
        overlay = Image.new("RGB", (width, height), bg)
        image = Image.blend(image, overlay, 0.82)
        draw = ImageDraw.Draw(image)

        margin = int(width * 0.08)
        draw.rectangle([margin, int(height * 0.16), margin + int(width * 0.07),
                        int(height * 0.16) + 8], fill=accent)

        label = (self.style_label or "Content asset").upper()
        draw.text((margin, int(height * 0.22)), label, font=_font(max(16, width // 54)),
                  fill=accent)

        title = self.title or "Untitled"
        title_font = _font(max(28, width // 20))
        wrapped = textwrap.wrap(title, width=28)[:4]
        y = int(height * 0.30)
        for line in wrapped:
            draw.text((margin, y), line, font=title_font, fill=fg)
            y += int(title_font.size * 1.22) if hasattr(title_font, "size") else 46

        footer = _font(max(14, width // 72))
        draw.text((margin, height - margin - 24),
                  "content-forge · generated cover",
                  font=footer, fill=fg)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return ImageResult(
            data=buffer.getvalue(), provider=self.name, width=width, height=height,
            generated=False,
            meta={"reason": "all generative providers failed", "palette": self.palette},
        )
