"""Deterministic art direction, applied after generation.

The diversity engine picks an art direction and a palette for every run, and
the brief asks for *visual* diversity across executions. Prompting alone does
not deliver that on a free renderer: measured 9 Nov 2026, the Pollinations
anonymous tier returns the same photoreal look whether the prompt asks for a
risograph print, a flat vector illustration or a single-weight line drawing.
The subject tracks the prompt; the styling does not.

So styling stops being a request and becomes a transform. The generated image
supplies the subject, and the selected art direction and palette are applied
here with Pillow, which makes the diversity requirement *verifiable* - two runs
with different art directions cannot look alike, because the pixels are
produced by different code paths - rather than a hope about a model's
instruction-following.

Every transform is deterministic given (image, art_direction, palette, seed),
so a run reproduces exactly. If a transform is unknown the image passes through
untouched: an unrecognised art direction must never cost a run its visual.
"""
from __future__ import annotations

import logging
import random

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from forge.providers.image.card import PALETTE_HEX

log = logging.getLogger("forge.image.treatment")


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _ramp(stops: list[tuple[int, int, int]]) -> list[int]:
    """Build a 256-entry RGB lookup table interpolating between colour stops.

    A two-colour `colorize` washes out, because real duotone work puts a third
    colour in the midtones. The LUT makes the mapping explicit and cheap: one
    `point()` call per image.
    """
    table_r: list[int] = []
    table_g: list[int] = []
    table_b: list[int] = []
    segments = len(stops) - 1
    for value in range(256):
        position = value / 255 * segments
        index = min(int(position), segments - 1)
        t = position - index
        start, end = stops[index], stops[index + 1]
        table_r.append(round(start[0] + (end[0] - start[0]) * t))
        table_g.append(round(start[1] + (end[1] - start[1]) * t))
        table_b.append(round(start[2] + (end[2] - start[2]) * t))
    return table_r + table_g + table_b


def _gradient_map(image: Image.Image, stops: list[str]) -> Image.Image:
    """Recolour by luminance through the palette. This is what makes two art
    directions visibly different rather than subtly different."""
    grey = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    return grey.convert("RGB").point(_ramp([_rgb(s) for s in stops]))


def _duotone(image: Image.Image, dark: str, light: str) -> Image.Image:
    return _gradient_map(image, [dark, light])


def _tritone(image: Image.Image, dark: str, mid: str, light: str) -> Image.Image:
    return _gradient_map(image, [dark, mid, light])


def _grain(image: Image.Image, seed: int, strength: int = 14) -> Image.Image:
    """Seeded grain.

    `Image.effect_noise` draws from PIL's own unseeded RNG, so it made the
    treatment irreproducible - the same run replayed produced different pixels,
    which contradicts the run record's reproducibility guarantee. The noise is
    generated here from the run seed instead.
    """
    width, height = image.size
    rng = random.Random(seed)
    half = max(1, min(127, strength))
    noise_bytes = bytes(
        max(0, min(255, 128 + int(rng.gauss(0, half))))
        for _ in range(width * height)
    )
    noise = Image.frombytes("L", (width, height), noise_bytes)
    return ImageChops.overlay(image, noise.convert("RGB"))


def _posterise(image: Image.Image, bits: int) -> Image.Image:
    return ImageOps.posterize(image.convert("RGB"), bits)


