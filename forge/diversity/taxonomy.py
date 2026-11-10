"""The option pools the diversity engine samples from.

Each dimension is independent, which is what makes the combinatorics work: six
styles x ten angles x eight art directions is 480 distinct content/visual
pairings before palette and composition are considered. Diversity is therefore
a property of the *selection algorithm*, not of a prompt asking Claude to
"be creative".
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Style:
    key: str
    label: str
    structure: list[str]
    voice: str
    guidance: str


STYLES: dict[str, Style] = {
    "article": Style(
        key="article",
        label="Explanatory article",
        structure=["hook", "context", "three_developments", "what_it_means", "close"],
        voice="measured, third-person, journalistic",
        guidance=(
            "Explain how the subject works and why it matters, in the reader's own "
            "terms. Lead with the concrete, not the abstract. Pitch the vocabulary "
            "at the stated audience: the format sets the shape, not the reading level."
        ),
    ),
    "guide": Style(
        key="guide",
        label="Practical how-to guide",
        structure=["promise", "prerequisites", "numbered_steps", "pitfalls", "checklist"],
        voice="direct, second-person, instructional",
        guidance=(
            "Every section must leave the reader able to do something. Use numbered "
            "steps with concrete inputs and expected outcomes. No motivational filler."
        ),
    ),
    "opinion": Style(
        key="opinion",
        label="Argued opinion piece",
        structure=["claim", "why_consensus_is_wrong", "evidence", "steelman", "conclusion"],
        voice="first-person, confident, willing to disagree",
        guidance=(
            "Say something a reasonable person could disagree with, then defend it. "
            "A claim nobody would argue with is not a position. Put the opposing "
            "view at its strongest before answering it. No both-sides hedging."
        ),
    ),
    "trend_analysis": Style(
        key="trend_analysis",
        label="Trend analysis",
        structure=["what_changed", "drivers", "evidence", "second_order_effects", "outlook"],
        voice="analytical, forward-looking, data-aware",
        guidance=(
            "Separate what is already true from what is forecast, and label the "
            "forecast as such. Name the second-order effects others miss."
        ),
    ),
    "listicle": Style(
        key="listicle",
        label="Structured list",
        structure=["framing", "enumerated_items", "synthesis"],
        voice="brisk, concrete, scannable",
        guidance=(
            "Five to seven numbered items, each standing alone rather than running "
            "on as prose. Every item makes a claim and backs it with one specific. "
            "Items must not overlap; order them so the strongest lands first."
        ),
    ),
    "case_study": Style(
        key="case_study",
        label="Case study",
        structure=["situation", "constraint", "intervention", "result", "transferable_lesson"],
        voice="narrative, specific, outcome-focused",
        guidance=(
            "Anchor on one named situation and follow it through. Quantify the result "
            "where plausible, and state explicitly what does and does not transfer."
        ),
    ),
}

# The interpretive lens applied on top of the style.
ANGLES: dict[str, str] = {
    "economics": "follow the money: who pays, who captures the value, what it costs",
    "human_impact": "centre the people whose daily work or life changes",
    "contrarian": "attack the most widely repeated claim on the subject",
    "beginner": "assume no prior knowledge and build understanding from zero",
    "practitioner": "speak to someone who already does this for a living",
    "future": "project three to five years out from what is observable today",
    "risk": "catalogue what can go wrong and how it is mitigated",
    "comparison": "set the subject against its closest alternative and score both",
    "data_led": "build the argument from numbers, rates and magnitudes",
    "historical": "trace how this came to be, and what the precedent predicts",
}

AUDIENCES: dict[str, str] = {
    "executives": "senior decision-makers who care about cost, risk and timing",
    "practitioners": "hands-on specialists who resent being talked down to",
    "newcomers": "curious readers new to the subject",
    "investors": "capital allocators looking for durable advantage",
    "builders": "engineers and operators who will implement this",
}

# --- visual dimensions, sampled independently of the text dimensions ---

ART_DIRECTIONS: dict[str, str] = {
    "editorial_photography": (
        "documentary editorial photograph, natural available light, shallow depth of "
        "field, 35mm, candid and unstaged, photojournalistic"
    ),
    "flat_vector": (
        "flat vector illustration, bold geometric shapes, generous negative space, "
        "crisp edges, no gradients, modern editorial infographic style"
    ),
    "isometric_3d": (
        "isometric 3D illustration, precise technical axonometric projection, soft "
        "studio lighting, matte clay materials, miniature-diorama feel"
    ),
    "cinematic_render": (
        "cinematic 3D render, volumetric light, dramatic rim lighting, physically "
        "based materials, shallow focus, film-still composition"
    ),
    "collage": (
        "mixed-media editorial collage, torn paper and halftone print textures, "
        "cut-out photographic fragments, layered analogue feel"
    ),
    "data_abstract": (
        "abstract data visualisation as art, flowing nodes and connective lines, "
        "gradient mesh fields, no readable text or axis labels, generative-art feel"
    ),
    "line_illustration": (
        "single-weight line illustration, continuous confident contour, minimal "
        "two-tone fill, technical-drawing restraint, lots of white space"
    ),
    "risograph": (
        "risograph print aesthetic, two-colour spot inks with visible misregistration, "
        "coarse paper grain, limited flat palette, screen-printed poster feel"
    ),
}

PALETTES: dict[str, str] = {
    "cobalt_sand": "deep cobalt blue, warm sand, bone white, single ember-orange accent",
    "forest_clay": "dark forest green, terracotta clay, oat cream, muted brass accent",
    "ink_amber": "near-black ink, warm amber, soft grey paper, pale gold highlight",
    "slate_mint": "cool slate grey, bright mint, chalk white, faint coral accent",
    "oxblood_stone": "oxblood red, pale stone, charcoal, dusty pink highlight",
    "teal_copper": "deep teal, polished copper, off-white, soft shadow neutrals",
    "monochrome_pop": "high-contrast greyscale with exactly one saturated electric-blue accent",
    "dusk_violet": "dusk violet, inky navy, warm grey, muted peach glow",
}

COMPOSITIONS: dict[str, str] = {
    "hero_wide": "wide establishing shot, subject centred in the middle third, horizon low",
    "close_detail": "tight close-up on a single telling detail, background falling away",
    "overhead_flatlay": "directly overhead flat-lay, objects arranged on a grid, even light",
    "environmental": "subject small within a larger environment that explains the context",
    "diagonal_dynamic": "strong diagonal composition, implied motion across the frame",
    "centred_symmetry": "rigorous central symmetry, architectural framing, calm and formal",
}

# Dimensions the engine samples, in order. Each maps to its pool's keys.
DIMENSIONS: dict[str, list[str]] = {
    "style": list(STYLES),
    "angle": list(ANGLES),
    "audience": list(AUDIENCES),
    "art_direction": list(ART_DIRECTIONS),
    "palette": list(PALETTES),
    "composition": list(COMPOSITIONS),
}

# The tuple that must not repeat inside the combo cooldown window.
COMBO_KEY: tuple[str, ...] = ("style", "angle", "art_direction")

NEGATIVE_PROMPT = (
    "text, lettering, words, captions, watermark, signature, logo, UI, charts with "
    "axis labels, extra fingers, deformed hands, distorted faces, low resolution, "
    "jpeg artifacts, oversaturated, stock-photo cliche, collage of unrelated objects"
)
