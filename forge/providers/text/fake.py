"""Fixture-backed text provider used when FAKE_PROVIDERS=1.

Lives in its own module so that the offline path - which is what the test suite
and the evaluator's first run both use - pulls in no provider SDK at all.
"""
from __future__ import annotations

from typing import Any

from forge.providers.text.usage import Usage


class FakeTextProvider:
    """Fixture-backed stand-in used when FAKE_PROVIDERS=1."""
    name = "fake"

    def __init__(self, usage: Usage | None = None):
        self.usage = usage or Usage()
        self._last_topic = "the subject"

    async def complete_json(self, *, stage: str, model: str, effort: str, system: str,
                            user: str, schema: dict[str, Any],
                            max_tokens: int = 8000,
                            usage: Usage | None = None) -> dict[str, Any]:
        from forge.providers.text import fixtures
        self.usage.add(model, stage, 900, 700)
        if usage is not None and usage is not self.usage:
            usage.add(model, stage, 900, 700)
        # The revise prompt carries no Topic: line, so remember the last one.
        topic = _topic_from(user)
        if topic == "the subject":
            topic = self._last_topic
        else:
            self._last_topic = topic
        if stage == "outline":
            return fixtures.outline(topic, _selection_from(user))
        if stage.startswith("draft"):
            attempt = 1 if "revise" in stage else 0
            return fixtures.draft(topic, _selection_from(user), attempt=attempt)
        if stage == "image_prompt":
            return fixtures.image_prompt(topic, _selection_from(user))
        return {}

    async def complete_text(self, *, stage: str, model: str, effort: str, system: str,
                            user: str, max_tokens: int = 8000,
                            usage: Usage | None = None) -> str:
        self.usage.add(model, stage, 500, 300)
        if usage is not None and usage is not self.usage:
            usage.add(model, stage, 500, 300)
        return "fixture text"


def _topic_from(user: str) -> str:
    for line in user.splitlines():
        if line.lower().startswith("topic:"):
            return line.split(":", 1)[1].strip()
    return "the subject"


def _selection_from(user: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in user.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            if key in {"style", "angle", "audience", "art_direction", "palette", "composition"}:
                out[key] = value.strip()
    return out
