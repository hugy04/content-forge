"""Stage 5a: turn the editorial image brief into a generation prompt."""
from __future__ import annotations

import dataclasses

from forge.pipeline.prompts import (
    IMAGE_PROMPT_SCHEMA,
    IMAGE_PROMPT_SYSTEM,
    composition_for,
    image_prompt_user,
)


async def run(ctx, deps) -> None:
    # The engine picks a composition before the outline exists, so it cannot
    # know whether it is framing a thing or a place. Record the correction
    # rather than letting the run claim a framing it did not use.
    framing, note = composition_for(ctx.selection, ctx.outline)
    if note:
        ctx.warnings.append(note)
        ctx.selection = dataclasses.replace(ctx.selection, composition=framing)

    ctx.image_prompt = await deps.text.complete_json(
        stage="image_prompt",
        model=deps.settings.prompt_model,
        effort=deps.settings.prompt_effort,
        system=IMAGE_PROMPT_SYSTEM,
        user=image_prompt_user(ctx.topic, ctx.selection, ctx.outline),
        schema=IMAGE_PROMPT_SCHEMA,
        max_tokens=1200,
        usage=ctx.usage,
        # Leave 30s behind: the image itself still has to be fetched. Without this the
        # provider's model fall-forward can spend the whole budget.
        budget=max(0.0, ctx.deadline.remaining - 30.0),
    )
