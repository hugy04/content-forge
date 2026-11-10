"""Stage 5a: turn the editorial image brief into a generation prompt."""
from __future__ import annotations

from forge.pipeline.prompts import (
    IMAGE_PROMPT_SCHEMA,
    IMAGE_PROMPT_SYSTEM,
    image_prompt_user,
)


async def run(ctx, deps) -> None:
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
