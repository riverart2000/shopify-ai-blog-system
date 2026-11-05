"""A shoppable page for the Instagram and TikTok bio link.

Posts on those networks can't carry a clickable link, so captions say "link in bio". This keeps one Shopify page
(e.g. /pages/shop-our-feed) showing the products most recently posted, newest first, each opening its own product
page. The page is rebuilt every hour and only rewritten when its contents change.

Only a page made here is ever updated: if another page already has the handle, the refresh stops and says so.
"""
from __future__ import annotations

import hashlib
import html
from typing import Any, Awaitable, Callable

from config import StoreConfig
from db.social_engine import observability
from db.social_engine.connection import now
from db.social_engine import products as product_store

from .context import StoreContext
from .errors import ConfigurationError, ExternalServiceError
from .settings import ShoppableSettings
from .utm import tracked_url

GraphQL = Callable[[StoreConfig, str, dict], Awaitable[dict]]

MARKER = "<!-- social-autopilot:shoppable -->"
BIO_SOURCE = "linkinbio"  # utm_source for visits that come through the bio link
IMAGE_WIDTH = 600
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}

FIND = "query($q:String!){ pages(first:1, query:$q){ nodes{ id handle body } } }"
GET = "query($id:ID!){ page(id:$id){ id handle body } }"
CREATE = (
    "mutation($page:PageCreateInput!){ pageCreate(page:$page){ page{ id handle } userErrors{ field message } } }"
)
UPDATE = (
    "mutation($id:ID!,$page:PageUpdateInput!){ pageUpdate(id:$id, page:$page)"
    "{ page{ id handle } userErrors{ field message } } }"
)

STYLE = (
    "<style>.sa-feed{max-width:1100px;margin:0 auto}.sa-feed p{text-align:center;margin:0 0 20px}"
    ".sa-feed-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px}"
    ".sa-feed-grid a{display:block;text-decoration:none;color:inherit}"
    ".sa-feed-grid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:10px;display:block}"
    ".sa-feed-grid span{display:block;font-size:14px;line-height:1.3;margin-top:6px}"
    ".sa-feed-grid .sa-price{font-weight:600}</style>"
)


def state_key(store_id: str) -> str:
    return f"shoppable:{store_id}"


def page_url(sctx: StoreContext) -> str:
    return f"{sctx.storefront_url}/pages/{sctx.settings.shoppable.page_handle}"


def price_label(product: dict) -> str:
    low, high = product.get("price_min"), product.get("price_max")
    if low is None:
        return ""
    currency = str(product.get("currency") or "").upper()
    amount = f"{SYMBOLS.get(currency, currency + ' ')}{float(low):.2f}"
    return f"From {amount}" if high is not None and float(high) > float(low) else amount


def _sized(image_url: str) -> str:
    return f"{image_url}{'&' if '?' in image_url else '?'}width={IMAGE_WIDTH}"


def render_page(products: list[dict], sctx: StoreContext) -> str:
    """The page body: an intro line and a grid of product tiles. Everything from the catalogue is escaped."""
    settings = sctx.settings.shoppable
    tiles = []
    for product in products:
        link = tracked_url(
            product.get("url") or sctx.product_url(product.get("handle", "")),
            provider=BIO_SOURCE, campaign=settings.page_handle, content=product.get("handle", ""),
            settings=sctx.settings.utm,
        )
        title = html.escape(str(product.get("title") or ""))
        price = html.escape(price_label(product))
        tiles.append(
            f'<a href="{html.escape(link, quote=True)}">'
            f'<img src="{html.escape(_sized(product["featured_image_url"]), quote=True)}" alt="{title}" '
            f'loading="lazy" width="{IMAGE_WIDTH}" height="{IMAGE_WIDTH}">'
            f"<span>{title}</span>" + (f'<span class="sa-price">{price}</span>' if price else "") + "</a>"
        )
    intro = f"<p>{html.escape(settings.intro)}</p>" if settings.intro.strip() else ""
    return f'{MARKER}\n<div class="sa-feed">{STYLE}{intro}<div class="sa-feed-grid">{"".join(tiles)}</div></div>'


async def _default_graphql(store: StoreConfig, query: str, variables: dict) -> dict:
    import shopify_client

    return await shopify_client.graphql_request(store, query, variables)


def _checked(result: dict | None, what: str) -> dict:
    if not result:
        raise ExternalServiceError("Shopify", f"returned nothing when {what}.")
    errors = result.get("userErrors") or []
    if errors:
        refusal = "; ".join(e.get("message", "") for e in errors)[:300]
        raise ExternalServiceError("Shopify", f"refused {what}: {refusal}", retryable=False)
    return result.get("page") or {}


async def _our_page(sctx: StoreContext, state: dict, call: GraphQL) -> str:
    """The id of the page to update, or "" to create one. Never returns a page that wasn't made here."""
    handle = sctx.settings.shoppable.page_handle
    if state.get("page_id") and state.get("handle") == handle:
        page = (await call(sctx.store_config, GET, {"id": state["page_id"]})).get("page")
        if page:
            return str(page["id"])
    found = (await call(sctx.store_config, FIND, {"q": f"handle:{handle}"})).get("pages", {}).get("nodes") or []
    page = next((p for p in found if p.get("handle") == handle), None)
    if page is None:
        return ""
    if MARKER not in str(page.get("body") or ""):
        raise ConfigurationError(
            f"A page with the address /pages/{handle} already exists and wasn't made by Social Autopilot. "
            "Choose another address in Settings → Shoppable page."
        )
    return str(page["id"])


async def refresh(sctx: StoreContext, *, graphql: GraphQL | None = None) -> dict[str, Any]:
    """Rebuild the page from the products most recently posted. Writes to Shopify only when it changed."""
    settings: ShoppableSettings = sctx.settings.shoppable
    if not settings.enabled:
        return {"status": "off"}
    since = now() - settings.days * 86400
    products = await product_store.list_recently_posted(sctx.store_id, since=since, limit=settings.max_products)
    if not products:
        return {"status": "empty", "message": f"No product has been posted in the last {settings.days} days."}
    body = render_page(products, sctx)
    digest = hashlib.sha256(f"{settings.title}\n{body}".encode()).hexdigest()
    state = await observability.get_state(state_key(sctx.store_id), None) or {}
    call = graphql or _default_graphql
    page_id = await _our_page(sctx, state, call)
    summary = {"url": page_url(sctx), "products": len(products)}
    if page_id and state.get("hash") == digest and state.get("page_id") == page_id:
        return {"status": "unchanged", **summary}
    if page_id:
        data = await call(sctx.store_config, UPDATE, {"id": page_id, "page": {"title": settings.title, "body": body}})
        _checked(data.get("pageUpdate"), "updating the shoppable page")
        status = "updated"
    else:
        page = {"title": settings.title, "handle": settings.page_handle, "body": body, "isPublished": True}
        data = await call(sctx.store_config, CREATE, {"page": page})
        page_id = str(_checked(data.get("pageCreate"), "creating the shoppable page").get("id") or "")
        status = "created"
    await observability.set_state(state_key(sctx.store_id), {
        "page_id": page_id, "handle": settings.page_handle, "hash": digest, "products": len(products),
        "updated_at": now(),
    })
    return {"status": status, **summary}


async def status(sctx: StoreContext) -> dict[str, Any]:
    state = await observability.get_state(state_key(sctx.store_id), None) or {}
    return {
        "enabled": sctx.settings.shoppable.enabled, "url": page_url(sctx), "published": bool(state.get("page_id")),
        "products": int(state.get("products") or 0), "updated_at": state.get("updated_at"),
    }
