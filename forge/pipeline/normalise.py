"""Reduce a request to the subject it is about.

The pipeline's contract is *topic in, content asset out*. People do not read
contracts: they type "Make me a picture of a mango" because every other box
with a text field in front of it is a chatbot.

Taken literally, that string is a topic - so the planner commissions a piece
*about the act of requesting mango pictures*, which is how one real run
produced "Valuing Synthetic Product Mockups: An Investor's Guide". The output
was coherent; it was just about the wrong thing.

Stripping the imperative wrapper is the whole fix. "Make me a picture of a
mango" and "mango" then take the same path, and the piece - and its image - are
about fruit. What we do not do is switch to a different output contract when we
detect an instruction: the brief specifies 500-800 words and an image, and a
request phrased as a command is still a request for that.
"""
from __future__ import annotations

import re

# Split into two passes. A bare courtesy ("please") is not on its own evidence
# that the string is an instruction, so one of the two middle passes must bite
# before we rewrite anything - otherwise "The ONDC rollout..." loses its
# article to the noun pass.
_COURTESY = re.compile(
    r"^\s*(?:please\s+|kindly\s+|hey[, ]+|hi[, ]+|can\s+you\s+|could\s+you\s+|"
    r"would\s+you\s+|pls\s+)+", re.IGNORECASE)

# "I want", "I'd like", "I need" - a request frame with no verb of its own.
_WANT = re.compile(r"^\s*i(?:'?d)?\s+(?:want|like|need)\s+", re.IGNORECASE)

# An instruction verb, longest-first so "write me an article about" is consumed
# before "write".
_VERB = re.compile(
    r"""^\s*
    (?:make|create|generate|give|get|draw|paint|render|produce|build|
       write|compose|draft|do|show|design)
    (?:\s+me)?
    \s+""",
    re.IGNORECASE | re.VERBOSE,
)

# The thing being asked for, plus whatever points at the real subject.
_NOUN = re.compile(
    r"""^\s*
    (?:(?:a|an|the|some)\s+)?
    (?:(?:nice|good|short|long|quick|detailed|brief|simple)\s+)?
    (?:picture|image|photo|photograph|illustration|graphic|visual|
       article|piece|post|blog(?:\s*post)?|guide|essay|write[\s-]?up|
       story|report|analysis|content)
    \s+(?:of|about|on|for|regarding|covering)
    \s+""",
    re.IGNORECASE | re.VERBOSE,
)

# Trailing courtesies.
_TRAIL = re.compile(r"[\s,.!?]*\b(?:please|thanks|thank\s+you|asap)\b[\s,.!?]*$",
                    re.IGNORECASE)

_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)

MIN_SUBJECT_CHARS = 2


def normalise_topic(raw: str) -> tuple[str, str | None]:
    """Return ``(topic, note)``.

    ``note`` is None when nothing was stripped. When something was, it carries a
    human-readable record of the rewrite, which the run attaches as a warning so
    the change is visible rather than silent - a caller who meant the literal
    string can see that we did not use it.
    """
    original = raw.strip()
    if not original:
        return original, None

    work = _COURTESY.sub("", original)
    bit = False
    for pattern in (_WANT, _VERB):
        new = pattern.sub("", work)
        if new != work:
            work, bit = new, True
    noun = _NOUN.sub("", work)
    if noun != work:
        work, bit = noun, True

    if not bit:
        # No instruction wrapper. Leave the topic exactly as written - "The
        # case against micro-SaaS" is a title, and its article is load-bearing.
        return original, None

    subject = _ARTICLE.sub("", _TRAIL.sub("", work)).strip(" \t\n\r,.;:!?\"'")

    # Never hand the planner something too short to plan against, and never
    # "normalise" a topic into nothing.
    if len(subject) < MIN_SUBJECT_CHARS or subject.lower() == original.lower():
        return original, None

    return subject, (
        f"read the request as a topic: {original!r} -> {subject!r}. "
        "This service turns a subject into a 500-800 word piece with an "
        "illustration; it does not take instructions."
    )
