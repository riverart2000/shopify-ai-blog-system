"""Freshness guard: never publish a price or stock level that is no longer true.

Each generated post stores a snapshot of the products it shows. Before
publishing — and while a post waits in Postiz — the snapshot is compared with
the latest synced catalogue:

* product sold out / removed → the post is skipped (Outfy skips sold-out items)
* price or sale status changed → the post is pulled back and regenerated
"""
from __future__ import annotations

import logging
import time
from typing import Any

from db.social_engine import posts as post_store
from db.social_engine import products as product_store

from ..activity import record
from ..jobs.queue import enqueue
from .generate import product_snapshot

logger = logging.getLogger("ai_blog_server.social_engine.pipeline")


def compare(snapshot: dict[str, dict], current: dict[str, dict]) -> tuple[str, str]:
    """Return (verdict, reason) where verdict is ok | sold_out | changed."""
    for pid, before in snapshot.items():
        now = current.get(pid)
        if now is None or not now["available"]:
            return "sold_out", f"'{before.get('title') or pid}' is no longer available"
        if abs(now["price_min"] - before["price_min"]) >= 0.01:
            return "changed", f"'{before.get('title') or pid}' price changed from {before['price_min']} to {now['price_min']}"
        if now["on_sale"] != before["on_sale"] or (now["compare_at_max"] or 0) != (before["compare_at_max"] or 0):
            return "changed", f"'{before.get('title') or pid}' sale status changed"
    return "ok", ""


async def check_post(store_id: str, post: dict) -> tuple[str, str]:
    snapshot = (post.get("generation") or {}).get("product_snapshot") or {}
    if not snapshot:
        return "ok", ""
    products = await product_store.get_products(store_id, list(snapshot))
    current = product_snapshot([p for p in products if not p.get("deleted_at")])
    return compare(snapshot, current)


async def apply_verdict(store_id: str, post: dict, verdict: str, reason: str) -> None:
    """Act on a stale post: cancel remote copies, then skip or regenerate."""
    from ..context import load_store_context
    from ..publishers import get_publisher

    remote = [t for t in post.get("targets") or [] if t.get("external_id") and t["status"] in ("scheduled", "draft")]
    if remote:
        sctx = await load_store_context(store_id, correlation_id=post["id"])
        publisher = get_publisher(sctx, correlation_id=post["id"])
        try:
            for target in remote:  # each channel version is its own Postiz group
                await publisher.delete_post(target["external_id"])
        finally:
            await publisher.aclose()
    for target in post.get("targets") or []:
        if target["status"] not in ("published",):
            await post_store.update_target(target["id"], status="cancelled" if verdict == "sold_out" else "pending",
                                           external_id="", error_type="Stale", error_message=reason)
    if verdict == "sold_out":
        await post_store.update_post(store_id, post["id"], status="cancelled", error_type="Stale",
                                     error_message=f"Skipped: {reason}.")
        await record(store_id, "post", post["id"], f"Skipped — {reason}", level="warning", stage="freshness")
        return
    await post_store.update_post(store_id, post["id"], status="planned", error_type="Stale",
                                 error_message=f"Regenerating: {reason}.")
    await enqueue("post.generate", {"post_id": post["id"], "force": True}, store_id=store_id,
                  dedupe_key=f"generate:{post['id']}", correlation_id=post["id"])
    await record(store_id, "post", post["id"], f"Pulled back and regenerating — {reason}",
                 level="warning", stage="freshness")


async def sweep_scheduled(store_id: str) -> dict[str, Any]:
    """Check posts waiting in Postiz (not yet published) for stale product data."""
    now = int(time.time())
    posts = await post_store.list_posts(store_id, statuses=("scheduled", "publishing", "approved"),
                                        scheduled_from=now, limit=300)
    results: dict[str, Any] = {"checked": 0, "sold_out": 0, "changed": 0}
    # Re-read products going out in the next ~day straight from Shopify (stock-only
    # changes don't always reach the incremental sync).
    soon = {str(pid) for row in posts if (row.get("scheduled_at") or 0) <= now + 26 * 3600
            for pid in row.get("product_ids") or []}
    if soon:
        from ..catalog.sync import refresh_products

        try:
            results["refresh"] = await refresh_products(store_id, sorted(soon)[:60],
                                                        correlation_id=f"freshness:{store_id}")
        except Exception as exc:  # stale-but-synced data is still checked below
            results["refresh_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("Freshness refresh failed for %s: %s", store_id, exc, extra={"store_id": store_id})
    for row in posts:
        post = await post_store.get_post(store_id, row["id"])
        if not post:
            continue
        results["checked"] += 1
        verdict, reason = await check_post(store_id, post)
        if verdict != "ok":
            results[verdict] += 1
            await apply_verdict(store_id, post, verdict, reason)
    return results
