"""Enqueue helpers used by API routes, rules and other jobs."""
from __future__ import annotations

import time

from db.social_engine import jobs as job_store

from . import registry


def _ensure_handlers_loaded() -> None:
    from . import load_handlers

    load_handlers()


async def enqueue(
    job_type: str,
    payload: dict | None = None,
    *,
    store_id: str = "",
    run_in: int = 0,
    run_at: int | None = None,
    priority: int = 0,
    dedupe_key: str = "",
    correlation_id: str = "",
    max_attempts: int | None = None,
) -> tuple[str, bool]:
    """Queue a job. Returns (job_id, created). Unknown job types are rejected."""
    _ensure_handlers_loaded()
    spec = registry.get(job_type)
    if spec is None:
        raise ValueError(f"Unknown job type {job_type!r}")
    when = run_at if run_at is not None else int(time.time()) + max(0, int(run_in))
    return await job_store.enqueue(
        job_type,
        payload or {},
        store_id=store_id,
        run_at=when,
        priority=priority,
        max_attempts=max_attempts or spec.max_attempts,
        dedupe_key=dedupe_key,
        correlation_id=correlation_id,
    )
