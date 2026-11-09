"""Stage 1: choose this run's creative coordinates before any model is called."""
from __future__ import annotations

from forge.diversity.engine import DiversityEngine


async def run(ctx, deps) -> None:
    engine: DiversityEngine = deps.engine
    history = deps.store.recent_selections(deps.settings.diversity_history)
    ctx.selection = engine.select(ctx.run_id, history, overrides=ctx.overrides)
    deps.store.record_selection(
        ctx.run_id, ctx.created_at, ctx.topic_hash, ctx.selection.as_dict()
    )
