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
from typing import Any

import anthropic

from forge.providers.retry import BREAKER, ProviderError, retrying
from forge.providers.text.usage import PRICING, Usage  # re-exported for callers

log = logging.getLogger("forge.text")


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
                    schema: dict[str, Any] | None,
                    usage: Usage | None = None) -> str:
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
                counts = (response.usage.input_tokens, response.usage.output_tokens)
                self.usage.add(model, stage, *counts)
                if usage is not None and usage is not self.usage:
                    usage.add(model, stage, *counts)
                text = "".join(b.text for b in response.content if b.type == "text")
                if not text.strip():
                    raise ProviderError(f"claude returned no text at stage {stage}")
                return text
        raise ProviderError("unreachable")

    async def complete_json(self, *, stage: str, model: str, effort: str, system: str,
                            user: str, schema: dict[str, Any],
                            max_tokens: int = 8000,
                            usage: Usage | None = None) -> dict[str, Any]:
        raw = await self._call(stage=stage, model=model, effort=effort, system=system,
                               user=user, max_tokens=max_tokens, schema=schema,
                               usage=usage)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"claude returned unparseable JSON at stage {stage}: {exc}"
            ) from exc

    async def complete_text(self, *, stage: str, model: str, effort: str, system: str,
                            user: str, max_tokens: int = 8000,
                            usage: Usage | None = None) -> str:
        return await self._call(stage=stage, model=model, effort=effort, system=system,
                                user=user, max_tokens=max_tokens, schema=None,
                                usage=usage)
