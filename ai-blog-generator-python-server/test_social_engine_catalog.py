"""Social Engine catalogue: normalisation, change detection, sync, webhooks, eligibility.

Shopify is replaced by an in-memory fake GraphQL server (httpx.MockTransport);
token functions are monkeypatched, so nothing here touches the network.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re

import httpx
import pytest

import shopify_client
from db.social_engine import observability
from db.social_engine import products as product_store
from services.social_engine import http as se_http
from services.social_engine.catalog import (
    changes, eligible_products, format_price, html_to_text, normalize_product, process_shopify_webhook,
    product_images, shopify_api, store_product, sync_catalog, verify_shopify_hmac,
)
from services.social_engine.catalog.normalize import content_hash, normalize_collection
from services.social_engine.errors import ExternalServiceError, RateLimited
from services.social_engine.settings import save_settings

STOREFRONT = "https://bioluxelab.com"


# ---------------------------------------------------------------------------
# Fixtures and fakes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """No real waiting; records every throttle/backoff wait the client asked for."""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(shopify_api, "_sleep", fake_sleep)
    monkeypatch.setattr(shopify_api, "HTTP_BACKOFF_BASE", 0.001)
    shopify_api._INVENTORY_DENIED.clear()
    shopify_api._INVENTORY_WARNED.clear()
    yield recorded
    shopify_api._INVENTORY_DENIED.clear()
    shopify_api._INVENTORY_WARNED.clear()


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    """Fake token cache/refresh so the real OAuth endpoint is never called."""
    state = {"get": 0, "fresh": 0}

    async def get_token(store):
        state["get"] += 1
        return "cached-token"

    async def fresh_token(store):
        state["fresh"] += 1
        return f"fresh-token-{state['fresh']}"

    monkeypatch.setattr(shopify_client, "_get_token", get_token)
    monkeypatch.setattr(shopify_client, "_fetch_fresh_token", fresh_token)
    return state


def make_node(
    pid: int,
    *,
    price: str = "20.00",
    compare: str | None = None,
    status: str = "ACTIVE",
    published: bool = True,
    available: bool = True,
    inventory: int | None = 5,
    images: tuple[str, ...] = ("main.jpg", "side.jpg"),
    updated: str = "2026-10-01T10:00:00Z",
    tags: tuple[str, ...] = ("calm",),
    collections: tuple[tuple[str, str, str], ...] = (("501", "calm", "Calm"),),
    variants: list[dict] | None = None,
    title: str | None = None,
) -> dict:
    """A Shopify Admin GraphQL product node shaped like the 2026-07 API returns it."""
    image_nodes = [
        {"image": {"url": f"https://cdn.shopify.com/files/{pid}-{name}?v=1", "altText": name, "width": 800, "height": 800}}
        for name in images
    ]
    variant_nodes = variants or [{
        "id": f"gid://shopify/ProductVariant/{pid}1", "legacyResourceId": f"{pid}1", "title": "Default Title",
        "price": price, "compareAtPrice": compare, "sku": f"SKU-{pid}", "availableForSale": available,
        "inventoryQuantity": inventory,
    }]
    prices = [v["price"] for v in variant_nodes]
    node = {
        "id": f"gid://shopify/Product/{pid}", "legacyResourceId": str(pid), "handle": f"product-{pid}",
        "title": title or f"Product {pid}", "descriptionHtml": "<p>Calming <b>blend</b> &amp; more</p>",
        "productType": "Tea", "vendor": "Bioluxe", "tags": list(tags), "status": status,
        "onlineStoreUrl": f"{STOREFRONT}/products/product-{pid}" if published else None,
        "publishedAt": "2026-01-01T00:00:00Z" if published else None,
        "createdAt": f"2026-01-{pid % 28 + 1:02d}T00:00:00Z", "updatedAt": updated,
        "totalInventory": inventory,
        "featuredMedia": dict(image_nodes[0]) if image_nodes else None,
        "media": {"nodes": image_nodes},
        "variants": {"nodes": variant_nodes},
        "priceRangeV2": {
            "minVariantPrice": {"amount": min(prices, key=float), "currencyCode": "GBP"},
            "maxVariantPrice": {"amount": max(prices, key=float), "currencyCode": "GBP"},
        },
        "collections": {"nodes": [
            {"id": f"gid://shopify/Collection/{cid}", "legacyResourceId": cid, "handle": handle, "title": name}
            for cid, handle, name in collections
        ]},
    }
    if inventory is None:
        node.pop("totalInventory")
        for variant in variant_nodes:
            variant.pop("inventoryQuantity", None)
    return node


OK_COST = {"requestedQueryCost": 12, "actualQueryCost": 6,
           "throttleStatus": {"maximumAvailable": 2000.0, "currentlyAvailable": 1990, "restoreRate": 100.0}}


class FakeShopify:
    """Minimal Admin GraphQL server: products (filter, sort, cursor pages), product by id, collections."""

    def __init__(self, products: list[dict], *, page_size: int = 2, collections: list[dict] | None = None) -> None:
        self.products = {int(node["legacyResourceId"]): node for node in products}
        self.collections = collections or []
        self.page_size = page_size
        self.member_page_size = 2
        self.requests: list[dict] = []
        self.reject_tokens: set[str] = set()
        self.throttle_times = 0
        self.deny_inventory = False
        self.fail_products_page: int | None = None   # 1-based products page that returns HTTP 503
        self.max_cost: int | None = None             # emulate the per-query cost limit (40 points/product)
        self.hidden: set[int] = set()                # exist by id but missing from the listing
        self._products_pages = 0

    def install(self) -> "FakeShopify":
        se_http.TRANSPORT_OVERRIDES["shopify"] = httpx.MockTransport(self.handler)
        return self

    def set(self, node: dict) -> None:
        self.products[int(node["legacyResourceId"])] = node

    def remove(self, pid: int) -> None:
        self.products.pop(pid, None)

    def product_requests(self) -> list[dict]:
        return [r for r in self.requests if "query CatalogProducts(" in r["query"]]

    @staticmethod
    def _ok(data: dict, cost: dict | None = None) -> httpx.Response:
        return httpx.Response(200, json={"data": data, "extensions": {"cost": cost or OK_COST}})

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/admin/api/2026-07/graphql.json"
        body = json.loads(request.content)
        query, variables = body["query"], body.get("variables") or {}
        token = request.headers.get("X-Shopify-Access-Token")
        self.requests.append({"query": query, "variables": variables, "token": token})
        if token in self.reject_tokens:
            return httpx.Response(401, json={"errors": "[API] Invalid API key or access token"})
        if self.throttle_times > 0:
            self.throttle_times -= 1
            return httpx.Response(200, json={
                "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                "extensions": {"cost": {"requestedQueryCost": 300, "actualQueryCost": None, "throttleStatus": {
                    "maximumAvailable": 2000.0, "currentlyAvailable": 100, "restoreRate": 100.0}}},
            })
        if self.deny_inventory and "totalInventory" in query:
            return httpx.Response(200, json={"data": None, "errors": [{
                "message": "Access denied for totalInventory field. Required access: `read_inventory` access scope.",
                "path": ["products", "nodes", 0, "totalInventory"],
                "extensions": {"code": "ACCESS_DENIED", "requiredAccess": "`read_inventory` access scope."},
            }]})
        if "query CatalogProducts(" in query:
            return self._products(variables, query)
        if "query CatalogProduct(" in query:
            pid = int(variables["id"].rsplit("/", 1)[-1])
            node = self.products.get(pid)
            return self._ok({"product": self._shape(node, query) if node else None})
        if "query CatalogProductsExist" in query:
            ids = [int(gid.rsplit("/", 1)[-1]) for gid in variables["ids"]]
            return self._ok({"nodes": [{"legacyResourceId": str(pid)} if pid in self.products else None for pid in ids]})
        if "query CatalogCollections" in query:
            return self._collections(variables)
        if "query CatalogCollectionProducts" in query:
            collection = next(c for c in self.collections if c["id"] == variables["id"])
            return self._ok({"collection": {"products": self._members(collection, variables["first"], variables.get("after"))}})
        raise AssertionError(f"unexpected query: {query[:80]}")

    @staticmethod
    def _shape(node: dict, query: str) -> dict:
        """Return only the inventory fields the query asked for, like GraphQL does."""
        if "totalInventory" in query:
            return node
        shaped = {k: v for k, v in node.items() if k != "totalInventory"}
        shaped["variants"] = {"nodes": [
            {k: v for k, v in variant.items() if k != "inventoryQuantity"} for variant in node["variants"]["nodes"]
        ]}
        return shaped

    def _products(self, variables: dict, query: str) -> httpx.Response:
        first = int(variables["first"])
        if self.max_cost and first * 40 > self.max_cost:
            return httpx.Response(200, json={"errors": [{
                "message": f"Query cost is {first * 40}, which exceeds the single query max cost limit ({self.max_cost}).",
                "extensions": {"code": "MAX_COST_EXCEEDED", "cost": first * 40, "maxCost": self.max_cost},
            }]})
        self._products_pages += 1
        if self.fail_products_page and self._products_pages >= self.fail_products_page:
            return httpx.Response(503, text="Service Unavailable")
        nodes = [n for pid, n in self.products.items() if pid not in self.hidden]
        match = re.search(r"updated_at:>'([^']+)'", variables.get("query") or "")
        if match:
            nodes = [n for n in nodes if n["updatedAt"] > match.group(1)]
        if variables.get("sortKey") == "UPDATED_AT":
            nodes.sort(key=lambda n: (n["updatedAt"], int(n["legacyResourceId"])))
        else:
            nodes.sort(key=lambda n: int(n["legacyResourceId"]))
        start = int(variables.get("after") or 0)
        end = start + min(first, self.page_size)
        page = [self._shape(node, query) for node in nodes[start:end]]
        per_product = 40 if self.max_cost else 4
        return self._ok({"products": {
            "pageInfo": {"hasNextPage": end < len(nodes), "endCursor": str(end) if page else None},
            "nodes": page,
        }}, {**OK_COST, "requestedQueryCost": first * per_product + 2})

    def _members(self, collection: dict, first: int, after: str | None) -> dict:
        start = int(after or 0)
        end = start + min(int(first), self.member_page_size)
        members = collection["members"]
        return {
            "pageInfo": {"hasNextPage": end < len(members), "endCursor": str(end)},
            "nodes": [{"legacyResourceId": str(pid)} for pid in members[start:end]],
        }

    def _collections(self, variables: dict) -> httpx.Response:
        nodes = [
            {
                "id": c["id"], "legacyResourceId": c["id"].rsplit("/", 1)[-1], "handle": c["handle"], "title": c["title"],
                "image": {"url": f"https://cdn.shopify.com/{c['handle']}.jpg"}, "productsCount": {"count": len(c["members"])},
                "products": self._members(c, variables["productsFirst"], None),
            }
            for c in self.collections
        ]
        return self._ok({"collections": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}})


def collection(cid: str, handle: str, members: list[int]) -> dict:
    return {"id": f"gid://shopify/Collection/{cid}", "handle": handle, "title": handle.title(), "members": members}


async def _event_types(store_id: str, **kwargs) -> list[str]:
    return [row["event_type"] for row in await product_store.list_events(store_id, **kwargs)]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def test_normalize_sale_discount_and_prices():
    node = make_node(7, variants=[
        {"id": "gid://shopify/ProductVariant/71", "title": "Small", "price": "16.00", "compareAtPrice": "20.00",
         "sku": "S", "availableForSale": True, "inventoryQuantity": 3},
        {"id": "gid://shopify/ProductVariant/72", "title": "Large", "price": "19.99", "compareAtPrice": "29.99",
         "sku": "L", "availableForSale": False, "inventoryQuantity": 0},
        {"id": "gid://shopify/ProductVariant/73", "title": "Refill", "price": "30.00", "compareAtPrice": "0.00",
         "sku": "R", "availableForSale": True, "inventoryQuantity": 9},
    ])
    product = normalize_product(node, storefront_url=STOREFRONT + "/")
    assert product["product_id"] == "7" and product["gid"] == "gid://shopify/Product/7"
    assert product["url"] == "https://bioluxelab.com/products/product-7"
    assert (product["price_min"], product["price_max"], product["currency"]) == (16.0, 30.0, "GBP")
    assert product["compare_at_max"] == 29.99          # the 0.00 compare-at price is ignored
    assert product["on_sale"] is True
    assert product["discount_pct"] == 33.3              # (29.99 - 19.99) / 29.99
    assert [v["id"] for v in product["variants"]] == ["71", "72", "73"]
    assert product["variants"][2]["compare_at_price"] is None
    assert product["description"] == "Calming blend & more"
    assert product["collections"] == [{"id": "501", "handle": "calm", "title": "Calm"}]
    assert product["tags"] == ["calm"] and product["total_inventory"] == 5

    full_price = normalize_product(make_node(8, price="20.00", compare="20.00"), storefront_url=STOREFRONT)
    assert full_price["on_sale"] is False and full_price["compare_at_max"] is None and full_price["discount_pct"] == 0


def test_normalize_availability_rules():
    def norm(**kwargs):
        return normalize_product(make_node(1, **kwargs), storefront_url=STOREFRONT)

    assert norm()["available"] is True
    assert norm(status="DRAFT")["available"] is False
    unpublished = norm(published=False)
    assert unpublished["available"] is False and unpublished["url"] == "" and unpublished["published_at"] == ""
    assert norm(available=False, inventory=0)["available"] is False
    # Without read_inventory the counts are missing; availableForSale still decides.
    no_scope = norm(inventory=None)
    assert no_scope["total_inventory"] is None and no_scope["available"] is True
    assert no_scope["variants"][0]["inventory_quantity"] is None
    # Unknown publication state (fields not fetched) is treated as published.
    node = make_node(2)
    node.pop("publishedAt")
    node.pop("onlineStoreUrl")
    assert normalize_product(node, storefront_url=STOREFRONT)["available"] is True


def test_images_dedupe_featured_first_and_rotation():
    node = make_node(3, images=("a.jpg", "b.jpg", "c.jpg"))
    node["featuredMedia"] = {"image": {"url": "https://cdn.shopify.com/files/3-b.jpg?v=99", "altText": "", "width": 1, "height": 1}}
    node["media"]["nodes"].append({})                 # a video: no MediaImage fields
    product = normalize_product(node, storefront_url=STOREFRONT)
    urls = [image["url"] for image in product["images"]]
    assert urls == [
        "https://cdn.shopify.com/files/3-b.jpg?v=99",
        "https://cdn.shopify.com/files/3-a.jpg?v=1",
        "https://cdn.shopify.com/files/3-c.jpg?v=1",
    ]
    assert product["featured_image_url"] == urls[0]
    assert product_images(product) == [urls[0]]
    assert product_images(product, offset=2, max_images=2) == [urls[2], urls[0]]
    assert product_images(product, offset=4, max_images=1) == [urls[1]]
    assert product_images(product, max_images=10) == urls
    assert product_images(product, max_images=0) == []
    assert product_images({"images": [], "featured_image_url": "https://x/y.jpg"}) == ["https://x/y.jpg"]
    assert product_images({"images": []}) == []


def test_format_price_and_taxes():
    assert format_price(24.99) == "£24.99"
    assert format_price("24.9") == "£24.90"
    assert format_price(20, include_taxes_pct=20) == "£24.00"
    assert format_price(10, "€", include_taxes_pct=17.5) == "€11.75"
    assert format_price(1234.5, "$") == "$1,234.50"
    assert format_price("9.995") == "£10.00"
    assert format_price(None) == "" and format_price("n/a") == ""


def test_html_to_text_and_content_hash():
    html = "<h2>Why</h2><ul><li>Calm</li><li>Focus&nbsp;&amp; sleep</li></ul><script>x()</script><p>End</p>"
    assert html_to_text(html) == "Why\n- Calm\n- Focus & sleep\nEnd"
    long_text = html_to_text("<p>" + "word " * 1000 + "</p>", max_chars=100)
    assert len(long_text) <= 100 and long_text.endswith("…")

    product = normalize_product(make_node(4), storefront_url=STOREFRONT)
    assert product["content_hash"] == content_hash(dict(product))
    stock_only = normalize_product(make_node(4, inventory=99), storefront_url=STOREFRONT)
    assert stock_only["content_hash"] == product["content_hash"]  # stock counts are not content
    cheaper = normalize_product(make_node(4, price="18.00"), storefront_url=STOREFRONT)
    assert cheaper["content_hash"] != product["content_hash"]


def test_normalize_collection_counts_and_members():
    node = {"id": "gid://shopify/Collection/9", "legacyResourceId": "9", "handle": "h", "title": "T",
            "image": None, "productsCount": {"count": 3},
            "products": {"nodes": [{"legacyResourceId": "1"}, {"legacyResourceId": "2"}, {"legacyResourceId": "1"}]}}
    assert normalize_collection(node) == {
        "collection_id": "9", "gid": "gid://shopify/Collection/9", "handle": "h", "title": "T",
        "image_url": "", "products_count": 3, "product_ids": ["1", "2"],
    }


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def _p(**kwargs) -> dict:
    return normalize_product(make_node(10, **kwargs), storefront_url=STOREFRONT)


def test_diff_new_product_and_initial_import():
    new = _p()
    assert changes.diff(None, new, initial_import=True) == []
    [(event_type, details)] = changes.diff(None, new, initial_import=False)
    assert event_type == "product_created" and details["title"] == "Product 10" and details["available"] is True
    assert changes.diff(new, _p(), initial_import=False) == []


def test_diff_price_and_sale_events():
    base = _p(price="20.00")
    drop = changes.diff(base, _p(price="15.00", compare="20.00"), initial_import=False)
    assert [e[0] for e in drop] == ["price_drop", "sale_started"]
    assert drop[0][1].items() >= {"old_price": 20.0, "new_price": 15.0, "pct": 25.0, "currency": "GBP"}.items()
    assert drop[1][1]["new_discount_pct"] == 25.0 and drop[1][1]["new_compare_at_max"] == 20.0

    assert changes.diff(base, _p(price="19.85"), initial_import=False) == []        # < 1 %
    rise = changes.diff(base, _p(price="22.00"), initial_import=False)
    assert rise == [("price_increase", rise[0][1])] and rise[0][1]["pct"] == 10.0

    on_sale = _p(price="15.00", compare="20.00")
    ended = changes.diff(on_sale, _p(price="15.00"), initial_import=False)
    assert [e[0] for e in ended] == ["sale_ended"] and ended[0][1]["old_discount_pct"] == 25.0


def test_diff_stock_publish_remove_and_images():
    live = _p()
    out = changes.diff(live, _p(available=False, inventory=0), initial_import=False)
    assert out == [("out_of_stock", out[0][1])]
    assert out[0][1]["old_inventory"] == 5 and out[0][1]["new_inventory"] == 0
    back = changes.diff(_p(available=False, inventory=0), live, initial_import=False)
    assert [e[0] for e in back] == ["back_in_stock"]

    published = changes.diff(_p(status="DRAFT"), live, initial_import=False)
    assert [e[0] for e in published] == ["product_published"]
    assert published[0][1]["old_status"] == "DRAFT" and published[0][1]["new_status"] == "ACTIVE"
    assert [e[0] for e in changes.diff(_p(published=False), live, initial_import=False)] == ["product_published"]

    archived = changes.diff(live, _p(status="ARCHIVED"), initial_import=False)
    assert archived[0][0] == "product_removed" and archived[0][1]["reason"] == "archived"
    restored = changes.diff({**live, "deleted_at": 123}, live, initial_import=False)
    assert [e[0] for e in restored] == ["product_published"]

    images = changes.diff(live, _p(images=("main.jpg", "new.jpg")), initial_import=False)
    assert [e[0] for e in images] == ["images_changed"]
    assert images[0][1]["added"] == ["https://cdn.shopify.com/files/10-new.jpg?v=1"]
    assert images[0][1]["removed"] == ["https://cdn.shopify.com/files/10-side.jpg?v=1"]
    assert changes.removed_event(live, "deleted") == ("product_removed", {
        "title": "Product 10", "handle": "product-10", "reason": "deleted", "old_status": "ACTIVE", "new_status": "DELETED",
    })


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def _catalog() -> list[dict]:
    return [
        make_node(101, updated="2026-10-01T10:00:00Z"),
        make_node(102, updated="2026-10-03T12:00:00Z", collections=(("501", "calm", "Calm"), ("502", "sale", "Sale"))),
        make_node(103, updated="2026-10-02T09:30:00Z", status="DRAFT"),
    ]


@pytest.mark.asyncio
async def test_first_full_sync_paginates_imports_quietly_and_saves_cursor(se_db, tokens):
    fake = FakeShopify(_catalog(), page_size=2, collections=[
        collection("501", "calm", [101, 102, 103]), collection("502", "sale", [102]),
    ]).install()
    summary = await sync_catalog(se_db, correlation_id="sync-1")

    assert summary["mode"] == "full" and summary["initial_import"] is True
    assert (summary["fetched"], summary["created"], summary["deleted"], summary["collections"]) == (3, 3, 0, 2)
    assert summary["events"] == {}                                   # initial import announces nothing
    assert len(fake.product_requests()) == 2                         # two pages
    assert fake.product_requests()[1]["variables"]["after"] == "2"
    assert fake.product_requests()[0]["variables"]["query"] is None
    assert {r["token"] for r in fake.requests} == {"cached-token"} and tokens["fresh"] == 0

    assert await observability.get_state(f"catalog:cursor:{se_db}") == "2026-10-03T11:58:00Z"
    stored = await product_store.get_product(se_db, "102")
    assert stored["url"] == "https://bioluxelab.com/products/product-102" and stored["available"] is True
    assert [c["id"] for c in stored["collections"]] == ["501", "502"]
    assert (await product_store.get_product(se_db, "103"))["status"] == "DRAFT"
    calm = await product_store.get_collection(se_db, "501")
    assert calm["product_ids"] == ["101", "102", "103"] and calm["products_count"] == 3  # 2 member pages
    assert await _event_types(se_db) == []

    [row] = await observability.list_activity(entity_type="catalog", entity_id=se_db)
    assert row["level"] == "info" and "Catalog full sync: 3 fetched, 3 new" in row["message"]
    ledger = await observability.list_api_calls(correlation_id="sync-1")
    assert ledger and {r["service"] for r in ledger} == {"shopify"}
    assert {r["operation"] for r in ledger} == {"products.list", "collections.list", "collections.products"}


@pytest.mark.asyncio
async def test_incremental_sync_uses_updated_at_filter_and_records_events(se_db):
    fake = FakeShopify(_catalog(), page_size=2).install()
    await sync_catalog(se_db)
    fake.requests.clear()

    fake.set(make_node(101, price="15.00", compare="20.00", updated="2026-10-05T08:00:00Z"))
    fake.set(make_node(104, updated="2026-10-05T09:00:00Z"))
    fake.set(make_node(103, updated="2026-10-05T07:00:00Z"))          # DRAFT → ACTIVE
    summary = await sync_catalog(se_db)

    request = fake.product_requests()[0]["variables"]
    assert request["query"] == "updated_at:>'2026-10-03T11:58:00Z'" and request["sortKey"] == "UPDATED_AT"
    assert summary["mode"] == "incremental" and summary["initial_import"] is False
    assert (summary["fetched"], summary["created"], summary["updated"], summary["unchanged"]) == (4, 1, 2, 1)
    assert summary["events"] == {"price_drop": 1, "sale_started": 1, "product_created": 1, "product_published": 1}
    assert not any("CatalogCollections" in r["query"] for r in fake.requests)
    assert await observability.get_state(f"catalog:cursor:{se_db}") == "2026-10-05T08:58:00Z"

    events = await product_store.list_events(se_db, product_id="101")
    assert [e["event_type"] for e in events] == ["price_drop", "sale_started"]
    assert events[0]["details"]["old_price"] == 20.0 and events[0]["details"]["new_price"] == 15.0
    assert events[0]["source"] == "sync"

    again = await sync_catalog(se_db)              # only the overlap is re-read: no repeats, cursor kept
    assert (again["fetched"], again["unchanged"], again["events"]) == (1, 1, {})
    assert await observability.get_state(f"catalog:cursor:{se_db}") == "2026-10-05T08:58:00Z"


@pytest.mark.asyncio
async def test_overlapping_syncs_record_each_change_once(se_db):
    fake = FakeShopify(_catalog(), page_size=1).install()
    await sync_catalog(se_db)
    fake.set(make_node(101, price="15.00", updated="2026-10-05T08:00:00Z"))
    first, second = await asyncio.gather(sync_catalog(se_db), sync_catalog(se_db, full=True))
    assert first["fetched"] and second["fetched"]
    assert await _event_types(se_db) == ["price_drop"]
    assert await product_store.list_product_ids(se_db) == {"101", "102", "103"}


@pytest.mark.asyncio
async def test_full_sync_marks_products_missing_from_shopify_as_deleted(se_db):
    fake = FakeShopify(_catalog()).install()
    await sync_catalog(se_db)
    fake.remove(101)
    summary = await sync_catalog(se_db, full=True)
    assert summary["deleted"] == 1 and summary["events"] == {"product_removed": 1}
    assert (await product_store.get_product(se_db, "101"))["deleted_at"]
    assert await product_store.list_product_ids(se_db) == {"102", "103"}
    [event] = await product_store.list_events(se_db, event_types=["product_removed"])
    assert event["product_id"] == "101" and event["details"]["reason"] == "deleted"
    assert (await sync_catalog(se_db, full=True))["deleted"] == 0   # not removed twice


@pytest.mark.asyncio
async def test_partial_failure_during_full_sync_never_deletes(se_db):
    fake = FakeShopify(_catalog(), page_size=2).install()
    await sync_catalog(se_db)
    cursor = await observability.get_state(f"catalog:cursor:{se_db}")

    fake.fail_products_page = 2                                       # page 2 keeps returning 503
    fake._products_pages = 0
    with pytest.raises(ExternalServiceError) as info:
        await sync_catalog(se_db, full=True)
    assert "HTTP 503" in info.value.message
    assert await product_store.list_product_ids(se_db) == {"101", "102", "103"}
    assert await _event_types(se_db) == []
    assert await observability.get_state(f"catalog:cursor:{se_db}") == cursor
    latest = (await observability.list_activity(entity_type="catalog", entity_id=se_db))[0]
    assert latest["level"] == "error" and "stopped at the products step after 2 products" in latest["message"]


@pytest.mark.asyncio
async def test_products_missing_from_listing_are_confirmed_by_id_before_deleting(se_db):
    fake = FakeShopify(_catalog()).install()
    await sync_catalog(se_db)
    fake.hidden = {101, 102, 103}                                    # listing comes back empty...
    fake.remove(103)                                                 # ...but only 103 is really gone
    fake.requests.clear()
    summary = await sync_catalog(se_db, full=True)
    assert summary["deleted"] == 1 and await product_store.list_product_ids(se_db) == {"101", "102"}
    [check] = [r for r in fake.requests if "CatalogProductsExist" in r["query"]]
    assert sorted(check["variables"]["ids"]) == [f"gid://shopify/Product/{pid}" for pid in (101, 102, 103)]


# ---------------------------------------------------------------------------
# GraphQL client resilience
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_401_refreshes_token_once_then_succeeds(se_db, tokens):
    fake = FakeShopify(_catalog()).install()
    fake.reject_tokens = {"cached-token"}
    summary = await sync_catalog(se_db, correlation_id="auth")
    assert summary["fetched"] == 3 and tokens["fresh"] == 1
    assert [r["token"] for r in fake.requests][:2] == ["cached-token", "fresh-token-1"]
    assert {r["token"] for r in fake.requests[1:]} == {"fresh-token-1"}
    ledger = await observability.list_api_calls(correlation_id="auth", only_errors=True)
    assert [row["status_code"] for row in ledger] == [401]


@pytest.mark.asyncio
async def test_401_after_refresh_is_a_clear_permanent_error(se_db, tokens):
    fake = FakeShopify(_catalog()).install()
    fake.reject_tokens = {"cached-token", "fresh-token-1"}
    with pytest.raises(ExternalServiceError) as info:
        await sync_catalog(se_db)
    assert info.value.retryable is False and "even after refreshing" in info.value.message
    assert tokens["fresh"] == 1


@pytest.mark.asyncio
async def test_throttled_queries_wait_for_the_bucket_then_retry(se_db, sleeps):
    fake = FakeShopify(_catalog(), page_size=5).install()
    fake.throttle_times = 2
    summary = await sync_catalog(se_db)
    assert summary["fetched"] == 3
    assert sleeps[:2] == [pytest.approx(2.25), pytest.approx(2.25)]  # (300 - 100) / 100 + 0.25

    fake.throttle_times = 100
    with pytest.raises(RateLimited) as info:
        await sync_catalog(se_db, full=True)
    assert "still throttled" in info.value.message


@pytest.mark.asyncio
async def test_missing_inventory_scope_falls_back_to_available_for_sale(se_db, caplog):
    fake = FakeShopify([make_node(201), make_node(202, available=False, inventory=0)], page_size=5).install()
    fake.deny_inventory = True
    caplog.set_level(logging.WARNING, logger="ai_blog_server.social_engine.catalog")
    summary = await sync_catalog(se_db)

    assert summary["fetched"] == 2
    queries = [r["query"] for r in fake.product_requests()]
    assert "totalInventory" in queries[0] and all("nventory" not in q for q in queries[1:])
    assert len(queries) == 2
    in_stock, sold_out = await product_store.get_products(se_db, ["201", "202"])
    assert in_stock["total_inventory"] is None and in_stock["available"] is True
    assert sold_out["available"] is False
    warnings = [r for r in caplog.records if "read_inventory" in r.getMessage()]
    assert len(warnings) == 1

    fake.requests.clear()
    await sync_catalog(se_db, full=True)                              # remembered: no second denial
    assert all("totalInventory" not in r["query"] for r in fake.requests)
    assert len([r for r in caplog.records if "read_inventory" in r.getMessage()]) == 1


@pytest.mark.asyncio
async def test_query_cost_limit_shrinks_the_page(se_db):
    fake = FakeShopify([make_node(300 + i) for i in range(30)], page_size=50).install()
    fake.max_cost = 1000                                             # 40 points/product → at most 25 per page
    summary = await sync_catalog(se_db)
    firsts = [r["variables"]["first"] for r in fake.product_requests()]
    assert firsts == [50, 22, 23] and summary["fetched"] == 30          # shrink, then calibrate from cost


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def test_verify_shopify_hmac():
    body = b'{"id":101,"title":"Tea"}'
    signature = base64.b64encode(hmac.new(b"shh", body, hashlib.sha256).digest()).decode()
    assert verify_shopify_hmac(body, signature, "shh") is True
    assert verify_shopify_hmac(body, f" {signature}\n", "shh") is True
    assert verify_shopify_hmac(body + b" ", signature, "shh") is False
    assert verify_shopify_hmac(body, signature, "other") is False
    assert verify_shopify_hmac(body, "", "shh") is False and verify_shopify_hmac(body, signature, "") is False
    assert verify_shopify_hmac(body, "not base64 ✓", "shh") is False


@pytest.mark.asyncio
async def test_webhook_update_refetches_product_and_records_events(se_db):
    fake = FakeShopify(_catalog()).install()
    await sync_catalog(se_db)
    fake.set(make_node(101, price="15.00", compare="20.00", updated="2026-10-06T08:00:00Z"))

    # The payload is partial and stale; the refetched product is what gets stored.
    result = await process_shopify_webhook(se_db, "products/update", {"id": 101, "title": "Old title"}, correlation_id="wh-1")
    assert result == {"action": "updated", "topic": "products/update", "product_id": "101",
                      "events": ["price_drop", "sale_started"]}
    stored = await product_store.get_product(se_db, "101")
    assert stored["title"] == "Product 101" and stored["price_min"] == 15.0 and stored["on_sale"] is True
    events = await product_store.list_events(se_db, product_id="101")
    assert {e["source"] for e in events} == {"webhook"}

    repeat = await process_shopify_webhook(se_db, "PRODUCTS_UPDATE", {"admin_graphql_api_id": "gid://shopify/Product/101"})
    assert repeat["action"] == "unchanged" and repeat["events"] == []
    [row] = await observability.list_activity(entity_type="product", entity_id="101", correlation_id="wh-1")
    assert "price_drop" in row["message"]


@pytest.mark.asyncio
async def test_webhook_create_announces_new_product(se_db):
    fake = FakeShopify(_catalog()).install()
    await sync_catalog(se_db)
    fake.set(make_node(150, updated="2026-10-06T08:00:00Z"))
    result = await process_shopify_webhook(se_db, "products/create", {"id": 150})
    assert result["action"] == "created" and result["events"] == ["product_created"]
    # A sync that later sees the same product does not announce it again.
    summary = await sync_catalog(se_db)
    assert summary["events"] == {}


@pytest.mark.asyncio
async def test_webhook_delete_and_vanished_products(se_db):
    fake = FakeShopify(_catalog()).install()
    await sync_catalog(se_db)
    fake.remove(101)
    result = await process_shopify_webhook(se_db, "products/delete", {"id": 101})
    assert result == {"action": "deleted", "topic": "products/delete", "product_id": "101", "events": ["product_removed"]}
    assert (await process_shopify_webhook(se_db, "products/delete", {"id": 101}))["events"] == []
    assert await _event_types(se_db, product_id="101") == ["product_removed"]

    # An update webhook for a product Shopify no longer has is treated as a delete.
    fake.remove(102)
    late = await process_shopify_webhook(se_db, "products/update", {"id": 102})
    assert late["action"] == "deleted" and late["events"] == ["product_removed"]


@pytest.mark.asyncio
async def test_webhook_sync_topics_and_unknown_topics(se_db):
    assert (await process_shopify_webhook(se_db, "inventory_levels/update", {"inventory_item_id": 1}))["action"] == "incremental_sync"
    assert (await process_shopify_webhook(se_db, "COLLECTIONS_UPDATE", {"id": 5}))["action"] == "full_sync"
    assert (await process_shopify_webhook(se_db, "collections/delete", {"id": 5}))["action"] == "full_sync"
    assert (await process_shopify_webhook(se_db, "app/uninstalled", {}))["action"] == "ignored"


@pytest.mark.asyncio
async def test_stale_data_never_overwrites_newer_rows(se_db):
    newer = normalize_product(make_node(400, price="15.00", updated="2026-10-06T10:00:00Z"), storefront_url=STOREFRONT)
    older = normalize_product(make_node(400, price="20.00", updated="2026-10-06T09:00:00Z"), storefront_url=STOREFRONT)
    await store_product(se_db, newer, initial_import=False)
    result = await store_product(se_db, older, initial_import=False)
    assert result["outcome"] == "stale" and result["events"] == []
    assert (await product_store.get_product(se_db, "400"))["price_min"] == 15.0
    assert await _event_types(se_db) == ["product_created"]


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eligible_products_applies_outfy_rules(se_db):
    nodes = [
        make_node(1, tags=("Calm",)),
        make_node(2, available=False, inventory=0),
        make_node(3, status="DRAFT"),
        make_node(4, published=False),
        make_node(5, tags=("No-Social",)),
        make_node(6),
        make_node(7, price="15.00", compare="20.00", collections=(("502", "sale", "Sale"),), tags=("promo",)),
        make_node(8),
        make_node(9, collections=(), tags=("bundle",)),
    ]
    for node in nodes:
        await product_store.upsert_product(se_db, normalize_product(node, storefront_url=STOREFRONT))
    await product_store.mark_deleted(se_db, ["8"])
    await product_store.upsert_collection(se_db, {"collection_id": "502", "product_ids": ["7", "9"]})
    await save_settings(se_db, {"content": {"excluded_product_ids": ["gid://shopify/Product/6"]}})

    def ids(rows):
        return sorted(int(row["product_id"]) for row in rows)

    assert ids(await eligible_products(se_db)) == [1, 7, 9]
    assert ids(await eligible_products(se_db, in_stock_only=False)) == [1, 2, 7, 9]
    assert ids(await eligible_products(se_db, on_sale_only=True)) == [7]
    assert ids(await eligible_products(se_db, collection_ids=["502"])) == [7, 9]   # 9 via the collections sync
    assert ids(await eligible_products(se_db, collection_ids=["gid://shopify/Collection/501"])) == [1]
    assert ids(await eligible_products(se_db, include_tags=["CALM", "promo"])) == [1, 7]
    assert ids(await eligible_products(se_db, exclude_tags=["calm"])) == [7, 9]
    assert ids(await eligible_products(se_db, exclude_product_ids=["7", 9])) == [1]
    assert ids(await eligible_products(se_db, collection_ids=[])) == [1, 7, 9]
