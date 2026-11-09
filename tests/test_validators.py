from __future__ import annotations

from forge.pipeline.validators import validate

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
