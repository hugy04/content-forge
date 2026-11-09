"""Claude text provider.

Notes that matter for this SDK generation (anthropic>=2.x, Claude Opus 5.5):
  * `temperature` / `top_p` / `top_k` are rejected with a 400 - creative range
    is steered by the prompt and by `output_config.effort`, not by sampling.
  * Thinking is always on and cannot be disabled, so the `thinking` parameter is
    omitted entirely; depth is controlled by `effort`.
  * Assistant prefill is gone, so JSON is obtained with structured outputs
    (`output_config.format`) rather than by priming an opening brace.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic

from forge.providers.retry import BREAKER, ProviderError, retrying

log = logging.getLogger("forge.text")

# USD per million tokens, for the cost figures in the run record.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.00, 50.00),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    per_call: list[dict[str, Any]] = field(default_factory=list)

    def add(self, model: str, stage: str, input_tokens: int, output_tokens: int) -> None:
        rate_in, rate_out = PRICING.get(model, (4.00, 20.00))
        cost = (input_tokens / 1_000_000) * rate_in + (output_tokens / 1_000_000) * rate_out
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += cost
        self.calls += 1
        self.per_call.append({
            "stage": stage, "model": model, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "cost_usd": round(cost, 6),
        })

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
            "per_call": self.per_call,
        }


class ClaudeTextProvider:
    name = "claude"

    def __init__(self, api_key: str, usage: Usage | None = None):
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Put it in .env, or run with "
                "FAKE_PROVIDERS=1 to use fixtures."
            )
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)
        self.usage = usage or Usage()

    async def _call(self, *, stage: str, model: str, effort: str, system: str,
                    user: str, max_tokens: int,
                    schema: dict[str, Any] | None) -> str:
        output_config: dict[str, Any] = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        async for attempt in retrying(attempts=3):
            with attempt:
                BREAKER.check(f"claude:{model}")
                try:
                    response = await self._client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        system=system,
                        messages=[{"role": "user", "content": user}],
                        output_config=output_config,
                    )
                except (anthropic.RateLimitError, anthropic.APIConnectionError,
                        anthropic.APITimeoutError) as exc:
                    BREAKER.record_failure(f"claude:{model}")
                    raise ProviderError(f"claude transient failure: {exc}") from exc
                except anthropic.APIStatusError as exc:
                    if exc.status_code >= 500:
                        BREAKER.record_failure(f"claude:{model}")
                        raise ProviderError(f"claude server error {exc.status_code}") from exc
                    raise  # 4xx: a bug in our request, not worth retrying

                if response.stop_reason == "refusal":
                    detail = getattr(response.stop_details, "category", None)
                    raise ProviderError(f"claude refused the request (category={detail})")
                if response.stop_reason == "max_tokens":
                    BREAKER.record_failure(f"claude:{model}")
                    raise ProviderError(
                        f"claude hit max_tokens at stage {stage}; output truncated"
                    )

                BREAKER.record_success(f"claude:{model}")
                self.usage.add(
                    model, stage,
                    response.usage.input_tokens, response.usage.output_tokens,
                )
                text = "".join(b.text for b in response.content if b.type == "text")
                if not text.strip():
                    raise ProviderError(f"claude returned no text at stage {stage}")
                return text
        raise ProviderError("unreachable")

    async def complete_json(self, *, stage: str, model: str, effort: str, system: str,
                            user: str, schema: dict[str, Any],
                            max_tokens: int = 8000) -> dict[str, Any]:
        raw = await self._call(stage=stage, model=model, effort=effort, system=system,
                               user=user, max_tokens=max_tokens, schema=schema)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"claude returned unparseable JSON at stage {stage}: {exc}"
            ) from exc

    async def complete_text(self, *, stage: str, model: str, effort: str, system: str,
                            user: str, max_tokens: int = 8000) -> str:
        return await self._call(stage=stage, model=model, effort=effort, system=system,
                                user=user, max_tokens=max_tokens, schema=None)


class FakeTextProvider:
    """Fixture-backed stand-in used when FAKE_PROVIDERS=1."""
    name = "fake"

    def __init__(self, usage: Usage | None = None):
        self.usage = usage or Usage()
        self._last_topic = "the subject"

    async def complete_json(self, *, stage: str, model: str, effort: str, system: str,
                            user: str, schema: dict[str, Any],
                            max_tokens: int = 8000) -> dict[str, Any]:
        from forge.providers.text import fixtures
        self.usage.add(model, stage, 900, 700)
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
                            user: str, max_tokens: int = 8000) -> str:
        self.usage.add(model, stage, 500, 300)
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
