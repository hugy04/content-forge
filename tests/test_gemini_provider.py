"""Gemini provider behaviour that is not about the network.

These cover the three things measured against a live free key on 9 Nov 2026:
retired model ids (404), capacity-limited newest ids (503), and Gemini's
OpenAPI schema dialect.
"""
from __future__ import annotations

import pytest

from forge.providers.retry import ProviderError
from forge.providers.text.gemini import (
    MODEL_PREFERENCE,
    GeminiTextProvider,
    _is_capacity_failure,
    to_gemini_schema,
)


def provider() -> GeminiTextProvider:
    return GeminiTextProvider("test-key", default_model="gemini-3.6-flash")


# -- schema translation -------------------------------------------------------

def test_additional_properties_is_stripped_because_gemini_rejects_it():
    out = to_gemini_schema({
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    })
    assert "additionalProperties" not in out
    assert out["required"] == ["a"]


def test_nested_objects_and_array_items_are_translated_too():
    out = to_gemini_schema({
        "type": "object",
        "properties": {
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"heading": {"type": "string"}},
                    "required": ["heading"],
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    })
    assert "additionalProperties" not in out["properties"]["sections"]["items"]


def test_property_order_is_preserved_for_stable_output():
    out = to_gemini_schema({
        "type": "object",
        "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
    })
    assert out["propertyOrdering"] == ["title", "body"]


def test_the_real_pipeline_schemas_survive_translation():
    from forge.pipeline.prompts import DRAFT_SCHEMA, IMAGE_PROMPT_SCHEMA, OUTLINE_SCHEMA

    for schema in (OUTLINE_SCHEMA, DRAFT_SCHEMA, IMAGE_PROMPT_SCHEMA):
        out = to_gemini_schema(schema)
        assert "additionalProperties" not in repr(out)
        assert out["type"] == "object"


# -- capacity classification --------------------------------------------------

@pytest.mark.parametrize("message", [
    "gemini 503 at stage draft: This model is currently experiencing high demand.",
    "gemini 429 at stage draft: You exceeded your current quota",
    "gemini 404 at stage draft: no longer available to new users",
])
def test_capacity_failures_are_recognised(message):
    assert _is_capacity_failure(ProviderError(message))


def test_a_genuine_bug_is_not_treated_as_a_capacity_failure():
    assert not _is_capacity_failure(
        ProviderError("gemini 400 at stage draft: Invalid JSON payload")
    )


# -- cross-model fallback -----------------------------------------------------

def test_fallback_list_starts_with_the_requested_model_and_is_bounded():
    p = provider()
    p._available = list(MODEL_PREFERENCE)
    order = p._fallback_models("gemini-3.8-flash")
    assert order[0] == "gemini-3.8-flash"
    assert len(order) <= 3
    assert len(set(order)) == len(order)


def test_fallback_list_works_before_discovery_has_run():
    assert provider()._fallback_models("gemini-3.6-flash")[0] == "gemini-3.6-flash"


@pytest.mark.asyncio
async def test_a_503_moves_to_the_next_model_rather_than_failing():
    """Backoff cannot fix sustained capacity pressure; a different model can."""
    p = provider()
    p._available = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    p._resolved["gemini-3.8-flash"] = "gemini-3.8-flash"
    tried: list[str] = []

    async def fake_attempt(*, stage, model, effort, system, user, max_tokens,
                           schema, usage=None):
        tried.append(model)
        if model == "gemini-3.8-flash":
            raise ProviderError("gemini 503: This model is experiencing high demand")
        return "recovered"

    p._attempt = fake_attempt
    out = await p._call(stage="draft", model="gemini-3.8-flash", effort="low",
                        system="s", user="u", max_tokens=100, schema=None)
    assert out == "recovered"
    assert len(tried) == 2 and tried[0] == "gemini-3.8-flash"


