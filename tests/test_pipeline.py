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


def test_the_health_check_asks_about_the_active_text_provider():
    """Regression: the hosted deploy reported a healthy service as broken.

    `/healthz` checked `anthropic_api_key` regardless of which backend was
    selected. On the Gemini default - the shipped configuration - that key is
    deliberately empty, so a correctly configured instance advertised itself as
    unconfigured, and the obvious "fix" is to paste a key the code never reads.
    """
    from tests.conftest import isolated_settings

    gemini = isolated_settings(text_provider="gemini", gemini_api_key="g-key")
    assert gemini.text_provider_key == "g-key"
    assert gemini.text_provider_configured is True

    # The Anthropic key being blank must not make the Gemini path look broken.
    assert gemini.anthropic_api_key == ""

    claude = isolated_settings(text_provider="claude", anthropic_api_key="a-key")
    assert claude.text_provider_key == "a-key"
    assert claude.text_provider_configured is True

    neither = isolated_settings(text_provider="gemini")
    assert neither.text_provider_configured is False

    # Fixture mode needs no key at all - that is the whole point of it.
    offline = isolated_settings(text_provider="gemini", fake_providers=True)
    assert offline.text_provider_configured is True


def test_an_unknown_text_provider_reports_unconfigured_rather_than_crashing():
    """A typo in TEXT_PROVIDER should surface as a health signal, not a 500."""
    from tests.conftest import isolated_settings

    bogus = isolated_settings(text_provider="gemeni", gemini_api_key="g-key")
    assert bogus.text_provider_key == ""
    assert bogus.text_provider_configured is False


async def test_a_deadline_blown_late_keeps_what_was_already_produced(deps, monkeypatch):
    """Regression: a hosted run produced an image and threw it away.

    The budget ran out at the draft stage, the whole run was marked `failed`,
    and publish never ran - so a generated image, a finished outline and three
    minutes of the caller's waiting were discarded. On a free tier the usual
    reason the clock runs out is an upstream model queueing, which is not a
    reason to bin the work that did complete.
    """
    from forge.pipeline.context import DeadlineExceeded
    from forge.pipeline.stages import qc as qc_stage

    async def blow_the_budget(ctx, deps):
        raise DeadlineExceeded("qc needs ~20s but only 3s of the 300s budget remains")

    monkeypatch.setattr(qc_stage, "run", blow_the_budget)
    ctx = await generate("A topic whose clock runs out late", deps=deps)

    assert ctx.status == "partial", "work that completed must not be thrown away"
    assert ctx.draft, "the draft produced before the deadline is still attached"
    assert any("deadline exceeded" in e for e in ctx.errors)
    assert any("five-minute budget ran out" in w for w in ctx.warnings), (
        "the run must say why it is short, not just that it is")
    assert "local" in ctx.artifacts, "the salvage must still reach the sink"


async def test_a_deadline_blown_before_anything_exists_is_still_a_failure(deps):
    """Salvage is not an excuse to call an empty run successful."""
    from forge.pipeline.context import Deadline

    ctx = RunContext(topic="No time at all")
    ctx.deadline = Deadline(budget_seconds=0.0001)
    result = await run_pipeline(ctx, deps)

    assert result.status == "failed"
    assert not result.draft


async def test_the_image_carries_a_visible_caption_as_well_as_alt_text(deps):
    """Alt text was written for screen readers and then never shown.

    An editorial image that illustrates a piece obliquely - empty stadium seats
    for an article about a manager leaving - leaves a reader decoding it. A
    caption is how every publication solves that, and the text already existed.
    """
    ctx = await generate("A topic that needs a captioned image", deps=deps)
    markdown = Path(ctx.artifacts["local"]["folder"], "content.md").read_text()

    alt = ctx.image_prompt.get("alt_text")
    assert alt, "the pipeline should always produce alt text"
    assert f"![{alt}](image.png)" in markdown, "alt text belongs on the image"
    assert f"*{alt}*" in markdown, "and the same line should render as a caption"


