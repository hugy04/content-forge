"""Pipeline orchestration.

The shape that matters is the fork after `outline`: the text branch (draft, then
the QC/revise loop) and the visual branch (image prompt, then generation) have
no dependency on each other once the outline exists, so they run concurrently.
On a typical run that overlaps roughly 20-40 seconds of image generation with
the draft, which is the difference between comfortably inside the five-minute
budget and uncomfortably close to it.

Failure policy:
  * text branch fails  -> the run fails; there is no asset without the words
  * visual branch fails -> the run degrades to `partial` and still publishes
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from forge.config import Settings, get_settings
from forge.diversity.engine import DiversityEngine
from forge.pipeline.context import Deadline, DeadlineExceeded, RunContext
from forge.pipeline.stages import (
    draft as draft_stage,
    image as image_stage,
    image_prompt as image_prompt_stage,
    notify as notify_stage,
    outline as outline_stage,
    plan as plan_stage,
    publish as publish_stage,
    qc as qc_stage,
)
from forge.providers.text.usage import Usage
from forge.store.db import Store

log = logging.getLogger("forge.runner")


@dataclass
class Deps:
    settings: Settings
    store: Store
    engine: DiversityEngine
    text: Any


def build_text_provider(settings: Settings, usage: Usage) -> Any:
    """Pick the text backend. Imports are local so that running on one provider
    never requires the other's SDK to be installed."""
    if settings.fake_providers:
        from forge.providers.text.fake import FakeTextProvider
        return FakeTextProvider(usage)

    provider = settings.text_provider.strip().lower()
    if provider == "gemini":
        from forge.providers.text.gemini import GeminiTextProvider
        return GeminiTextProvider(
            settings.gemini_api_key, usage,
            default_model=settings.gemini_model,
            timeout=settings.text_timeout_seconds,
            min_interval=settings.gemini_min_interval_seconds,
        )
    if provider in {"claude", "anthropic"}:
        from forge.providers.text.claude import ClaudeTextProvider
        return ClaudeTextProvider(settings.anthropic_api_key, usage)
    raise ValueError(
        f"unknown TEXT_PROVIDER={settings.text_provider!r}; "
        "expected one of: gemini, claude"
    )


def build_deps(settings: Settings | None = None) -> Deps:
    settings = settings or get_settings()
    usage = Usage(rate_override=settings.price_override)
    text = build_text_provider(settings, usage)
    return Deps(
        settings=settings,
        store=Store(settings.db_path),
        engine=DiversityEngine(settings.diversity_cooldown, settings.diversity_history),
        text=text,
    )


async def _timed(ctx: RunContext, name: str, coro) -> None:
    start = time.monotonic()
    try:
        await coro
    finally:
        ctx.mark(name, time.monotonic() - start)


async def _text_branch(ctx: RunContext, deps: Deps) -> None:
    await _timed(ctx, "draft", draft_stage.run(ctx, deps))
    await _timed(ctx, "qc", qc_stage.run(ctx, deps))


async def _visual_branch(ctx: RunContext, deps: Deps) -> None:
    await _timed(ctx, "image_prompt", image_prompt_stage.run(ctx, deps))
    await _timed(ctx, "image", image_stage.run(ctx, deps))


async def run_pipeline(ctx: RunContext, deps: Deps | None = None) -> RunContext:
    deps = deps or build_deps()
    settings = deps.settings
    ctx.deadline = ctx.deadline or Deadline(budget_seconds=settings.deadline_seconds)
    # ctx.usage stays the run's own ledger; deps.text.usage is the
    # process-wide total exposed by /v1/stats.
    ctx.usage.rate_override = deps.settings.price_override

    _persist(ctx, deps, status="running")

    try:
        await _timed(ctx, "plan", plan_stage.run(ctx, deps))
        await _timed(ctx, "outline", outline_stage.run(ctx, deps))

        # The fork. return_exceptions keeps a failed image branch from
        # cancelling a healthy text branch mid-write.
        text_result, visual_result = await asyncio.gather(
            _text_branch(ctx, deps),
            _visual_branch(ctx, deps),
            return_exceptions=True,
        )
        if isinstance(text_result, BaseException):
            raise text_result
        if isinstance(visual_result, BaseException):
            log.warning("run %s visual branch failed: %s", ctx.run_id, visual_result)
            ctx.errors.append(f"visual branch failed: {visual_result}")

        ctx.status = _verdict(ctx)
        await _timed(ctx, "publish", publish_stage.run(ctx, deps))
        await _timed(ctx, "notify", notify_stage.run(ctx, deps))
        ctx.status = _verdict(ctx)

    except DeadlineExceeded as exc:
        ctx.status = "failed"
        ctx.errors.append(f"deadline exceeded: {exc}")
        log.error("run %s exceeded its budget: %s", ctx.run_id, exc)
    except Exception as exc:  # noqa: BLE001 - the record must capture any failure
        ctx.status = "failed"
        ctx.errors.append(f"{type(exc).__name__}: {exc}")
        log.exception("run %s failed", ctx.run_id)

    # Freeze the clock before anything reads it, so the recorded duration is
    # the run's own and not "time until someone printed it".
    ctx.deadline.stop()
    _persist(ctx, deps, status=ctx.status)
    return ctx


def _verdict(ctx: RunContext) -> str:
    """completed only when the words passed the gate and the image is real."""
    if ctx.errors:
        return "partial" if ctx.draft else "failed"
    if not ctx.draft:
        return "failed"
    if not ctx.qc.get("passed"):
        return "partial"
    if ctx.image is None or not getattr(ctx.image, "generated", False):
        return "partial"
    return "completed"


def _persist(ctx: RunContext, deps: Deps, status: str) -> None:
    sel = ctx.selection.as_dict() if ctx.selection else {}
    deps.store.upsert_run({
        "run_id": ctx.run_id,
        "idempotency_key": ctx.idempotency_key,
        "topic": ctx.topic,
        "status": status,
        "style": sel.get("style"),
        "angle": sel.get("angle"),
        "audience": sel.get("audience"),
        "art_direction": sel.get("art_direction"),
        "palette": sel.get("palette"),
        "composition": sel.get("composition"),
        "title": ctx.draft.get("title") if ctx.draft else None,
        "word_count": ctx.word_count if ctx.draft else None,
        "revisions": ctx.revisions,
        "image_provider": getattr(ctx.image, "provider", None),
        "qc_passed": int(bool(ctx.qc.get("passed"))),
        "duration_ms": int(ctx.deadline.elapsed * 1000) if ctx.deadline else None,
        "cost_usd": round(ctx.usage.cost_usd, 6),
        "input_tokens": ctx.usage.input_tokens,
        "output_tokens": ctx.usage.output_tokens,
        "created_at": ctx.created_at,
        "payload": ctx.summary(),
    })


async def generate(topic: str, overrides: dict[str, str] | None = None,
                   notify_url: str = "", deps: Deps | None = None) -> RunContext:
    """Convenience entry point used by the API, the worker and the CLI."""
    ctx = RunContext(topic=topic, overrides=overrides or {}, notify_url=notify_url)
    return await run_pipeline(ctx, deps)
