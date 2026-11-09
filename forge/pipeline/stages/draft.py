"""Stage 3: write the piece against the approved plan."""
from __future__ import annotations

from forge.pipeline.prompts import DRAFT_SCHEMA, DRAFT_SYSTEM, draft_user


async def run(ctx, deps) -> None:
    ctx.deadline.require("draft", needs=25)
    ctx.draft = await deps.text.complete_json(
        stage="draft",
        model=deps.settings.draft_model,
        effort=deps.settings.draft_effort,
        system=DRAFT_SYSTEM,
        user=draft_user(
            ctx.topic, ctx.selection, ctx.outline,
            deps.settings.target_words_min, deps.settings.target_words_max,
        ),
        schema=DRAFT_SCHEMA,
        max_tokens=8000,
    )
