"""Social Engine catalogue: Shopify product sync, change detection and eligibility.

    sync_catalog(store_id, full=False)          → summary dict (jobs: catalog.sync / catalog.sync_full)
    process_shopify_webhook(store_id, topic, payload) → {"action": ...}
    verify_shopify_hmac(raw_body, hmac_header, secret) → bool
    eligible_products(store_id, collection_ids=..., ...) → product rows the auto-pilot may post
    product_images(product, offset=, max_images=) / format_price(value, symbol, include_taxes_pct)

Change events (see `changes.EVENT_TYPES`) are stored in `social_product_events`
and read with `db.social_engine.products.list_events`.
"""
from .changes import (
    BACK_IN_STOCK, EVENT_TYPES, IMAGES_CHANGED, OUT_OF_STOCK, PRICE_DROP, PRICE_INCREASE, PRODUCT_CREATED,
    PRODUCT_PUBLISHED, PRODUCT_REMOVED, SALE_ENDED, SALE_STARTED, diff, in_stock, is_live,
)
from .eligibility import eligible_products
from .normalize import (
    content_hash, format_price, html_to_text, image_urls, legacy_id, normalize_collection, normalize_product,
    product_images, storefront_product_url,
)
from .shopify_api import API_VERSION, QueryCostExceeded, ShopifyAccessDenied, ShopifyCatalogClient
from .sync import cursor_key, store_product, sync_catalog
from .webhooks import normalize_topic, process_shopify_webhook, verify_shopify_hmac

__all__ = [
    "API_VERSION", "BACK_IN_STOCK", "EVENT_TYPES", "IMAGES_CHANGED", "OUT_OF_STOCK", "PRICE_DROP",
    "PRICE_INCREASE", "PRODUCT_CREATED", "PRODUCT_PUBLISHED", "PRODUCT_REMOVED", "QueryCostExceeded",
    "SALE_ENDED", "SALE_STARTED", "ShopifyAccessDenied", "ShopifyCatalogClient", "content_hash", "cursor_key",
    "diff", "eligible_products", "format_price", "html_to_text", "image_urls", "in_stock", "is_live", "legacy_id",
    "normalize_collection", "normalize_product", "normalize_topic", "process_shopify_webhook", "product_images",
    "store_product", "storefront_product_url", "sync_catalog", "verify_shopify_hmac",
]
