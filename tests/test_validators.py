from __future__ import annotations

import pytest

from forge.pipeline.validators import gate_distance, validate

GOOD_TITLE = "Why Service Bays Are the Real Constraint"


def piece(words: int, **over):
    body = over.pop("body", None) or (
        "## First\n\n" + ("word " * (words // 2)) + "\n\n## Second\n\n" + ("word " * (words // 2))
    )
    base = {"title": GOOD_TITLE, "subtitle": "s",
            "meta_description": "A description of a reasonable and realistic length here.",
            "body_markdown": body}
    base.update(over)
    return base


def test_accepts_a_piece_inside_the_contract():
    result = validate(piece(640), target_min=500, target_max=800)
    assert result["passed"] is True
    assert 500 <= result["word_count"] <= 800


def test_rejects_a_short_piece_and_says_how_much_to_add():
    result = validate(piece(200), target_min=500, target_max=800)
    assert result["passed"] is False
    assert "word_count" in result["errors"]
    assert "Expand" in result["repair_instruction"]


def test_rejects_a_long_piece_and_says_how_much_to_cut():
    result = validate(piece(1200), target_min=500, target_max=800)
    assert result["passed"] is False
    assert "Cut" in result["repair_instruction"]


def test_rejects_placeholder_text():
    result = validate(piece(640, body="## A\n\n" + "word " * 700 + "\n\n## B\n\nTODO finish this"),
                      target_min=500, target_max=800)
    assert "no_placeholders" in result["errors"]


def test_rejects_an_h1_in_the_body():
    body = "# Title\n\n## A\n\n" + ("word " * 320) + "\n\n## B\n\n" + ("word " * 320)
    result = validate(piece(640, body=body), target_min=500, target_max=800)
    assert "no_h1_in_body" in result["errors"]


def test_requires_at_least_two_sections():
    result = validate(piece(640, body="word " * 640), target_min=500, target_max=800)
    assert "has_sections" in result["errors"]


def test_boilerplate_is_a_warning_not_a_failure():
    body = ("## A\n\nIn today's fast-paced world it's important to note that "
            + "word " * 320 + "\n\n## B\n\n" + "word " * 320)
    result = validate(piece(640, body=body), target_min=500, target_max=800)
    assert result["passed"] is True
    assert "ai_tells" in result["warnings"]


def test_code_fences_do_not_count_toward_the_word_budget():
    fence = "```\n" + ("codeword " * 400) + "\n```\n"
    body = "## A\n\n" + ("word " * 300) + "\n\n" + fence + "\n## B\n\n" + ("word " * 300)
    result = validate(piece(600, body=body), target_min=500, target_max=800)
    assert result["word_count"] < 700


# -- gate distance -----------------------------------------------------------

def _report(errors, words):
    return {"errors": errors, "word_count": words, "passed": not errors}


def test_a_shorter_shortfall_scores_better_even_though_both_still_fail():
    """Regression: the repair loop compared error *counts*, so a revision that
    moved a draft from 424 to 490 words looked like no progress and was thrown
    away - taking the remaining revision budget with it."""
    worse = gate_distance(_report(["word_count"], 424), 500, 800)
    better = gate_distance(_report(["word_count"], 490), 500, 800)
    assert better < worse


def test_passing_scores_zero():
    assert gate_distance(_report([], 650), 500, 800) == 0.0


def test_each_failed_check_costs_at_least_one():
    one = gate_distance(_report(["has_sections"], 650), 500, 800)
    two = gate_distance(_report(["has_sections", "no_h1_in_body"], 650), 500, 800)
    assert one == 1.0
    assert two == 2.0


def test_overshooting_is_penalised_like_undershooting():
    over = gate_distance(_report(["word_count"], 950), 500, 800)
    under = gate_distance(_report(["word_count"], 350), 500, 800)
    assert over == pytest.approx(under, abs=0.01)


def test_a_draft_inside_the_band_adds_no_word_penalty():
    assert gate_distance(_report(["has_sections"], 650), 500, 800) == 1.0


def test_fewer_errors_always_beats_a_marginally_better_word_count():
    """Count still dominates: structure matters more than a few words."""
    two_errors_close = gate_distance(_report(["word_count", "has_sections"], 495), 500, 800)
    one_error_far = gate_distance(_report(["word_count"], 300), 500, 800)
    assert one_error_far < two_errors_close



def _tells(result):
    return next(c for c in result["checks"] if c["name"] == "ai_tells")["detail"]


def test_a_page_of_em_dashes_is_counted_as_a_tell():
    """The house style forbids them, so the gate has to be able to see them."""
    body = "## First\n\n" + "A sentence \u2014 with an interruption. " * 160
    result = validate(piece(640, body=body), target_min=500, target_max=800)
    assert "em dashes" in _tells(result)


def test_one_em_dash_is_not_worth_reporting():
    """A single one is a stylistic slip; a page of them is the giveaway."""
    body = ("## First\n\nA sentence \u2014 with one interruption.\n\n"
            "## Second\n\n" + "plain prose carries the rest. " * 120)
    result = validate(piece(640, body=body), target_min=500, target_max=800)
    assert "em dashes" not in _tells(result)


def test_every_format_is_told_to_produce_one_shape():
    """A reader should not have to learn a new shape because the engine rolled
    differently, so the house rule lives in the draft prompt, once."""
    from forge.diversity.taxonomy import STYLES
    from forge.pipeline.prompts import DRAFT_SYSTEM

    assert "No bullet points and no numbered lists, in any format" in DRAFT_SYSTEM
    assert "a blank line, then one or more paragraphs" in DRAFT_SYSTEM

    # And no format's own guidance may contradict it.
    for key, style in STYLES.items():
        assert "numbered steps" not in style.guidance.lower(), key
        assert "numbered items" not in style.guidance.lower(), key
