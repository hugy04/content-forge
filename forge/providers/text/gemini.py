"""Gemini text provider, spoken over the REST API.

Why REST rather than the google-genai SDK: the pipeline needs exactly two verbs
(JSON completion, text completion) and already owns its retry, circuit-breaker
and deadline machinery. A thin HTTP client keeps the dependency surface at one
library (httpx, already required by the image providers) and makes every wire
detail visible in this file, which matters because the three details below are
the ones that actually break in practice.

  1. MODEL IDS MOVE. Google retires Flash ids on a roughly quarterly cadence
     (`gemini-2.5-flash` was deprecated in Oct 2026), so a hardcoded id is a
     time bomb. This provider treats the configured id as a preference, and on
     a NOT_FOUND it calls ListModels and resolves the best available candidate
     once per process.
  2. SCHEMA DIALECT DIFFERS. Gemini's `responseSchema` is an OpenAPI subset and
     rejects `additionalProperties`, which JSON Schema (and the Anthropic
     structured-output format) requires. Schemas are translated, not duplicated,
     so the pipeline keeps one schema definition per stage.
  3. THINKING IS CONFIGURED, NOT BUDGETED. Gemini 3.x takes
     `thinkingConfig.thinkingLevel`; older models took `thinkingBudget`. If the
     field is rejected the call is retried once without it rather than failing,
     so a model change cannot take the pipeline down.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from forge.providers.retry import (
    BREAKER,
    CircuitOpen,
    ProviderError,
    retrying,
)
from forge.providers.throttle import LIMITER, TooLongToWait
from forge.providers.text.usage import Usage

log = logging.getLogger("forge.text.gemini")

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

# Preference order used when the configured model is not available to this key.
# Newest first; the first id the API actually offers wins.
# Ordered by what a free key can actually reach, measured 9 Nov 2026 - not by
# version number. The newest ids are the most capacity-constrained, so they sit
# below the one that answers reliably. Re-check with scripts/gemini_doctor.py.
MODEL_PREFERENCE: tuple[str, ...] = (
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-flash-latest",
)

_EFFORT_TO_LEVEL = {"low": "low", "medium": "medium", "high": "high"}


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate a JSON Schema into Gemini's OpenAPI subset.

    Drops the keywords Gemini rejects and preserves key order explicitly via
    `propertyOrdering`, which measurably improves field-ordering stability in
    the generated JSON.
    """
    unsupported = {"additionalProperties", "$schema", "definitions", "$defs", "default"}
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in unsupported:
            continue
        if key == "properties" and isinstance(value, dict):
            out["properties"] = {k: to_gemini_schema(v) for k, v in value.items()}
            out["propertyOrdering"] = list(value.keys())
        elif key == "items" and isinstance(value, dict):
            out["items"] = to_gemini_schema(value)
        else:
            out[key] = value
    return out


