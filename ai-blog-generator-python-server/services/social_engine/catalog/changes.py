"""Pure change detection between a stored product row and a fresh Shopify product.

Events feed the auto-pilot (new arrivals, price drops, back in stock, ...).
`diff` works on both stored rows (`db.social_engine.products.get_product`) and
freshly normalised products, because both use the same field names.
"""
from __future__ import annotations

from typing import Any

from .normalize import image_urls, in_stock

PRODUCT_CREATED = "product_created"
PRODUCT_PUBLISHED = "product_published"
PRODUCT_REMOVED = "product_removed"
PRICE_DROP = "price_drop"
PRICE_INCREASE = "price_increase"
SALE_STARTED = "sale_started"
SALE_ENDED = "sale_ended"
BACK_IN_STOCK = "back_in_stock"
OUT_OF_STOCK = "out_of_stock"
IMAGES_CHANGED = "images_changed"

EVENT_TYPES = (
    PRODUCT_CREATED, PRODUCT_PUBLISHED, PRODUCT_REMOVED, PRICE_DROP, PRICE_INCREASE,
    SALE_STARTED, SALE_ENDED, BACK_IN_STOCK, OUT_OF_STOCK, IMAGES_CHANGED,
)

PRICE_CHANGE_THRESHOLD_PCT = 1.0

Event = tuple[str, dict[str, Any]]


def is_live(product: dict) -> bool:
    """Visible to shoppers: ACTIVE, published to the Online Store and not deleted."""
    return (
        not product.get("deleted_at")
        and str(product.get("status") or "").upper() == "ACTIVE"
        and bool(product.get("url"))
    )


def _price(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _base(product: dict) -> dict[str, Any]:
    return {"title": product.get("title") or "", "handle": product.get("handle") or ""}


def removed_event(previous: dict, reason: str) -> Event:
    """`product_removed` for a product that was deleted, archived or unpublished."""
    return PRODUCT_REMOVED, {
        **_base(previous),
        "reason": reason,
        "old_status": str(previous.get("status") or ""),
        "new_status": "DELETED" if reason == "deleted" else reason.upper(),
    }


def diff(previous: dict | None, new: dict, *, initial_import: bool) -> list[Event]:
    """Events implied by moving from `previous` (None = unknown product) to `new`.

    During the initial import of a catalogue nothing is announced as "new";
    otherwise the auto-pilot would post every existing product as a new arrival.
    """
    if previous is None:
        if initial_import:
            return []
        return [(PRODUCT_CREATED, {
            **_base(new),
            "status": new.get("status") or "",
            "available": bool(new.get("available")),
            "price": _price(new.get("price_min")),
            "currency": new.get("currency") or "",
            "url": new.get("url") or "",
        })]

    events: list[Event] = []
    was_deleted = bool(previous.get("deleted_at"))
    old_live, new_live = is_live(previous), is_live(new)
    old_status = "DELETED" if was_deleted else str(previous.get("status") or "")
    new_status = str(new.get("status") or "")

    if new_live and not old_live:
        events.append((PRODUCT_PUBLISHED, {
            **_base(new), "old_status": old_status, "new_status": new_status, "url": new.get("url") or "",
        }))
    elif old_live and not new_live:
        reason = "archived" if new_status.upper() == "ARCHIVED" else "unpublished"
        events.append((PRODUCT_REMOVED, {
            **_base(new), "reason": reason, "old_status": old_status, "new_status": new_status,
        }))
    if was_deleted:
        return events  # a restored product is "published", not a price/stock change

    old_price, new_price = _price(previous.get("price_min")), _price(new.get("price_min"))
    if old_price > 0 and new_price > 0 and old_price != new_price:
        change = round((new_price - old_price) / old_price * 100, 6)
        details = {
            **_base(new), "old_price": old_price, "new_price": new_price, "pct": round(abs(change), 1),
            "old_price_max": _price(previous.get("price_max")), "new_price_max": _price(new.get("price_max")),
            "currency": new.get("currency") or previous.get("currency") or "",
        }
        if change <= -PRICE_CHANGE_THRESHOLD_PCT:
            events.append((PRICE_DROP, details))
        elif change >= PRICE_CHANGE_THRESHOLD_PCT:
            events.append((PRICE_INCREASE, details))

    old_sale, new_sale = bool(previous.get("on_sale")), bool(new.get("on_sale"))
    if new_sale != old_sale:
        events.append((SALE_STARTED if new_sale else SALE_ENDED, {
            **_base(new),
            "old_discount_pct": _price(previous.get("discount_pct")),
            "new_discount_pct": _price(new.get("discount_pct")),
            "old_compare_at_max": previous.get("compare_at_max"),
            "new_compare_at_max": new.get("compare_at_max"),
            "price": new_price,
            "currency": new.get("currency") or "",
        }))

    if old_live and new_live:
        old_stock, new_stock = in_stock(previous), in_stock(new)
        if old_stock != new_stock:
            events.append((BACK_IN_STOCK if new_stock else OUT_OF_STOCK, {
                **_base(new),
                "old_in_stock": old_stock, "new_in_stock": new_stock,
                "old_inventory": previous.get("total_inventory"),
                "new_inventory": new.get("total_inventory"),
            }))

    old_images, new_images = image_urls(previous), image_urls(new)
    if old_images != new_images:
        events.append((IMAGES_CHANGED, {
            **_base(new),
            "old_count": len(old_images), "new_count": len(new_images),
            "added": [url for url in new_images if url not in old_images][:10],
            "removed": [url for url in old_images if url not in new_images][:10],
            "old_featured": old_images[0] if old_images else "",
            "new_featured": new_images[0] if new_images else "",
        }))
    return events
