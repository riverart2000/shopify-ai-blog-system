"""Read-only Shopify Admin GraphQL client for the product catalogue.

* Every request goes through `ServiceClient("shopify")`, so it lands in the
  `social_api_calls` ledger.
* HTTP 401 → the access token is refreshed once and the request retried.
* GraphQL `THROTTLED` errors → wait for the cost bucket to refill
  (`extensions.cost.throttleStatus`) and retry a few times.
* Shopify rejects any single query whose requested cost exceeds 1000 points:
  pages start at 50 products (≈212 points on 2026-07) and shrink automatically
  on MAX_COST_EXCEEDED or when the reported cost per node is too high.
* The app may lack `read_inventory`: inventory fields are then dropped from the
  query and availability falls back to each variant's `availableForSale`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Callable, Iterable

import httpx

import shopify_client
from config import StoreConfig

from ..errors import ConfigurationError, ExternalServiceError, RateLimited
from ..http import CallContext, ServiceClient
from .normalize import connection_nodes, legacy_id, product_gid

logger = logging.getLogger("ai_blog_server.social_engine.catalog")

API_VERSION = "2026-07"
SERVICE = "shopify"

PAGE_SIZE = 50
MAX_QUERY_COST = 1000             # Shopify's single-query requested-cost limit
IMAGES_PER_PRODUCT = 10
VARIANTS_PER_PRODUCT = 50
COLLECTIONS_PER_PRODUCT = 20
COLLECTIONS_PAGE_SIZE = 50
COLLECTION_PRODUCTS_FIRST = 50    # members fetched with each collection page
COLLECTION_PRODUCTS_PAGE = 250    # follow-up pages for big collections
MAX_COLLECTION_PRODUCTS = 2500
EXISTS_BATCH = 250                # ids per nodes(ids:) existence check
MAX_THROTTLE_RETRIES = 5
MAX_THROTTLE_WAIT_SECONDS = 60.0
INVENTORY_RECHECK_SECONDS = 6 * 3600
HTTP_BACKOFF_BASE = 1.5
INVENTORY_FIELDS = frozenset({"totalInventory", "inventoryQuantity"})

# store_id → monotonic time at which Shopify denied the inventory fields.
_INVENTORY_DENIED: dict[str, float] = {}
_INVENTORY_WARNED: set[str] = set()


async def _sleep(seconds: float) -> None:
    """Indirection so tests can skip real waiting."""
    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ShopifyAccessDenied(ConfigurationError):
    """The app lacks an access scope for a field in the query."""
    code = "shopify_access_denied"

    def __init__(self, message: str, *, fields: list[str], required_access: str, details: dict | None = None) -> None:
        super().__init__(message, details=details)
        self.fields = fields
        self.required_access = required_access

    @property
    def inventory_related(self) -> bool:
        text = f"{self.required_access} {self.message}".lower()
        return bool(set(self.fields) & INVENTORY_FIELDS) or "inventory" in text


class QueryCostExceeded(ExternalServiceError):
    """The query's requested cost is above Shopify's per-query maximum."""
    code = "shopify_query_cost"

    def __init__(self, message: str, *, cost: float | None, max_cost: float | None, details: dict | None = None) -> None:
        super().__init__(SERVICE, message, retryable=False, details=details)
        self.cost = cost
        self.max_cost = max_cost

    def fitting_page_size(self, page_size: int) -> int:
        if not self.cost or not self.max_cost or page_size <= 1:
            return max(1, page_size // 2)
        return max(1, min(page_size - 1, int(page_size * float(self.max_cost) * 0.9 / float(self.cost))))


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

_IMAGE = "image { url altText width height }"


def _product_fragment(include_inventory: bool) -> str:
    total = "totalInventory" if include_inventory else ""
    quantity = "inventoryQuantity" if include_inventory else ""
    return f"""
