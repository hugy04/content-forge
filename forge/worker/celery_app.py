"""Celery application for queue mode.

Inline mode and queue mode execute the identical pipeline; the only difference
is who calls it and what happens on failure. Queue mode adds durable retries, a
concurrency cap that protects provider rate limits, and the ability to scale
horizontally by starting more workers against the same Redis.
"""
from __future__ import annotations

from celery import Celery

from forge.config import get_settings

settings = get_settings()

celery_app = Celery(
    "content_forge",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["forge.worker.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,                 # survive a worker killed mid-run
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,        # long tasks: do not hoard the queue
    task_time_limit=settings.deadline_seconds + 120,
    task_soft_time_limit=settings.deadline_seconds + 60,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    task_default_queue="forge",
)
