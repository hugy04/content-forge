"""Google Drive + Docs + Sheets, through an Apps Script web app.

Why a web app rather than the Drive REST API: this runs against a consumer
Google account. A service account has no Drive storage quota of its own, so
uploads into a shared My Drive folder fail with storageQuotaExceeded, and full
OAuth means a GCP project, a consent screen and a refresh token to keep alive.
An Apps Script web app deployed "execute as me" sidesteps both - it already runs
as the account that owns the Drive - and the whole integration becomes one
authenticated POST. The trade-off is a shared-secret rather than OAuth scopes,
which is documented in docs/DESIGN.md.
"""
from __future__ import annotations

import base64
from typing import Any

import httpx


class GoogleDriveSink:
    name = "gdrive"

    def __init__(self, webapp_url: str, shared_secret: str, timeout: float = 60.0):
        if not webapp_url:
            raise ValueError("gdrive sink needs GDRIVE_WEBAPP_URL")
        self.webapp_url = webapp_url
        self.shared_secret = shared_secret
        self.timeout = timeout

    async def publish(self, ctx) -> dict[str, Any]:
        sel = ctx.selection.as_dict() if ctx.selection else {}
        payload = {
            "secret": self.shared_secret,
            "run_id": ctx.run_id,
            "topic": ctx.topic,
            "title": ctx.draft.get("title", ctx.topic),
            "subtitle": ctx.draft.get("subtitle", ""),
            "meta_description": ctx.draft.get("meta_description", ""),
            "body_markdown": ctx.draft.get("body_markdown", ""),
            "alt_text": ctx.image_prompt.get("alt_text", ""),
            "image_prompt": ctx.image_prompt.get("prompt", ""),
            "image_base64": (
                base64.b64encode(ctx.image.data).decode() if ctx.image else ""
            ),
            "style": sel.get("style"),
            "angle": sel.get("angle"),
            "audience": sel.get("audience"),
            "art_direction": sel.get("art_direction"),
            "palette": sel.get("palette"),
            "word_count": ctx.word_count,
            "revisions": ctx.revisions,
            "qc_passed": bool(ctx.qc.get("passed")),
            "image_provider": getattr(ctx.image, "provider", "none"),
            "duration_seconds": round(ctx.deadline.elapsed, 2) if ctx.deadline else None,
            "cost_usd": round(ctx.usage.cost_usd, 6),
            "status": ctx.status,
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(self.webapp_url, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"apps script returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"apps script returned non-JSON: {response.text[:200]}") from exc
        if not body.get("ok"):
            raise RuntimeError(f"apps script reported failure: {body}")
        return {"ok": True, **body}
