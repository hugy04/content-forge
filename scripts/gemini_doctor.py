"""Probe a Gemini API key: what models does it actually have, and do they work?

Model ids and free-tier quotas both move faster than any hardcoded default, so
this is the first thing to run after putting GEMINI_API_KEY in .env. It prints
the exact ids to set, and exits non-zero if the key cannot generate text.

    python scripts/gemini_doctor.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge.config import get_settings  # noqa: E402
from forge.providers.text.gemini import API_ROOT, GeminiTextProvider  # noqa: E402

TEXT_HINTS = ("flash", "pro")
IMAGE_HINTS = ("image", "imagen")


async def main() -> int:
    settings = get_settings()
    key = settings.gemini_api_key
    if not key:
        print("GEMINI_API_KEY is empty in .env - nothing to probe.")
        return 2

    provider = GeminiTextProvider(key, default_model=settings.gemini_model)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30, headers={"x-goog-api-key": key}) as c:
            resp = await c.get(f"{API_ROOT}/models", params={"pageSize": 200})
        if resp.status_code != 200:
            print(f"ListModels failed: HTTP {resp.status_code}\n{resp.text[:400]}")
            return 2
        entries = resp.json().get("models", [])
        ids = sorted(e.get("name", "").removeprefix("models/") for e in entries)

        text_ids = [m for m in ids if any(h in m for h in TEXT_HINTS)
                    and not any(h in m for h in IMAGE_HINTS) and "embedding" not in m]
        image_ids = [m for m in ids if any(h in m for h in IMAGE_HINTS)]

        print(f"{len(ids)} models visible to this key.\n")
        print("TEXT candidates:")
        for m in text_ids:
            print(f"  {m}")
        print("\nIMAGE candidates:")
        for m in image_ids or ["  (none - the image chain will use Pollinations)"]:
            print(f"  {m}")

        chosen = await provider._resolve(settings.gemini_model)
        print(f"\nResolved text model: {chosen}")

        print("Smoke test ...", end=" ", flush=True)
        out = await provider.complete_json(
            stage="doctor", model=chosen, effort="low",
            system="You reply with JSON only.",
            user="Return {\"ok\": true, \"word\": \"ready\"}.",
            schema={"type": "object",
                    "properties": {"ok": {"type": "boolean"},
                                   "word": {"type": "string"}},
                    "required": ["ok", "word"],
                    "additionalProperties": False},
            max_tokens=256,
        )
        print(f"OK -> {out}")
        print(f"Tokens: {provider.usage.as_dict()}")
        print("\nSet these in .env:")
        print(f"  TEXT_PROVIDER=gemini")
        print(f"  GEMINI_MODEL={chosen}")
        if image_ids:
            print(f"  GEMINI_IMAGE_MODEL={image_ids[0]}")
        return 0
    finally:
        await provider.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
