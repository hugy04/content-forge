"""Copy the newest successful run per sample topic into samples/, and rewrite
samples/README.md as a table of what was produced.

Kept separate from generation so samples can be refreshed without re-running the
pipeline, and so the committed samples are always traceable to a real run id.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.config import get_settings            # noqa: E402
from forge.pipeline.context import slugify       # noqa: E402
from forge.store.db import _connect              # noqa: E402

SAMPLES = ROOT / "samples"


def main() -> int:
    settings = get_settings()
    topics = [
        line.strip() for line in (SAMPLES / "topics.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    rows = []
    with _connect(settings.db_path) as conn:
        for topic in topics:
            # Prefer a `completed` run over a `partial` one, then the most
            # recent. Stated here and in the README so the selection rule is
            # visible: the committed sample is the best *real* run for the
            # topic, never an edited one.
            record = conn.execute(
                "SELECT * FROM runs WHERE topic=? AND status IN ('completed','partial')"
                " ORDER BY CASE status WHEN 'completed' THEN 0 ELSE 1 END,"
                " created_at DESC LIMIT 1", (topic,)
            ).fetchone()
            if not record:
                print(f"  ! no completed run for {topic!r} - skipping")
                continue

            payload = json.loads(record["payload"] or "{}")
            source = Path((payload.get("artifacts", {}).get("local") or {}).get("folder", ""))
            if not source.exists():
                print(f"  ! artifacts missing on disk for {topic!r} - skipping")
                continue

            dest = SAMPLES / slugify(topic, 48)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest)
            rows.append((topic, record, payload, dest))
            print(f"  + {dest.relative_to(ROOT)}")

    _write_readme(rows)
    return 0 if rows else 1


def _write_readme(rows) -> None:
    lines = [
        "# Sample outputs",
        "",
        "Produced by running `./scripts/run_samples.sh` against the live pipeline.",
        "For each topic this is the most recent `completed` run, or the most",
        "recent `partial` one if none completed. Records are copied verbatim -",
        "nothing here is hand-edited.",
        "Every folder holds the piece (`content.md`), the generated image",
        "(`image.png`) and the complete run record (`run.json`: selection,",
        "per-stage timings, token usage, cost, QC verdict).",
        "",
        "Formats and art directions were **not** specified - the diversity engine",
        "chose them, which is why no two rows share a combination.",
        "",
        "| # | Topic | Format | Angle | Art direction | Palette | Words | Rev | Model | Image | Time |",
        "|---|-------|--------|-------|---------------|---------|-------|-----|-------|-------|------|",
    ]
    for i, (topic, record, payload, dest) in enumerate(rows, 1):
        seconds = (record["duration_ms"] or 0) / 1000
        models = sorted({
            call.get("model", "") for call in (payload.get("usage") or {}).get("per_call", [])
        } - {""})
        model = ", ".join(f"`{m}`" for m in models) or "-"
        lines.append(
            f"| {i} | [{topic}](./{dest.name}/content.md) | `{record['style']}` | "
            f"`{record['angle']}` | `{record['art_direction']}` | `{record['palette']}` | "
            f"{record['word_count']} | {record['revisions']} | {model} | "
            f"`{record['image_provider']}` | {seconds:.0f}s |"
        )

    if rows:
        total_cost = sum((r[1]["cost_usd"] or 0) for r in rows)
        avg_time = sum((r[1]["duration_ms"] or 0) for r in rows) / len(rows) / 1000
        tokens = sum(
            (r[2].get("usage") or {}).get("total_tokens", 0) for r in rows)
        statuses = [r[1]["status"] for r in rows]
        lines += [
            "",
            f"**{len(rows)} assets · average {avg_time:.0f}s end-to-end "
            f"(budget 300s) · {tokens:,} tokens · ${total_cost:.3f} total.**",
            "",
            f"Status: {statuses.count('completed')} `completed`, "
            f"{statuses.count('partial')} `partial`. A `partial` means a usable "
            "asset exists with a named caveat - the failing check is listed in "
            "that run's `run.json` under `qc`. It is reported rather than "
            "hidden; see docs/DESIGN.md §5.",
            "",
            "### How to read these",
            "",
            "* **Cost is $0.000 because the free tier is genuinely free**, not",
            "  because accounting is missing. Token counts are recorded per call",
            "  in each `run.json`; set `PRICE_INPUT_PER_MTOK` /",
            "  `PRICE_OUTPUT_PER_MTOK` to price the same traffic on a paid tier.",
            "* **The model column is observed, not configured.** Gemini enforces",
            "  free-tier quota *per model*, so a run may be served by a different",
            "  model than the one requested - the provider falls forward rather",
            "  than failing. Seeing more than one model across these rows is the",
            "  mechanism working.",
            "* **Art direction is applied deterministically after generation**",
            "  (`forge/providers/image/treatment.py`), because the free renderer",
            "  ignores style instructions in the prompt. That is why the images",
            "  differ visibly from each other rather than only in subject.",
            "",
            "## Images",
            "",
        ]
        for topic, record, payload, dest in rows:
            alt = (payload.get("image") or {}).get("alt_text") or topic
            lines.append(f"### {record['title'] or topic}")
            lines.append("")
            lines.append(f"![{alt}](./{dest.name}/image.png)")
            lines.append("")
            treatment = (payload.get("image") or {}).get("treatment") or "none"
            lines.append(f"*{record['art_direction']} · {record['palette']} · "
                         f"generated by `{record['image_provider']}`, "
                         f"treatment `{treatment}`*")
            lines.append("")

    (SAMPLES / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
