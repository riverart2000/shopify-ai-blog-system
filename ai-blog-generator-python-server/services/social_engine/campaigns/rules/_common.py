"""Helpers shared by campaign rules."""
from __future__ import annotations

import logging
from datetime import date

from db.social_engine import posts as post_store
from db.social_engine import products as product_store

from ...models import MULTI_PRODUCT_CONTENT_TYPES, ContentType
from ..base import PlanItem, RuleContext
from ..selection import candidate_summary, image_offset_for

logger = logging.getLogger("ai_blog_server.social_engine.campaigns")


async def already_planned(rctx: RuleContext, dedupe_key: str) -> bool:
    return await post_store.get_post_by_dedupe(rctx.store_id, dedupe_key) is not None


def products_needed(content_type: str, max_products: int = 4) -> int:
    if content_type in MULTI_PRODUCT_CONTENT_TYPES:
        return max(2, min(4, max_products))
    if content_type in {ContentType.QUOTE, ContentType.BLOG_ARTICLE}:
        return 0
    return 1


def make_item(
    rctx: RuleContext,
    *,
    dedupe_key: str,
    content_type: str,
    products: list[dict],
    template_key: str = "",
    angle: str = "",
    title: str = "",
    texts: dict | None = None,
    options: dict | None = None,
) -> PlanItem:
    rotate = rctx.sctx.settings.content.rotate_images
    opts = {"image_offset": image_offset_for(products[0], rotate) if products else 0}
    opts.update(options or {})
    return PlanItem(
        dedupe_key=dedupe_key,
        content_type=content_type,
        product_ids=[str(p["product_id"]) for p in products],
        channel_ids=list(rctx.channel_ids),
        template_key=template_key,
        title=title or (products[0]["title"] if products else rctx.campaign.get("name", "")),
        angle=angle,
        texts=dict(texts or {}),
        options=opts,
    )


async def new_events(rctx: RuleContext, event_types: tuple[str, ...], *, lookback_days: int = 3) -> list[dict]:
    """Product events after this campaign's cursor (first run: last `lookback_days`)."""
    cursor = rctx.state.get("event_cursor")
    if cursor is None:
        since = int(rctx.now.timestamp()) - lookback_days * 86400
        events = await product_store.list_events(rctx.store_id, event_types=event_types, since=since)
    else:
        events = await product_store.list_events(rctx.store_id, event_types=event_types, after_id=int(cursor))
    if events:
        rctx.state["event_cursor"] = max(int(e["id"]) for e in events)
    elif cursor is None:
        latest = await product_store.list_events(rctx.store_id, since=int(rctx.now.timestamp()) - 60, limit=1)
        rctx.state["event_cursor"] = int(latest[-1]["id"]) if latest else 0
    return events


async def performance_scores(rctx: RuleContext) -> dict[str, float]:
    """Engagement per impression by product id from collected metrics (0 when unknown)."""
    try:
        rows = await post_store.metric_rollup(rctx.store_id, group_by="product", limit=500)
    except Exception:  # metrics are optional input to decisions
        logger.debug("Product metric roll-up unavailable", exc_info=True)
        return {}
    scores: dict[str, float] = {}
    for row in rows:
        impressions = float(row.get("impressions") or 0) or float(row.get("views") or 0)
        engagement = float(row.get("engagement") or 0) or sum(
            float(row.get(k) or 0) for k in ("likes", "comments", "shares", "saves", "clicks")
        )
        scores[str(row["dimension"])] = round(engagement / impressions, 4) if impressions else 0.0
    return scores


def summaries(rctx: RuleContext, products: list[dict], scores: dict[str, float]) -> list[dict]:
    now = rctx.now.timestamp()
    return [candidate_summary(p, now=now, performance_score=scores.get(str(p["product_id"]), 0.0)) for p in products]


def day_key(day: date) -> str:
    return day.isoformat()
