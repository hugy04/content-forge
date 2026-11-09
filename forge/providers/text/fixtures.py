"""Canned responses for FAKE_PROVIDERS=1.

Lets the whole pipeline, the test suite and a reviewer's first run execute with
no API key and no network. The fixtures are shaped exactly like real responses,
including a deliberately short first draft so the QC/revise loop is exercised.
"""
from __future__ import annotations

_LOREM_SENTENCES = [
    "The shift is already visible in the numbers, even if the headlines have not caught up.",
    "Operators who moved early are now defending a position rather than chasing one.",
    "What looks like a technology story is, underneath, a story about who absorbs the cost.",
    "The constraint is rarely capability; it is the handoff between two teams that do not share a metric.",
    "Incentives explain the pace better than any roadmap does.",
    "That gap is where the next five years of advantage will be won or lost.",
]


def _body(words: int, heading_count: int = 4) -> str:
    """Build a markdown body of approximately `words` words."""
    parts: list[str] = []
    per_section = max(1, words // max(1, heading_count))
    for i in range(heading_count):
        parts.append(f"## Section {i + 1}")
        chunk: list[str] = []
        count = 0
        while count < per_section:
            sentence = _LOREM_SENTENCES[(i + len(chunk)) % len(_LOREM_SENTENCES)]
            chunk.append(sentence)
            count += len(sentence.split())
        parts.append(" ".join(chunk))
    return "\n\n".join(parts)


def outline(topic: str, selection: dict) -> dict:
    return {
        "title": f"{topic}: What Actually Changes",
        "subtitle": "A fixture outline produced without calling the API.",
        "hook": "The interesting part is not the technology. It is who pays for it.",
        "meta_description": (
            f"A {selection.get('style', 'article')} on {topic}, written for "
            f"{selection.get('audience', 'practitioners')}."
        ),
        "keywords": [topic.lower(), "analysis", "operations", "cost"],
        "sections": [
            {"heading": "Where things stand", "word_budget": 160,
             "points": ["Baseline today", "What shifted recently"]},
            {"heading": "What is driving it", "word_budget": 180,
             "points": ["Primary driver", "Secondary driver"]},
            {"heading": "What it means in practice", "word_budget": 180,
             "points": ["Operational consequence", "Cost consequence"]},
            {"heading": "What to watch", "word_budget": 120,
             "points": ["Leading indicator", "Risk"]},
        ],
        "key_takeaways": [
            "The bottleneck is organisational, not technical.",
            "Early movers are defending, not chasing.",
            "Cost absorption decides the pace.",
        ],
        "image_brief": (
            f"A single clear scene representing {topic}, emphasising the human and "
            "operational reality rather than abstract technology."
        ),
    }


def draft(topic: str, selection: dict, attempt: int = 0) -> dict:
    # First attempt is deliberately under the floor so the revise loop runs.
    words = 380 if attempt == 0 else 640
    return {
        "title": f"{topic}: What Actually Changes",
        "subtitle": "A fixture draft produced without calling the API.",
        "body_markdown": _body(words),
        "meta_description": f"A fixture {selection.get('style', 'article')} about {topic}.",
    }


def image_prompt(topic: str, selection: dict) -> dict:
    return {
        "prompt": (
            f"{selection.get('art_direction', 'editorial photograph')} depicting {topic}; "
            f"{selection.get('composition', 'wide establishing shot')}; "
            f"palette: {selection.get('palette', 'muted neutrals')}; no text"
        ),
        "alt_text": f"An illustrative image representing {topic}.",
    }