def _flatten(image: Image.Image, levels: int, stops: list[str],
             keep: float = 0.0) -> Image.Image:
    """Quantise luminance into N bands, then map the bands to the palette.

    Posterising *after* recolouring produces hue artefacts, because each RGB
    channel is quantised independently. Quantising the luminance first and
    colouring second keeps every band on the palette.

    The luminance step throws away hue, which is correct for a direction that
    is genuinely ink-limited and wrong for one that is only stylised. Some
    subjects *are* their colour - a mango that comes back grey-mapped onto a
    violet ramp is no longer recognisably a mango - so `keep` blends the
    original back in. The palette still sets the mood; it no longer overrules
    what the thing actually looks like.
    """
    grey = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=2)
    step = max(1, 256 // levels)
    banded = grey.point(lambda v: min(255, (v // step) * step + step // 2))
    mapped = banded.convert("RGB").point(_ramp([_rgb(s) for s in stops]))
    if keep <= 0:
        return mapped
    return Image.blend(mapped, image.convert("RGB"), min(keep, 1.0))


def _vignette(image: Image.Image, strength: float = 0.55) -> Image.Image:
    width, height = image.size
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).ellipse(
        (-width * 0.25, -height * 0.35, width * 1.25, height * 1.35), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(min(width, height) // 8))
    dark = ImageEnhance.Brightness(image).enhance(1.0 - strength)
    return Image.composite(image, dark, mask)


def _letterbox(image: Image.Image, ratio: float = 0.08) -> Image.Image:
    width, height = image.size
    bar = int(height * ratio)
    out = image.copy()
    draw = ImageDraw.Draw(out)
    draw.rectangle((0, 0, width, bar), fill=(8, 8, 8))
    draw.rectangle((0, height - bar, width, height), fill=(8, 8, 8))
    return out


def _paper_border(image: Image.Image, paper: str, inset: int = 26) -> Image.Image:
    width, height = image.size
    canvas = Image.new("RGB", (width, height), _rgb(paper))
    inner = image.resize((width - inset * 2, height - inset * 2), Image.LANCZOS)
    canvas.paste(inner, (inset, inset))
    return canvas


def _edges(image: Image.Image, ink: str, paper: str) -> Image.Image:
    """Line art: CONTOUR already returns dark strokes on white, which survives
    a soft threshold. FIND_EDGES needs a hard threshold and erases thin work."""
    grey = ImageOps.grayscale(image)
    # Denoise hard before edge detection. CONTOUR amplifies sensor noise into
    # a field of speckle that reads as dirt rather than draughtsmanship, and no
    # amount of thresholding afterwards separates specks from strokes.
    grey = grey.filter(ImageFilter.MedianFilter(5))
    grey = grey.filter(ImageFilter.GaussianBlur(1.4))
    grey = ImageOps.autocontrast(grey, cutoff=3)

    lines = grey.filter(ImageFilter.CONTOUR)
    lines = ImageOps.autocontrast(lines, cutoff=1)
    lines = lines.point(lambda v: 0 if v < 165 else 255)
    # Median on the thresholded image erases isolated pixels while leaving
    # connected strokes intact; MinFilter then restores stroke weight.
    lines = lines.filter(ImageFilter.MedianFilter(3))
    lines = lines.filter(ImageFilter.MinFilter(3))
    return ImageOps.colorize(lines, black=_rgb(ink), white=_rgb(paper))


def _offset_channels(image: Image.Image, shift: int = 4) -> Image.Image:
    """Mis-registration, as a two-pass print would produce."""
    r, g, b = image.split()
    r = ImageChops.offset(r, shift, 0)
    b = ImageChops.offset(b, -shift, shift // 2)
    return Image.merge("RGB", (r, g, b))


def _grid(image: Image.Image, accent: str, step: int = 64) -> Image.Image:
    out = image.copy()
    draw = ImageDraw.Draw(out, "RGBA")
    colour = (*_rgb(accent), 70)
    for x in range(0, out.width, step):
        draw.line((x, 0, x, out.height), fill=colour, width=1)
    for y in range(0, out.height, step):
        draw.line((0, y, out.width, y), fill=colour, width=1)
    return out


def apply(image: Image.Image, art_direction: str, palette: str,
          seed: int = 0) -> tuple[Image.Image, str]:
    """Return the treated image and the name of the transform applied."""
    dark, paper, accent = PALETTE_HEX.get(palette, PALETTE_HEX["ink_amber"])
    base = image.convert("RGB")

    try:
        if art_direction == "editorial_photography":
            # The one direction that should still read as a photograph: grade
            # it towards the palette rather than replacing its colour.
            out = ImageEnhance.Color(base).enhance(0.55)
            out = ImageEnhance.Contrast(out).enhance(1.18)
            out = Image.blend(out, _tritone(out, dark, accent, paper), 0.42)

        elif art_direction == "cinematic_render":
            out = _tritone(base, dark, accent, paper)
            out = Image.blend(base, out, 0.72)
            out = _vignette(ImageEnhance.Contrast(out).enhance(1.15))
            out = _letterbox(out)

        elif art_direction == "risograph":
            out = _flatten(base, 6, [dark, accent, paper])
            out = Image.blend(out, _tritone(base, dark, accent, paper), 0.35)
            out = _offset_channels(out, 3)
            out = _grain(out, seed, 10)

        elif art_direction == "line_illustration":
            out = _edges(base, dark, paper)

        elif art_direction == "flat_vector":
            out = _flatten(base, 7, [dark, accent, paper], keep=0.3)

        elif art_direction == "isometric_3d":
            out = _flatten(base, 8, [dark, accent, paper], keep=0.32)
            out = ImageEnhance.Brightness(out).enhance(1.04)

        elif art_direction == "collage":
            out = _flatten(base, 10, [dark, accent, paper], keep=0.42)
            out = _offset_channels(out, 3)
            out = _paper_border(out, paper, inset=26)
            out = _grain(out, seed, 6)

        elif art_direction == "data_abstract":
            out = _flatten(base, 5, [dark, accent, paper])
            out = _grid(out, accent)

        else:
            log.info("no treatment registered for %r; passing through", art_direction)
            return base, "none"

        return out, art_direction
    except Exception as exc:  # noqa: BLE001 - styling must never fail a run
        log.warning("treatment %s failed (%s); using the untreated image",
                    art_direction, exc)
        return base, "failed"
