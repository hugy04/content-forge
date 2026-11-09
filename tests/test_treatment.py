"""Art direction is applied deterministically, not requested in a prompt.

The renderer available on the free tier ignores style conditioning, so these
tests assert the property the brief actually asks for: two runs with different
art directions must not produce the same pixels.
"""
from __future__ import annotations

import hashlib

import pytest
from PIL import Image

from forge.diversity.taxonomy import ART_DIRECTIONS, PALETTES
from forge.providers.image.treatment import _ramp, apply


def source() -> Image.Image:
    """A deterministic, non-uniform test image."""
    img = Image.new("RGB", (160, 90))
    pixels = img.load()
    for x in range(160):
        for y in range(90):
            pixels[x, y] = ((x * 3) % 256, (y * 5) % 256, (x + y) % 256)
    return img


def digest(image: Image.Image) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()


def test_every_art_direction_has_a_treatment():
    src = source()
    for art in ART_DIRECTIONS:
        _, applied = apply(src, art, "ink_amber")
        assert applied == art, f"{art} fell through to the pass-through branch"


def test_different_art_directions_produce_different_images():
    """This is the visual-diversity requirement, stated as a test."""
    src = source()
    digests = {art: digest(apply(src, art, "ink_amber")[0]) for art in ART_DIRECTIONS}
    assert len(set(digests.values())) == len(ART_DIRECTIONS), (
        f"art directions collided: {digests}")


def test_different_palettes_produce_different_images():
    src = source()
    digests = {p: digest(apply(src, "flat_vector", p)[0]) for p in PALETTES}
    assert len(set(digests.values())) == len(PALETTES)


def test_the_same_inputs_reproduce_the_same_image():
    src = source()
    first = apply(src, "risograph", "dusk_violet", seed=7)[0]
    second = apply(src, "risograph", "dusk_violet", seed=7)[0]
    assert digest(first) == digest(second)


def test_treatment_preserves_the_frame_size():
    src = source()
    for art in ART_DIRECTIONS:
        out, _ = apply(src, art, "slate_mint")
        assert out.size == src.size, f"{art} changed the frame size"


def test_an_unknown_art_direction_passes_the_image_through_untouched():
    """An unrecognised option must never cost a run its visual."""
    src = source()
    out, applied = apply(src, "not_a_real_direction", "ink_amber")
    assert applied == "none"
    assert digest(out) == digest(src.convert("RGB"))


def test_an_unknown_palette_falls_back_rather_than_raising():
    out, applied = apply(source(), "flat_vector", "no_such_palette")
    assert applied == "flat_vector"
    assert out.size == (160, 90)


def test_line_illustration_actually_produces_line_art():
    """Regression: an over-aggressive threshold once erased the whole image."""
    out, _ = apply(source(), "line_illustration", "monochrome_pop")
    greys = out.convert("L").getdata()
    dark = sum(1 for v in greys if v < 128)
    assert dark > 0, "no strokes survived the threshold"
    assert dark < len(greys) * 0.9, "the image is almost entirely ink"


def test_the_colour_ramp_spans_its_stops():
    table = _ramp([(0, 0, 0), (255, 128, 64)])
    assert len(table) == 768
    assert table[0] == 0 and table[255] == 255        # red channel endpoints
    assert table[256] == 0 and table[511] == 128      # green
    assert table[512] == 0 and table[767] == 64       # blue


@pytest.mark.parametrize("art", list(ART_DIRECTIONS))
def test_treatment_never_raises_on_a_grayscale_or_tiny_image(art):
    for src in (Image.new("L", (12, 7), 128), Image.new("RGB", (1, 1), (9, 9, 9))):
        out, applied = apply(src, art, "forest_clay")
        assert applied in {art, "failed"}
        assert out.mode == "RGB"
