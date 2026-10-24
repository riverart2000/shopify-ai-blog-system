"""Product selection for auto-pilot rules (Outfy SmartQ ordering + rotation).

Orders:
  latest              newest products first
  latest_then_random  products added in the last 30 days first, then random
  random              shuffled
  least_posted        never-posted first, then longest since last post
  on_sale_first       biggest discount first
  ai                  Gemma chooses (handled by the rule via ai.decisions; this
                      module supplies the pre-filtered candidates and fallback)

Repeats: with reposting off a product is never posted twice; with it on, a
product repeats only after every eligible product has been posted (and not
within `repost_after_days`).
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Iterable

from db.social_engine import posts as post_store

from .base import ProductFilter, RuleContext

ORDERS = ("latest", "latest_then_random", "random", "least_posted", "on_sale_first", "ai")
ACTIVE_POST_STATUSES = ("planned", "generating", "pending_approval", "approved", "publishing", "scheduled")


async def candidate_products(rctx: RuleContext, product_filter: ProductFilter | None = None) -> list[dict]:
    """Eligible products for this campaign (published, in stock, not excluded, in scope)."""
    from ..catalog import eligible_products

    pf = product_filter or rctx.product_filter
    products = await eligible_products(
        rctx.store_id,
        collection_ids=pf.collection_ids or None,
        include_tags=pf.include_tags or None,
        exclude_tags=pf.exclude_tags or None,
        exclude_product_ids=pf.exclude_product_ids or None,
        in_stock_only=pf.in_stock_only,
        on_sale_only=pf.on_sale_only,
        settings=rctx.sctx.settings,
    )
    if pf.product_ids:
        wanted = {str(pid) for pid in pf.product_ids}
        products = [p for p in products if str(p["product_id"]) in wanted]
    return products


async def upcoming_product_ids(store_id: str, *, horizon_days: int = 14) -> set[str]:
    """Products already in the pipeline (planned → scheduled), to avoid double-booking."""
    now = int(time.time())
    rows = await post_store.list_posts(
        store_id, statuses=ACTIVE_POST_STATUSES, scheduled_from=now - 86400,
        scheduled_to=now + horizon_days * 86400, limit=500,
    )
    unscheduled = await post_store.list_posts(store_id, statuses=("planned", "generating"), limit=500)
    ids: set[str] = set()
    for row in [*rows, *unscheduled]:
        ids.update(str(pid) for pid in row.get("product_ids") or [])
    return ids


def _created_ts(product: dict) -> float:
    value = product.get("shopify_created_at") or ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def order_products(products: list[dict], order: str, rng: random.Random, *, now: float | None = None) -> list[dict]:
    now = now or time.time()
    items = list(products)
    if order == "latest":
        items.sort(key=_created_ts, reverse=True)
    elif order == "latest_then_random":
        recent = [p for p in items if now - _created_ts(p) <= 30 * 86400]
        older = [p for p in items if p not in recent]
        recent.sort(key=_created_ts, reverse=True)
        rng.shuffle(older)
        items = recent + older
    elif order == "random":
        rng.shuffle(items)
    elif order == "on_sale_first":
        rng.shuffle(items)
        items.sort(key=lambda p: float(p.get("discount_pct") or 0), reverse=True)
    else:  # least_posted (also the deterministic fallback for "ai")
        rng.shuffle(items)
        items.sort(key=lambda p: (p.get("last_posted_at") or 0, p.get("post_count") or 0))
    return items


def apply_repeat_policy(
    products: list[dict],
    *,
    allow_reposting: bool,
    repost_after_days: int,
    now: float | None = None,
) -> list[dict]:
    """Outfy repeat rules, preserving the incoming order within each group."""
    now = now or time.time()
    never = [p for p in products if not p.get("last_posted_at")]
    if not allow_reposting:
        return never
    cutoff = now - repost_after_days * 86400
    rested = [p for p in products if p.get("last_posted_at") and p["last_posted_at"] <= cutoff]
    rested.sort(key=lambda p: p["last_posted_at"])
    # Never-posted products always go first, so a product only repeats once
    # every eligible product has had its turn (or when slots outnumber them).
    return never + rested


async def pick_products(
    rctx: RuleContext,
    *,
    count: int,
    order: str,
    candidates: list[dict] | None = None,
    exclude_ids: Iterable[str] = (),
    allow_reposting: bool | None = None,
) -> list[dict]:
    settings = rctx.sctx.settings.content
    pool = candidates if candidates is not None else await candidate_products(rctx)
    blocked = {str(pid) for pid in exclude_ids}
    pool = [p for p in pool if str(p["product_id"]) not in blocked]
    ordered = order_products(pool, "least_posted" if order == "ai" else order, rctx.rng,
                             now=rctx.now.timestamp())
    allowed = apply_repeat_policy(
        ordered,
        allow_reposting=settings.allow_reposting if allow_reposting is None else allow_reposting,
        repost_after_days=settings.repost_after_days,
        now=rctx.now.timestamp(),
    )
    return allowed[: max(0, count)]


def image_offset_for(product: dict, settings_rotate: bool) -> int:
    """Rotate through a product's images on each repost (Outfy "Rotate images")."""
    return int(product.get("post_count") or 0) if settings_rotate else 0


def candidate_summary(product: dict, *, now: float | None = None, performance_score: float = 0.0) -> dict:
    """Compact product description sent to Gemma for decisions."""
    now = now or datetime.now(timezone.utc).timestamp()
    last = product.get("last_posted_at")
    return {
        "product_id": str(product["product_id"]),
        "title": product.get("title", ""),
        "price": product.get("price_min"),
        "on_sale": bool(product.get("on_sale")),
        "discount_pct": product.get("discount_pct") or 0,
        "collections": [c.get("title", "") for c in product.get("collections") or []][:4],
        "tags": (product.get("tags") or [])[:8],
        "days_since_posted": None if not last else round((now - last) / 86400, 1),
        "post_count": int(product.get("post_count") or 0),
        "is_new": now - _created_ts(product) <= 30 * 86400,
        "performance_score": round(performance_score, 3),
    }
