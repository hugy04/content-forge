"""Human-readable names for the option pools.

The strings in `taxonomy.py` are written *for the model*: "documentary editorial
photograph, natural available light, shallow depth of field, 35mm" is exactly
what the image prompt needs and exactly what nobody wants to read in a dropdown.

So display names live here, separately. The taxonomy keeps saying what the model
is told; this says what a person is shown. Keeping them apart means a wording
change for readability can never quietly change what gets generated.

A key missing from here falls back to its own name with underscores removed, so
adding an option to the taxonomy can never break the page.
"""
from __future__ import annotations

LABELS: dict[str, dict[str, tuple[str, str]]] = {
    # What the piece pays attention to.
    "angle": {
        "economics":    ("Money", "who pays and who profits"),
        "human_impact": ("People", "who it affects day to day"),
        "contrarian":   ("Disagree", "argues against what most people say"),
        "beginner":     ("Basics", "explains it from scratch"),
        "practitioner": ("Expert", "for people who already do this"),
        "future":       ("What's next", "where it is heading"),
        "risk":         ("Risks", "what can go wrong"),
        "comparison":   ("Alternatives", "weighs it against the usual options"),
        "data_led":     ("Numbers", "built on figures and measurements"),
        "historical":   ("History", "how it got here"),
    },
    # Who the piece is written for.
    "audience": {
        "executives":    ("Decision-makers", "senior people weighing cost and risk"),
        "practitioners": ("Specialists", "people who do this for a living"),
        "newcomers":     ("Beginners", "no prior knowledge assumed"),
        "investors":     ("Investors", "where the money is going"),
        "builders":      ("Engineers", "the people who will build it"),
    },
    # What the picture looks like.
    "art_direction": {
        "editorial_photography": ("Photo", "looks like a real photograph"),
        "flat_vector":           ("Flat illustration", "simple shapes, bold colour"),
        "isometric_3d":          ("3D blocks", "angled 3D, like a small model"),
        "cinematic_render":      ("Movie still", "dramatic lighting, film look"),
        "collage":               ("Paper collage", "torn paper, cut-out pieces"),
        "data_abstract":         ("Abstract", "shapes and patterns, no real objects"),
        "line_illustration":     ("Line drawing", "black outlines on plain paper"),
        "risograph":             ("Poster print", "two inks, slightly off-register"),
    },
    # Every palette is four colours. Named after the mood rather than two of
    # them, because "Blue & sand" reads as a choice between two colours.
    "palette": {
        "cobalt_sand":    ("Ocean", "deep blue, sand, bone white, orange"),
        "forest_clay":    ("Forest", "forest green, terracotta, cream, brass"),
        "ink_amber":      ("Ink", "near-black, amber, grey paper, pale gold"),
        "slate_mint":     ("Arctic", "slate grey, mint, chalk white, coral"),
        "oxblood_stone":  ("Clay", "deep red, pale stone, charcoal, dusty pink"),
        "teal_copper":    ("Copper", "deep teal, copper, off-white, soft greys"),
        "monochrome_pop": ("Mono", "black and white, with one bright blue"),
        "dusk_violet":    ("Dusk", "violet, navy, warm grey, peach"),
    },
    # How close the picture is to its subject.
    "composition": {
        "hero_wide":        ("Wide", "whole subject, room around it"),
        "close_detail":     ("Close-up", "right up against one detail"),
        "overhead_flatlay": ("From above", "looking straight down"),
        "environmental":    ("Far away", "small, inside its surroundings"),
        "diagonal_dynamic": ("Angled", "tilted, a sense of movement"),
        "centred_symmetry": ("Centred", "dead centre, calm and even"),
    },
}


def describe(dimension: str, key: str) -> tuple[str, str]:
    """Return ``(label, hint)`` for one option, never raising on an unknown key."""
    label, hint = LABELS.get(dimension, {}).get(key, (key.replace("_", " "), ""))
    return label, hint


def options(dimension: str, pool: dict[str, str]) -> list[tuple[str, str, str]]:
    """``(key, label, hint)`` for every option in a pool, in the pool's order."""
    return [(key, *describe(dimension, key)) for key in pool]
