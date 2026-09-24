"""
shopify_client.py — Shopify Admin REST API interactions.
Handles: token fetch/cache, fetching blogs, uploading images, publishing articles.
"""
from __future__ import annotations

import base64
import asyncio
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx

import db
from config import StoreConfig
from utils import clean_title, log_debug_payload

logger = logging.getLogger("ai_blog_server")

SHOPIFY_API_VERSION = "2025-01"
SHARED_GUIDE_NAMESPACE = "custom"
SHARED_GUIDE_TITLE_KEY = "ai_blog_related_guide_title"
SHARED_GUIDE_URL_KEY = "ai_blog_related_guide_url"
SHARED_GUIDE_EXCERPT_KEY = "ai_blog_related_guide_excerpt"
REVIEW_RATING_KEY = "ai_reviews_rating"
REVIEW_COUNT_KEY = "ai_reviews_count"


class ShopifyError(Exception):
    pass


@dataclass
class ShopifyBlog:
    id: int
    handle: str
    title: str


@dataclass
class ShopifyProduct:
    id: int
    title: str
    handle: str
    url: str          # canonical storefront URL


@dataclass
class ShopifyArticle:
    id: int
    blog_id: int
    blog_handle: str
    title: str
    handle: str
    body_html: str
    summary_html: str
    tags: str
    article_url: str
    image_url: str
    published_at: str


@dataclass
class PublishResult:
    article_id: int
    article_url: str
    blog_handle: str
    title: str
    product_page_linked: bool = False
    product_page_link_error: str = ""


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

TOKEN_TTL_SECONDS = 23 * 60 * 60  # treat as expired after 23 hours (token lasts 24)


async def _fetch_fresh_token(store: StoreConfig) -> str:
    """Call Shopify OAuth token endpoint and return the access token."""
    url = f"https://{store.myshopify_domain}/admin/oauth/access_token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": store.client_id,
        "client_secret": store.client_secret,
    }
    logger.debug("Fetching fresh access token for store %s", store.myshopify_domain)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
        )
        if response.status_code != 200:
            raise ShopifyError(
                f"Token fetch for {store.myshopify_domain} failed "
                f"({response.status_code}): {response.text[:200]}"
            )
        body = response.json()

    token = body.get("access_token")
    if not token:
        raise ShopifyError(
            f"Token response for {store.myshopify_domain} missing 'access_token': {body}"
        )

    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    await db.save_token(store.id, token, expires_at)
    logger.info("Fetched and cached new access token for store %s", store.id)
    return token


async def _get_token(store: StoreConfig) -> str:
    """Return a valid access token, using the DB cache when possible."""
    cached = await db.get_cached_token(store.id)
    if cached:
        logger.debug("Using cached token for store %s", store.id)
        return cached
    return await _fetch_fresh_token(store)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _base_url(store: StoreConfig) -> str:
    return f"https://{store.myshopify_domain}/admin/api/{SHOPIFY_API_VERSION}"


def _headers(token: str) -> dict[str, str]:
    return {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }


async def _get(client: httpx.AsyncClient, url: str, token: str) -> dict:
    log_debug_payload(logger, f"Shopify GET → {url}", None)
    response = await client.get(url, headers=_headers(token))
    if response.status_code != 200:
        raise ShopifyError(
            f"Shopify GET {url} returned {response.status_code}: {response.text[:300]}"
        )
    body = response.json()
    log_debug_payload(logger, f"Shopify GET ← {url}", body)
    return body


async def _post(client: httpx.AsyncClient, url: str, token: str, payload: dict) -> dict:
    log_debug_payload(logger, f"Shopify POST → {url}", payload)
    response = await client.post(url, headers=_headers(token), json=payload)
    if response.status_code not in (200, 201):
        raise ShopifyError(
            f"Shopify POST {url} returned {response.status_code}: {response.text[:300]}"
        )
    body = response.json()
    log_debug_payload(logger, f"Shopify POST ← {url}", body)
    return body


async def _put(client: httpx.AsyncClient, url: str, token: str, payload: dict) -> dict:
    log_debug_payload(logger, f"Shopify PUT → {url}", payload)
    response = await client.put(url, headers=_headers(token), json=payload)
    if response.status_code != 200:
        raise ShopifyError(
            f"Shopify PUT {url} returned {response.status_code}: {response.text[:300]}"
        )
    body = response.json()
    log_debug_payload(logger, f"Shopify PUT ← {url}", body)
    return body


async def _graphql(client: httpx.AsyncClient, store: StoreConfig, token: str, query: str, variables: dict) -> dict:
    url = f"{_base_url(store)}/graphql.json"
    log_debug_payload(logger, f"Shopify GraphQL → {url}", {"query": query, "variables": variables})
    response = await client.post(url, headers=_headers(token), json={"query": query, "variables": variables})
    if response.status_code not in (200, 201):
        raise ShopifyError(
            f"Shopify GraphQL {url} returned {response.status_code}: {response.text[:300]}"
        )
    body = response.json()
    if body.get("errors"):
        raise ShopifyError(f"Shopify GraphQL errors: {json.dumps(body['errors'])[:300]}")
    data = body.get("data", {})
    log_debug_payload(logger, f"Shopify GraphQL ← {url}", data)
    return data


async def _delete(client: httpx.AsyncClient, url: str, token: str) -> None:
    response = await client.delete(url, headers=_headers(token))
    if response.status_code not in (200, 202, 204):
        raise ShopifyError(
            f"Shopify DELETE {url} returned {response.status_code}: {response.text[:300]}"
        )


