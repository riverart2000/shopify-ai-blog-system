"""Inbound webhooks. They return 2xx immediately and do the work in a job.

* Postiz (post published): `POST /api/social-engine/webhooks/postiz/{store_id}/{token}`
  The token is a per-store secret (Postiz doesn't sign webhooks). The payload
  only triggers a reconcile — state always comes from the Postiz API.
* Shopify product webhooks arrive at the embedded app, which verifies the HMAC
  with the app secret and forwards them to `/api/social-engine/internal/shopify-webhook`
  (backend API key required).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from db.social_engine import observability
from services.social_engine.jobs.queue import enqueue
from services.social_engine.settings import load_settings

from .common import require_api_key, resolve_store_id

public_router = APIRouter(prefix="/api/social-engine/webhooks")
internal_router = APIRouter(prefix="/api/social-engine/internal", dependencies=[Depends(require_api_key)])

_MAX_BODY = 2_000_000


@public_router.post("/postiz/{store_id}/{token}")
async def postiz_webhook(store_id: str, token: str, request: Request) -> dict[str, Any]:
    settings = await load_settings(store_id)
    expected = settings.postiz.webhook_token
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook token.")
    body = await request.body()
    if len(body) > _MAX_BODY:
        raise HTTPException(status_code=413, detail="Payload too large.")
    try:
        payload: Any = json.loads(body or b"{}")
    except ValueError:
        payload = {"raw": body[:5000].decode("utf-8", errors="replace")}
    fingerprint = hashlib.sha256(body).hexdigest()
    event_id, is_new = await observability.record_webhook(
        source="postiz", topic="post.published", dedupe_key=fingerprint, payload=payload, store_id=store_id,
    )
    if is_new:
        await enqueue("postiz.webhook", {"webhook_event_id": event_id}, store_id=store_id,
                      dedupe_key=f"postiz.webhook:{event_id}", correlation_id=f"webhook:{event_id}")
    return {"ok": True, "duplicate": not is_new}


class ShopifyWebhook(BaseModel):
    store_id: str = ""
    shop: str = ""
    topic: str
    webhook_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


_PRODUCT_TOPICS = {
    "products/create", "products/update", "products/delete", "inventory_levels/update",
    "collections/create", "collections/update", "collections/delete",
}


@internal_router.post("/shopify-webhook")
async def shopify_webhook(payload: ShopifyWebhook) -> dict[str, Any]:
    topic = payload.topic.lower().replace("_", "/") if "/" not in payload.topic else payload.topic.lower()
    if topic not in _PRODUCT_TOPICS:
        return {"ok": True, "ignored": topic}
    sid = payload.store_id
    if not sid and payload.shop:
        import db

        store = await db.get_store_by_domain(payload.shop.strip().lower())
        sid = str(store["id"]) if store else ""
    sid = await resolve_store_id(sid)
    dedupe = payload.webhook_id or hashlib.sha256(
        json.dumps(payload.payload, sort_keys=True, default=str).encode() + topic.encode()
    ).hexdigest()
    event_id, is_new = await observability.record_webhook(
        source="shopify", topic=topic, dedupe_key=dedupe, payload=payload.payload, store_id=sid,
    )
    if is_new and (await load_settings(sid)).enabled:
        await enqueue("catalog.webhook", {"webhook_event_id": event_id}, store_id=sid,
                      dedupe_key=f"catalog.webhook:{event_id}", correlation_id=f"webhook:{event_id}")
    return {"ok": True, "duplicate": not is_new}