class GeminiTextProvider:
    """Free-tier-first text provider. Interface-compatible with ClaudeTextProvider."""

    name = "gemini"

    def __init__(self, api_key: str, usage: Usage | None = None,
                 default_model: str = "gemini-3.8-flash",
                 timeout: float = 120.0, min_interval: float = 0.0):
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey, put it in .env, or run "
                "with FAKE_PROVIDERS=1 to use fixtures."
            )
        self._key = api_key
        self._default_model = default_model
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"x-goog-api-key": api_key}
        )
        self.usage = usage or Usage()
        self._resolved: dict[str, str] = {}
        self._available: list[str] = []
        # Free-tier request-per-minute caps are low; spacing calls is cheaper
        # than discovering the cap with a 429 and a backoff.
        self._min_interval = min_interval
        # Models whose *daily* quota is spent. The breaker half-opens after 60s,
        # which is right for a transient fault and wrong for a cap that resets
        # at midnight Pacific - without this the pipeline re-probes a dead model
        # on every run for the rest of the day.
        self._exhausted: set[str] = set()
        self._lock = asyncio.Lock()
        self._no_thinking: set[str] = set()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- model resolution ---------------------------------------------------

    async def list_models(self) -> list[str]:
        response = await self._client.get(f"{API_ROOT}/models", params={"pageSize": 200})
        response.raise_for_status()
        names: list[str] = []
        for entry in response.json().get("models", []):
            methods = entry.get("supportedGenerationMethods") or entry.get(
                "supportedActions") or []
            if methods and "generateContent" not in methods:
                continue
            names.append(entry.get("name", "").removeprefix("models/"))
        return [n for n in names if n]

    async def _resolve(self, requested: str) -> str:
        """Map a requested model id onto one this key can actually call."""
        if requested in self._resolved:
            return self._resolved[requested]
        async with self._lock:
            if requested in self._resolved:
                return self._resolved[requested]
            try:
                available = await self.list_models()
                self._available = available
            except Exception as exc:  # discovery is best-effort
                log.warning("gemini ListModels failed (%s); using %s as-is", exc, requested)
                self._resolved[requested] = requested
                return requested
            chosen = requested if requested in available else None
            if chosen is None:
                for candidate in (requested, *MODEL_PREFERENCE):
                    match = next((m for m in available if m == candidate), None) or next(
                        (m for m in available if m.startswith(candidate)), None)
                    if match:
                        chosen = match
                        break
            if chosen is None:
                flashes = sorted(m for m in available if "flash" in m)
                chosen = flashes[0] if flashes else (available[0] if available else requested)
            if chosen != requested:
                log.warning("gemini model %s unavailable; resolved to %s", requested, chosen)
            self._resolved[requested] = chosen
            return chosen

    # -- the call ------------------------------------------------------------

    def _body(self, *, system: str, user: str, effort: str, max_tokens: int,
              schema: dict[str, Any] | None, with_thinking: bool) -> dict[str, Any]:
        generation: dict[str, Any] = {
            # Thinking tokens are drawn from this same budget, so leave headroom.
            "maxOutputTokens": min(max(max_tokens * 2, 8192), 32768),
        }
        if schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = to_gemini_schema(schema)
        if with_thinking:
            generation["thinkingConfig"] = {
                "thinkingLevel": _EFFORT_TO_LEVEL.get(effort, "medium")
            }
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": generation,
        }

    async def _post(self, model: str, body: dict[str, Any]) -> httpx.Response:
        return await self._client.post(
            f"{API_ROOT}/models/{model}:generateContent", json=body
        )

    def _fallback_models(self, primary: str) -> list[str]:
        """Models to try, in order, if `primary` is unavailable.

        Measured on a free key (Nov 2026): `gemini-3.8-flash` and
        `gemini-flash-latest` both answer 503 "experiencing high demand" while
        `gemini-3.6-flash` serves normally. Capacity pressure on the newest id
        is not a transient blip that backoff fixes - it is a reason to use a
        different model. Retry handles the blip; this handles the blip that
        lasts.
        """
        pool = self._available or list(MODEL_PREFERENCE)
        ordered = [m for m in MODEL_PREFERENCE
                   if m in pool and m != primary and m not in self._exhausted]
        candidates = [primary, *ordered]
        if primary in self._exhausted and ordered:
            candidates = ordered
        return candidates[:3]

    async def _call(self, *, stage: str, model: str, effort: str, system: str,
                    user: str, max_tokens: int,
                    schema: dict[str, Any] | None,
                    usage: Usage | None = None) -> str:
        primary = await self._resolve(model or self._default_model)
        candidates = self._fallback_models(primary)
        last: ProviderError | None = None

        for index, candidate in enumerate(candidates):
            try:
                return await self._attempt(
                    stage=stage, model=candidate, effort=effort, system=system,
                    user=user, max_tokens=max_tokens, schema=schema, usage=usage,
                )
            except ProviderError as exc:
                last = exc
                if index + 1 < len(candidates) and _is_capacity_failure(exc):
                    log.warning(
                        "gemini %s unavailable (%s); falling forward to %s",
                        candidate, exc, candidates[index + 1],
                    )
                    continue
                raise
        raise last or ProviderError("gemini: no candidate model succeeded")

    async def _attempt(self, *, stage: str, model: str, effort: str, system: str,
                       user: str, max_tokens: int,
                       schema: dict[str, Any] | None,
                       usage: Usage | None = None) -> str:
        resolved = model
        key = f"gemini:{resolved}"

        async for attempt in retrying(attempts=3):
            with attempt:
                BREAKER.check(key)
                await LIMITER.acquire(f"text:{resolved}", self._min_interval, 90.0)
                body = self._body(
                    system=system, user=user, effort=effort, max_tokens=max_tokens,
                    schema=schema, with_thinking=resolved not in self._no_thinking,
                )
                try:
                    response = await self._post(resolved, body)
                except httpx.HTTPError as exc:
                    BREAKER.record_failure(key)
                    raise ProviderError(f"gemini transport failure: {exc}") from exc

                if response.status_code == 400 and "thinking" in response.text.lower():
                    # This model does not take thinkingLevel. Drop it permanently
                    # for this id and retry immediately rather than burning the
                    # backoff budget on a deterministic failure.
                    log.info("gemini %s rejected thinkingConfig; disabling it", resolved)
                    self._no_thinking.add(resolved)
                    body = self._body(
                        system=system, user=user, effort=effort,
                        max_tokens=max_tokens, schema=schema, with_thinking=False,
                    )
                    response = await self._post(resolved, body)

                if response.status_code in (429, 500, 502, 503, 504):
                    detail = _message(response)
                    if response.status_code == 429 and _is_daily_cap(response):
                        # Nothing recovers this before midnight Pacific.
                        self._exhausted.add(resolved)
                        log.warning(
                            "gemini %s has spent its daily quota; excluding it "
                            "for the rest of this process", resolved,
                        )
                    BREAKER.record_failure(key)
                    raise ProviderError(
                        f"gemini {response.status_code} at stage {stage}: {detail}"
                    )
                if response.status_code >= 400:
                    # 4xx other than rate limiting is our bug; do not retry it.
                    raise ProviderError(
                        f"gemini {response.status_code} at stage {stage}: "
                        f"{_message(response)}"
                    )

                payload = response.json()
                text, thinking = _extract(payload)
                finish = _finish_reason(payload)
                if finish == "MAX_TOKENS":
                    BREAKER.record_failure(key)
                    raise ProviderError(
                        f"gemini hit maxOutputTokens at stage {stage}; output truncated"
                    )
                if finish in {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"}:
                    raise ProviderError(f"gemini blocked the request at {stage} ({finish})")
                if not text.strip():
                    BREAKER.record_failure(key)
                    raise ProviderError(
                        f"gemini returned no text at stage {stage} (finish={finish})"
                    )

                BREAKER.record_success(key)
                meta = payload.get("usageMetadata", {})
                counts = (
                    int(meta.get("promptTokenCount", 0)),
                    int(meta.get("candidatesTokenCount", 0)),
                    int(meta.get("thoughtsTokenCount", 0)),
                )
                # Two ledgers on purpose: `self.usage` is the process total
                # behind /v1/stats, `usage` is this run's own, so a batch does
                # not report every run's cost as the running total.
                self.usage.add(resolved, stage, *counts)
                if usage is not None and usage is not self.usage:
                    usage.add(resolved, stage, *counts)
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
            # Some responses wrap JSON in a fenced block despite responseMimeType.
            stripped = _unfence(raw)
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                raise ProviderError(
                    f"gemini returned unparseable JSON at stage {stage}: {exc}"
                ) from exc

    async def complete_text(self, *, stage: str, model: str, effort: str, system: str,
                            user: str, max_tokens: int = 8000,
                            usage: Usage | None = None) -> str:
        return await self._call(stage=stage, model=model, effort=effort, system=system,
                                user=user, max_tokens=max_tokens, schema=None,
                                usage=usage)


CAPACITY_MARKERS = ("503", "429", "high demand", "overloaded", "quota", "404",
                    "rate limit", "resource_exhausted")


def _is_capacity_failure(exc: ProviderError) -> bool:
    """Is this a 'use a different model' failure rather than a real error?

    CircuitOpen counts. An open breaker means *this model* has failed
    repeatedly, which is precisely when another model should be tried - without
    this, the breaker that exists to protect the latency budget instead becomes
    the thing that fails the run.
    """
    if isinstance(exc, (CircuitOpen, TooLongToWait)):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in CAPACITY_MARKERS)


def _extract(payload: dict[str, Any]) -> tuple[str, str]:
    """Split a candidate's parts into answer text and thought summaries."""
    candidates = payload.get("candidates") or []
    if not candidates:
        return "", ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    answer = [p.get("text", "") for p in parts if p.get("text") and not p.get("thought")]
    thought = [p.get("text", "") for p in parts if p.get("text") and p.get("thought")]
    return "".join(answer), "".join(thought)


def _finish_reason(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        block = (payload.get("promptFeedback") or {}).get("blockReason")
        return block or "NO_CANDIDATES"
    return candidates[0].get("finishReason", "STOP")


def _is_daily_cap(response: httpx.Response) -> bool:
    """Distinguish 'too fast' from 'done for today'.

    A per-minute 429 is worth retrying in a few seconds; a per-day 429 is not
    worth retrying at all, and the two are only separable from the quota
    violation detail Google attaches to the error.
    """
    try:
        body = response.json()
    except Exception:
        return "per day" in response.text.lower()
    blob = str(body).lower()
    return "per day" in blob or "perday" in blob


def _message(response: httpx.Response) -> str:
    try:
        return str((response.json().get("error") or {}).get("message", ""))[:300]
    except Exception:
        return response.text[:300]


def _unfence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()
