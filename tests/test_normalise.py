"""A request phrased as an instruction is still a request for a topic.

The regression these guard: a real run with the topic "Make me a picture of a
mango" produced a 595-word investor's guide to valuing synthetic product
mockups. Nothing failed - the planner was handed a sentence about wanting a
picture and dutifully planned a piece about that.
"""
from __future__ import annotations

import pytest

from forge.pipeline.context import RunContext
from forge.pipeline.normalise import normalise_topic


@pytest.mark.parametrize("raw,expected", [
    ("Make me a picture of a mango ", "mango"),
    ("make me a picture of a mango", "mango"),
    ("draw a mango", "mango"),
    ("Please write an article about EV battery recycling in India",
     "EV battery recycling in India"),
    ("can you generate an image of a floodlit stadium, thanks", "floodlit stadium"),
    ("I'd like a blog post on warehouse robotics", "warehouse robotics"),
])
def test_instruction_wrappers_are_stripped(raw, expected):
    topic, note = normalise_topic(raw)
    assert topic == expected
    assert note is not None and raw.strip() in note


@pytest.mark.parametrize("topic", [
    "How dealerships use AI to cut service-bay turnaround time",
    "EV battery recycling in India: who actually pays?",
    "The case against micro-SaaS",
    "What the ONDC rollout means for small retailers",
    "How to drive a car",
    "mango",
])
def test_real_topics_are_left_exactly_as_written(topic):
    """Including the four committed samples.

    The leading article in "The case against micro-SaaS" is load-bearing, so
    article-stripping only happens once an instruction verb has been removed.
    """
    assert normalise_topic(topic) == (topic, None)


def test_normalisation_never_empties_a_topic():
    for raw in ("make", "create a", "please", "   ", "draw"):
        topic, _ = normalise_topic(raw)
        assert topic == raw.strip()


def test_the_context_normalises_and_says_so():
    ctx = RunContext(topic="Make me a picture of a mango")
    assert ctx.topic == "mango"
    assert any("does not take instructions" in w for w in ctx.warnings)


def test_both_spellings_share_one_idempotency_key():
    """Otherwise the same piece is commissioned - and paid for - twice."""
    a = RunContext(topic="Make me a picture of a mango")
    b = RunContext(topic="mango")
    assert a.idempotency_key == b.idempotency_key


def test_an_ordinary_topic_adds_no_warning():
    ctx = RunContext(topic="The case against micro-SaaS")
    assert ctx.topic == "The case against micro-SaaS"
    assert ctx.warnings == []
