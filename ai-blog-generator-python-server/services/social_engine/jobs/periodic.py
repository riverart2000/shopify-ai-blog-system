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
    needs_catalog: bool = False     # skipped for brands with no Shopify catalogue (Settings → catalog.source)


PERIODIC_TASKS: list[PeriodicTask] = [
    PeriodicTask("sync_channels", "channels.sync", 3600),
    PeriodicTask("sync_catalog_incremental", "catalog.sync", 900, needs_catalog=True),
    PeriodicTask("sync_catalog_full", "catalog.sync_full", 86400, needs_catalog=True),
    PeriodicTask("evaluate_campaigns", "campaigns.evaluate", 300, priority=5),
    PeriodicTask("auto_occasion_promotions", "promotions.auto_occasions", 3600, priority=6, needs_catalog=True),
    PeriodicTask("reconcile_posts", "posts.reconcile", 600, priority=3),
    PeriodicTask("collect_post_metrics", "analytics.collect", 3600),
    PeriodicTask("collect_channel_metrics", "analytics.channels", 86400),
    PeriodicTask("poll_notifications", "notifications.poll", 1800),
    PeriodicTask("founder_videos", "founder.scan", 300, priority=4),
    PeriodicTask("photo_audit", "catalog.photo_audit", 1800, priority=1, needs_catalog=True),
    PeriodicTask("shoppable_page", "shoppable.refresh", 3600, priority=1, needs_catalog=True),
    PeriodicTask("listings_check", "listings.check", 86400, priority=0),
    PeriodicTask("listings_work", "listings.work_next", 12 * 3600, priority=0),
    PeriodicTask("maintenance", "maintenance.prune", 86400, per_store=False, requires_enabled=False),
]


def _state_key(task: PeriodicTask, store_id: str) -> str:
    return f"periodic:{task.name}:{store_id or '*'}"


async def _enabled_stores() -> dict[str, bool]:
    """Store id → whether it has a product catalogue, for every store with the engine switched on."""
    from ..settings import load_settings

    enabled: dict[str, bool] = {}
    for store in await db.get_stores():
        store_id = str(store["id"])
        try:
            settings = await load_settings(store_id)
        except Exception:
            logger.exception("Could not load Social Engine settings for %s", store_id)
            continue
        if settings.enabled:
            enabled[store_id] = settings.catalog.source != "none"
    return enabled


async def _enabled_store_ids() -> list[str]:
    return list(await _enabled_stores())


async def schedule_due_periodic_jobs(now: int | None = None) -> list[str]:
    """Enqueue every periodic job whose interval has elapsed. Returns job ids."""
    from . import registry, load_handlers

    load_handlers()
    now = now or int(time.time())
    enabled_stores = await _enabled_stores()
    created: list[str] = []
    for task in PERIODIC_TASKS:
        if registry.get(task.job_type) is None:
            continue  # handler module not installed yet
        store_ids = [store_id for store_id, has_catalog in enabled_stores.items()
                     if has_catalog or not task.needs_catalog] if task.per_store else [""]
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
