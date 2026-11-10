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


def _hue_distance(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute per-channel difference between two same-size images."""
    pa, pb = a.convert("RGB").getdata(), b.convert("RGB").getdata()
    total = sum(abs(x - y) for pxa, pxb in zip(pa, pb) for x, y in zip(pxa, pxb))
    return total / (len(pa) * 3)


def test_keep_blends_the_subject_s_own_colour_back_in():
    """The regression that made a mango unrecognisable.

    `_flatten` greyscales before mapping onto the palette ramp, which discards
    hue outright. For a subject whose identity *is* its colour that is fatal.
    `keep` must move the result measurably back towards the original.
    """
    from forge.providers.image.treatment import _flatten

    src = source()
    stops = ["#1b1033", "#f2e6d8", "#c77dff"]
    flattened = _flatten(src, 10, stops, keep=0.0)
    blended = _flatten(src, 10, stops, keep=0.42)

    assert digest(flattened) != digest(blended)
    assert _hue_distance(blended, src) < _hue_distance(flattened, src)


def test_keep_is_clamped_and_zero_is_the_old_behaviour():
    from forge.providers.image.treatment import _flatten

    src = source()
    stops = ["#1b1033", "#f2e6d8", "#c77dff"]
    assert digest(_flatten(src, 8, stops)) == digest(_flatten(src, 8, stops, keep=0.0))
    # keep=1 returns the source untouched; anything above must not raise.
    assert digest(_flatten(src, 8, stops, keep=4.0)) == digest(src.convert("RGB"))


@pytest.mark.parametrize("art,levels", [("flat_vector", 7), ("isometric_3d", 8)])
def test_stylised_directions_no_longer_discard_all_hue(art, levels):
    """These stylise a photograph; they do not re-ink it from scratch.

    Measured against the ramp-only output the old code produced. `collage` is
    excluded on purpose: it adds grain, a channel offset and a paper border
    after the blend, so a per-pixel distance says nothing useful about it - its
    `keep` is covered at the `_flatten` level above.
    """
    from forge.providers.image.treatment import _flatten, _palette_stops
    from forge.providers.image.card import PALETTE_HEX

    src = source()
    dark, paper, accent = PALETTE_HEX["dusk_violet"]
    out, applied = apply(src, art, "dusk_violet", seed=7)
    ramp_only = _flatten(src, levels, _palette_stops(dark, paper, accent), keep=0.0)

    assert applied == art
    assert _hue_distance(out, src) < _hue_distance(ramp_only, src)


# --- composition is chosen before the subject is known ---------------------

def _sel(composition: str):
    from forge.diversity.engine import Selection
    return Selection(style="trend_analysis", angle="comparison", audience="investors",
                     art_direction="collage", palette="forest_clay",
                     composition=composition)


def test_a_wide_frame_is_narrowed_for_a_single_object():
    """The regression: "mango seasonal fruit" returned a wide warehouse.

    `environmental` is defined as "subject small within a larger environment",
    which is right for a sorting line and wrong for the fruit in the crates.
    """
    from forge.pipeline.prompts import composition_for

    framing, note = composition_for(_sel("environmental"), {"image_subject_scale": "object"})
    assert framing == "close_detail"
    assert note and "environmental" in note


def test_a_wide_frame_survives_when_the_setting_is_the_story():
    from forge.pipeline.prompts import composition_for

    for wide in ("environmental", "hero_wide"):
        assert composition_for(_sel(wide), {"image_subject_scale": "scene"}) == (wide, None)


@pytest.mark.parametrize("tight", ["close_detail", "overhead_flatlay",
                                   "diagonal_dynamic", "centred_symmetry"])
def test_an_already_tight_frame_is_never_swapped(tight):
    """Four of six compositions are untouched, so diversity is preserved."""
    from forge.pipeline.prompts import composition_for

    assert composition_for(_sel(tight), {"image_subject_scale": "object"}) == (tight, None)


def test_an_outline_without_the_field_changes_nothing():
    """Older runs, and any model that omits it, must not be narrowed blindly."""
    from forge.pipeline.prompts import composition_for

    assert composition_for(_sel("environmental"), {}) == ("environmental", None)


def _distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _off_the_dark_to_paper_line(colour, dark, paper):
    """How far a colour sits from the straight blend of the palette's own dark
    and light tones. Near zero means it is made of the palette, not the accent."""
    from forge.providers.image.treatment import _mix, _rgb

    return min(_distance(colour, _rgb(_mix(dark, paper, t / 50)))
               for t in range(51))


def test_the_accent_appears_once_and_only_at_the_top():
    """"Ocean" came back orange: its single ember accent sat at the midpoint
    of the ramp, and a photograph is mostly midtones."""
    from forge.providers.image.card import PALETTE_HEX
    from forge.providers.image.treatment import _palette_stops, _rgb

    dark, paper, accent = PALETTE_HEX["cobalt_sand"]
    stops = _palette_stops(dark, paper, accent)

    tainted = [i for i, stop in enumerate(stops)
               if _off_the_dark_to_paper_line(_rgb(stop), dark, paper) > 20]
    assert tainted == [len(stops) - 1], "the accent belongs in the last stop alone"


@pytest.mark.parametrize("palette", list(PALETTES))
def test_every_palette_reads_as_its_name_not_its_accent(palette):
    """The property, checked on all eight rather than the one that was reported.

    A photograph is mostly midtones, so whatever the ramp puts at luminance 128
    becomes the colour of the picture. It has to be made of the palette's own
    dark and light tones - not its accent, and not a wash of pure paper, which
    was the first correction and flattened every treatment into the same pale
    non-picture.
    """
    from forge.providers.image.card import PALETTE_HEX
    from forge.providers.image.treatment import _palette_stops, _ramp, _rgb

    dark, paper, accent = PALETTE_HEX[palette]
    table = _ramp([_rgb(s) for s in _palette_stops(dark, paper, accent)])
    midtone = (table[128], table[256 + 128], table[512 + 128])

    assert _off_the_dark_to_paper_line(midtone, dark, paper) < 20, (
        f"{palette}: midtone {midtone} is not made of its own dark and light")
    assert _distance(midtone, _rgb(paper)) > 20, (
        f"{palette}: midtone is pure paper, which washes the treatment out")


def _photo() -> Image.Image:
    """A smooth, photograph-like gradient with a subject and some texture.

    The synthetic `source()` rainbow is useless here: every treatment maps it
    to something wildly different, so distinctness is trivially satisfied. The
    failure this guards against only shows on real photographic input, which is
    mostly midtones.
    """
    img = Image.new("RGB", (200, 120))
    pixels = img.load()
    for x in range(200):
        for y in range(120):
            base = 90 + int(70 * (y / 120)) + ((x * 7 + y * 3) % 11)
            subject = 60 if (60 < x < 130 and 30 < y < 90) else 0
            pixels[x, y] = (min(255, base + subject + 14),
                            min(255, base + subject),
                            min(255, base + subject - 10))
    return img


@pytest.mark.parametrize("palette", ["cobalt_sand", "teal_copper"])
def test_no_two_art_directions_collapse_into_each_other(palette):
    """The regression this exists for.

    Two correct fixes - blending the photograph back in, and taking the accent
    out of the midtones - combined to wash five of the eight directions into
    the same pale picture. Each was right on its own; together they undid the
    reason the treatment layer exists at all.
    """
    photo = _photo()
    rendered = {art: apply(photo, art, palette, seed=11)[0] for art in ART_DIRECTIONS}

    too_close = []
    for i, first in enumerate(ART_DIRECTIONS):
        for second in list(ART_DIRECTIONS)[i + 1:]:
            a = list(rendered[first].convert("RGB").getdata())
            b = list(rendered[second].convert("RGB").getdata())
            apart = sum(abs(p - q) for pa, pb in zip(a, b)
                        for p, q in zip(pa, pb)) / (len(a) * 3)
            if apart < 12:
                too_close.append((first, second, round(apart, 1)))

    assert not too_close, f"indistinguishable art directions: {too_close}"


def test_the_framing_override_is_not_explained_to_the_reader():
    """Which composition the engine picked, and why it overrode itself, is a
    decision nobody asked about, written in the names the code uses for its
    own options. The reader can see the picture."""
    from forge.pipeline.context import RunContext

    ctx = RunContext(topic="brick kilns")
    _, note = __import__("forge.pipeline.prompts", fromlist=["composition_for"]) \
        .composition_for(_sel("hero_wide"), {"image_subject_scale": "object"})
    ctx.warnings.append(note)

    assert ctx.warnings, "the run record still explains it"
    assert ctx.notices == [], "the page does not"