async def test_the_markdown_is_downloadable_even_when_the_image_is_missing(deps):
    """The page used to derive the .md URL by editing the image URL.

    That breaks precisely when a run has no image - the degraded case whose
    text a reader most wants to keep - so the sink states both paths.
    """
    ctx = await generate("A topic for the download links", deps=deps)
    local = ctx.artifacts["local"]

    assert local["markdown_url_path"].endswith("/content.md")
    assert local["markdown_url_path"].startswith("/artifacts/")
    # Independent of whether an image was produced.
    assert "image.png" not in local["markdown_url_path"]


def test_a_repair_note_stays_out_of_the_reader_s_way():
    """`warnings` is the engineering record; `notices` is what a reader sees."""
    from forge.pipeline.context import RunContext

    ctx = RunContext(topic="battery recycling")
    ctx.warnings.append("markdown arrived without line breaks and was normalised "
                        "(headings and lists restored)")
    assert ctx.warnings, "the record keeps it"
    assert ctx.notices == [], "the page does not show it"


def test_a_warning_that_changed_the_request_is_shown():
    """Being told the topic was rewritten is not plumbing, it is the answer."""
    from forge.pipeline.context import RunContext

    ctx = RunContext(topic="Make me a picture of a mango")
    assert ctx.topic == "mango"
    assert any("does not take instructions" in n for n in ctx.notices)


def test_notices_travel_in_the_summary():
    from forge.pipeline.context import RunContext

    ctx = RunContext(topic="Please write an article about warehouse robotics")
    summary = ctx.summary()
    assert "notices" in summary and "warnings" in summary
    assert summary["notices"] == ctx.notices


def test_changing_any_setting_produces_a_different_run():
    """The regression: changing Image style re-served the cached run.

    The key was built from topic + style + angle + date, which was every
    dimension that existed when it was written. Once all six became settable,
    four of them could be changed without changing the key, and the cached run
    came back unchanged - indistinguishable from the setting being ignored.
    """
    from forge.diversity.taxonomy import DIMENSIONS
    from forge.pipeline.context import RunContext

    base = {"style": "opinion", "angle": "economics", "audience": "newcomers",
            "art_direction": "risograph", "palette": "cobalt_sand",
            "composition": "environmental"}
    alternative = {"style": "guide", "angle": "risk", "audience": "builders",
                   "art_direction": "flat_vector", "palette": "forest_clay",
                   "composition": "close_detail"}

    reference = RunContext(topic="air pollution", overrides=base).idempotency_key
    for dimension in DIMENSIONS:
        changed = dict(base, **{dimension: alternative[dimension]})
        key = RunContext(topic="air pollution", overrides=changed).idempotency_key
        assert key != reference, f"changing {dimension} did not change the key"


def test_an_identical_request_still_shares_one_key():
    """The point of the key: a retry must not pay for the work twice."""
    from forge.pipeline.context import RunContext

    overrides = {"style": "opinion", "palette": "cobalt_sand"}
    a = RunContext(topic="air pollution", overrides=overrides)
    b = RunContext(topic="Air Pollution ", overrides=dict(overrides))
    assert a.idempotency_key == b.idempotency_key


def test_the_fallback_card_is_not_announced_twice():
    """The card carries its own label, inside the image, where it stays with
    the file. A banner above it is the page saying so a second time."""
    from forge.pipeline.context import RunContext

    ctx = RunContext(topic="battery recycling")
    ctx.warnings.append("no generative image provider was reachable; a locally "
                        "rendered fallback card was used instead")
    assert ctx.warnings, "the run record still knows"
    assert ctx.notices == [], "the reader is told once, by the card"


def test_the_card_label_does_not_read_as_an_incident_report():
    from forge.providers.image.card import CardProvider
    import inspect

    source = inspect.getsource(CardProvider)
    assert "no generative provider reachable" not in source
    assert "generated cover" in source
