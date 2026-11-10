"""Always-on sink: everything lands on disk first.

Having one sink that cannot fail over the network is what lets every other sink
be best-effort. It is also what the API serves images from, and what the sample
outputs committed to the repo are copied from.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _front_matter(ctx) -> str:
    """Minimal YAML front matter, hand-rolled to avoid a dependency."""
    def esc(v: Any) -> str:
        return '"' + str(v).replace('\\', '\\\\').replace('"', '\\"') + '"'

    sel = ctx.selection.as_dict() if ctx.selection else {}
    lines = [
        "---",
        f"title: {esc(ctx.draft.get('title', ctx.topic))}",
        f"subtitle: {esc(ctx.draft.get('subtitle', ''))}",
        f"topic: {esc(ctx.topic)}",
        f"description: {esc(ctx.draft.get('meta_description', ''))}",
        f"style: {esc(sel.get('style'))}",
        f"angle: {esc(sel.get('angle'))}",
        f"audience: {esc(sel.get('audience'))}",
        f"art_direction: {esc(sel.get('art_direction'))}",
        f"palette: {esc(sel.get('palette'))}",
        f"composition: {esc(sel.get('composition'))}",
        f"word_count: {ctx.word_count}",
        f"revisions: {ctx.revisions}",
        f"qc_passed: {str(ctx.qc.get('passed', False)).lower()}",
        f"image_provider: {esc(getattr(ctx.image, 'provider', 'none'))}",
        f"image_alt: {esc(ctx.image_prompt.get('alt_text', ''))}",
        f"run_id: {esc(ctx.run_id)}",
        f"generated_at: {esc(ctx.created_at)}",
        "keywords:",
    ]
    for kw in ctx.outline.get("keywords", [])[:10]:
        lines.append(f"  - {esc(kw)}")
    lines.append("---")
    return "\n".join(lines)


class LocalSink:
    name = "local"

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)

    async def publish(self, ctx) -> dict[str, Any]:
        folder = self.out_dir / f"{ctx.created_at[:10]}_{ctx.slug}_{ctx.run_id[:8]}"
        folder.mkdir(parents=True, exist_ok=True)

        body = ctx.draft.get("body_markdown", "")
        title = ctx.draft.get("title", ctx.topic)
        alt = ctx.image_prompt.get("alt_text", title)
        # Caption as well as alt text: the first is for a reader looking at the
        # image, the second for one who cannot. Markdown renderers show an
        # italic line under an image as a caption, and GitHub and Docs both do.
        image_md = ("![{alt}](image.png)\n\n*{alt}*\n\n".format(alt=alt)
                    if ctx.image else "")
        document = f"{_front_matter(ctx)}\n\n# {title}\n\n{image_md}{body}\n"

        (folder / "content.md").write_text(document, encoding="utf-8")
        if ctx.image is not None:
            (folder / "image.png").write_bytes(ctx.image.data)
        (folder / "run.json").write_text(
            json.dumps(ctx.summary(), indent=2, default=str), encoding="utf-8"
        )

        return {
            "ok": True,
            "folder": str(folder),
            "markdown": str(folder / "content.md"),
            "image": str(folder / "image.png") if ctx.image is not None else None,
            "run_json": str(folder / "run.json"),
            "url_path": f"/artifacts/{folder.name}/image.png" if ctx.image else None,
        }
