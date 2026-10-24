"""Pure conversion of Shopify Admin GraphQL nodes into catalogue rows.

Nothing here touches the network or the database, so every rule (sale maths,
availability, image order, price display) is unit-testable in isolation.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable

DESCRIPTION_MAX_CHARS = 2000
DEFAULT_CURRENCY = "GBP"

_SCRIPT_STYLE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")
_LIST_ITEM = re.compile(r"(?i)<\s*li\b[^>]*>")
_BLOCK_BREAK = re.compile(r"(?i)<\s*(br|/p|/div|/li|/h[1-6]|/tr|/ul|/ol|/blockquote|/section|/table)\b[^>]*>")
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def legacy_id(value: Any) -> str:
    """'gid://shopify/Product/123', 123 or '123' → '123' ('' when missing)."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.startswith("gid://"):
        text = text.rsplit("/", 1)[-1].split("?", 1)[0]
    return text


def product_gid(product_id: Any) -> str:
    """Numeric id or gid → 'gid://shopify/Product/<id>'."""
    text = str(product_id or "").strip()
    return text if text.startswith("gid://") else f"gid://shopify/Product/{text}"


def html_to_text(value: str | None, *, max_chars: int = DESCRIPTION_MAX_CHARS) -> str:
    """Convert product description HTML to readable plain text, truncated on a word boundary."""
    if not value:
        return ""
    text = _SCRIPT_STYLE.sub(" ", str(value))
    text = _LIST_ITEM.sub("\n- ", text)
    text = _BLOCK_BREAK.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line and line != "-")
    if len(text) <= max_chars:
        return text
    cut = text[: max(0, max_chars - 1)]
    if " " in cut[-80:]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-\n") + "…"


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("amount")
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _money(value: Any) -> float | None:
    number = _decimal(value)
    return float(number) if number is not None else None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def connection_nodes(connection: Any) -> list[dict]:
    """Accept a GraphQL connection ({nodes} or {edges}) or a plain list."""
    if not connection:
        return []
    if isinstance(connection, list):
        return [item for item in connection if isinstance(item, dict)]
    if isinstance(connection, dict):
        if isinstance(connection.get("nodes"), list):
            return [item for item in connection["nodes"] if isinstance(item, dict)]
        if isinstance(connection.get("edges"), list):
            return [edge["node"] for edge in connection["edges"] if isinstance(edge, dict) and isinstance(edge.get("node"), dict)]
    return []


def _image_from(raw: Any) -> dict | None:
    """MediaImage ({image: {...}}), Image ({url|src, altText}) or a URL string → image dict."""
    if not raw:
        return None
    if isinstance(raw, str):
        return {"url": raw, "alt": "", "width": None, "height": None}
    if not isinstance(raw, dict):
        return None
    image = raw.get("image") if isinstance(raw.get("image"), dict) else raw
    url = image.get("url") or image.get("src") or image.get("originalSrc") or ""
    if not url:
        return None
    return {
        "url": str(url),
        "alt": str(image.get("altText") or image.get("alt") or raw.get("alt") or ""),
        "width": _int_or_none(image.get("width")),
        "height": _int_or_none(image.get("height")),
    }


def _image_key(url: str) -> str:
    """Dedupe key: Shopify CDN URLs differ only by `?v=` cache busters for the same file."""
    return url.split("?", 1)[0].strip().lower()


