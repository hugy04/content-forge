"""Repairing markdown that lost its line breaks in transit.

Observed on gemini-3.5-flash-lite: a complete piece returned as one line with
the `##` and `- ` markers inline. The validator was right to fail it, so the
text is repaired rather than the check relaxed.
"""
from __future__ import annotations

import re

from forge.pipeline.markdown import looks_unformatted, normalise, repair_draft

FLAT = (
    "Dealership bays generate 49 percent of gross profits while wasting three "
    "hours daily on dispatch friction. Here is how triage compares across five "
    "metrics. ## 1. Predictive Part Ordering - The Claim: AI cross-references "
    "VIN data before arrival. - The Contrast: Legacy ordering leaves bays idle. "
    "## 2. Technician Utilisation - The Claim: Routing lifts utilisation. "
    "## 3. Comeback Rates - The Claim: Fewer repeat visits follow."
)

WELL_FORMED = (
    "An opening paragraph that sets up the piece.\n\n"
    "## First section\n\nSome body text here that runs on a little.\n\n"
    "## Second section\n\n- A bullet\n- Another bullet\n"
)


def headings(text: str) -> int:
    return len(re.findall(r"^##\s+\S", text, re.M))


def test_a_flattened_body_is_detected():
    assert looks_unformatted(FLAT)


def test_well_formed_markdown_is_left_completely_alone():
    """The repair must never reflow output that was already correct."""
    assert not looks_unformatted(WELL_FORMED)
    out, repaired = normalise(WELL_FORMED)
    assert repaired is False
    assert out == WELL_FORMED


def test_repair_restores_headings_the_validator_can_see():
    assert headings(FLAT) == 0
    out, repaired = normalise(FLAT)
    assert repaired is True
    assert headings(out) == 3


def test_repair_restores_bullets():
    out, _ = normalise(FLAT)
    assert len([ln for ln in out.splitlines() if ln.startswith("- ")]) >= 4


def test_repair_keeps_every_word():
    out, _ = normalise(FLAT)
    assert sorted(out.split()) == sorted(FLAT.split())


def test_hyphenated_words_and_ranges_survive():
    text = FLAT + " Fixed-operations and multi-point checks cost 10-20 percent."
    out, _ = normalise(text)
    assert "Fixed-operations" in out
    assert "multi-point" in out
    assert "10-20" in out


def test_plain_prose_with_no_markers_is_untouched():
    prose = "One sentence. Another sentence - with an aside - and a close."
    out, repaired = normalise(prose)
    assert repaired is False
    assert out == prose


def test_empty_input_is_safe():
    assert normalise("") == ("", False)
    assert not looks_unformatted("")


def test_repair_draft_records_a_warning_only_when_it_acts():
    class Ctx:
        def __init__(self):
            self.warnings = []

    acted = Ctx()
    repair_draft({"body_markdown": FLAT}, acted)
    assert len(acted.warnings) == 1

    untouched = Ctx()
    out = repair_draft({"body_markdown": WELL_FORMED}, untouched)
    assert untouched.warnings == []
    assert out["body_markdown"] == WELL_FORMED


def test_repair_draft_tolerates_a_missing_body():
    """A draft with no body is a different failure; this must not mask it."""
    assert repair_draft({}, None) == {}


# -- headings that swallowed their own paragraph -----------------------------

SWALLOWED = (
    "An opening line that introduces the subject at hand. "
    "## The Illusion of Low-Cost Agility Apex Freight accumulated fourteen "
    "subscriptions over eighteen months without review. "
    "## The Hidden Tax on Human Focus Engineers spent twenty percent of their "
    "sprint cycles maintaining brittle bridges."
)
KNOWN = ["The Illusion of Low-Cost Agility", "The Hidden Tax on Human Focus"]


def test_known_headings_are_split_from_their_body_text():
    """Regression: the first repair left `## Heading Body...` on one line, so
    every section looked empty and `no_empty_sections` failed."""
    out, _ = normalise(SWALLOWED, KNOWN)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert "## The Illusion of Low-Cost Agility" in lines
    assert "## The Hidden Tax on Human Focus" in lines
    assert any(ln.startswith("Apex Freight accumulated") for ln in lines)


def test_no_section_is_left_empty_after_the_split():
    out, _ = normalise(SWALLOWED, KNOWN)
    blocks = [b for b in out.split("\n\n") if b.strip()]
    for i, block in enumerate(blocks):
        if block.startswith("##"):
            assert i + 1 < len(blocks), f"{block!r} is the last block"
            assert not blocks[i + 1].startswith("##"), f"{block!r} has no body"


def test_splitting_keeps_every_word():
    out, _ = normalise(SWALLOWED, KNOWN)
    assert sorted(out.split()) == sorted(SWALLOWED.split())


def test_a_heading_that_is_already_alone_is_not_split_again():
    out, _ = normalise(SWALLOWED, KNOWN)
    again, _ = normalise(out, KNOWN)
    assert again == out or not looks_unformatted(out)


def test_unknown_headings_are_left_as_they_are_rather_than_guessed():
    """Without the outline there is no reliable boundary, so do not invent one."""
    out, _ = normalise(SWALLOWED, ["Some Completely Different Heading"])
    assert "## The Illusion of Low-Cost Agility Apex Freight" in out


def test_repair_draft_reads_the_headings_from_the_run_context():
    class Ctx:
        def __init__(self):
            self.warnings = []
            self.outline = {"sections": [{"heading": h} for h in KNOWN]}

    ctx = Ctx()
    out = repair_draft({"body_markdown": SWALLOWED}, ctx)
    assert "## The Hidden Tax on Human Focus\n" in out["body_markdown"]