@pytest.mark.asyncio
async def test_a_400_fails_immediately_without_burning_other_models():
    p = provider()
    p._available = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
    p._resolved["gemini-3.6-flash"] = "gemini-3.6-flash"
    tried: list[str] = []

    async def fake_attempt(*, stage, model, effort, system, user, max_tokens,
                           schema, usage=None):
        tried.append(model)
        raise ProviderError("gemini 400: Invalid JSON payload")

    p._attempt = fake_attempt
    with pytest.raises(ProviderError, match="400"):
        await p._call(stage="draft", model="gemini-3.6-flash", effort="low",
                      system="s", user="u", max_tokens=100, schema=None)
    assert tried == ["gemini-3.6-flash"]


@pytest.mark.asyncio
async def test_exhausting_every_candidate_raises_the_last_error():
    p = provider()
    p._available = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
    p._resolved["gemini-3.6-flash"] = "gemini-3.6-flash"

    async def always_overloaded(**kwargs):
        raise ProviderError(f"gemini 503 for {kwargs['model']}")

    p._attempt = always_overloaded
    with pytest.raises(ProviderError, match="503"):
        await p._call(stage="draft", model="gemini-3.6-flash", effort="low",
                      system="s", user="u", max_tokens=100, schema=None)


def test_an_empty_key_is_refused_with_a_pointer_to_the_free_signup():
    with pytest.raises(ValueError, match="aistudio.google.com"):
        GeminiTextProvider("")


@pytest.mark.asyncio
async def test_an_open_breaker_falls_forward_instead_of_failing_the_run():
    """Regression: the breaker that protects the budget must not fail the run.

    Observed live - once gemini-3.6-flash tripped its breaker on quota 429s,
    every later run raised CircuitOpen and died, even though two other models
    were serving normally.
    """
    from forge.providers.retry import CircuitOpen

    p = provider()
    p._available = ["gemini-3.6-flash", "gemini-3-flash-preview"]
    p._resolved["gemini-3.6-flash"] = "gemini-3.6-flash"
    tried: list[str] = []

    async def fake_attempt(*, stage, model, effort, system, user, max_tokens,
                           schema, usage=None):
        tried.append(model)
        if model == "gemini-3.6-flash":
            raise CircuitOpen("circuit open for gemini:gemini-3.6-flash")
        return "served by the fallback model"

    p._attempt = fake_attempt
    out = await p._call(stage="outline", model="gemini-3.6-flash", effort="low",
                        system="s", user="u", max_tokens=100, schema=None)
    assert out == "served by the fallback model"
    assert tried == ["gemini-3.6-flash", "gemini-3-flash-preview"]


def test_circuit_open_is_classified_as_a_capacity_failure():
    from forge.providers.retry import CircuitOpen

    assert _is_capacity_failure(CircuitOpen("circuit open for gemini:x"))


def test_a_day_exhausted_model_is_dropped_from_the_candidate_list():
    """A daily cap is not a transient fault; re-probing it all day is waste."""
    p = provider()
    p._available = ["gemini-3.6-flash", "gemini-3-flash-preview",
                    "gemini-3.5-flash-lite"]
    p._exhausted.add("gemini-3.6-flash")
    order = p._fallback_models("gemini-3.6-flash")
    assert "gemini-3.6-flash" not in order
    assert order[0] == "gemini-3-flash-preview"


def test_exhausting_every_model_still_returns_something_to_try():
    p = provider()
    p._available = ["gemini-3.6-flash"]
    p._exhausted.add("gemini-3.6-flash")
    assert p._fallback_models("gemini-3.6-flash") == ["gemini-3.6-flash"]


@pytest.mark.parametrize("payload,expected", [
    ({"error": {"details": [{"violations": [
        {"description": "Generate content requests per day limit"}]}]}}, True),
    ({"error": {"details": [{"violations": [
        {"description": "Generate content requests per minute"}]}]}}, False),
])
def test_daily_caps_are_distinguished_from_per_minute_caps(payload, expected):
    import httpx

    from forge.providers.text.gemini import _is_daily_cap

    response = httpx.Response(429, json=payload)
    assert _is_daily_cap(response) is expected
