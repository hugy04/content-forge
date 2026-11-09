"""Stage 4: gate the draft, and repair it a bounded number of times.

The loop is bounded for two reasons: an unbounded repair loop can burn the
five-minute budget on a model that will not converge, and each pass costs money.
After the budget is spent the run still completes - it is marked `partial` with
the failing checks attached, which is more useful to a content team than a hard
failure that returns nothing.
"""
from __future__ import annotations

import logging

from forge.pipeline.prompts import DRAFT_SCHEMA, REVISE_SYSTEM, revise_user
from forge.pipeline.validators import validate

log = logging.getLogger("forge.qc")


async def run(ctx, deps) -> None:
    settings = deps.settings
    ctx.qc = validate(
        ctx.draft,
        target_min=settings.target_words_min,
        target_max=settings.target_words_max,
        outline=ctx.outline,
    )

    while not ctx.qc["passed"] and ctx.revisions < settings.max_revisions:
        if ctx.deadline.remaining < 30:
            ctx.warnings.append(
                f"stopped revising after {ctx.revisions} pass(es): "
                f"only {ctx.deadline.remaining:.0f}s of budget left"
            )
            break

        instruction = ctx.qc["repair_instruction"] or "Fix the failing checks."
        log.info("run %s revision %d: %s", ctx.run_id, ctx.revisions + 1, ctx.qc["errors"])
        ctx.revisions += 1

        revised = await deps.text.complete_json(
            stage=f"draft_revise_{ctx.revisions}",
            model=settings.draft_model,
            effort=settings.draft_effort,
            system=REVISE_SYSTEM,
            user=revise_user(
                ctx.draft, instruction,
                settings.target_words_min, settings.target_words_max,
                ctx.qc["word_count"],
            ),
            schema=DRAFT_SCHEMA,
            max_tokens=8000,
            usage=ctx.usage,
        )
        # Keep the revision only if it is actually an improvement on the gate.
        candidate = validate(
            revised,
            target_min=settings.target_words_min,
            target_max=settings.target_words_max,
            outline=ctx.outline,
        )
        if candidate["passed"] or len(candidate["errors"]) < len(ctx.qc["errors"]):
            ctx.draft, ctx.qc = revised, candidate
        else:
            ctx.warnings.append(
                f"revision {ctx.revisions} did not improve the gate; kept the previous draft"
            )
            break

    if not ctx.qc["passed"]:
        ctx.warnings.append(f"quality gate not met: {ctx.qc['errors']}")
