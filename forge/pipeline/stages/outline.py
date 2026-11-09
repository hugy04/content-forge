"""Stage 2: plan the piece. One Claude call, structured output."""
from __future__ import annotations

from forge.pipeline.prompts import OUTLINE_SCHEMA, OUTLINE_SYSTEM, outline_user


async def run(ctx, deps) -> None:
    ctx.deadline.require("outline", needs=20)
    target = (deps.settings.target_words_min + deps.settings.target_words_max) // 2
    ctx.outline = await deps.text.complete_json(
        stage="outline",
        model=deps.settings.outline_model,
        effort=deps.settings.outline_effort,
        system=OUTLINE_SYSTEM,
        user=outline_user(ctx.topic, ctx.selection, target),
        schema=OUTLINE_SCHEMA,
        max_tokens=4000,
    )
