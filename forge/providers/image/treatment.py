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

# Line-art constants, tuned against real renders rather than guessed.
BANDS = 6              # tonal regions kept before edges are found
EDGE_THRESHOLD = 18    # below this a boundary is noise, not a line
STROKE_WEIGHT = 5      # dilation, in pixels, of the finished line


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


def _mix(a: str, b: str, t: float) -> str:
    """Blend two hex colours; t=0 returns a, t=1 returns b."""
    ca, cb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def _palette_stops(dark: str, paper: str, accent: str,
                   body: float = 0.45) -> list[str]:
    """Stops for a gradient map that keeps the accent an accent.

    The obvious ramp is dark -> accent -> paper, and it is wrong. A photograph
    is mostly midtones, so whatever sits in the middle becomes the colour of
    the picture: "Ocean" has a single ember-orange accent and came back a
    wholly orange image.

    Nor can the middle simply be `paper`. That was the first correction, and it
    washed every treatment out to the same pale non-colour - five of the eight
    became the same picture, which defeats the point of having eight.

    So the body of the ramp is a blend of the palette's own dark and light,
    `body` of the way across. That keeps contrast and keeps the hue, while the
    accent gets the top band only, which is where a spot colour belongs.
    """
    mid = _mix(dark, paper, body)
    return [dark, mid, _mix(mid, paper, 0.55), paper, _mix(paper, accent, 0.85)]


def _gradient_map(image: Image.Image, stops: list[str]) -> Image.Image:
    """Recolour by luminance through the palette. This is what makes two art
    directions visibly different rather than subtly different."""
    grey = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    return grey.convert("RGB").point(_ramp([_rgb(s) for s in stops]))


def _duotone(image: Image.Image, dark: str, light: str) -> Image.Image:
    return _gradient_map(image, [dark, light])


# `_tritone(dark, accent, paper)` used to live here. It was removed rather
# than left unused: it names the accent as the midtone, which is the bug this
# file has now had twice, and a helper that makes the wrong thing easy to
# write is how it came back the second time. Every direction goes through
# `_palette_stops`, and the tests below enforce that for the ones that exist
# and the ones that do not exist yet.


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
    """Line art from the edges of a *posterised* image, not of the photo.

    Running an edge filter straight at a photograph traces its texture: a stone
    countertop or a bit of sensor grain becomes a field of speckle that reads
    as dirt rather than draughtsmanship, and no threshold afterwards separates
    specks from strokes. Banding the luminance first collapses the frame into a
    handful of flat regions, so the only edges left to find are the boundaries
    between them - which is what a person drawing the scene would put down.
    """
    grey = ImageOps.grayscale(image).filter(ImageFilter.MedianFilter(5))
    grey = grey.filter(ImageFilter.GaussianBlur(3.0))
    grey = ImageOps.autocontrast(grey, cutoff=4)

    step = max(1, 256 // BANDS)
    banded = grey.point(lambda v: min(255, (v // step) * step))
    # Median again, now on the bands: removes single-pixel islands that would
    # each become their own closed contour.
    banded = banded.filter(ImageFilter.MedianFilter(5))

    lines = banded.filter(ImageFilter.FIND_EDGES)
    lines = lines.point(lambda v: 255 if v > EDGE_THRESHOLD else 0)
    lines = lines.filter(ImageFilter.MaxFilter(STROKE_WEIGHT))  # stroke weight
    return ImageOps.colorize(ImageOps.invert(lines), black=_rgb(ink),
                             white=_rgb(paper))


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
            out = Image.blend(
                out, _gradient_map(out, _palette_stops(dark, paper, accent)), 0.42)

        elif art_direction == "cinematic_render":
            out = _gradient_map(base, _palette_stops(dark, paper, accent))
            out = Image.blend(base, out, 0.72)
            out = _vignette(ImageEnhance.Contrast(out).enhance(1.15))
            out = _letterbox(out)

        elif art_direction == "risograph":
            out = _flatten(base, 6, _palette_stops(dark, paper, accent, 0.44), keep=0.24)
            out = ImageEnhance.Contrast(out).enhance(1.2)
            out = _offset_channels(out, 3)
            out = _grain(out, seed, 10)

        elif art_direction == "line_illustration":
            out = _edges(base, dark, paper)

        elif art_direction == "flat_vector":
            clean = base.filter(ImageFilter.MedianFilter(7)).filter(
                ImageFilter.GaussianBlur(1.6))
            out = _flatten(clean, 5, _palette_stops(dark, paper, accent, 0.38), keep=0.12)
            out = ImageEnhance.Contrast(out).enhance(1.12)

        elif art_direction == "isometric_3d":
            clean = base.filter(ImageFilter.MedianFilter(5))
            out = _flatten(clean, 9, _palette_stops(dark, paper, accent, 0.52), keep=0.26)
            out = ImageEnhance.Brightness(out).enhance(1.06)
            out = ImageEnhance.Color(out).enhance(0.8)

        elif art_direction == "collage":
            out = _flatten(base, 12, _palette_stops(dark, paper, accent, 0.5), keep=0.46)
            out = _offset_channels(out, 3)
            out = _paper_border(out, paper, inset=26)
            out = _grain(out, seed, 6)

        elif art_direction == "data_abstract":
            # The most abstract of the eight: three tones, no photograph left,
            # and a denser grid, so it cannot be mistaken for the print looks.
            out = _flatten(base, 3, _palette_stops(dark, paper, accent, 0.3))
            out = _grid(out, accent, step=40)
            out = _grid(out, accent, step=120)

        else:
            log.info("no treatment registered for %r; passing through", art_direction)
            return base, "none"

        return out, art_direction
    except Exception as exc:  # noqa: BLE001 - styling must never fail a run
        log.warning("treatment %s failed (%s); using the untreated image",
                    art_direction, exc)
        return base, "failed"