def _is_404(exc: Exception) -> bool:
    return " returned 404:" in str(exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def graphql_request(
    store: StoreConfig,
    query: str,
    variables: Optional[dict] = None,
    timeout: float = 45,
) -> dict:
    """Execute an authenticated Shopify Admin GraphQL request.

    Feature services use this public boundary instead of reaching into token
    internals. GraphQL top-level errors are normalised to ``ShopifyError`` by
    ``_graphql``.
    """
    token = await _get_token(store)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await _graphql(client, store, token, query, variables or {})


async def run_shopifyql_query(store: StoreConfig, query_text: str) -> list[dict]:
    """Run ShopifyQL and return rows keyed by the API column names."""
    query = """
      query IntelligenceShopifyQL($query: String!) {
        shopifyqlQuery(query: $query) {
          tableData {
            columns { name displayName dataType }
            rows
          }
          parseErrors
        }
      }
    """
    data = await graphql_request(store, query, {"query": query_text})
    result = data.get("shopifyqlQuery") or {}
    parse_errors = result.get("parseErrors") or []
    if parse_errors:
        raise ShopifyError(f"ShopifyQL parse error: {json.dumps(parse_errors)[:500]}")
    table = result.get("tableData") or {}
    columns = [col.get("name", "") for col in table.get("columns", [])]
    output: list[dict] = []
    for raw_row in table.get("rows", []) or []:
        if isinstance(raw_row, str):
            try:
                raw_row = json.loads(raw_row)
            except json.JSONDecodeError:
                continue
        if isinstance(raw_row, dict):
            output.append(raw_row)
        elif isinstance(raw_row, list):
            output.append(dict(zip(columns, raw_row)))
    return output

async def fetch_blogs(store: StoreConfig) -> list[ShopifyBlog]:
    """Return all blogs for the store."""
    url = f"{_base_url(store)}/blogs.json"
    token = await _get_token(store)
    async with httpx.AsyncClient(timeout=30) as client:
        data = await _get(client, url, token)
    return [
        ShopifyBlog(id=b["id"], handle=b["handle"], title=b.get("title", b["handle"]))
        for b in data.get("blogs", [])
    ]


def _storefront_domain(store: StoreConfig) -> str:
    if store.custom_domain:
        return store.custom_domain.strip().replace("https://", "").replace("http://", "").rstrip("/")
    return store.myshopify_domain.replace(".myshopify.com", ".com")


async def fetch_store_articles(store: StoreConfig, limit_per_blog: int = 50) -> list[ShopifyArticle]:
    """Return current articles across the store's blogs.

    ``limit_per_blog=0`` follows Shopify cursor pagination until every article
    has been read. Positive values retain the bounded behaviour used by lighter
    features such as internal-link suggestions.
    """
    blogs = await fetch_blogs(store)
    token = await _get_token(store)
    articles: list[ShopifyArticle] = []
    requested_limit = int(limit_per_blog)
    page_size = 250 if requested_limit <= 0 else min(max(requested_limit, 1), 250)
    async with httpx.AsyncClient(timeout=45) as client:
        for blog in blogs:
            url = (
                f"{_base_url(store)}/blogs/{blog.id}/articles.json"
                f"?limit={page_size}&fields=id,blog_id,title,handle,body_html,summary_html,tags,image,published_at"
            )
            read_for_blog = 0
            while url:
                response = await client.get(url, headers=_headers(token))
                if response.status_code != 200:
                    raise ShopifyError(
                        f"Shopify GET {url} returned {response.status_code}: {response.text[:300]}"
                    )
                data = response.json()
                log_debug_payload(logger, f"Shopify GET ← {url}", data)
                for article in data.get("articles", []):
                    if requested_limit > 0 and read_for_blog >= requested_limit:
                        break
                    image = article.get("image") or {}
                    articles.append(
                        ShopifyArticle(
                            id=article.get("id", 0),
                            blog_id=article.get("blog_id", blog.id),
                            blog_handle=blog.handle,
                            title=article.get("title", ""),
                            handle=article.get("handle", ""),
                            body_html=article.get("body_html", ""),
                            summary_html=article.get("summary_html", ""),
                            tags=article.get("tags", ""),
                            article_url=(
                                f"https://{_storefront_domain(store)}/blogs/{blog.handle}/{article.get('handle', '')}"
                            ),
                            image_url=image.get("src", ""),
                            published_at=article.get("published_at", ""),
                        )
                    )
                    read_for_blog += 1
                if requested_limit > 0 and read_for_blog >= requested_limit:
                    break
                url = str((response.links.get("next") or {}).get("url") or "")
    return articles


async def get_access_scopes(store: StoreConfig) -> set[str]:
    """Return the access scopes granted to the current app installation."""
    token = await _get_token(store)
    url = f"https://{store.myshopify_domain}/admin/oauth/access_scopes.json"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=_headers(token))
    if response.status_code != 200:
        raise ShopifyError(
            f"Shopify GET {url} returned {response.status_code}: {response.text[:300]}"
        )
    body = response.json()
    return {
        scope.get("handle", "")
        for scope in body.get("access_scopes", [])
        if scope.get("handle")
    }


async def _find_article_blog_id(store: StoreConfig, article_id: int) -> Optional[int]:
    """Search blogs for an article id and return its current blog id if found."""
    blogs = await fetch_blogs(store)
    token = await _get_token(store)
    async with httpx.AsyncClient(timeout=30) as client:
        for blog in blogs:
            url = (
                f"{_base_url(store)}/blogs/{blog.id}/articles/{article_id}.json"
                f"?fields=id,blog_id,title,handle"
            )
            try:
                data = await _get(client, url, token)
            except ShopifyError as exc:
                if _is_404(exc):
                    continue
                raise
            article = data.get("article") or {}
            if article:
                return int(article.get("blog_id") or blog.id)
    return None


async def _delete_article_once(store: StoreConfig, blog_id: int, article_id: int) -> None:
    token = await _get_token(store)
    url = f"{_base_url(store)}/blogs/{blog_id}/articles/{article_id}.json"
    async with httpx.AsyncClient(timeout=30) as client:
        await _delete(client, url, token)


async def delete_article(store: StoreConfig, blog_id: int, article_id: int) -> None:
    """Delete a Shopify blog article by numeric blog and article id."""
    try:
        await _delete_article_once(store, blog_id, article_id)
        return
    except ShopifyError as exc:
        if not _is_404(exc):
            raise

    resolved_blog_id = await _find_article_blog_id(store, article_id)
    if resolved_blog_id is not None and resolved_blog_id != blog_id:
        logger.warning(
            "delete_article: article %s moved or was scanned under stale blog id %s; retrying with blog %s",
            article_id,
            blog_id,
            resolved_blog_id,
        )
        await _delete_article_once(store, resolved_blog_id, article_id)
        return

    try:
        scopes = await get_access_scopes(store)
    except ShopifyError:
        scopes = set()

    if scopes and "write_content" not in scopes:
        granted = ", ".join(sorted(scopes)) or "none"
        raise ShopifyError(
            "Deleting blog posts requires the `write_content` scope. "
            f"This store currently granted: {granted}. Reinstall or update the app scopes, then try again."
        )

    if resolved_blog_id is None:
        raise ShopifyError(
            f"Shopify could not find article {article_id} on the store. "
            "It may already have been deleted, unpublished in a different app context, or the scan is stale. "
            "Refresh Current Store Posts and try again."
        )

    raise ShopifyError(
        f"Shopify could find article {article_id} on blog {resolved_blog_id}, but deletion still failed. "
        "If this store was installed before `write_content` was requested, reinstall/update the app scopes and try again."
    )


