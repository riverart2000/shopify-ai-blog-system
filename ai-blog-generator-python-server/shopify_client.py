"""
shopify_client.py — Shopify Admin REST API interactions.
Handles: token fetch/cache, fetching blogs, uploading images, publishing articles.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx

import db
from config import StoreConfig
from utils import log_debug_payload

logger = logging.getLogger("ai_blog_server")

SHOPIFY_API_VERSION = "2025-01"
SHARED_GUIDE_NAMESPACE = "custom"
SHARED_GUIDE_TITLE_KEY = "ai_blog_related_guide_title"
SHARED_GUIDE_URL_KEY = "ai_blog_related_guide_url"
SHARED_GUIDE_EXCERPT_KEY = "ai_blog_related_guide_excerpt"


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
    """Return current articles across the store's blogs."""
    blogs = await fetch_blogs(store)
    token = await _get_token(store)
    articles: list[ShopifyArticle] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for blog in blogs:
            url = (
                f"{_base_url(store)}/blogs/{blog.id}/articles.json"
                f"?limit={limit_per_blog}&fields=id,blog_id,title,handle,body_html,summary_html,tags,image,published_at"
            )
            data = await _get(client, url, token)
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
    Upload an image (by URL or base64 data URI) to Shopify Files and return the
    public CDN URL. Passes the image as a URL fetch (src) for regular URLs or as
    a base64 attachment for data URIs produced by logo_service compositing.
    Returns None on failure so the caller can continue without images.
    """
    url = f"{_base_url(store)}/files.json"

    if image_url.startswith("data:"):
        # Extract base64 payload from data URI (data:<mime>;base64,<b64>)
        try:
            header, b64data = image_url.split(",", 1)
            content_type = header.split(";")[0].split(":")[1]
        except Exception:
            logger.warning("Malformed data URI passed to upload_image_to_shopify, skipping")
            return None
        payload = {
            "file": {
                "attachment": b64data,
                "filename": filename,
                "content_type": content_type,
            }
        }
    else:
        payload = {
            "file": {
                "src": image_url,
                "filename": filename,
                "content_type": "image/png",
            }
        }
    try:
        token = await _get_token(store)
        async with httpx.AsyncClient(timeout=60) as client:
            data = await _post(client, url, token, payload)
        file_obj = data.get("file", {})
        # Try several possible paths Shopify uses across API versions
        cdn_url = (
            file_obj.get("public_url")
            or file_obj.get("src")
            or (file_obj.get("image") or {}).get("src")
            or (file_obj.get("preview_image") or {}).get("image", {}).get("src")
        )
        if cdn_url:
            logger.debug("Uploaded image to Shopify CDN: %s", cdn_url)
            return cdn_url
        # Shopify sometimes processes async — fall back to original xAI URL
        logger.warning(
            "Shopify file upload returned no CDN URL (response: %s) — using source URL",
            str(file_obj)[:200],
        )
        return image_url
    except ShopifyError as exc:
        logger.warning("Image upload to Shopify failed (using source URL): %s", exc)
        return image_url


def _build_article_html(
    content: str,
    image_urls: list[str],
    keywords: list[str],
    hashtags: list[str],
) -> str:
    """
    Insert images into the HTML content, then append a visible tags section
    and a hidden SEO keyword div at the bottom.
    """
    if not image_urls:
        result = content
    else:
        def img_tag(url: str) -> str:
            return f'<img src="{url}" style="max-width:100%;height:auto;margin:24px 0;" />\n'

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

    # Visible tags section
    if keywords or hashtags:
        tags_html = '<div style="margin-top:40px;padding-top:24px;border-top:1px solid #e5e7eb;">'
        if keywords:
            kw_pills = "".join(
                f'<span style="display:inline-block;background:#eff6ff;color:#1d4ed8;'
                f'border-radius:6px;padding:4px 12px;font-size:13px;font-weight:500;'
                f'margin:4px 4px 4px 0;">{k}</span>'
                for k in keywords
            )
            tags_html += f'<div style="margin-bottom:10px;">{kw_pills}</div>'
        if hashtags:
            ht_pills = "".join(
                f'<span style="display:inline-block;background:#f0fdf4;color:#15803d;'
                f'border-radius:6px;padding:4px 12px;font-size:13px;font-weight:500;'
                f'margin:4px 4px 4px 0;">{t}</span>'
                for t in hashtags
            )
            tags_html += f'<div>{ht_pills}</div>'
        tags_html += '</div>'
        result += tags_html

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
    product_url: str = "",
    product_title: str = "",
) -> PublishResult:
    """
    Upload images, insert them into HTML, then publish the article to Shopify.
    """
    # Upload images to Shopify CDN
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

    # Embed all CDN images in the body (skip data URIs — Shopify may strip them).
    # The first image is ALSO set as the article's featured image below.
    cdn_urls_for_body = [u for u in image_cdn_urls if not u.startswith("data:")]
    body_html = _build_article_html(content_html, cdn_urls_for_body, keywords, hashtags)

    # Append keywords and hashtags as hidden text for SEO — no visible headings
    if keywords or hashtags:
        kw_html = ""
        if keywords:
            kw_items = " ".join(f"<span>{k}</span>" for k in keywords)
            kw_html += kw_items
        if hashtags:
            ht_items = " ".join(f"<span>{t}</span>" for t in hashtags)
            kw_html += " " + ht_items
        body_html += (
            f'<div style="font-size:1px;color:transparent;line-height:1;'
            f'overflow:hidden;height:1px;" aria-hidden="true">{kw_html}</div>'
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

    # Featured image — use the first uploaded CDN image if available
    if image_cdn_urls:
        first = image_cdn_urls[0]
        if first.startswith("data:"):
            try:
                _, b64data = first.split(",", 1)
                article["image"] = {"attachment": b64data, "filename": "featured_image.jpg"}
            except Exception:
                pass  # skip featured image if data URI is malformed
        else:
            article["image"] = {"src": first}

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

    token = await _get_token(store)
    metafields = [
        {
            "ownerId": f"gid://shopify/Product/{product_id}",
            "namespace": SHARED_GUIDE_NAMESPACE,
            "key": SHARED_GUIDE_TITLE_KEY,
            "type": "single_line_text_field",
            "value": guide_title,
        },
        {
            "ownerId": f"gid://shopify/Product/{product_id}",
            "namespace": SHARED_GUIDE_NAMESPACE,
            "key": SHARED_GUIDE_URL_KEY,
            "type": "url",
            "value": guide_url,
        },
        {
            "ownerId": f"gid://shopify/Product/{product_id}",
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
