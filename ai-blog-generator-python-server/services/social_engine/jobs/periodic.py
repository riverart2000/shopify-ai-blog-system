"""Recurring background work, expressed as ordinary jobs.

Each entry is enqueued at most once per interval per store (dedupe key), so
running several worker processes never duplicates recurring work. Add a new
recurring task by appending a `PeriodicTask`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import db
from db.social_engine import observability

from .queue import enqueue

logger = logging.getLogger("ai_blog_server.social_engine.periodic")


@dataclass(frozen=True)
class PeriodicTask:
    name: str
    job_type: str
    interval_seconds: int
    per_store: bool = True
    requires_enabled: bool = True   # only for stores with the engine switched on
    priority: int = 0


PERIODIC_TASKS: list[PeriodicTask] = [
    PeriodicTask("sync_channels", "channels.sync", 3600),
    PeriodicTask("sync_catalog_incremental", "catalog.sync", 900),
    PeriodicTask("sync_catalog_full", "catalog.sync_full", 86400),
    PeriodicTask("evaluate_campaigns", "campaigns.evaluate", 300, priority=5),
    PeriodicTask("reconcile_posts", "posts.reconcile", 600, priority=3),
    PeriodicTask("collect_post_metrics", "analytics.collect", 3600),
    PeriodicTask("collect_channel_metrics", "analytics.channels", 86400),
    PeriodicTask("poll_notifications", "notifications.poll", 1800),
    PeriodicTask("maintenance", "maintenance.prune", 86400, per_store=False, requires_enabled=False),
]


def _state_key(task: PeriodicTask, store_id: str) -> str:
    return f"periodic:{task.name}:{store_id or '*'}"


async def _enabled_store_ids() -> list[str]:
    from ..settings import load_settings

    enabled: list[str] = []
    for store in await db.get_stores():
        store_id = str(store["id"])
        try:
            if (await load_settings(store_id)).enabled:
                enabled.append(store_id)
        except Exception:
            logger.exception("Could not load Social Engine settings for %s", store_id)
    return enabled


async def schedule_due_periodic_jobs(now: int | None = None) -> list[str]:
    """Enqueue every periodic job whose interval has elapsed. Returns job ids."""
    from . import registry, load_handlers

    load_handlers()
    now = now or int(time.time())
    enabled_stores = await _enabled_store_ids()
    created: list[str] = []
    for task in PERIODIC_TASKS:
        if registry.get(task.job_type) is None:
            continue  # handler module not installed yet
        store_ids = enabled_stores if task.per_store else [""]
        if task.per_store and not task.requires_enabled:
            store_ids = [str(s["id"]) for s in await db.get_stores()]
        for store_id in store_ids:
            key = _state_key(task, store_id)
            last = await observability.get_state(key, 0) or 0
            if now - int(last) < task.interval_seconds:
                continue
            job_id, was_created = await enqueue(
                task.job_type, {"trigger": "periodic"}, store_id=store_id, priority=task.priority,
                dedupe_key=f"{key}", correlation_id=key,
            )
            await observability.set_state(key, now)
            if was_created:
                created.append(job_id)
    return created


async def periodic_status() -> list[dict]:
    states = await observability.list_state("periodic:")
    out = []
    for key, item in sorted(states.items()):
        _, name, store_id = key.split(":", 2)
        out.append({"task": name, "store_id": store_id, "last_enqueued_at": item.get("value")})
    return out
