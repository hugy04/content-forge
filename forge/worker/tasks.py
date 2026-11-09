from __future__ import annotations

import asyncio
import logging
from typing import Any

from celery import shared_task

from forge.pipeline.context import RunContext
from forge.pipeline.runner import build_deps, run_pipeline
from forge.worker.celery_app import celery_app  # noqa: F401 - registers the app

log = logging.getLogger("forge.worker")


@celery_app.task(
    name="forge.generate",
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=2,
)
def generate_task(self, topic: str, overrides: dict[str, str] | None = None,
                  notify_url: str = "") -> dict[str, Any]:
    """Run one topic through the pipeline inside a worker process."""
    deps = build_deps()
    ctx = RunContext(topic=topic, overrides=overrides or {}, notify_url=notify_url)
    log.info("worker picked up %s (task %s)", topic, self.request.id)
    result = asyncio.run(run_pipeline(ctx, deps))
    return result.summary()