fragment CatalogProduct on Product {{
  id legacyResourceId handle title descriptionHtml productType vendor tags status
  onlineStoreUrl publishedAt createdAt updatedAt {total}
  featuredMedia {{ ... on MediaImage {{ {_IMAGE} }} }}
  media(first: {IMAGES_PER_PRODUCT}) {{ nodes {{ ... on MediaImage {{ {_IMAGE} }} }} }}
  variants(first: {VARIANTS_PER_PRODUCT}) {{
    nodes {{ id legacyResourceId title price compareAtPrice sku availableForSale {quantity} }}
  }}
  priceRangeV2 {{ minVariantPrice {{ amount currencyCode }} maxVariantPrice {{ amount currencyCode }} }}
  collections(first: {COLLECTIONS_PER_PRODUCT}) {{ nodes {{ id legacyResourceId handle title }} }}
}}"""


def products_query(include_inventory: bool = True) -> str:
    return """query CatalogProducts($first: Int!, $after: String, $query: String, $sortKey: ProductSortKeys) {
  products(first: $first, after: $after, query: $query, sortKey: $sortKey) {
    pageInfo { hasNextPage endCursor }
    nodes { ...CatalogProduct }
  }
}""" + _product_fragment(include_inventory)


def product_query(include_inventory: bool = True) -> str:
    return """query CatalogProduct($id: ID!) {
  product(id: $id) { ...CatalogProduct }
}""" + _product_fragment(include_inventory)


COLLECTIONS_QUERY = """query CatalogCollections($first: Int!, $after: String, $productsFirst: Int!) {
  collections(first: $first, after: $after, sortKey: ID) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id legacyResourceId handle title
      image { url }
      productsCount { count }
      products(first: $productsFirst) { pageInfo { hasNextPage endCursor } nodes { legacyResourceId } }
    }
  }
}"""

COLLECTION_PRODUCTS_QUERY = """query CatalogCollectionProducts($id: ID!, $first: Int!, $after: String) {
  collection(id: $id) {
    products(first: $first, after: $after) { pageInfo { hasNextPage endCursor } nodes { legacyResourceId } }
  }
}"""

PRODUCTS_EXIST_QUERY = """query CatalogProductsExist($ids: [ID!]!) {
  nodes(ids: $ids) { ... on Product { legacyResourceId } }
}"""


def _fit(per_node_cost: float, max_size: int) -> int:
    return max(1, min(max_size, int((MAX_QUERY_COST * 0.95 - 2) // max(1.0, per_node_cost))))


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _code(error: dict) -> str:
    return str((error.get("extensions") or {}).get("code") or "").upper()


def _is_throttled(errors: list[dict]) -> bool:
    return any(_code(e) == "THROTTLED" or "throttled" in str(e.get("message") or "").lower() for e in errors)


def _throttle_wait(cost: dict) -> float:
    status = cost.get("throttleStatus") or {}
    requested = float(cost.get("requestedQueryCost") or 0)
    available = float(status.get("currentlyAvailable") or 0)
    restore_rate = float(status.get("restoreRate") or 0) or 50.0
    wait = (requested - available) / restore_rate if requested > available else 1.0
    return min(MAX_THROTTLE_WAIT_SECONDS, max(0.5, wait + 0.25))


def _graphql_error(operation: str, errors: list[dict]) -> Exception:
    messages = "; ".join(str(e.get("message") or e).strip()[:300] for e in errors[:3])
    codes = {_code(e) for e in errors}
    if "ACCESS_DENIED" in codes:
        denied = [e for e in errors if _code(e) == "ACCESS_DENIED"]
        fields = [str(e["path"][-1]) for e in denied if isinstance(e.get("path"), list) and e["path"]]
        required = ", ".join(sorted({
            str((e.get("extensions") or {}).get("requiredAccess") or "").strip() for e in denied
        } - {""}))
        return ShopifyAccessDenied(
            f"Shopify denied {operation}: {messages}"
            + (f" The app needs: {required}." if required else ""),
            fields=fields, required_access=required, details={"errors": errors[:5]},
        )
    if "MAX_COST_EXCEEDED" in codes:
        ext = next((e.get("extensions") or {} for e in errors if _code(e) == "MAX_COST_EXCEEDED"), {})
        return QueryCostExceeded(
            f"{operation} asked for too much data in one query: {messages}",
            cost=ext.get("cost"), max_cost=ext.get("maxCost"), details={"errors": errors[:5]},
        )
    retryable = bool(codes & {"INTERNAL_SERVER_ERROR", "SERVICE_UNAVAILABLE", "TIMEOUT"})
    return ExternalServiceError(
        SERVICE, f"{operation} returned GraphQL errors: {messages}", retryable=retryable,
        details={"errors": errors[:5]},
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ShopifyCatalogClient:
    """Authenticated, read-only Admin GraphQL access for one store. Use as `async with`."""

    def __init__(self, store_config: StoreConfig, *, call_context: CallContext | None = None) -> None:
        self.store_config = store_config
        self.ctx = call_context or CallContext(store_id=store_config.id)
        self.last_cost: dict[str, Any] = {}
        self._token: str | None = None
        self._client = ServiceClient(
            SERVICE, f"https://{store_config.myshopify_domain}/admin/api/{API_VERSION}",
            timeout=45.0, backoff_base=HTTP_BACKOFF_BASE,
        )

    async def __aenter__(self) -> "ShopifyCatalogClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- inventory scope -----------------------------------------------------

    @property
    def inventory_enabled(self) -> bool:
        """False while Shopify recently denied the inventory fields for this store."""
        denied_at = _INVENTORY_DENIED.get(self.store_config.id)
        return denied_at is None or time.monotonic() - denied_at > INVENTORY_RECHECK_SECONDS

    def _disable_inventory(self, exc: ShopifyAccessDenied) -> None:
        _INVENTORY_DENIED[self.store_config.id] = time.monotonic()
        if self.store_config.id in _INVENTORY_WARNED:
            logger.debug("Inventory fields still denied for store %s", self.store_config.id)
            return
        _INVENTORY_WARNED.add(self.store_config.id)
        logger.warning(
            "Shopify app for store %s is missing the %s scope, so stock counts are unavailable; "
            "availability now uses each variant's availableForSale. Grant read_inventory to get counts.",
            self.store_config.id, exc.required_access or "`read_inventory`",
            extra={"operation": "social_engine.catalog", "store_id": self.store_config.id},
        )

    # -- transport -----------------------------------------------------------

    async def _access_token(self, *, fresh: bool) -> str:
        domain = self.store_config.myshopify_domain
        try:
            if fresh:
                return await shopify_client._fetch_fresh_token(self.store_config)
            return await shopify_client._get_token(self.store_config)
        except shopify_client.ShopifyError as exc:
            raise ExternalServiceError(SERVICE, f"Could not get an access token for {domain}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                SERVICE, f"Could not reach {domain} to get an access token: {type(exc).__name__}: {exc}",
            ) from exc

    async def execute(self, query: str, variables: dict | None = None, *, operation: str) -> dict:
        """Run one GraphQL query and return its `data` (handles 401 refresh and throttling)."""
        refreshed = False
        throttled = 0
        while True:
            if self._token is None:
                self._token = await self._access_token(fresh=False)
            try:
                body = await self._client.json(
                    "POST", "/graphql.json", operation=operation, ctx=self.ctx, idempotent=True,
                    json={"query": query, "variables": variables or {}},
                    headers={"X-Shopify-Access-Token": self._token, "Content-Type": "application/json"},
                )
            except ExternalServiceError as exc:
                if exc.status_code != 401:
                    raise
                if refreshed:
                    raise ExternalServiceError(
                        SERVICE,
                        f"{operation}: Shopify rejected the access token even after refreshing it (HTTP 401). "
                        "Check the app's client ID/secret and that the app is still installed.",
                        status_code=401, retryable=False,
                    ) from exc
                refreshed = True
                logger.info("Shopify returned 401 for %s on store %s; refreshing the token", operation, self.store_config.id)
                self._token = await self._access_token(fresh=True)
                continue

            if not isinstance(body, dict):
                raise ExternalServiceError(
                    SERVICE, f"{operation} returned an unexpected response: {str(body)[:200]}", retryable=False,
                )
            cost = (body.get("extensions") or {}).get("cost") or {}
            errors = body.get("errors")
            if errors:
                errors = errors if isinstance(errors, list) else [{"message": str(errors)}]
                errors = [e if isinstance(e, dict) else {"message": str(e)} for e in errors]
                if _is_throttled(errors):
                    throttled += 1
                    wait = _throttle_wait(cost)
                    if throttled > MAX_THROTTLE_RETRIES:
                        raise RateLimited(
                            f"Shopify {operation} is still throttled after {MAX_THROTTLE_RETRIES} waits; "
                            "the sync will continue later.",
                            retry_at=int(time.time() + max(wait, 30.0)), details={"cost": cost},
                        )
                    logger.info(
                        "Shopify throttled %s for store %s; waiting %.1fs (%d/%d)",
                        operation, self.store_config.id, wait, throttled, MAX_THROTTLE_RETRIES,
                    )
                    await _sleep(wait)
                    continue
                raise _graphql_error(operation, errors)
            self.last_cost = cost
            await self._pace(cost)
            return body.get("data") or {}

    async def _pace(self, cost: dict) -> None:
        """Wait before the next query when the bucket cannot pay for another one like it."""
        status = cost.get("throttleStatus") or {}
        requested = float(cost.get("requestedQueryCost") or 0)
        available = status.get("currentlyAvailable")
        restore_rate = float(status.get("restoreRate") or 0)
        if requested and available is not None and restore_rate and float(available) < requested:
            await _sleep(min(MAX_THROTTLE_WAIT_SECONDS, (requested - float(available)) / restore_rate))

    async def _query(self, build: Callable[[bool], str], variables: dict, *, operation: str) -> dict:
        """Execute, dropping the inventory fields if the app lacks `read_inventory`."""
        while True:
            include_inventory = self.inventory_enabled
            try:
                return await self.execute(build(include_inventory), variables, operation=operation)
            except ShopifyAccessDenied as exc:
                if include_inventory and exc.inventory_related:
                    self._disable_inventory(exc)
                    continue
                raise

    def _calibrated(self, size: int, max_size: int) -> int:
        requested = self.last_cost.get("requestedQueryCost") if self.last_cost else None
        if not requested or size <= 0:
            return size
        return _fit((float(requested) - 2) / size, max_size)

    async def _pages(
        self, *, root: str, build: Callable[[bool], str], variables: dict, operation: str, size: int, max_size: int,
    ) -> AsyncIterator[dict]:
        """Yield each page (connection dict) of `root`, following cursors to the end."""
        after: str | None = None
        while True:
            try:
                data = await self._query(build, {**variables, "first": size, "after": after}, operation=operation)
            except QueryCostExceeded as exc:
                smaller = exc.fitting_page_size(size)
                if smaller >= size:
                    raise
                logger.info("%s cost too high at %d per page; retrying with %d", operation, size, smaller)
                size = smaller
                continue
            connection = data.get(root) or {}
            next_size = self._calibrated(size, max_size)  # before the consumer runs other queries
            yield connection
            size = next_size
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return
            after = page_info.get("endCursor")
            if not after:
                raise ExternalServiceError(
                    SERVICE, f"{operation} reported another page but no cursor; stopped so nothing is skipped.",
                )

    # -- public queries ------------------------------------------------------

    async def iter_product_pages(self, *, query: str = "", sort_key: str = "ID") -> AsyncIterator[list[dict]]:
        """Yield pages of raw product nodes. `query` is Shopify search syntax, e.g. updated_at:>'…'."""
        async for connection in self._pages(
            root="products", build=products_query, operation="products.list",
            variables={"query": query or None, "sortKey": sort_key},
            size=PAGE_SIZE, max_size=PAGE_SIZE,
        ):
            yield connection_nodes(connection)

    async def fetch_products(self, *, query: str = "", sort_key: str = "ID") -> list[dict]:
        products: list[dict] = []
        async for page in self.iter_product_pages(query=query, sort_key=sort_key):
            products.extend(page)
        return products

    async def fetch_product(self, product_id: str | int) -> dict | None:
        """One product by numeric id or gid; None when it no longer exists."""
        data = await self._query(product_query, {"id": product_gid(product_id)}, operation="products.get")
        return data.get("product") or None

    async def existing_product_ids(self, product_ids: Iterable[str | int]) -> set[str]:
        """Which of these products still exist in Shopify (checked by id; unknown ids come back null)."""
        ids = sorted({legacy_id(pid) for pid in product_ids} - {""})
        found: set[str] = set()
        for start in range(0, len(ids), EXISTS_BATCH):
            batch = ids[start:start + EXISTS_BATCH]
            data = await self.execute(
                PRODUCTS_EXIST_QUERY, {"ids": [product_gid(pid) for pid in batch]}, operation="products.exist",
            )
            found.update(
                str(node["legacyResourceId"]) for node in data.get("nodes") or []
                if isinstance(node, dict) and node.get("legacyResourceId")
            )
        return found

    async def fetch_collections(self, *, max_products_per_collection: int = MAX_COLLECTION_PRODUCTS) -> list[dict]:
        """Every collection with its member products (`products.nodes`, capped)."""
        collections: list[dict] = []
        async for connection in self._pages(
            root="collections", build=lambda _inventory: COLLECTIONS_QUERY, operation="collections.list",
            variables={"productsFirst": COLLECTION_PRODUCTS_FIRST},
            size=COLLECTIONS_PAGE_SIZE, max_size=COLLECTIONS_PAGE_SIZE,
        ):
            for node in connection_nodes(connection):
                members, truncated = await self._collection_members(node, max_products_per_collection)
                collections.append({**node, "products": {"nodes": members}, "productsTruncated": truncated})
        return collections

    async def _collection_members(self, node: dict, cap: int) -> tuple[list[dict], bool]:
        products = node.get("products") or {}
        members = connection_nodes(products)
        page_info = products.get("pageInfo") or {}
        while page_info.get("hasNextPage") and page_info.get("endCursor") and node.get("id"):
            if len(members) >= cap:
                logger.info("Collection %s has more than %d products; keeping the first %d", node.get("handle"), cap, cap)
                return members[:cap], True
            data = await self.execute(
                COLLECTION_PRODUCTS_QUERY,
                {"id": node["id"], "first": COLLECTION_PRODUCTS_PAGE, "after": page_info["endCursor"]},
                operation="collections.products",
            )
            products = (data.get("collection") or {}).get("products") or {}
            members.extend(connection_nodes(products))
            page_info = products.get("pageInfo") or {}
        return members[:cap], len(members) > cap