async def fetch_products(store: StoreConfig, limit: int = 250) -> list[ShopifyProduct]:
    """Return up to `limit` products for the store."""
    url = f"{_base_url(store)}/products.json?limit={limit}&fields=id,title,handle"
    token = await _get_token(store)
    async with httpx.AsyncClient(timeout=30) as client:
        data = await _get(client, url, token)
    domain = store.myshopify_domain
    return [
        ShopifyProduct(
            id=p["id"],
            title=p.get("title", ""),
            handle=p.get("handle", ""),
            url=f"https://{domain}/products/{p['handle']}",
        )
        for p in data.get("products", [])
    ]


async def fetch_product_blog_sync_products(store: StoreConfig, limit: int = 250) -> list[dict]:
    """Return the live product/link fields required by the product-blog audit.

    This deliberately reads Shopify rather than the app loader cache so a re-sync
    is a genuine sanity check of the current catalogue.
    """
    query = """
      query ProductBlogSyncProducts($first: Int!) {
        products(first: $first, sortKey: TITLE) {
          nodes {
            id legacyResourceId title handle status descriptionHtml
            guideTitle: metafield(namespace: "custom", key: "ai_blog_related_guide_title") { value }
            guideUrl: metafield(namespace: "custom", key: "ai_blog_related_guide_url") { value }
            guideExcerpt: metafield(namespace: "custom", key: "ai_blog_related_guide_excerpt") { value }
          }
        }
      }
    """
    data = await graphql_request(store, query, {"first": min(max(int(limit), 1), 250)})
    return list(((data.get("products") or {}).get("nodes") or []))


async def fetch_blog_articles(
    store: StoreConfig,
    blog_handle: str,
    limit: int = 250,
) -> list[ShopifyArticle]:
    """Return the current articles for one exact blog handle."""
    blogs = await fetch_blogs(store)
    blog = next((item for item in blogs if item.handle == blog_handle), None)
    if blog is None:
        raise ShopifyError(
            f"Shopify blog handle '{blog_handle}' does not exist. No reconciliation was attempted."
        )

    token = await _get_token(store)
    url = (
        f"{_base_url(store)}/blogs/{blog.id}/articles.json"
        f"?limit={min(max(int(limit), 1), 250)}"
        "&fields=id,blog_id,title,handle,body_html,summary_html,tags,image,published_at"
    )
    async with httpx.AsyncClient(timeout=45) as client:
        data = await _get(client, url, token)

    articles: list[ShopifyArticle] = []
    for article in data.get("articles", []):
        image = article.get("image") or {}
        articles.append(
            ShopifyArticle(
                id=article.get("id", 0),
                blog_id=article.get("blog_id", blog.id),
                blog_handle=blog.handle,
                title=article.get("title", ""),
                handle=article.get("handle", ""),
                body_html=article.get("body_html", ""),
                summary_html=article.get("summary_html", ""),
                tags=article.get("tags", ""),
                article_url=(
                    f"https://{_storefront_domain(store)}/blogs/{blog.handle}/{article.get('handle', '')}"
                ),
                image_url=image.get("src", ""),
                published_at=article.get("published_at", ""),
            )
        )
    return articles


async def fetch_wellness_quiz_products(store: StoreConfig, limit: int = 250) -> list[dict]:
    """Return the richer, current catalogue snapshot used by the Wellness Quiz."""
    query = """
      query WellnessQuizProducts($first: Int!) {
        products(first: $first, sortKey: TITLE) {
          nodes {
            id legacyResourceId title handle status description tags productType totalInventory
            onlineStoreUrl
            featuredImage { url altText }
            priceRangeV2 { minVariantPrice { amount currencyCode } }
            variants(first: 1) { nodes { id legacyResourceId availableForSale } }
            collections(first: 12) { nodes { title handle } }
            guideTitle: metafield(namespace: "custom", key: "ai_blog_related_guide_title") { value }
            guideUrl: metafield(namespace: "custom", key: "ai_blog_related_guide_url") { value }
          }
        }
        pages(first: 250, query: "is_published:true") {
          nodes { handle title isPublished }
        }
      }
    """
    data = await graphql_request(store, query, {"first": min(max(int(limit), 1), 250)})
    products = list(((data.get("products") or {}).get("nodes") or []))
    pages = list(((data.get("pages") or {}).get("nodes") or []))
    storefront = (store.custom_domain or store.myshopify_domain).strip().rstrip("/")
    if storefront and not storefront.startswith(("http://", "https://")):
        storefront = f"https://{storefront}"
    published_pages = [page for page in pages if storefront and page.get("isPublished") and page.get("handle")]
    for product in products:
        product_handle = str(product.get("handle") or "")
        product_title = re.sub(r"\s+", " ", str(product.get("title") or "")).strip().lower()
        match = next(
            (page for page in published_pages if str(page.get("handle") or "") == f"{product_handle}-offer"),
            None,
        )
        if match is None and product_title:
            match = next(
                (
                    page for page in published_pages
                    if re.sub(r"\s+", " ", str(page.get("title") or "")).strip().lower().startswith(product_title)
                ),
                None,
            )
        product["_landing_page_url"] = (
            f"{storefront}/pages/{match.get('handle')}" if match else ""
        )
    return products


