"""Catalogue sync: Shopify → `social_products`, with change events for the auto-pilot.

* Incremental (every 15 min): products with `updated_at` after the saved cursor.
* Full (daily, and the very first sync): every product, then collections, then
  products Shopify no longer returns are marked deleted — only when the full
  fetch completed without error.
* Every product write is one atomic read-diff-write transaction, so overlapping
  syncs and webhooks never double-announce a change or resurrect stale data.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from db.social_engine import observability
from db.social_engine import products as product_store

from .. import activity
from ..context import load_store_context
from ..errors import RateLimited, describe
from .changes import Event, diff, removed_event
from .normalize import normalize_collection, normalize_product
from .shopify_api import ShopifyCatalogClient

logger = logging.getLogger("ai_blog_server.social_engine.catalog")

CURSOR_OVERLAP_SECONDS = 120


def cursor_key(store_id: str) -> str:
    return f"catalog:cursor:{store_id}"


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_stale(previous: dict | None, product: dict, *, authoritative: bool) -> bool:
    """True when `product` is older than the stored row (an out-of-order write)."""
    if previous is None:
        return False
    incoming = _parse_ts(product.get("shopify_updated_at"))
    if incoming is None:
        return False
    if previous.get("deleted_at"):
        # A page fetched before a delete webhook must not bring the product back.
        return not authoritative and incoming.timestamp() < float(previous["deleted_at"])
    stored = _parse_ts(previous.get("shopify_updated_at"))
    return stored is not None and incoming < stored


async def store_product(
    store_id: str, product: dict, *, initial_import: bool, source: str = "sync", authoritative: bool = False,
) -> dict:
    """Upsert one normalised product and store its change events atomically.

    `authoritative=True` means the data was just fetched by id (webhooks), so it
    may restore a row marked deleted. Returns {"outcome", "events", "previous"}
    where outcome is created | updated | unchanged | stale.
    """
    def decide(previous: dict | None) -> list[Event] | None:
        if _is_stale(previous, product, authoritative=authoritative):
            return None
        return diff(previous, product, initial_import=initial_import)

    previous, events = await product_store.save_product_with_events(store_id, product, decide, source=source)
    if events is None:
        outcome = "stale"
    elif previous is None or previous.get("deleted_at"):
        outcome = "created"
    elif previous.get("content_hash") != product.get("content_hash"):
        outcome = "updated"
    else:
        outcome = "unchanged"
    return {"outcome": outcome, "events": events or [], "previous": previous}


async def remove_product(store_id: str, product_id: str, *, source: str = "sync") -> list[Event]:
    """Mark a product deleted and record `product_removed` (no-op if already deleted)."""
    marked = await product_store.mark_products_deleted(
        store_id, [product_id], events_for=lambda row: [removed_event(row, "deleted")], source=source,
    )
    return [event for _, events in marked for event in events]



async def _remove_missing(api: ShopifyCatalogClient, store_id: str, seen: set[str]) -> int:
    """Mark products absent from a complete listing as deleted, after Shopify confirms by id.

    The by-id check keeps products created while the listing ran, and protects
    the catalogue if a listing ever comes back short.
    """
    candidates = await product_store.list_product_ids(store_id) - seen
    if not candidates:
        return 0
    still_there = await api.existing_product_ids(candidates)
    if still_there:
        logger.info(
            "%d product(s) missing from the Shopify listing still exist for store %s; keeping them",
            len(still_there), store_id,
        )
    marked = await product_store.mark_products_deleted(
        store_id, candidates - still_there, events_for=lambda row: [removed_event(row, "deleted")],
    )
    return len(marked)

def _next_cursor(previous: Any, newest: datetime | None, started_at: int, *, reset: bool) -> str:
    """Newest `updatedAt` seen minus the overlap; never moves backwards on incremental runs."""
    old = None if reset else _parse_ts(previous)
    if newest is None:
        if old is not None:
            return _iso(old)
        newest = datetime.fromtimestamp(started_at, tz=timezone.utc)
    candidate = newest - timedelta(seconds=CURSOR_OVERLAP_SECONDS)
    if old is not None and old > candidate:
        candidate = old
    return _iso(candidate)


def _summary_message(summary: dict) -> str:
    events = ", ".join(f"{name}×{count}" for name, count in sorted(summary["events"].items())) or "no events"
    text = (
        f"Catalog {summary['mode']} sync: {summary['fetched']} fetched, {summary['created']} new, "
        f"{summary['updated']} updated, {summary['unchanged']} unchanged, {summary['deleted']} removed"
    )
    if summary["mode"] == "full":
        text += f", {summary['collections']} collections"
    if summary["initial_import"]:
        text += " (initial import)"
    return f"{text}; {events}."


async def sync_catalog(store_id: str, *, full: bool = False, correlation_id: str = "") -> dict:
    """Sync the store's catalogue and return a summary of what changed."""
    started = time.monotonic()
    started_at = int(time.time())
    ctx = await load_store_context(store_id, correlation_id=correlation_id)
    key = cursor_key(store_id)
    cursor = await observability.get_state(key)
    initial_import = not cursor
    mode = "full" if full or initial_import else "incremental"
    summary: dict[str, Any] = {
        "mode": mode, "initial_import": initial_import, "fetched": 0, "created": 0, "updated": 0,
        "unchanged": 0, "stale": 0, "deleted": 0, "collections": 0, "events": {},
        "cursor": cursor or "", "duration_ms": 0,
    }
    events: Counter[str] = Counter()
    seen: set[str] = set()
    newest: datetime | None = None
    stage = "products"

    try:
        async with ShopifyCatalogClient(ctx.store_config, call_context=ctx.call_context(correlation_id)) as api:
            query = "" if mode == "full" else f"updated_at:>'{cursor}'"
            sort_key = "ID" if mode == "full" else "UPDATED_AT"
            async for page in api.iter_product_pages(query=query, sort_key=sort_key):
                for node in page:
                    product = normalize_product(node, storefront_url=ctx.storefront_url)
                    if not product["product_id"]:
                        continue
                    summary["fetched"] += 1
                    seen.add(product["product_id"])
                    updated_at = _parse_ts(product["shopify_updated_at"])
                    if updated_at and (newest is None or updated_at > newest):
                        newest = updated_at
                    result = await store_product(store_id, product, initial_import=initial_import)
                    summary[result["outcome"]] += 1
                    events.update(event_type for event_type, _ in result["events"])

            # Reaching this line means every product page was fetched without error.
            if mode == "full":
                stage = "deletion check"
                summary["deleted"] = await _remove_missing(api, store_id, seen)
                if summary["deleted"]:
                    events["product_removed"] += summary["deleted"]
            summary["cursor"] = _next_cursor(cursor, newest, started_at, reset=mode == "full")
            await observability.set_state(key, summary["cursor"])

            if mode == "full":
                stage = "collections"
                collections = [normalize_collection(node) for node in await api.fetch_collections()]
                collections = [c for c in collections if c["collection_id"]]
                for collection in collections:
                    await product_store.upsert_collection(store_id, collection)
                await product_store.delete_collections_except(store_id, [c["collection_id"] for c in collections])
                summary["collections"] = len(collections)
    except Exception as exc:
        summary["events"] = dict(events)
        summary["duration_ms"] = int((time.monotonic() - started) * 1000)
        error_type, message = describe(exc)
        summary["error"] = f"{error_type}: {message}"
        await activity.record(
            store_id, "catalog", store_id,
            f"Catalog {mode} sync stopped at the {stage} step after {summary['fetched']} products: {message}",
            level="warning" if isinstance(exc, RateLimited) else "error", stage="catalog.sync",
            data=summary, correlation_id=correlation_id,
        )
        raise

    summary["events"] = dict(events)
    summary["duration_ms"] = int((time.monotonic() - started) * 1000)
    await activity.record(
        store_id, "catalog", store_id, _summary_message(summary), stage="catalog.sync",
        data=summary, correlation_id=correlation_id,
    )
    return summary


async def refresh_products(store_id: str, product_ids: list[str], *, correlation_id: str = "") -> dict:
    """Re-fetch specific products by id (used before posting so price/stock are current).

    Inventory-only changes don't always move `updatedAt`, so the incremental sync
    can miss a sell-out; this targeted refresh closes that gap for posts that
    are about to go out.
    """
    ids = sorted({str(pid) for pid in product_ids if str(pid)})
    summary = {"refreshed": 0, "removed": 0, "events": {}}
    if not ids:
        return summary
    ctx = await load_store_context(store_id, correlation_id=correlation_id)
    async with ShopifyCatalogClient(ctx.store_config, call_context=ctx.call_context(correlation_id)) as api:
        for product_id in ids:
            node = await api.fetch_product(product_id)
            if node is None:
                events = await remove_product(store_id, product_id, source="refresh")
                summary["removed"] += 1
            else:
                product = normalize_product(node, storefront_url=ctx.storefront_url)
                events = (await store_product(store_id, product, initial_import=False, source="refresh",
                                              authoritative=True))["events"]
                summary["refreshed"] += 1
            for event_type, _details in events:
                summary["events"][event_type] = summary["events"].get(event_type, 0) + 1
    return summary
