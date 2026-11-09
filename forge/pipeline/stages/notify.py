"""Stage 7: optional outbound callback, for chaining into another system."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("forge.notify")


async def run(ctx, deps) -> None:
    url = ctx.notify_url or deps.settings.notify_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=ctx.summary())
        ctx.artifacts["notify"] = {"ok": response.status_code < 400,
                                   "status": response.status_code}
    except httpx.HTTPError as exc:
        log.warning("notify failed: %s", exc)
        ctx.warnings.append(f"notify callback failed: {exc}")
        ctx.artifacts["notify"] = {"ok": False, "error": str(exc)}
