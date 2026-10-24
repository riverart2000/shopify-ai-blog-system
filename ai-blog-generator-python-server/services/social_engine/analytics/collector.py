"""Pull metrics from Postiz into SQLite.

Per-post metrics are refreshed on a decaying schedule (young posts more often),
within the Postiz analytics coverage (only some networks report per-post data).
Channel metrics (followers, impressions...) are pulled daily.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from db.social_engine import config_store
from db.social_engine import posts as post_store

from ..context import load_store_context
from ..errors import SocialEngineError
from ..models import profile_for

logger = logging.getLogger("ai_blog_server.social_engine.analytics")

# (min post age, max post age, refresh interval) — young posts refresh more often.
_SCHEDULE = (
    (3600, 86400, 3 * 3600),               # first day: every 3h
    (86400, 7 * 86400, 12 * 3600),         # first week: twice a day
    (7 * 86400, 45 * 86400, 3 * 86400),    # up to ~6 weeks: every 3 days
)


async def collect_post_metrics(store_id: str, *, limit: int = 40) -> dict[str, Any]:
    from ..publishers import get_publisher

    due: dict[str, dict] = {}
    for min_age, max_age, refresh in _SCHEDULE:
        for target in await post_store.targets_due_for_metrics(
            min_age_seconds=min_age, max_age_seconds=max_age, refresh_after_seconds=refresh, limit=limit,
        ):
            if target["store_id"] == store_id:
                due.setdefault(target["id"], target)
    targets = [t for t in due.values() if profile_for(t["provider"]).has_post_analytics][:limit]
    if not targets:
        return {"collected": 0}

    sctx = await load_store_context(store_id)
    publisher = get_publisher(sctx, correlation_id=f"analytics:{store_id}")
    collected, failures = 0, []
    try:
        for target in targets:
            age_days = max(1, int((time.time() - (target.get("published_at") or time.time())) // 86400) + 1)
            period = 7 if age_days <= 7 else 30
            try:
                result = await publisher.post_metrics(target["external_id"], days=period)
            except SocialEngineError as exc:
                failures.append(f"{target['provider']} {target['external_id']}: {exc.message}")
                if exc.retryable:
                    continue
                # Unsupported/unknown post → store an empty snapshot so we don't hammer it.
                result = None
            await post_store.add_post_metrics(
                store_id, target, result.metrics if result else {}, period_days=period,
                raw=result.raw if result else {"error": failures[-1] if failures else ""},
            )
            collected += 1
    finally:
        await publisher.aclose()
    if failures:
        logger.warning("Metrics unavailable for %d post(s): %s", len(failures), "; ".join(failures[:5]),
                       extra={"store_id": store_id, "operation": "social_engine.analytics"})
    return {"collected": collected, "failures": len(failures)}


async def collect_channel_metrics(store_id: str, *, days: int = 30) -> dict[str, Any]:
    from ..publishers import get_publisher

    channels = [c for c in await config_store.list_channels(store_id, usable_only=True)
                if profile_for(c["provider"]).has_post_analytics]
    if not channels:
        return {"channels": 0}
    sctx = await load_store_context(store_id)
    publisher = get_publisher(sctx, correlation_id=f"analytics:{store_id}")
    stored = 0
    errors: list[str] = []
    try:
        for channel in channels:
            try:
                rows = await publisher.channel_metrics(channel["channel_id"], days=days)
            except SocialEngineError as exc:
                errors.append(f"{channel['name']}: {exc.message}")
                continue
            for row in rows:
                await post_store.upsert_channel_metric(
                    store_id, channel["channel_id"], str(row.get("metric", "")), str(row.get("day", "")),
                    float(row.get("value") or 0),
                )
                stored += 1
    finally:
        await publisher.aclose()
    return {"channels": len(channels), "points": stored, "errors": errors}
