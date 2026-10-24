"""Slot priority between campaigns.

Daily slots are capped per channel (default 2). Promotions and occasion posts
must go out on their day, so when a day is full they may take a slot from a
SmartQ post that is still local (not yet sent to Postiz). The displaced post
is cancelled with a clear reason; nothing already in Postiz is ever touched.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from db.social_engine import posts as post_store

from ..activity import record
from .base import RuleContext

DISPLACEABLE_RULES = ("smartq", "evergreen", "brand_content", "blog_smartq")
_LOCAL_STATUSES = ("planned", "generating", "pending_approval", "approved")


async def displace_for(rctx: RuleContext, *, day: date, channel_ids: list[str], reason: str) -> int:
    tz = rctx.planner.tz
    start = int(datetime.combine(day, time(0, 0), tzinfo=tz).timestamp())
    end = int(datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=tz).timestamp())
    candidates = await post_store.list_posts(
        rctx.store_id, statuses=_LOCAL_STATUSES, scheduled_from=start - 86400, scheduled_to=end, limit=200,
    )
    remaining = set(channel_ids)
    freed = 0
    for post in candidates:
        if not remaining:
            break
        if post.get("rule_type") not in DISPLACEABLE_RULES:
            continue
        times = {str(k): int(v) for k, v in ((post.get("options") or {}).get("channel_times") or {}).items()}
        on_day = {cid for cid, ts in times.items() if start <= ts < end}
        overlap = on_day & remaining
        if not overlap:
            continue
        if rctx.dry_run:
            # Preview: free the slots in the in-memory planner only; the post stays as it is.
            if post["id"] in rctx.simulated_displaced:
                continue
            rctx.simulated_displaced.add(post["id"])
            for cid, ts in times.items():
                rctx.planner.unreserve([cid], ts)
            remaining -= overlap
            freed += 1
            continue
        cancelled = await post_store.transition_post(
            rctx.store_id, post["id"], from_statuses=_LOCAL_STATUSES, to_status="cancelled",
            error_type="Displaced", error_message=f"Slot given to promotion: {reason}",
        )
        if not cancelled:
            continue
        for target in await post_store.list_targets_for_post(post["id"]):
            if target["status"] in ("pending", "failed"):
                await post_store.update_target(target["id"], status="cancelled")
        for cid, ts in times.items():
            rctx.planner.unreserve([cid], ts)
        remaining -= overlap
        freed += 1
        await record(rctx.store_id, "post", post["id"], f"Slot given to promotion '{reason}'",
                     level="info", stage="schedule")
    return freed
