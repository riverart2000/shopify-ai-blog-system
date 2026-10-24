"""Keep local target status in sync with Postiz (source of truth after sending).

Runs every 10 minutes and whenever a Postiz webhook arrives. Handles:
published (+ release URL), failed on the platform (exact error), rescheduled
or turned into a draft inside Postiz, deleted inside Postiz, and targets left
"uncertain" by an ambiguous create call.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from db.social_engine import posts as post_store

from ..activity import record
from ..context import load_store_context
from .publish import _reconcile_uncertain, refresh_post_status

logger = logging.getLogger("ai_blog_server.social_engine.pipeline")

_TRACKED = ("scheduled", "draft", "publishing", "uncertain")
_MISSING_GRACE_SECONDS = 3600
_MAX_WINDOW_DAYS = 45


async def reconcile_store(store_id: str, *, post_ids: list[str] | None = None) -> dict[str, Any]:
    from ..publishers import get_publisher

    targets = await post_store.list_targets(store_id, statuses=_TRACKED, limit=2000)
    if post_ids:
        wanted = set(post_ids)
        targets = [t for t in targets if t["post_id"] in wanted]
    if not targets:
        return {"checked": 0}

    sctx = await load_store_context(store_id)
    publisher = get_publisher(sctx, correlation_id=f"reconcile:{store_id}")
    counts = {"checked": len(targets), "published": 0, "failed": 0, "rescheduled": 0,
              "missing": 0, "uncertain_resolved": 0, "unchanged": 0}
    touched_posts: set[str] = set()
    try:
        # 1) Uncertain targets first (needs per-target lookups).
        for post_id in sorted({t["post_id"] for t in targets if t["status"] == "uncertain"}):
            post = await post_store.get_post(store_id, post_id)
            if post:
                counts["uncertain_resolved"] += await _reconcile_uncertain(publisher, post)
                touched_posts.add(post_id)

        # 2) Everything with an external id, in one window query.
        known = [t for t in targets if t.get("external_id") and t["status"] != "uncertain"]
        if known:
            now = int(time.time())
            times = [int(t.get("scheduled_at") or now) for t in known]
            start = max(min(times) - 2 * 86400, now - _MAX_WINDOW_DAYS * 86400)
            end = min(max(times) + 2 * 86400, now + _MAX_WINDOW_DAYS * 86400)
            remote = await publisher.list_posts(
                datetime.fromtimestamp(start, tz=timezone.utc), datetime.fromtimestamp(end, tz=timezone.utc),
            )
            by_id = {p.external_id: p for p in remote}
            for target in known:
                external = by_id.get(target["external_id"])
                changed = await _apply_remote_state(store_id, target, external, window=(start, end))
                if changed:
                    counts[changed] = counts.get(changed, 0) + 1
                    touched_posts.add(target["post_id"])
                else:
                    counts["unchanged"] += 1
    finally:
        await publisher.aclose()

    for post_id in touched_posts:
        await refresh_post_status(store_id, post_id)
    return counts


async def _apply_remote_state(store_id: str, target: dict, external, *, window: tuple[int, int]) -> str:
    now = int(time.time())
    if external is None:
        scheduled = int(target.get("scheduled_at") or 0)
        in_window = window[0] <= scheduled <= window[1]
        if in_window and scheduled and scheduled < now - _MISSING_GRACE_SECONDS:
            await post_store.transition_target(
                target["id"], from_statuses=_TRACKED, to_status="failed", error_type="MissingInPostiz",
                error_message="This post is no longer in Postiz (it may have been deleted there).",
                last_synced_at=now,
            )
            await record(store_id, "post", target["post_id"],
                         f"{target['provider']} version not found in Postiz; marked failed",
                         level="warning", stage="reconcile")
            return "missing"
        return ""

    state = (external.state or "").upper()
    if state == "PUBLISHED":
        published_at = int(external.publish_date.timestamp()) if external.publish_date else now
        if target["status"] != "published" or target.get("release_url") != external.release_url:
            await post_store.transition_target(
                target["id"], from_statuses=_TRACKED + ("published",), to_status="published",
                published_at=published_at, release_url=external.release_url or "",
                error_type="", error_message="", last_synced_at=now,
            )
            await record(store_id, "post", target["post_id"], f"Published on {target['provider']}",
                         stage="reconcile", data={"release_url": external.release_url})
            return "published"
        return ""
    if state == "ERROR":
        await post_store.transition_target(
            target["id"], from_statuses=_TRACKED, to_status="failed", error_type="PlatformRejected",
            error_message=external.error or "The platform rejected this post (no message from Postiz).",
            last_synced_at=now,
        )
        await record(store_id, "post", target["post_id"],
                     f"{target['provider']} rejected the post: {external.error or 'no message'}",
                     level="error", stage="reconcile")
        return "failed"
    if state in ("QUEUE", "DRAFT"):
        new_status = "draft" if state == "DRAFT" else "scheduled"
        remote_ts = int(external.publish_date.timestamp()) if external.publish_date else target.get("scheduled_at")
        if new_status != target["status"] or (remote_ts and remote_ts != target.get("scheduled_at")):
            await post_store.transition_target(
                target["id"], from_statuses=_TRACKED, to_status=new_status,
                scheduled_at=remote_ts, last_synced_at=now,
            )
            return "rescheduled"
    return ""


def window_for(days: int) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=days), now + timedelta(days=days)
