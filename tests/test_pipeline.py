"""End-to-end, with fixtures: no API key, no network, no Redis."""
from __future__ import annotations

import json
from pathlib import Path

from forge.pipeline.context import Deadline, DeadlineExceeded, RunContext, count_words
from forge.pipeline.runner import generate, run_pipeline


async def test_full_run_produces_every_artifact(tmp_settings, deps):
    ctx = await generate("EV battery recycling in India", deps=deps)

    assert ctx.status in ("completed", "partial")
    assert ctx.selection is not None
    assert tmp_settings.target_words_min <= ctx.word_count <= tmp_settings.target_words_max

    folder = Path(ctx.artifacts["local"]["folder"])
    assert (folder / "content.md").exists()
    assert (folder / "image.png").exists()
    assert (folder / "run.json").exists()

    record = json.loads((folder / "run.json").read_text())
    assert record["run_id"] == ctx.run_id
    assert record["body_markdown"], "run.json must be self-contained"

    markdown = (folder / "content.md").read_text()
    assert markdown.startswith("---"), "front matter is missing"
    assert f"run_id: \"{ctx.run_id}\"" in markdown


async def test_the_quality_gate_repairs_a_short_draft(deps):
    """The fixture's first draft is deliberately under the floor."""
    ctx = await generate("A topic needing revision", deps=deps)
    assert ctx.revisions >= 1, "the revise loop should have been triggered"
    assert ctx.qc["passed"] is True


async def test_revision_count_is_bounded(deps):
    deps.settings.max_revisions = 2
    ctx = await generate("Bounded revisions", deps=deps)
    assert ctx.revisions <= 2


async def test_every_stage_is_timed(deps):
    ctx = await generate("Timing coverage", deps=deps)
    for stage in ("plan", "outline", "draft", "qc", "image_prompt", "image", "publish"):
        assert stage in ctx.timings, f"{stage} was not timed"
    assert ctx.deadline.elapsed < 300


async def test_consecutive_runs_differ_in_style_and_art_direction(deps):
    a = await generate("First topic", deps=deps)
    b = await generate("Second topic", deps=deps)
    assert a.selection.style != b.selection.style
    assert a.selection.art_direction != b.selection.art_direction


async def test_a_blown_deadline_fails_the_run_cleanly(deps):
    ctx = RunContext(topic="No time left")
    ctx.deadline = Deadline(budget_seconds=0.0001)
    result = await run_pipeline(ctx, deps)
    assert result.status == "failed"
    assert any("deadline" in e.lower() for e in result.errors)


async def test_the_run_is_persisted_and_retrievable(deps):
    ctx = await generate("Persistence check", deps=deps)
    record = deps.store.get_run(ctx.run_id)
    assert record is not None
    assert record["status"] == ctx.status
    assert record["word_count"] == ctx.word_count


async def test_stats_aggregate_across_runs(deps):
    await generate("Stats one", deps=deps)
    await generate("Stats two", deps=deps)
    stats = deps.store.stats()
    assert stats["total"] >= 2
    assert stats["avg_word_count"] > 0
    assert len(stats["style_distribution"]) >= 1


def test_word_count_ignores_markdown_syntax():
    assert count_words("## Heading\n\n**bold** _italic_ word") == 4


async def test_each_run_in_a_batch_reports_only_its_own_token_usage(deps):
    """The provider ledger is shared; the run ledger must not be.

    Regression: ctx.usage was aliased to the provider's process-wide Usage, so
    the fourth run in a batch reported all four runs' tokens as its own, and
    every per-asset cost figure downstream was wrong.
    """
    totals = []
    for topic in ("First batch topic", "Second batch topic", "Third batch topic"):
        ctx = await generate(topic, deps=deps)
        totals.append(ctx.usage.input_tokens)

    assert totals[0] > 0
    assert all(t == totals[0] for t in totals), (
        f"per-run usage is accumulating across the batch: {totals}")
    assert deps.text.usage.input_tokens == sum(totals), (
        "the provider ledger should still hold the process-wide total")


async def test_the_recorded_duration_stops_when_the_run_does(deps):
    """Regression: a live clock read after the fact reports the wrong number.

    `Deadline.elapsed` must stay live during the run (stages budget against
    it), but once the run is over it has to freeze - otherwise a batch runner
    that prints durations at the end reports the whole batch's wall clock for
    every run in it.
    """
    import asyncio

    ctx = await generate("A topic for the clock test", deps=deps)
    first = ctx.deadline.elapsed
    recorded = ctx.summary()["total_seconds"]
    await asyncio.sleep(0.2)

    assert ctx.deadline.elapsed == first, "the clock kept running after the run"
    assert abs(recorded - first) < 0.01
