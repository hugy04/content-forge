"""Command line entry point.

    python -m forge.cli "topic"                 one asset
    python -m forge.cli --file topics.txt       a batch, with a concurrency cap
    python -m forge.cli "topic" --style opinion force a format

Exists so samples can be produced, and the pipeline exercised, without the API
running - which is also what makes it convenient in cron or CI.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from forge.config import get_settings
from forge.diversity.taxonomy import DIMENSIONS
from forge.pipeline.context import RunContext
from forge.pipeline.runner import build_deps, run_pipeline


def _print(ctx) -> None:
    sel = ctx.selection.as_dict() if ctx.selection else {}
    mark = {"completed": "✓", "partial": "~", "failed": "✗"}.get(ctx.status, "?")
    print(f"  {mark} [{ctx.status}] {ctx.title}")
    print(f"      {sel.get('style')} / {sel.get('angle')} / {sel.get('audience')}"
          f"  |  art: {sel.get('art_direction')} / {sel.get('palette')}")
    print(f"      {ctx.word_count} words, {ctx.revisions} revision(s), "
          f"image via {getattr(ctx.image, 'provider', 'none')}, "
          f"{ctx.deadline.elapsed:.1f}s, ${ctx.usage.cost_usd:.4f}")
    if ctx.artifacts.get("local"):
        print(f"      {ctx.artifacts['local']['folder']}")
    for warning in ctx.warnings:
        print(f"      ! {warning}")
    for error in ctx.errors:
        print(f"      X {error}")


async def _main(args) -> int:
    settings = get_settings()
    deps = build_deps(settings)

    topics = list(args.topics)
    if args.file:
        topics += [l.strip() for l in Path(args.file).read_text().splitlines()
                   if l.strip() and not l.startswith("#")]
    if not topics:
        print("no topics given", file=sys.stderr)
        return 2

    overrides = {k: getattr(args, k) for k in DIMENSIONS if getattr(args, k, None)}
    semaphore = asyncio.Semaphore(args.concurrency)
    print(f"content-forge: {len(topics)} topic(s), concurrency {args.concurrency}, "
          f"images {settings.image_provider_chain}\n")

    async def one(topic: str):
        async with semaphore:
            ctx = RunContext(topic=topic, overrides=overrides)
            return await run_pipeline(ctx, deps)

    results = await asyncio.gather(*(one(t) for t in topics), return_exceptions=True)

    failures = 0
    for topic, result in zip(topics, results):
        if isinstance(result, BaseException):
            print(f"  ✗ [crashed] {topic}: {result}")
            failures += 1
            continue
        _print(result)
        if result.status == "failed":
            failures += 1

    ok = len(topics) - failures
    print(f"\n{ok}/{len(topics)} succeeded | total ${sum(
        r.usage.cost_usd for r in results if not isinstance(r, BaseException)):.4f}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="forge", description="Generate content assets.")
    parser.add_argument("topics", nargs="*", help="one or more topic strings")
    parser.add_argument("--file", help="file with one topic per line")
    parser.add_argument("--concurrency", type=int, default=2)
    for dimension in DIMENSIONS:
        parser.add_argument(f"--{dimension.replace('_', '-')}", dest=dimension,
                            choices=DIMENSIONS[dimension], default=None)
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
