"""Deterministic quality gate.

Asking a model "is this good?" costs a call and returns a different answer each
time. These checks are cheap, repeatable, and unit-testable, so the 500-800 word
contract in the brief becomes something the pipeline enforces rather than hopes
for. Errors trigger a bounded revision; warnings are recorded and shipped with
the run so a human can judge them.
"""
from __future__ import annotations

import re
from typing import Any

from forge.pipeline.context import count_words

PLACEHOLDERS = [
    "lorem ipsum", "todo", "tbd", "[insert", "xxx", "placeholder",
    "your text here", "<name>", "fill in",
]

# Phrases that mark text as machine-written boilerplate. Reported as warnings,
# never as failures: style is a judgement call, so this surfaces the evidence and
# lets a human decide rather than burning a revision pass on it.
AI_TELLS = [
    "in today's fast-paced", "in today's rapidly evolving", "it's important to note",
    "it is important to note", "in conclusion,", "delve into", "navigate the complexities",
    "in the ever-evolving", "unlock the potential", "game-changer", "tapestry of",
    "it's worth noting", "when it comes to", "at the end of the day",
]

AI_TELL_LIMIT = 0


def _check(name: str, ok: bool, level: str, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "level": level, "detail": detail}


def validate(draft: dict[str, Any], *, target_min: int, target_max: int,
             outline: dict[str, Any] | None = None) -> dict[str, Any]:
    body = draft.get("body_markdown") or ""
    title = draft.get("title") or ""
    meta = draft.get("meta_description") or ""
    words = count_words(body)
    checks: list[dict[str, Any]] = []

    # --- the contract from the brief -------------------------------------
    in_range = target_min <= words <= target_max
    checks.append(_check(
        "word_count", in_range, "error",
        f"{words} words (required {target_min}-{target_max})",
    ))

    # --- structure --------------------------------------------------------
    headings = re.findall(r"^##\s+\S.*$", body, re.M)
    checks.append(_check(
        "has_sections", len(headings) >= 2, "error",
        f"{len(headings)} '##' section headings found (need at least 2)",
    ))
    checks.append(_check(
        "no_h1_in_body", not re.search(r"^#\s+\S", body, re.M), "error",
        "body must not contain an H1; the title is separate metadata",
    ))
    checks.append(_check(
        "has_title", 10 <= len(title) <= 110, "error",
        f"title is {len(title)} characters (want 10-110)",
    ))

    # --- content hygiene --------------------------------------------------
    lowered = body.lower()
    found_placeholders = [p for p in PLACEHOLDERS if p in lowered]
    checks.append(_check(
        "no_placeholders", not found_placeholders, "error",
        f"placeholder text found: {found_placeholders}" if found_placeholders
        else "no placeholder text",
    ))
    checks.append(_check(
        "no_empty_sections",
        not re.search(r"^##\s+.*\n+(?=^##\s)", body, re.M), "error",
        "every section heading is followed by prose",
    ))

    found_tells = [t for t in AI_TELLS if t in lowered]
    # Counted alongside the stock phrases rather than as its own gate: one em
    # dash is a stylistic slip, a page of them is the giveaway.
    if body.count("\u2014") > 2:
        found_tells.append(f"em dashes (x{body.count(chr(0x2014))})")
    checks.append(_check(
        "ai_tells", len(found_tells) <= AI_TELL_LIMIT, "warn",
        f"boilerplate phrases: {found_tells}" if found_tells else "none found",
    ))
    checks.append(_check(
        "meta_description", 50 <= len(meta) <= 180, "warn",
        f"meta description is {len(meta)} characters (want 50-180)",
    ))
    checks.append(_check(
        "no_broken_links", "](url)" not in lowered and "](#)" not in lowered, "warn",
        "no obvious placeholder links",
    ))

    if outline and outline.get("sections"):
        planned = len(outline["sections"])
        checks.append(_check(
            "follows_outline", abs(len(headings) - planned) <= 1, "warn",
            f"{len(headings)} sections written against {planned} planned",
        ))

    errors = [c for c in checks if c["level"] == "error" and not c["ok"]]
    warnings = [c for c in checks if c["level"] == "warn" and not c["ok"]]

    return {
        "passed": not errors,
        "word_count": words,
        "checks": checks,
        "errors": [c["name"] for c in errors],
        "warnings": [c["name"] for c in warnings],
        "repair_instruction": _repair(errors, words, target_min, target_max, headings),
    }


def gate_distance(report: dict[str, Any], target_min: int, target_max: int) -> float:
    """How far a draft is from passing, as a single comparable number.

    Counting failed checks is too coarse to steer a repair loop: a revision
    that moves a draft from 424 words to 490 still fails `word_count`, so by
    count it looks like no progress and the loop discards real improvement and
    stops. Distance keeps the partial credit - each failed check costs 1.0, and
    `word_count` additionally costs its shortfall measured in units of the
    allowed band, so getting closer always scores better.
    """
    errors = set(report.get("errors") or [])
    distance = float(len(errors))
    if "word_count" in errors:
        words = report.get("word_count", 0)
        band = max(1, target_max - target_min)
        if words < target_min:
            distance += (target_min - words) / band
        elif words > target_max:
            distance += (words - target_max) / band
    return distance


def _repair(errors: list[dict[str, Any]], words: int, target_min: int,
            target_max: int, headings: list[str]) -> str | None:
    """Turn failed checks into one specific instruction for the revise call."""
    if not errors:
        return None
    names = {e["name"] for e in errors}
    parts: list[str] = []

    if "word_count" in names:
        if words < target_min:
            deficit = (target_min + target_max) // 2 - words
            parts.append(
                f"Expand the piece by about {deficit} words. Add substance to the "
                f"thinnest sections: another concrete example, a mechanism, or a "
                f"consequence. Do not pad with adjectives or restate existing points."
            )
        else:
            excess = words - (target_min + target_max) // 2
            parts.append(
                f"Cut about {excess} words. Remove the weakest sentences and any "
                f"restatement. Keep every specific, number and named example."
            )
    if "has_sections" in names:
        parts.append("Break the piece into at least two '##' sections with real headings.")
    if "no_h1_in_body" in names:
        parts.append("Remove the '#' H1 heading from the body; keep headings at '##'.")
    if "has_title" in names:
        parts.append("Rewrite the title to between 10 and 110 characters.")
    if "no_placeholders" in names:
        parts.append("Replace every placeholder with real content.")
    if "no_empty_sections" in names:
        parts.append("Every '##' heading must be followed by prose.")
    return " ".join(parts)
