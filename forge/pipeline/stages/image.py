"""Stage 5b: generate the visual asset through the provider chain."""
from __future__ import annotations

import hashlib

import io

from PIL import Image

from forge.diversity.taxonomy import NEGATIVE_PROMPT, STYLES
from forge.providers.image.registry import ImageRegistry
from forge.providers.image.treatment import apply as apply_treatment
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
            # Leave room for publish; never spend the whole remaining budget
            # waiting on a paced provider.
            budget=max(0.0, ctx.deadline.remaining - 25),
        )
    except ProviderError as exc:
        ctx.errors.append(f"image generation failed entirely: {exc}")
        ctx.image_attempts = [{"provider": "chain", "ok": False, "error": str(exc)}]
        return

    # Art direction is applied here, not left to the renderer. The locally
    # drawn card already embodies its palette, so it is left alone.
    if result.generated:
        result = _styled(result, ctx)

    ctx.image = result
    ctx.image_attempts = attempts
    if not result.generated:
        ctx.warnings.append(
            "no generative image provider was reachable; a locally rendered "
            "fallback card was used instead"
        )


def _styled(result, ctx):
    """Apply the run's art direction to a generated image."""
    try:
        source = Image.open(io.BytesIO(result.data))
        treated, applied = apply_treatment(
            source,
            ctx.selection.art_direction,
            ctx.selection.palette,
            seed=int(ctx.run_id[:8], 16),
        )
        buffer = io.BytesIO()
        treated.save(buffer, format="PNG")
        result.data = buffer.getvalue()
        result.width, result.height = treated.size
        result.meta["treatment"] = applied
    except Exception as exc:  # noqa: BLE001 - never lose a good image to styling
        ctx.warnings.append(f"art-direction treatment skipped: {exc}")
        result.meta["treatment"] = "skipped"
    return result