async def _fetch_product_by_handle(
    store: StoreConfig,
    product_handle: str,
    fields: str = "id,title,handle,body_html,tags,images",
) -> Optional[dict]:
    """Return the first product matching a storefront handle, or None.

    Shopify Admin's `/products/{id}.json` endpoint expects a numeric product ID,
    not a storefront handle. Product-blog flows start from the storefront URL, so
    resolve the product through the list endpoint filtered by handle.
    """
    normalized_handle = product_handle.strip().strip("/")
    if not normalized_handle:
        return None
    url = (
        f"{_base_url(store)}/products.json?handle={quote(normalized_handle)}"
        f"&limit=1&fields={quote(fields, safe=',')}"
    )
    token = await _get_token(store)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            data = await _get(client, url, token)
        products = data.get("products", [])
        if not products:
            logger.warning("_fetch_product_by_handle: no product for handle=%s", normalized_handle)
            return None
        return products[0]
    except Exception as exc:
        logger.warning("_fetch_product_by_handle failed for %s: %s", normalized_handle, exc)
        return None


async def fetch_product_image_url(store: StoreConfig, product_handle: str) -> Optional[str]:
    """Return the src URL of the first (main) image for a product, or None."""
    try:
        product = await _fetch_product_by_handle(store, product_handle, fields="id,handle,images")
        images = (product or {}).get("images", [])
        if images:
            src = images[0].get("src")
            logger.info("fetch_product_image_url: handle=%s src=%s", product_handle, src)
            return src
        logger.warning("fetch_product_image_url: no images found for handle=%s", product_handle)
    except Exception as exc:
        logger.warning("fetch_product_image_url failed for %s: %s", product_handle, exc)
    return None


async def fetch_product_details(store: StoreConfig, product_handle: str) -> Optional[dict]:
    """Return product metadata: title, plain-text description, and tags string.
    Used to enrich the LLM prompt with accurate product context.
    """
    try:
        product = await _fetch_product_by_handle(
            store,
            product_handle,
            fields="id,title,handle,body_html,tags,images",
        )
        if not product:
            logger.warning("fetch_product_details: empty product for handle=%s", product_handle)
            return None
        return {
            "id": product.get("id"),
            "handle": product.get("handle", product_handle),
            "title": product.get("title", ""),
            "description": product.get("body_html", ""),
            "tags": product.get("tags", ""),
        }
    except Exception as exc:
        logger.warning("fetch_product_details failed for %s: %s", product_handle, exc)
    return None


async def fetch_product_image_data_uri(store: StoreConfig, product_handle: str) -> Optional[str]:
    """Fetch the product's main image via the admin API and return as a base64 data URI.
    Downloads the image bytes using the admin access token so no public CDN access is needed.
    """
    token = await _get_token(store)
    try:
        product = await _fetch_product_by_handle(store, product_handle, fields="id,handle,images")
        images = (product or {}).get("images", [])
        if not images:
            logger.warning("fetch_product_image_data_uri: no images for handle=%s", product_handle)
            return None
        img_src = images[0].get("src")
        if not img_src:
            return None
        logger.info("Downloading product image via admin token: %s", img_src[:80])
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(
                img_src,
                headers={"X-Shopify-Access-Token": token},
            )
            resp.raise_for_status()
            img_bytes = resp.content
            content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        b64 = base64.b64encode(img_bytes).decode()
        logger.info(
            "Product image downloaded: %d bytes content_type=%s",
            len(img_bytes), content_type,
        )
        return f"data:{content_type};base64,{b64}"
    except Exception as exc:
        logger.warning("fetch_product_image_data_uri failed for %s: %s", product_handle, exc)
    return None


async def resolve_blog_id(store: StoreConfig, blog_handle: str) -> int:
    """Return the numeric blog ID for a given handle."""
    blogs = await fetch_blogs(store)
    match = next((b for b in blogs if b.handle == blog_handle), None)
    if match:
        return match.id
    if blogs:
        logger.warning(
            "Blog handle '%s' not found for store %s, falling back to first blog '%s'",
            blog_handle,
            store.myshopify_domain,
            blogs[0].handle,
        )
        return blogs[0].id
    raise ShopifyError(f"No blogs found for store {store.myshopify_domain}.")


