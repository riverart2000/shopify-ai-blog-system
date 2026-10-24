"""Shopify webhooks for the catalogue.

Product payloads can arrive partial or out of order, so create/update webhooks
only identify *which* product changed; the product is then re-fetched by id
through GraphQL and stored with the same atomic diff as the sync.
Inventory and collection webhooks ask the caller to enqueue a sync job instead
of running one inline.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from db.social_engine import observability

from .. import activity
from ..context import load_store_context
from ..errors import ValidationError
from .normalize import legacy_id, normalize_product
from .shopify_api import ShopifyCatalogClient
from .sync import cursor_key, remove_product, store_product

logger = logging.getLogger("ai_blog_server.social_engine.catalog")

PRODUCT_UPSERT_TOPICS = frozenset({"products/create", "products/update"})
PRODUCT_DELETE_TOPIC = "products/delete"
INCREMENTAL_SYNC_TOPICS = frozenset({
    "inventory_levels/update", "inventory_levels/connect", "inventory_levels/disconnect", "inventory_items/update",
})
FULL_SYNC_TOPICS = frozenset({"collections/create", "collections/update", "collections/delete"})

_ENUM_TOPICS = {
    topic.upper().replace("/", "_"): topic
    for topic in (*PRODUCT_UPSERT_TOPICS, PRODUCT_DELETE_TOPIC, *INCREMENTAL_SYNC_TOPICS, *FULL_SYNC_TOPICS)
}


def verify_shopify_hmac(raw_body: bytes, hmac_header: str, secret: str) -> bool:
    """Check `X-Shopify-Hmac-Sha256` (base64 HMAC-SHA256 of the raw body) in constant time."""
    if not secret or not hmac_header:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
    expected = base64.b64encode(digest)
    return hmac.compare_digest(expected, hmac_header.strip().encode("utf-8"))


def normalize_topic(topic: str) -> str:
    """'products/update', 'PRODUCTS_UPDATE' or ' Products/Update ' → 'products/update'."""
    cleaned = (topic or "").strip()
    return _ENUM_TOPICS.get(cleaned.upper(), cleaned.lower())


def _payload_product_id(payload: dict) -> str:
    return legacy_id(payload.get("id") or payload.get("admin_graphql_api_id") or payload.get("product_id"))


async def process_shopify_webhook(store_id: str, topic: str, payload: dict, *, correlation_id: str = "") -> dict:
    """Apply one Shopify webhook. Returns {"action": ...}; sync actions are for the caller to enqueue."""
    topic_key = normalize_topic(topic)
    payload = payload if isinstance(payload, dict) else {}

    if topic_key in INCREMENTAL_SYNC_TOPICS:
        return {"action": "incremental_sync", "topic": topic_key}
    if topic_key in FULL_SYNC_TOPICS:
        return {"action": "full_sync", "topic": topic_key}
    if topic_key not in PRODUCT_UPSERT_TOPICS and topic_key != PRODUCT_DELETE_TOPIC:
        return {"action": "ignored", "topic": topic_key}

    product_id = _payload_product_id(payload)
    if not product_id:
        raise ValidationError(f"Shopify {topic_key} webhook has no product id; payload keys: {sorted(payload)[:10]}")

    if topic_key == PRODUCT_DELETE_TOPIC:
        return await _deleted(store_id, topic_key, product_id, correlation_id)

    ctx = await load_store_context(store_id, correlation_id=correlation_id)
    async with ShopifyCatalogClient(ctx.store_config, call_context=ctx.call_context(correlation_id)) as api:
        node = await api.fetch_product(product_id)
    if node is None:
        return await _deleted(store_id, topic_key, product_id, correlation_id)

    # Before the first catalogue sync every product looks "new"; only a create webhook really is.
    never_synced = not await observability.get_state(cursor_key(store_id))
    product = normalize_product(node, storefront_url=ctx.storefront_url)
    result = await store_product(
        store_id, product, initial_import=never_synced and topic_key != "products/create",
        source="webhook", authoritative=True,
    )
    event_types = [event_type for event_type, _ in result["events"]]
    await activity.record(
        store_id, "product", product_id,
        f"Shopify {topic_key}: {result['outcome']}" + (f" ({', '.join(event_types)})" if event_types else ""),
        stage="catalog.webhook", data={"topic": topic_key, "outcome": result["outcome"], "events": event_types},
        correlation_id=correlation_id,
    )
    return {"action": result["outcome"], "topic": topic_key, "product_id": product_id, "events": event_types}


async def _deleted(store_id: str, topic_key: str, product_id: str, correlation_id: str) -> dict:
    events = await remove_product(store_id, product_id, source="webhook")
    event_types = [event_type for event_type, _ in events]
    await activity.record(
        store_id, "product", product_id,
        f"Shopify {topic_key}: product {'removed' if events else 'already removed or unknown'}",
        stage="catalog.webhook", data={"topic": topic_key, "events": event_types}, correlation_id=correlation_id,
    )
    return {"action": "deleted", "topic": topic_key, "product_id": product_id, "events": event_types}
