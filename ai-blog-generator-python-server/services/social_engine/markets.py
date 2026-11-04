"""Which country a promotion is for: the UK, the US, or both.

The store sells to both on one web address (bioluxelab.com; Shopify shows US visitors dollar prices).
A promotion's market changes when it posts, the prices it shows and how captions are written. It never
limits who can use its discount code: codes work for every customer, anywhere.

- "GB": the store's own schedule (UK time), £ prices, British English. Same as before markets existed.
- "US": 09:00–21:00 New York time, Shopify's own US dollar prices, American English.
- "both": 12:00–22:00 UK time (UK afternoon/evening = US morning/afternoon), no prices (the offer is
  the message, since one post is seen in both currencies).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger("ai_blog_server.social_engine.markets")


@dataclass(frozen=True)
class Market:
    key: str
    label: str
    timezone: str | None       # posting hours are in this zone (None = the store's own schedule)
    window_start: str | None
    window_end: str | None
    audience: str              # told to the caption writer


MARKETS: dict[str, Market] = {
    "GB": Market("GB", "UK", None, None, None, ""),
    "US": Market("US", "US", "America/New_York", "09:00", "21:00",
                 "Shoppers in the United States: American spelling, prices in US dollars ($)."),
    "both": Market("both", "UK & US", "Europe/London", "12:00", "22:00",
                   "Shoppers in both the UK and the US: words that work for both, no prices (the offer applies "
                   "to everyone)."),
}
MARKET_KEYS = tuple(MARKETS)


def market_for(countries: tuple[str, ...] | list[str]) -> str:
    """The market for an occasion celebrated in these countries."""
    wanted = {c.upper() for c in countries}
    if wanted == {"US"}:
        return "US"
    if wanted == {"GB"}:
        return "GB"
    return "both"


def get_market(key: str | None) -> Market:
    return MARKETS.get(key or "GB", MARKETS["GB"])


def schedule_for(market: str | None, schedule: Any) -> Any:
    """A campaign's schedule overrides with the market's posting hours (what the campaign set itself wins)."""
    m = get_market(market)
    if m.timezone is None:
        return schedule
    updates = {"timezone": m.timezone}
    if not schedule.window_start:
        updates["window_start"] = m.window_start
    if not schedule.window_end:
        updates["window_end"] = m.window_end
    return schedule.model_copy(update=updates)


PRICES_QUERY = """
query MarketPrices($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Product {
      id
      contextualPricing(context: {country: US}) {
        minVariantPricing { price { amount currencyCode } compareAtPrice { amount } }
        maxVariantPricing { price { amount currencyCode } compareAtPrice { amount } }
      }
    }
  }
}"""


def _gid(product: dict) -> str:
    gid = str(product.get("gid") or "")
    if gid.startswith("gid://"):
        return gid
    return f"gid://shopify/Product/{product.get('product_id')}"


async def us_prices(sctx: Any, products: list[dict], *, graphql: Any = None) -> dict[str, dict[str, float | None]]:
    """Shopify's own US dollar prices (what a US visitor sees on the site), by product id."""
    if not products:
        return {}
    if graphql is None:
        import shopify_client

        graphql = shopify_client.graphql_request
    by_gid = {_gid(p): str(p["product_id"]) for p in products}
    data = await graphql(sctx.store_config, PRICES_QUERY, {"ids": list(by_gid)})
    found: dict[str, dict[str, float | None]] = {}
    for node in (data or {}).get("nodes") or []:
        if not node or node.get("id") not in by_gid:
            continue
        pricing = node.get("contextualPricing") or {}
        low = pricing.get("minVariantPricing") or {}
        high = pricing.get("maxVariantPricing") or {}
        price = (low.get("price") or {})
        if price.get("currencyCode") != "USD":
            continue  # not a US price: show none rather than a wrong one

        def amount(value: dict | None) -> float | None:
            try:
                number = float((value or {}).get("amount"))
            except (TypeError, ValueError):
                return None
            return number if number > 0 else None

        found[by_gid[node["id"]]] = {
            "price_min": amount(price),
            "price_max": amount(high.get("price")),
            "compare_at_max": amount(high.get("compareAtPrice")) or amount(low.get("compareAtPrice")),
        }
    return found


async def apply_market(
    sctx: Any, products: list[dict], texts: dict[str, Any], market: str | None, *, graphql: Any = None,
) -> tuple[Any, list[dict], dict[str, Any], list[str]]:
    """The store context, products and texts a post is written and drawn with, for its market.

    Returns (context, products to show, texts, warnings). The original products are untouched (their
    £ prices stay in the post's snapshot, so the "price changed" check keeps working)."""
    m = get_market(market)
    if m.key == "GB":
        return sctx, products, texts, []
    settings = sctx.settings
    warnings: list[str] = []
    texts = {**texts, "audience": m.audience}
    if m.key == "US":
        try:
            prices = await us_prices(sctx, products, graphql=graphql)
        except Exception as exc:  # no US prices: the post shows none rather than £ ones
            logger.warning("US prices unavailable: %s", exc)
            prices = {}
            warnings.append("Shopify's US prices couldn't be read, so this post shows no prices.")
        shown = []
        for product in products:
            usd = prices.get(str(product.get("product_id")))
            shown.append({**product, **usd, "currency": "USD"} if usd else
                         {**product, "price_min": None, "price_max": None, "compare_at_max": None})
        brand = settings.brand.model_copy(update={"currency_symbol": "$", "include_taxes_pct": 0})
        if not prices:
            brand = brand.model_copy(update={"show_price": False})
        captions = settings.captions.model_copy(update={"language": "en-US"})
        if not prices:
            captions = captions.model_copy(update={"include_price": False})
        settings = settings.model_copy(update={"brand": brand, "captions": captions})
        return replace(sctx, settings=settings), shown, texts, warnings
    # both countries: one post seen in two currencies, so no prices at all
    brand = settings.brand.model_copy(update={"show_price": False})
    captions = settings.captions.model_copy(update={"include_price": False})
    settings = settings.model_copy(update={"brand": brand, "captions": captions})
    return replace(sctx, settings=settings), products, texts, warnings