async def upload_image_to_shopify(
    store: StoreConfig,
    image_url: str,
    filename: str,
) -> Optional[str]:
    """
    Upload an image to Shopify Files using the supported staged GraphQL flow.

    The old REST ``/files.json`` call returns HTTP 406 on current Shopify API
    versions. Generated images must be copied to Shopify's CDN before the model's
    temporary URL expires, otherwise articles retain broken or missing images.
    """
    from services.image_optimizer import optimize_image

    # Always ensure output filename ends with .webp since we compress/optimize to WebP
    if not filename.lower().endswith(".webp"):
        base_name = filename.rsplit(".", 1)[0]
        filename = f"{base_name}.webp"

    img_bytes = None
    if image_url.startswith("data:"):
        # Extract base64 payload from data URI (data:<mime>;base64,<b64>)
        try:
            header, b64data = image_url.split(",", 1)
            raw_data = base64.b64decode(b64data)
            # Optimize raw bytes
            optimized_bytes = optimize_image(raw_data)
            img_bytes = optimized_bytes
        except Exception:
            logger.warning("Malformed data URI passed to upload_image_to_shopify, skipping")
            return None
    else:
        # Fetch web image and optimize
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                res = await client.get(image_url, follow_redirects=True)
                res.raise_for_status()
                img_bytes = optimize_image(res.content)
        except Exception as exc:
            logger.warning("Failed to fetch/optimize image_url %s: %s", image_url[:80], exc)
            # If fetch fails, we'll let Shopify fetch the original URL directly as fallback
            img_bytes = None

    try:
        token = await _get_token(store)
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            original_source = image_url
            if img_bytes is not None:
                staged_query = """
                mutation stageBlogImage($input: [StagedUploadInput!]!) {
                  stagedUploadsCreate(input: $input) {
                    stagedTargets { url resourceUrl parameters { name value } }
                    userErrors { field message }
                  }
                }
                """
                staged_data = await _graphql(
                    client,
                    store,
                    token,
                    staged_query,
                    {
                        "input": [{
                            "filename": filename,
                            "mimeType": "image/webp",
                            "httpMethod": "POST",
                            "resource": "IMAGE",
                        }]
                    },
                )
                staged_result = staged_data.get("stagedUploadsCreate") or {}
                staged_errors = staged_result.get("userErrors") or []
                if staged_errors:
                    messages = "; ".join(str(error.get("message") or "") for error in staged_errors)
                    raise ShopifyError(f"Shopify staged image upload failed: {messages}")
                targets = staged_result.get("stagedTargets") or []
                if not targets:
                    raise ShopifyError("Shopify returned no staged target for the blog image.")
                target = targets[0]
                upload_response = await client.post(
                    target["url"],
                    data={item["name"]: item["value"] for item in target.get("parameters") or []},
                    files={"file": (filename, img_bytes, "image/webp")},
                )
                if upload_response.status_code not in (200, 201, 204):
                    raise ShopifyError(
                        f"Shopify staged image transfer returned {upload_response.status_code}: "
                        f"{upload_response.text[:300]}"
                    )
                original_source = target["resourceUrl"]

            create_query = """
            mutation createBlogImage($files: [FileCreateInput!]!) {
              fileCreate(files: $files) {
                files {
                  id
                  fileStatus
                  ... on MediaImage { image { url } }
                }
                userErrors { field message }
              }
            }
            """
            create_data = await _graphql(
                client,
                store,
                token,
                create_query,
                {
                    "files": [{
                        "alt": filename.rsplit(".", 1)[0].replace("_", " "),
                        "contentType": "IMAGE",
                        "originalSource": original_source,
                    }]
                },
            )
            create_result = create_data.get("fileCreate") or {}
            create_errors = create_result.get("userErrors") or []
            if create_errors:
                messages = "; ".join(str(error.get("message") or "") for error in create_errors)
                raise ShopifyError(f"Shopify fileCreate failed: {messages}")
            files = create_result.get("files") or []
            if not files or not files[0].get("id"):
                raise ShopifyError("Shopify fileCreate returned no image file ID.")
            file_id = str(files[0]["id"])
            initial_url = str(((files[0].get("image") or {}).get("url") or "")).strip()
            if files[0].get("fileStatus") == "READY" and initial_url:
                logger.info("Blog image stored on Shopify CDN: %s", initial_url[:100])
                return initial_url

            status_query = """
            query getBlogImage($id: ID!) {
              node(id: $id) {
                ... on MediaImage {
                  fileStatus
                  fileErrors { message }
                  image { url }
                }
              }
            }
            """
            for _ in range(30):
                await asyncio.sleep(1)
                status_data = await _graphql(
                    client, store, token, status_query, {"id": file_id}
                )
                node = status_data.get("node") or {}
                status = str(node.get("fileStatus") or "")
                if status == "READY" and (node.get("image") or {}).get("url"):
                    cdn_url = str(node["image"]["url"])
                    logger.info("Blog image stored on Shopify CDN: %s", cdn_url[:100])
                    return cdn_url
                if status == "FAILED":
                    errors = "; ".join(
                        str(error.get("message") or "") for error in node.get("fileErrors") or []
                    )
                    raise ShopifyError(
                        f"Shopify failed to process blog image {filename}: {errors or 'unknown error'}"
                    )
            raise ShopifyError(
                f"Shopify did not finish processing blog image {filename} within 30 seconds."
            )
    except ShopifyError as exc:
        logger.error("Image upload to Shopify failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 — product publish integrity check reports failure
        logger.exception("Unexpected Shopify image upload failure for %s", filename)
        return None


async def update_article_image(
    store: StoreConfig,
    blog_id: int,
    article_id: int,
    title: str,
    image_url: str,
) -> str:
    """Upload an image if needed, then attach it as the article featured image."""
    safe_title = "".join(c if c.isalnum() else "_" for c in title[:40]).lower() or "article"
    filename = f"{safe_title}_featured_image.png"
    cdn_url = await upload_image_to_shopify(store, image_url, filename)
    if not cdn_url:
        raise ShopifyError("Failed to prepare a featured image for Shopify article update.")

    payload = {
        "article": {
            "id": article_id,
            "image": {"src": cdn_url},
        }
    }
    url = f"{_base_url(store)}/blogs/{blog_id}/articles/{article_id}.json"
    token = await _get_token(store)
    async with httpx.AsyncClient(timeout=60) as client:
        data = await _put(client, url, token, payload)

    image = (data.get("article") or {}).get("image") or {}
    return image.get("src") or cdn_url


async def update_article_title(
    store: StoreConfig,
    blog_id: int,
    article_id: int,
    title: str,
) -> str:
    """Update a Shopify article's title. Returns the new title Shopify stored."""
    payload = {
        "article": {
            "id": article_id,
            "title": title,
        }
    }
    url = f"{_base_url(store)}/blogs/{blog_id}/articles/{article_id}.json"
    token = await _get_token(store)
    async with httpx.AsyncClient(timeout=60) as client:
        data = await _put(client, url, token, payload)
    return (data.get("article") or {}).get("title") or title


async def fetch_article_body_html(
    store: StoreConfig,
    blog_id: int,
    article_id: int,
) -> str:
    """Return the current body for one article for repair precondition checks."""
    url = (
        f"{_base_url(store)}/blogs/{int(blog_id)}/articles/{int(article_id)}.json"
        "?fields=id,body_html"
    )
    token = await _get_token(store)
    async with httpx.AsyncClient(timeout=45) as client:
        data = await _get(client, url, token)
    return str((data.get("article") or {}).get("body_html") or "")


async def update_article_body_html(
    store: StoreConfig,
    blog_id: int,
    article_id: int,
    body_html: str,
) -> str:
    """Replace one article body and return the exact body Shopify stored."""
    url = f"{_base_url(store)}/blogs/{int(blog_id)}/articles/{int(article_id)}.json"
    token = await _get_token(store)
    payload = {"article": {"id": int(article_id), "body_html": body_html}}
    async with httpx.AsyncClient(timeout=60) as client:
        data = await _put(client, url, token, payload)
    return str((data.get("article") or {}).get("body_html") or "")


def _build_article_html(
    content: str,
    image_urls: list[str],
    keywords: list[str],
    hashtags: list[str],
    long_tail_keywords: list[str] | None = None,
    pin_description: str = "",
    title: str = "",
    pin_image_url: str = "",
) -> str:
    """
    Insert images into the HTML content.

    Images carry Pinterest `data-pin-description` + descriptive `alt` text so they
    are optimised when saved to Pinterest. When ``pin_image_url`` is provided, a
    vertical pin image is embedded at the end with ``data-pin-media`` so Pinterest
    prefers it for the saved pin. Keywords and hashtags remain available to the
    app, Shopify article tags and social captions; they are intentionally not
    rendered as public or hidden article-body text.
    """
    long_tail_keywords = long_tail_keywords or []
    from html import escape as _esc

    pin_desc_attr = _esc(pin_description, quote=True) if pin_description else ""
    alt_attr = _esc(title, quote=True) if title else ""

    def img_tag(url: str) -> str:
        attrs = f'src="{url}" style="max-width:100%;height:auto;margin:24px 0;"'
        if alt_attr:
            attrs += f' alt="{alt_attr}"'
        if pin_desc_attr:
            attrs += f' data-pin-description="{pin_desc_attr}"'
        return f"<img {attrs} />\n"

    if not image_urls:
        result = content
    else:
        parts = content.split("</p>")
        result_parts = []
        image_index = 0

        for i, part in enumerate(parts):
            result_parts.append(part)
            if i < len(parts) - 1:
                result_parts.append("</p>")
                if image_index < len(image_urls):
                    result_parts.append(img_tag(image_urls[image_index]))
                    image_index += 1

        for url in image_urls[image_index:]:
            result_parts.append(img_tag(url))

        result = "".join(result_parts)

    # Vertical Pinterest pin image (preferred when users Save to Pinterest)
    if pin_image_url:
        pin_attrs = (
            f'src="{pin_image_url}" '
            'style="display:block;max-width:360px;width:100%;height:auto;margin:28px auto;'
            'border-radius:8px;" data-pin-media="' + pin_image_url + '"'
        )
        if alt_attr:
            pin_attrs += f' alt="{alt_attr}"'
        if pin_desc_attr:
            pin_attrs += f' data-pin-description="{pin_desc_attr}"'
        result += f"<img {pin_attrs} />\n"

    return result


async def publish_article(
    store: StoreConfig,
    blog_handle: str,
    title: str,
    content_html: str,
    summary: str,
    keywords: list[str],
    hashtags: list[str],
    author: str,
    image_url_list: list[str],
    featured_image_url: str = "",
    product_url: str = "",
    product_title: str = "",
    long_tail_keywords: list[str] | None = None,
    pin_description: str = "",
    pin_image_url: str = "",
) -> PublishResult:
    """
    Upload images, insert them into HTML, then publish the article to Shopify.
    """
    # Defensive: strip any heading-marker noise ("H2:", "## ", quotes) the model leaked.
    title = clean_title(title)
    long_tail_keywords = long_tail_keywords or []
    # Pinterest pin description: fall back to summary + hashtags when the model omits it.
    if not pin_description.strip():
        ht = " ".join(hashtags) if hashtags else ""
        pin_description = (summary + (" " + ht if ht else "")).strip()
    # Upload body images to Shopify CDN.
    # Shopify CDN URLs (cdn.shopify.com) are already hosted — use directly, no re-upload needed.
    image_cdn_urls: list[str] = []
    for i, img_url in enumerate(image_url_list):
        if "cdn.shopify.com" in img_url or img_url.startswith("https://") and not img_url.startswith("data:"):
            # Check if it's already a Shopify CDN URL — skip upload
            if "cdn.shopify.com" in img_url:
                logger.debug("Using Shopify CDN URL directly (no re-upload): %s", img_url[:80])
                image_cdn_urls.append(img_url)
                continue
        safe_title = "".join(c if c.isalnum() else "_" for c in title[:40]).lower()
        filename = f"{safe_title}_image_{i + 1}.png"
        cdn_url = await upload_image_to_shopify(store, img_url, filename)
        if cdn_url:
            image_cdn_urls.append(cdn_url)

    if product_url.strip() and len(image_cdn_urls) != len(image_url_list):
        raise ShopifyError(
            "Product blog image integrity check failed: "
            f"generated/prepared {len(image_url_list)} image(s), but only "
            f"{len(image_cdn_urls)} reached Shopify Files. The article was not "
            "published with missing images; check the exact upload error and retry."
        )

    # Embed all CDN images in the body (skip data URIs — Shopify may strip them).
    # The first image is ALSO set as the article's featured image below.
    cdn_urls_for_body = [u for u in image_cdn_urls if not u.startswith("data:")]

    featured_prepared_url = ""
    featured_candidate = featured_image_url.strip()
    if featured_candidate:
        if featured_candidate.startswith("data:"):
            featured_prepared_url = featured_candidate
        elif "cdn.shopify.com" in featured_candidate:
            featured_prepared_url = featured_candidate
        else:
            safe_title = "".join(c if c.isalnum() else "_" for c in title[:40]).lower()
            uploaded_featured = await upload_image_to_shopify(
                store,
                featured_candidate,
                f"{safe_title}_featured_image.png",
            )
            featured_prepared_url = uploaded_featured or featured_candidate
    elif image_cdn_urls:
        featured_prepared_url = image_cdn_urls[0]

    # Upload the vertical Pinterest pin image (if provided) to the CDN.
    pin_cdn_url = ""
    if pin_image_url:
        if "cdn.shopify.com" in pin_image_url:
            pin_cdn_url = pin_image_url
        else:
            safe_title = "".join(c if c.isalnum() else "_" for c in title[:40]).lower()
            uploaded = await upload_image_to_shopify(
                store, pin_image_url, f"{safe_title}_pin.png"
            )
            if uploaded and not uploaded.startswith("data:"):
                pin_cdn_url = uploaded

    body_html = _build_article_html(
        content_html,
        cdn_urls_for_body,
        keywords,
        hashtags,
        long_tail_keywords,
        pin_description=pin_description,
        title=title,
        pin_image_url=pin_cdn_url,
    )

    tags = ", ".join(keywords + [t.lstrip("#") for t in hashtags])
    blog_id = await resolve_blog_id(store, blog_handle)

    article: dict = {
        "title": title,
        "author": author,
        "body_html": body_html,
        "summary_html": f"<p>{summary}</p>",
        "tags": tags,
        "published": True,
        # SEO meta description — shown in Google search results
        "metafields": [
            {
                "key": "description_tag",
                "value": summary,
                "type": "single_line_text_field",
                "namespace": "global",
            }
        ],
    }

    # Featured image — use the dedicated featured image when provided, otherwise
    # fall back to the first prepared body image.
    if featured_prepared_url:
        if featured_prepared_url.startswith("data:"):
            try:
                _, b64data = featured_prepared_url.split(",", 1)
                article["image"] = {"attachment": b64data, "filename": "featured_image.jpg"}
            except Exception:
                pass  # skip featured image if data URI is malformed
        else:
            article["image"] = {"src": featured_prepared_url}

    payload = {"article": article}

    url = f"{_base_url(store)}/blogs/{blog_id}/articles.json"
    token = await _get_token(store)
    async with httpx.AsyncClient(timeout=30) as client:
        data = await _post(client, url, token, payload)

    article = data.get("article", {})
    article_id = article.get("id", 0)

    article_url = (
        f"https://{_storefront_domain(store)}"
        f"/blogs/{blog_handle}/{article.get('handle', '')}"
    )

    product_page_linked = False
    product_page_link_error = ""

    resolved_product_url = product_url.strip()
    if resolved_product_url:
        product_handle = resolved_product_url.rstrip("/").split("/")[-1]
        try:
            await _set_related_product_guide_metafields(
                store=store,
                product_handle=product_handle,
                guide_title=title,
                guide_url=article_url,
                guide_excerpt=summary,
            )
            product_page_linked = True
        except Exception as exc:
            product_label = product_title.strip() or product_handle or resolved_product_url
            product_page_link_error = str(exc)
            logger.warning(
                "Published article id=%s but could not update shared guide metafields for product %s: %s",
                article_id,
                product_label,
                exc,
            )

        try:
            await _update_product_description_with_guide_link(
                store=store,
                product_handle=product_handle,
                guide_title=title,
                guide_url=article_url,
                keywords=keywords,
                hashtags=hashtags,
                long_tail_keywords=long_tail_keywords,
            )
        except Exception as exc:
            product_label = product_title.strip() or product_handle or resolved_product_url
            logger.warning(
                "Published article id=%s but could not update body_html description for product %s: %s",
                article_id,
                product_label,
                exc,
            )

    logger.info(
        "Published article id=%s title='%s' to store=%s blog=%s",
        article_id,
        title,
        store.myshopify_domain,
        blog_handle,
    )

    return PublishResult(
        article_id=article_id,
        article_url=article_url,
        blog_handle=blog_handle,
        title=title,
        product_page_linked=product_page_linked,
        product_page_link_error=product_page_link_error,
    )


async def _set_related_product_guide_metafields(
    store: StoreConfig,
    product_handle: str,
    guide_title: str,
    guide_url: str,
    guide_excerpt: str,
) -> None:
    product = await _fetch_product_by_handle(store, product_handle, fields="id,handle,title")
    if not product:
        raise ShopifyError(f"No Shopify product found for handle '{product_handle}'.")

    product_id = product.get("id")
    if not product_id:
        raise ShopifyError(f"Shopify product '{product_handle}' did not return an id.")

    await set_related_product_guide_metafields_by_id(
        store=store,
        product_id=str(product_id),
        guide_title=guide_title,
        guide_url=guide_url,
        guide_excerpt=guide_excerpt,
    )


async def set_related_product_guide_metafields_by_id(
    store: StoreConfig,
    product_id: str,
    guide_title: str,
    guide_url: str,
    guide_excerpt: str,
) -> None:
    """Set the shared guide metafields when the audit already knows the product id."""
    owner_id = str(product_id).strip()
    if not owner_id:
        raise ShopifyError("Cannot update related guide metafields without a Shopify product id.")
    if not owner_id.startswith("gid://shopify/Product/"):
        owner_id = f"gid://shopify/Product/{owner_id}"

    token = await _get_token(store)
    metafields = [
        {
            "ownerId": owner_id,
            "namespace": SHARED_GUIDE_NAMESPACE,
            "key": SHARED_GUIDE_TITLE_KEY,
            "type": "single_line_text_field",
            "value": guide_title,
        },
        {
            "ownerId": owner_id,
            "namespace": SHARED_GUIDE_NAMESPACE,
            "key": SHARED_GUIDE_URL_KEY,
            "type": "url",
            "value": guide_url,
        },
        {
            "ownerId": owner_id,
            "namespace": SHARED_GUIDE_NAMESPACE,
            "key": SHARED_GUIDE_EXCERPT_KEY,
            "type": "multi_line_text_field",
            "value": guide_excerpt,
        },
    ]

    mutation = """
    mutation SetRelatedGuide($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        userErrors {
          field
          message
        }
      }
    }
    """

    async with httpx.AsyncClient(timeout=30) as client:
        data = await _graphql(client, store, token, mutation, {"metafields": metafields})

    user_errors = (data.get("metafieldsSet") or {}).get("userErrors") or []
    if user_errors:
        parts = []
        for err in user_errors:
            field = ".".join(err.get("field") or [])
            message = err.get("message", "Shopify rejected the related guide metafield update.")
            parts.append(f"{field}: {message}" if field else message)
        raise ShopifyError("; ".join(parts))


async def sync_product_description_guide_link(
    store: StoreConfig,
    product_id: str,
    product_handle: str,
    current_body_html: str,
    guide_title: str,
    guide_url: str,
) -> str:
    """Ensure one managed Related Guide paragraph points at the expected article.

    Existing product copy is preserved. Only paragraphs created by this system
    with the ``Related Guide:`` label are replaced; an unrelated merchant link
    is never removed.
    """
    numeric_id = str(product_id).strip().split("/")[-1]
    if not numeric_id:
        raise ShopifyError(f"Shopify product '{product_handle}' did not return an id.")

    body = current_body_html or ""
    escaped_title = html.escape(guide_title.strip() or "View product guide")
    escaped_url = html.escape(guide_url.strip(), quote=True)
    managed_paragraph = (
        '<p data-ai-blog-related-guide="true"><strong>Related Guide:</strong> '
        f'<a href="{escaped_url}" target="_blank" rel="noopener">{escaped_title}</a></p>'
    )
    managed_pattern = re.compile(
        r'<p\b[^>]*>\s*<strong\b[^>]*>\s*Related\s+Guide:\s*</strong>\s*'
        r'<a\b[^>]*>.*?</a>\s*</p>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    existing = list(managed_pattern.finditer(body))
    if existing:
        if len(existing) == 1 and guide_url.strip() in existing[0].group(0):
            return "description already linked"
        replaced = managed_pattern.sub("", body).rstrip()
        new_body = f"{replaced}\n{managed_paragraph}" if replaced else managed_paragraph
        action = "replaced stale Related Guide link"
    elif guide_url.strip() and guide_url.strip() in body:
        return "description already linked"
    else:
        new_body = f"{body.rstrip()}\n{managed_paragraph}" if body.strip() else managed_paragraph
        action = "added missing Related Guide link"

    if new_body == body:
        return "description already linked"

    token = await _get_token(store)
    url = f"{_base_url(store)}/products/{numeric_id}.json"
    payload = {"product": {"id": int(numeric_id), "body_html": new_body}}
    async with httpx.AsyncClient(timeout=30) as client:
        await _put(client, url, token, payload)
    logger.info("Product-blog sync %s for product %s", action, product_handle)
    return action


async def append_product_link_to_article(
    store: StoreConfig,
    article: ShopifyArticle,
    product_title: str,
    product_url: str,
) -> None:
    """Restore the product CTA on an article with authoritative generation provenance."""
    escaped_title = html.escape(product_title.strip() or "this product")
    escaped_url = html.escape(product_url.strip(), quote=True)
    cta = (
        '<p data-ai-blog-product-link="true">'
        f'<a href="{escaped_url}" target="_blank" rel="noopener">Shop {escaped_title}</a></p>'
    )
    new_body = f"{(article.body_html or '').rstrip()}\n{cta}"
    token = await _get_token(store)
    url = f"{_base_url(store)}/blogs/{article.blog_id}/articles/{article.id}.json"
    payload = {"article": {"id": article.id, "body_html": new_body}}
    async with httpx.AsyncClient(timeout=30) as client:
        await _put(client, url, token, payload)
    article.body_html = new_body
    logger.info(
        "Product-blog sync restored product CTA article=%s product_url=%s",
        article.id,
        product_url,
    )


async def set_product_review_metafields(
    store: StoreConfig, product_handle: str, average: float, count: int,
    review_cache: dict | None = None,
) -> None:
    """Keep public Liquid/JSON-LD rating aggregates in sync with moderation."""
    product = await _fetch_product_by_handle(store, product_handle, fields="id,handle,title")
    if not product or not product.get("id"):
        raise ShopifyError(f"No Shopify product found for review handle '{product_handle}'.")
    owner_id = f"gid://shopify/Product/{product['id']}"
    metafields = [
        {
            "ownerId": owner_id, "namespace": "custom", "key": REVIEW_RATING_KEY,
            "type": "number_decimal", "value": f"{max(0.0, min(float(average), 5.0)):.2f}",
        },
        {
            "ownerId": owner_id, "namespace": "custom", "key": REVIEW_COUNT_KEY,
            "type": "number_integer", "value": str(max(0, int(count))),
        },
        {
            "ownerId": owner_id, "namespace": "custom", "key": "ai_reviews_cache",
            "type": "json", "value": json.dumps(review_cache or {}, ensure_ascii=False),
        },
    ]
    mutation = """
    mutation SetProductReviewAggregate($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        userErrors { field message }
      }
    }
    """
    token = await _get_token(store)
    async with httpx.AsyncClient(timeout=30) as client:
        data = await _graphql(client, store, token, mutation, {"metafields": metafields})
    errors = (data.get("metafieldsSet") or {}).get("userErrors") or []
    if errors:
        raise ShopifyError("; ".join(str(item.get("message") or "") for item in errors))


async def set_store_review_cache(store: StoreConfig, review_cache: dict) -> None:
    """Cache the public store-review summary on Shopify's AppInstallation."""
    token = await _get_token(store)
    query = "query ReviewAppInstallation { currentAppInstallation { id } }"
    async with httpx.AsyncClient(timeout=30) as client:
        installation_data = await _graphql(client, store, token, query, {})
        installation_id = str((installation_data.get("currentAppInstallation") or {}).get("id") or "")
        if not installation_id:
            raise ShopifyError("Shopify did not return the current app installation ID for store review caching.")
        mutation = """
        mutation SetStoreReviewCache($metafields: [MetafieldsSetInput!]!) {
          metafieldsSet(metafields: $metafields) { userErrors { field message } }
        }
        """
        data = await _graphql(client, store, token, mutation, {
            "metafields": [{
                "ownerId": installation_id, "namespace": "reviews", "key": "store_cache",
                "type": "json", "value": json.dumps(review_cache, ensure_ascii=False),
            }]
        })
    errors = (data.get("metafieldsSet") or {}).get("userErrors") or []
    if errors:
        raise ShopifyError("; ".join(str(item.get("message") or "") for item in errors))


async def _update_product_description_with_guide_link(
    store: StoreConfig,
    product_handle: str,
    guide_title: str,
    guide_url: str,
    keywords: list[str],
    hashtags: list[str],
    long_tail_keywords: list[str] | None = None,
) -> None:
    product = await _fetch_product_by_handle(store, product_handle, fields="id,handle,title,body_html")
    if not product:
        raise ShopifyError(f"No Shopify product found for handle '{product_handle}'.")

    product_id = product.get("id")
    if not product_id:
        raise ShopifyError(f"Shopify product '{product_handle}' did not return an id.")

    current_body = product.get("body_html", "") or ""

    # Ensure we don't double append
    if guide_url in current_body:
        logger.info("Product %s already includes guide link in description. Skipping update.", product_handle)
        return

    append_parts = []
    append_parts.append(f'<p><strong>Related Guide:</strong> <a href="{guide_url}" target="_blank" rel="noopener">{guide_title}</a></p>')

    snippet = "\n" + "\n".join(append_parts)
    new_body = current_body + snippet

    token = await _get_token(store)
    url = f"{_base_url(store)}/products/{product_id}.json"
    payload = {
        "product": {
            "id": product_id,
            "body_html": new_body
        }
    }

    async with httpx.AsyncClient(timeout=30) as client:
        await _put(client, url, token, payload)

    logger.info("Updated product %s description with related guide link.", product_handle)
