"""Token and cost accounting, shared by every text provider.

Kept out of any single provider module because the pipeline is provider-agnostic:
a run may be served by Gemini, by Claude, or by fixtures, and the run record
should account for all three the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# USD per million tokens (input, output).
#
# The Gemini models below are the ones this project targets on the AI Studio
# FREE tier, where the marginal cost of a run is genuinely zero - so they are
# recorded at 0.0 and the run record reports tokens rather than dollars. To
# model paid-tier economics (see docs/DESIGN.md "Scale"), override with
# PRICE_INPUT_PER_MTOK / PRICE_OUTPUT_PER_MTOK in .env.
PRICING: dict[str, tuple[float, float]] = {
    # Anthropic (metered from the first token; kept for the paid upgrade path).
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.00, 50.00),
}

DEFAULT_RATE: tuple[float, float] = (0.0, 0.0)


def rate_for(model: str, override: tuple[float, float] | None = None) -> tuple[float, float]:
    if override is not None:
        return override
    return PRICING.get(model, DEFAULT_RATE)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    per_call: list[dict[str, Any]] = field(default_factory=list)
    rate_override: tuple[float, float] | None = None

    def add(self, model: str, stage: str, input_tokens: int, output_tokens: int,
            thinking_tokens: int = 0) -> None:
        rate_in, rate_out = rate_for(model, self.rate_override)
        billable_out = output_tokens + thinking_tokens
        cost = (input_tokens / 1_000_000) * rate_in + (billable_out / 1_000_000) * rate_out
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.thinking_tokens += thinking_tokens
        self.cost_usd += cost
        self.calls += 1
        self.per_call.append({
            "stage": stage, "model": model, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "thinking_tokens": thinking_tokens,
            "cost_usd": round(cost, 6),
        })

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "total_tokens": self.input_tokens + self.output_tokens + self.thinking_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
            "per_call": self.per_call,
        }
