"""A revision the reader asked for, rather than one QC demanded.

The machinery is the same: `revise_user` is the prompt QC already uses to say
"trim 90 words from section 3". The only difference is where the instruction
comes from. That is worth saying plainly, because it is the reason this is
twenty lines rather than a second pipeline - an editor making a targeted change
is an editor making a targeted change, whoever asked.

What it deliberately does not do is regenerate the image. Someone asking for a
sharper opening is not asking for a different picture, and re-running the image
chain would cost them a minute and the pacing window for no reason.
"""
from __future__ import annotations

import logging

from forge.pipeline.markdown import repair_draft
from forge.pipeline.prompts import DRAFT_SCHEMA, REVISE_SYSTEM, revise_user
from forge.pipeline.validators import validate

log = logging.getLogger("forge.refine")

MAX_INSTRUCTION = 500


async def run(ctx, deps, instruction: str) -> None:
    settings = deps.settings
    instruction = instruction.strip()[:MAX_INSTRUCTION]
    if not instruction:
        raise ValueError("a revision needs an instruction")

    log.info("run %s: reader revision %r", ctx.run_id, instruction[:80])
    revised = await deps.text.complete_json(
        stage=f"reader_revise_{ctx.revisions + 1}",
        model=settings.draft_model,
        effort=settings.draft_effort,
        system=REVISE_SYSTEM,
        user=revise_user(ctx.draft, instruction,
                         settings.target_words_min, settings.target_words_max,
                         ctx.word_count),
        schema=DRAFT_SCHEMA,
        max_tokens=4000,
        usage=ctx.usage,
        budget=ctx.deadline.remaining,
    )
    ctx.draft = repair_draft(revised, ctx)
    ctx.revisions += 1

    # The length contract still applies: a reader asking for "more detail" does
    # not get to push the piece outside 500-800 words.
    ctx.qc = validate(ctx.draft,
                      target_min=settings.target_words_min,
                      target_max=settings.target_words_max,
                      outline=ctx.outline)
    if not ctx.qc["passed"]:
        ctx.warnings.append(
            "the revision left the piece outside the length contract: "
            + ", ".join(str(e) for e in ctx.qc["errors"]))
