"""Stage 5b: generate the visual asset through the provider chain."""
from __future__ import annotations

import hashlib

from forge.diversity.taxonomy import NEGATIVE_PROMPT, STYLES
from forge.providers.image.registry import ImageRegistry
from forge.providers.retry import ProviderError


async def run(ctx, deps) -> None:
    registry = ImageRegistry(deps.settings)
    # Seed from the run id so the same run reproduces the same image.
    seed = int(hashlib.sha256(ctx.run_id.encode()).hexdigest()[:8], 16)
    try:
        result, attempts = await registry.generate(
            prompt=ctx.image_prompt.get("prompt", ctx.topic),
            negative=NEGATIVE_PROMPT,
            seed=seed,
            title=ctx.outline.get("title") or ctx.topic,
            style_label=STYLES[ctx.selection.style].label,
            palette=ctx.selection.palette,
        )
    except ProviderError as exc:
        ctx.errors.append(f"image generation failed entirely: {exc}")
        ctx.image_attempts = [{"provider": "chain", "ok": False, "error": str(exc)}]
        return

    ctx.image = result
    ctx.image_attempts = attempts
    if not result.generated:
        ctx.warnings.append(
            "no generative image provider was reachable; a locally rendered "
            "fallback card was used instead"
        )
