"""Repair markdown that arrived without its line breaks.

Models returning markdown inside a JSON string field sometimes emit the
structural markers - `## `, `- `, `1. ` - without the newlines that make them
structural. Observed on gemini-3.5-flash-lite: a whole 500-word piece came back
as a single line reading `...five core operational metrics. ## 1. Predictive
Part Ordering - The Claim: ...`. It renders as one grey paragraph, and the
`has_sections` check correctly fails it, because `^##` never matches.

Loosening the validator would have been the wrong fix: the deliverable is a
markdown file that has to render in GitHub, in Google Docs and in the UI. The
text is repaired instead, and only when it is actually malformed - a body that
already has line structure is returned untouched, so this can never reflow
good output.
"""
from __future__ import annotations

import re

# A body with real structure has a newline every few sentences. One with almost
# none, yet carrying inline markers, is the failure case this module repairs.
_MIN_LINES_PER_1000_CHARS = 2.0


def looks_unformatted(text: str) -> bool:
    if not text.strip():
        return False
    newlines = text.count("\n")
    density = newlines / max(1, len(text) / 1000)
    has_inline_markers = bool(re.search(r"\S\s+#{2,6}\s+\S", text))
    return has_inline_markers and density < _MIN_LINES_PER_1000_CHARS


def _split_heading_from_body(line: str, known: list[str]) -> str:
    """Separate `## Heading Body text...` using the headings we already planned.

    Guessing where a heading ends is unreliable - "The Illusion of Low-Cost
    Agility Apex Freight accumulated..." has no punctuation at the boundary and
    the body's first words are capitalised too. But the outline stage already
    decided the headings, so the boundary is known exactly rather than inferred.
    """
    match = re.match(r"^(#{2,6})\s+(.*)$", line, re.S)
    if not match:
        return line
    hashes, rest = match.group(1), match.group(2).strip()
    for heading in sorted(known, key=len, reverse=True):
        heading = heading.strip()
        if heading and rest.startswith(heading) and len(rest) > len(heading):
            tail = rest[len(heading):].lstrip(" :-\u2014")
            if tail:
                return f"{hashes} {heading}\n\n{tail}"
    return line


def normalise(text: str, known_headings: list[str] | None = None) -> tuple[str, bool]:
    """Return (markdown, repaired). `repaired` is False when nothing changed."""
    if not looks_unformatted(text):
        return text, False

    out = text
    # Headings: put every ##..###### marker at the start of its own block.
    out = re.sub(r"\s*(?<!\n)(#{2,6})\s+", lambda m: "\n\n" + m.group(1) + " ", out)
    # Bullets: " - " between two words is a list marker in this failure mode.
    # Require a space on both sides so hyphenated-words and ranges survive.
    out = re.sub(r"(?<=[.:;!?\"'\w])\s+-\s+(?=[A-Z0-9])", "\n- ", out)
    # Numbered items that follow a sentence end.
    out = re.sub(r"(?<=[.!?])\s+(\d{1,2}\.)\s+(?=[A-Z])", r"\n\n\1 ", out)
    # Tidy: no more than one blank line, no trailing spaces.
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)

    # A heading that swallowed its own paragraph leaves a section that looks
    # empty. Split it back apart using the planned headings.
    if known_headings:
        out = "\n".join(
            _split_heading_from_body(line, known_headings) if line.startswith("#")
            else line
            for line in out.split("\n")
        )
        out = re.sub(r"\n{3,}", "\n\n", out)

    return out.strip(), True


def repair_draft(draft: dict, ctx=None) -> dict:
    """Normalise a draft's body in place, recording it as a warning if changed."""
    body = draft.get("body_markdown") or ""
    known = [
        section.get("heading", "")
        for section in ((getattr(ctx, "outline", None) or {}).get("sections") or [])
    ] if ctx is not None else []
    fixed, repaired = normalise(body, known)
    if repaired:
        draft["body_markdown"] = fixed
        if ctx is not None:
            ctx.warnings.append(
                "markdown arrived without line breaks and was normalised "
                "(headings and lists restored)"
            )
    return draft
