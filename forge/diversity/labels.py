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
    "angle": {
        "economics":    ("Follow the money", "who pays and who profits"),
        "human_impact": ("People affected", "whose daily work or life changes"),
        "contrarian":   ("Challenge the consensus", "argues against the common view"),
        "beginner":     ("Start from zero", "assumes no prior knowledge"),
        "practitioner": ("For people who do this", "speaks to working specialists"),
        "future":       ("Look ahead", "projects three to five years out"),
        "risk":         ("What can go wrong", "failure modes and how they're handled"),
        "comparison":   ("Versus the alternative", "scores it against its closest rival"),
        "data_led":     ("Numbers first", "builds the case from figures"),
        "historical":   ("How it got here", "traces the history and what it predicts"),
    },
    "audience": {
        "executives":    ("Executives", "decision-makers: cost, risk, timing"),
        "practitioners": ("Specialists", "hands-on experts in the field"),
        "newcomers":     ("Newcomers", "curious readers, new to the subject"),
        "investors":     ("Investors", "looking for a durable advantage"),
        "builders":      ("Engineers", "the people who'll actually build it"),
    },
    "art_direction": {
        "editorial_photography": ("Photograph", "looks like a real photo"),
        "flat_vector":           ("Flat illustration", "simple shapes, bold colour"),
        "isometric_3d":          ("Isometric 3D", "angled 3D, miniature-model feel"),
        "cinematic_render":      ("Cinematic", "film-still look, dramatic light"),
        "collage":               ("Collage", "torn paper and cut-out textures"),
        "data_abstract":         ("Abstract", "shapes and patterns, nothing literal"),
        "line_illustration":     ("Line drawing", "black lines, lots of white space"),
        "risograph":             ("Risograph print", "two inks, slightly off-register"),
    },
    "palette": {
        "cobalt_sand":    ("Blue and sand", "deep blue, warm sand, one orange accent"),
        "forest_clay":    ("Green and clay", "forest green, terracotta, oat cream"),
        "ink_amber":      ("Black and amber", "near-black, warm amber, grey paper"),
        "slate_mint":     ("Grey and mint", "cool slate, bright mint, chalk white"),
        "oxblood_stone":  ("Deep red and stone", "oxblood, pale stone, charcoal"),
        "teal_copper":    ("Teal and copper", "deep teal, polished copper, off-white"),
        "monochrome_pop": ("Black, white and blue", "greyscale with one electric blue"),
        "dusk_violet":    ("Violet and navy", "dusk violet, inky navy, warm grey"),
    },
    "composition": {
        "hero_wide":        ("Wide shot", "subject centred, plenty of room around it"),
        "close_detail":     ("Close-up", "tight on one telling detail"),
        "overhead_flatlay": ("From above", "objects laid out flat, even light"),
        "environmental":    ("In context", "subject small inside its setting"),
        "diagonal_dynamic": ("Diagonal", "strong diagonal, a sense of movement"),
        "centred_symmetry": ("Symmetrical", "centred, architectural, calm"),
    },
}


def describe(dimension: str, key: str) -> tuple[str, str]:
    """Return ``(label, hint)`` for one option, never raising on an unknown key."""
    label, hint = LABELS.get(dimension, {}).get(key, (key.replace("_", " "), ""))
    return label, hint


def options(dimension: str, pool: dict[str, str]) -> list[tuple[str, str, str]]:
    """``(key, label, hint)`` for every option in a pool, in the pool's order."""
    return [(key, *describe(dimension, key)) for key in pool]
