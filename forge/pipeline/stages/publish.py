"""Stage 6: write the artifacts to every configured sink.

Sink failures never fail the run. The local sink always runs, so the artifacts
exist on disk even when a remote sink is misconfigured or down.
"""
from __future__ import annotations

import logging

from forge.sinks import build_sinks

log = logging.getLogger("forge.publish")


async def run(ctx, deps) -> None:
    for sink in build_sinks(deps.settings):
        try:
            ctx.artifacts[sink.name] = await sink.publish(ctx)
        except Exception as exc:  # noqa: BLE001 - a sink must never kill a run
            log.warning("sink %s failed: %s", sink.name, exc)
            ctx.warnings.append(f"sink {sink.name} failed: {exc}")
            ctx.artifacts[sink.name] = {"ok": False, "error": str(exc)}