def _dedupe_images(images: Iterable[dict | None]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for image in images:
        if not image or not image.get("url"):
            continue
        key = _image_key(image["url"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(image)
    return unique


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    tags: list[str] = []
    for tag in value or []:
        cleaned = str(tag).strip()
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------

def _variant(node: dict) -> dict:
    gid = str(node.get("id") or "")
    compare_at = _money(node.get("compareAtPrice"))
    return {
        "id": legacy_id(node.get("legacyResourceId") or gid),
        "gid": gid,
        "title": str(node.get("title") or ""),
        "price": _money(node.get("price")),
        "compare_at_price": compare_at if compare_at and compare_at > 0 else None,
        "sku": str(node.get("sku") or ""),
        "inventory_quantity": _int_or_none(node.get("inventoryQuantity")),
        "available": bool(node.get("availableForSale")),
    }


def _sale_figures(variants: list[dict]) -> tuple[float | None, float]:
    """(max compare-at price above its own price, max % off rounded to 1 dp)."""
    compare_at_max: float | None = None
    discount = Decimal("0")
    for variant in variants:
        price = _decimal(variant.get("price"))
        compare = _decimal(variant.get("compare_at_price"))
        if price is None or compare is None or compare <= price or compare <= 0:
            continue
        compare_at_max = float(compare) if compare_at_max is None else max(compare_at_max, float(compare))
        discount = max(discount, (compare - price) / compare * 100)
    return compare_at_max, float(discount.quantize(Decimal("0.1"), ROUND_HALF_UP))


def _is_published(node: dict) -> bool:
    """Published to the Online Store. Unknown (fields not fetched) counts as published."""
    if "publishedAt" not in node and "onlineStoreUrl" not in node:
        return True
    return bool(node.get("publishedAt") or node.get("onlineStoreUrl"))


def storefront_product_url(storefront_url: str, handle: str) -> str:
    """Same rule as StoreContext.product_url, kept pure."""
    base = (storefront_url or "").strip().rstrip("/")
    return f"{base}/products/{handle}" if handle else base


def in_stock(product: dict) -> bool:
    """Any variant can be bought (respects 'continue selling when out of stock')."""
    variants = product.get("variants") or []
    if variants:
        return any(bool(variant.get("available")) for variant in variants)
    inventory = product.get("total_inventory")
    if inventory is not None:
        return int(inventory) > 0
    return bool(product.get("available"))


def normalize_product(node: dict, *, storefront_url: str, default_currency: str = DEFAULT_CURRENCY) -> dict:
    """Shopify GraphQL Product node → dict accepted by `db.social_engine.products.upsert_product`."""
    gid = str(node.get("id") or "")
    product_id = legacy_id(node.get("legacyResourceId") or gid)
    handle = str(node.get("handle") or "")
    status = str(node.get("status") or "ACTIVE").upper()
    published = _is_published(node)

    variants = [_variant(item) for item in connection_nodes(node.get("variants"))]
    price_range = node.get("priceRangeV2") or node.get("priceRange") or {}
    min_price = _money(price_range.get("minVariantPrice"))
    max_price = _money(price_range.get("maxVariantPrice"))
    variant_prices = [v["price"] for v in variants if v["price"] is not None]
    if min_price is None:
        min_price = min(variant_prices) if variant_prices else 0.0
    if max_price is None:
        max_price = max(variant_prices) if variant_prices else min_price
    currency = str(
        (price_range.get("minVariantPrice") or {}).get("currencyCode") or default_currency or DEFAULT_CURRENCY
    ).upper()
    compare_at_max, discount_pct = _sale_figures(variants)

    featured = _image_from(node.get("featuredMedia")) or _image_from(node.get("featuredImage"))
    gallery = [_image_from(item) for item in connection_nodes(node.get("media"))]
    gallery += [_image_from(item) for item in connection_nodes(node.get("images"))]
    images = _dedupe_images([featured, *gallery])

    collections = [
        {
            "id": legacy_id(item.get("legacyResourceId") or item.get("id")),
            "handle": str(item.get("handle") or ""),
            "title": str(item.get("title") or ""),
        }
        for item in connection_nodes(node.get("collections"))
    ]

    product: dict[str, Any] = {
        "product_id": product_id,
        "gid": gid or (product_gid(product_id) if product_id else ""),
        "handle": handle,
        "title": str(node.get("title") or ""),
        "description": html_to_text(node.get("descriptionHtml") or node.get("body_html") or node.get("description")),
        "product_type": str(node.get("productType") or ""),
        "vendor": str(node.get("vendor") or ""),
        "status": status,
        "url": storefront_product_url(storefront_url, handle) if published and handle else "",
        "featured_image_url": images[0]["url"] if images else "",
        "price_min": float(min_price),
        "price_max": float(max_price),
        "compare_at_max": compare_at_max,
        "currency": currency,
        "on_sale": compare_at_max is not None,
        "discount_pct": discount_pct,
        "total_inventory": _int_or_none(node.get("totalInventory")),
        "shopify_created_at": str(node.get("createdAt") or ""),
        "shopify_updated_at": str(node.get("updatedAt") or ""),
        "published_at": str(node.get("publishedAt") or ""),
        "tags": _tags(node.get("tags")),
        "images": images,
        "variants": variants,
        "collections": [c for c in collections if c["id"]],
    }
    product["available"] = status == "ACTIVE" and published and in_stock(product)
    product["content_hash"] = content_hash(product)
    return product


_HASH_FIELDS = (
    "title", "handle", "description", "product_type", "vendor", "status", "url",
    "price_min", "price_max", "compare_at_max", "currency", "on_sale", "discount_pct", "available",
)


def content_hash(product: dict) -> str:
    """sha256 of the fields that change what a post would say or show (not stock counts)."""
    payload = {field: product.get(field) for field in _HASH_FIELDS}
    payload["tags"] = sorted(tag.lower() for tag in product.get("tags") or [])
    payload["images"] = image_urls(product)
    payload["variants"] = [
        [v.get("title"), v.get("price"), v.get("compare_at_price"), bool(v.get("available"))]
        for v in product.get("variants") or []
    ]
    payload["collections"] = sorted(str(c.get("id")) for c in product.get("collections") or [])
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

def normalize_collection(node: dict) -> dict:
    """Collection node (with every member fetched into `products`) → `upsert_collection` dict."""
    raw_count = node.get("productsCount")
    count = _int_or_none(raw_count.get("count") if isinstance(raw_count, dict) else raw_count)
    product_ids = [legacy_id(item.get("legacyResourceId") or item.get("id")) for item in connection_nodes(node.get("products"))]
    product_ids = list(dict.fromkeys(pid for pid in product_ids if pid))
    image = node.get("image") or {}
    return {
        "collection_id": legacy_id(node.get("legacyResourceId") or node.get("id")),
        "gid": str(node.get("id") or ""),
        "handle": str(node.get("handle") or ""),
        "title": str(node.get("title") or ""),
        "image_url": str(image.get("url") or image.get("src") or "") if isinstance(image, dict) else "",
        "products_count": count if count is not None else len(product_ids),
        "product_ids": product_ids,
    }


# ---------------------------------------------------------------------------
# Helpers used by creatives and captions
# ---------------------------------------------------------------------------

def image_urls(product: dict) -> list[str]:
    """Distinct image URLs in display order (featured first); works on rows and normalised dicts."""
    raw = list(product.get("images") or [])
    if not raw and product.get("featured_image_url"):
        raw = [product["featured_image_url"]]
    return [image["url"] for image in _dedupe_images(_image_from(item) for item in raw)]


def product_images(product: dict, *, offset: int = 0, max_images: int = 1) -> list[str]:
    """Pick `max_images` URLs starting at `offset`, wrapping around (Outfy's 'Rotate images')."""
    urls = image_urls(product)
    if not urls or max_images <= 0:
        return []
    start = int(offset) % len(urls)
    return [urls[(start + i) % len(urls)] for i in range(min(int(max_images), len(urls)))]


def format_price(value: Any, symbol: str = "£", include_taxes_pct: float = 0) -> str:
    """'£24.99'; with `include_taxes_pct` the shown price includes that tax (Outfy 'Include taxes')."""
    amount = _decimal(value)
    if amount is None:
        return ""
    pct = _decimal(include_taxes_pct) or Decimal("0")
    if pct > 0:
        amount = amount * (Decimal("1") + pct / Decimal("100"))
    amount = amount.quantize(Decimal("0.01"), ROUND_HALF_UP)
    return f"{symbol or ''}{amount:,.2f}"
