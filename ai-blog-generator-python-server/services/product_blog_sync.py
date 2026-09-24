"""Audit and safely reconcile Shopify products with their product-guide articles."""
from __future__ import annotations

import html
import re
from collections import defaultdict
from collections.abc import Callable
from urllib.parse import unquote, urlparse

import aiosqlite

import shopify_client
from config import StoreConfig
from db.base import get_db_path


ProgressCallback = Callable[[str, str, int, int], None]

_HREF_RE = re.compile(r"href\s*=\s*(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL)
_ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<label>.*?)</a>", re.IGNORECASE | re.DOTALL)
_RELATED_GUIDE_RE = re.compile(
    r"<p\b[^>]*>\s*<strong\b[^>]*>\s*Related\s+Guide:\s*</strong>.*?</p>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_PRODUCT_URL_RE = re.compile(
    r"Product URL:\s*https?://[^\s]+/products/([a-z0-9][a-z0-9_-]*)",
    re.IGNORECASE,
)


def _path(value: str) -> str:
    raw = html.unescape(str(value or "")).strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urlparse(raw)
    path = parsed.path
    if not path.startswith("/"):
        for marker in ("/blogs/", "/products/"):
            marker_index = raw.find(marker)
            if marker_index >= 0:
                path = raw[marker_index:]
                break
    return unquote(path).rstrip("/").lower()


def _href_paths(markup: str) -> set[str]:
    return {
        path
        for _, href in _HREF_RE.findall(markup or "")
        if (path := _path(href))
    }


def _managed_guide_paths(markup: str) -> set[str]:
    paths: set[str] = set()
    for paragraph in _RELATED_GUIDE_RE.findall(markup or ""):
        paths.update(_href_paths(paragraph))
    return paths


def _product_handle_from_path(path: str) -> str:
    match = re.fullmatch(r"/products/([^/]+)", path)
    return match.group(1) if match else ""


def _article_cta_handles(article_html: str) -> set[str]:
    """Read only the generator's 'Shop …' CTA, not related-product links."""
    handles: set[str] = set()
    for anchor in _ANCHOR_RE.finditer(article_html or ""):
        href_match = _HREF_RE.search(anchor.group("attrs"))
        if not href_match:
            continue
        handle = _product_handle_from_path(_path(href_match.group(2)))
        label = html.unescape(_TAG_RE.sub(" ", anchor.group("label")))
        label = re.sub(r"\s+", " ", label).strip().lower()
        if handle and (label == "shop this product" or label.startswith("shop ")):
            handles.add(handle)
    return handles


def _plain_text(markup: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", markup or ""))
    return re.sub(r"\s+", " ", text).strip()


async def _generation_provenance(store_id: str, blog_handle: str) -> dict[str, set[str]]:
    """Map article URL paths to product handles recorded at publication time."""
    result: dict[str, set[str]] = defaultdict(set)
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT article_id, article_url, prompt_text FROM generations "
            "WHERE store_id=? AND blog_handle=? AND status='published' "
            "AND article_url IS NOT NULL ORDER BY created_at DESC, id DESC",
            (store_id, blog_handle),
        ) as cursor:
            rows = await cursor.fetchall()
    for row in rows:
        article_path = _path(row["article_url"] or "")
        match = _PRODUCT_URL_RE.search(row["prompt_text"] or "")
        if match:
            product_handle = match.group(1).lower()
            if article_path:
                result[article_path].add(product_handle)
            article_id = str(row["article_id"] or "").strip()
            if article_id:
                result[f"id:{article_id}"].add(product_handle)
    return dict(result)


def _metafield_value(product: dict, key: str) -> str:
    raw = product.get(key) or {}
    return str(raw.get("value") or "").strip() if isinstance(raw, dict) else ""


def _storefront_base(store: StoreConfig) -> str:
    domain = (store.custom_domain or store.myshopify_domain).strip().rstrip("/")
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    return domain


async def reconcile_product_blogs(
    store: StoreConfig,
    blog_handle: str = "inside-the-products",
    *,
    repair: bool = True,
    progress: ProgressCallback | None = None,
) -> dict:
    """Scan live Shopify data and repair only deterministic associations.

    A match is authoritative when either the generation history names the exact
    product URL for the article or the article contains one unambiguous generated
    ``Shop …`` CTA. Titles alone are never used to guess an association.
    """
    def report(stage: str, message: str, current: int = 0, total: int = 0) -> None:
        if progress:
            progress(stage, message, current, total)

    report("fetching", "Reading the live Shopify product catalogue and product blog.")
    products, articles, provenance = await _load_sources(store, blog_handle)
    total = len(products)
    report("matching", f"Matching {total} products against {len(articles)} current articles.", 0, total)

    articles_by_path = {_path(article.article_url): article for article in articles}
    article_claims: dict[int, set[str]] = {}
    article_sources: dict[int, str] = {}
    article_conflicts: dict[int, list[str]] = {}
    claims_by_product: dict[str, list[shopify_client.ShopifyArticle]] = defaultdict(list)

    for article in articles:
        article_path = _path(article.article_url)
        history_handles = (
            provenance.get(article_path, set())
            | provenance.get(f"id:{article.id}", set())
        )
        cta_handles = _article_cta_handles(article.body_html)
        conflicts: list[str] = []
        if len(history_handles) == 1:
            claims = set(history_handles)
            article_sources[article.id] = "generation history"
            unexpected_ctas = cta_handles - claims
            if unexpected_ctas:
                conflicts.append(
                    "Article generation history and its Shop CTA disagree: "
                    + ", ".join(sorted(unexpected_ctas))
                )
        elif len(history_handles) > 1:
            claims = set()
            article_sources[article.id] = "ambiguous generation history"
            conflicts.append("More than one product was recorded for this article in generation history.")
        elif len(cta_handles) == 1:
            claims = set(cta_handles)
            article_sources[article.id] = "article Shop CTA"
        elif len(cta_handles) > 1:
            claims = set()
            article_sources[article.id] = "ambiguous article CTAs"
            conflicts.append("Article contains Shop CTAs for more than one product.")
        else:
            claims = set()
            article_sources[article.id] = "unverified"

        article_claims[article.id] = claims
        article_conflicts[article.id] = conflicts
        for handle in claims:
            claims_by_product[handle].append(article)

    product_handles = {str(product.get("handle") or "").lower() for product in products}
    storefront = _storefront_base(store)
    results: list[dict] = []

    for index, product in enumerate(products, start=1):
        handle = str(product.get("handle") or "").strip().lower()
        title = str(product.get("title") or handle).strip()
        guide_url = _metafield_value(product, "guideUrl")
        guide_title = _metafield_value(product, "guideTitle")
        guide_excerpt = _metafield_value(product, "guideExcerpt")
        description_html = str(product.get("descriptionHtml") or "")
        description_paths = _href_paths(description_html)
        description_guide_paths = {
            path
            for path in description_paths
            if path.startswith(f"/blogs/{blog_handle.lower()}/")
        }
        managed_guide_paths = _managed_guide_paths(description_html)
        claimed = claims_by_product.get(handle, [])
        issues: list[str] = []
        repairs: list[str] = []
        errors: list[str] = []
        unresolved_issues: list[str] = []
        article: shopify_client.ShopifyArticle | None = None
        status = "matched"

        report("checking", f"Checking {title}", index, total)

        if len(claimed) > 1:
            status = "duplicate"
            issues.append(
                f"{len(claimed)} articles claim this product: "
                + ", ".join(article.article_url for article in claimed)
            )
        elif len(claimed) == 0:
            guide_article = articles_by_path.get(_path(guide_url)) if guide_url else None
            status = "mismatch" if guide_article else "missing"
            if guide_url and not guide_article:
                issues.append(f"Product guide URL does not resolve to an article in {blog_handle}: {guide_url}")
            elif guide_article:
                issues.append(
                    "The product points to an article, but that article cannot be verified as this "
                    "product's guide from generation history or its Shop CTA. No guess was made."
                )
            else:
                issues.append(f"No matching article exists in the {blog_handle} blog.")
        else:
            article = claimed[0]
            expected_path = _path(article.article_url)
            conflicts = article_conflicts.get(article.id, [])
            if conflicts:
                status = "mismatch"
                issues.extend(conflicts)
            else:
                if _path(guide_url) != expected_path:
                    issues.append("Product guide URL was missing, broken, or pointed to a different article.")
                if guide_title != article.title:
                    issues.append("Product guide title did not match the live article title.")
                stale_description_paths = description_guide_paths - {expected_path}
                stale_managed_paths = managed_guide_paths - {expected_path}
                stale_unmanaged_paths = stale_description_paths - stale_managed_paths
                if expected_path not in description_paths:
                    issues.append("Product description did not link to the matched guide.")
                if stale_description_paths:
                    stale_message = (
                        "Product description also contained stale product-guide link(s): "
                        + ", ".join(sorted(stale_description_paths))
                    )
                    issues.append(stale_message)
                    if stale_unmanaged_paths:
                        unresolved_issues.append(
                            stale_message
                            + ". These were not labelled as app-managed Related Guide links, so they were not removed."
                        )

                target_ctas = _article_cta_handles(article.body_html)
                if handle not in target_ctas:
                    issues.append("Matched article was missing its product Shop link.")

                if repair:
                    expected_excerpt = _plain_text(article.summary_html) or _plain_text(article.body_html)[:500]
                    if (
                        _path(guide_url) != expected_path
                        or guide_title != article.title
                        or guide_excerpt != expected_excerpt
                    ):
                        try:
                            await shopify_client.set_related_product_guide_metafields_by_id(
                                store,
                                str(product.get("id") or product.get("legacyResourceId") or ""),
                                article.title,
                                article.article_url,
                                expected_excerpt,
                            )
                            repairs.append("updated related-guide metafields")
                        except Exception as exc:  # noqa: BLE001 - report per product and continue
                            errors.append(f"Metafield repair failed: {type(exc).__name__}: {exc}")

                    if expected_path not in description_paths or stale_managed_paths:
                        try:
                            action = await shopify_client.sync_product_description_guide_link(
                                store,
                                str(product.get("legacyResourceId") or product.get("id") or ""),
                                handle,
                                description_html,
                                article.title,
                                article.article_url,
                            )
                            repairs.append(action)
                        except Exception as exc:  # noqa: BLE001 - report per product and continue
                            errors.append(f"Description repair failed: {type(exc).__name__}: {exc}")

                    if handle not in target_ctas and article_sources.get(article.id) == "generation history":
                        try:
                            product_url = f"{storefront}/products/{handle}"
                            await shopify_client.append_product_link_to_article(
                                store, article, title, product_url
                            )
                            repairs.append("restored article-to-product Shop link")
                        except Exception as exc:  # noqa: BLE001 - report per product and continue
                            errors.append(f"Article link repair failed: {type(exc).__name__}: {exc}")

                if errors:
                    status = "error"
                elif unresolved_issues:
                    status = "mismatch"
                elif repairs:
                    status = "repaired"
                elif issues:
                    status = "mismatch"

        results.append(
            {
                "product_id": str(product.get("id") or ""),
                "product_title": title,
                "product_handle": handle,
                "product_url": f"{storefront}/products/{handle}",
                "status": status,
                "article_title": article.title if article else "",
                "article_url": article.article_url if article else guide_url,
                "match_source": article_sources.get(article.id, "") if article else "",
                "issues": issues,
                "unresolved_issues": unresolved_issues,
                "repairs": repairs,
                "errors": errors,
            }
        )

    claimed_article_ids = {article.id for article_list in claims_by_product.values() for article in article_list}
    orphans = []
    for article in articles:
        claims = article_claims.get(article.id, set())
        if article.id not in claimed_article_ids or any(handle not in product_handles for handle in claims):
            orphans.append(
                {
                    "article_title": article.title,
                    "article_url": article.article_url,
                    "reason": "; ".join(article_conflicts.get(article.id, []))
                    or "No current Shopify product could be verified for this article.",
                }
            )

    counts = {
        key: sum(1 for item in results if item["status"] == key)
        for key in ("matched", "repaired", "missing", "mismatch", "duplicate", "error")
    }
    counts.update({"products": len(products), "articles": len(articles), "orphans": len(orphans)})
    report("complete", "Product-blog sanity check complete.", total, total)
    return {
        "ok": True,
        "blog_handle": blog_handle,
        "repair_mode": repair,
        "counts": counts,
        "products": results,
        "orphans": orphans,
        "message": (
            f"Checked {len(products)} products and {len(articles)} articles: "
            f"{counts['matched']} matched, {counts['repaired']} repaired, "
            f"{counts['missing']} missing, {counts['mismatch']} mismatched, "
            f"{counts['duplicate']} duplicate and {counts['error']} repair errors."
        ),
    }


async def _load_sources(
    store: StoreConfig,
    blog_handle: str,
) -> tuple[list[dict], list[shopify_client.ShopifyArticle], dict[str, set[str]]]:
    products = await shopify_client.fetch_product_blog_sync_products(store, limit=250)
    articles = await shopify_client.fetch_blog_articles(store, blog_handle, limit=250)
    provenance = await _generation_provenance(store.id, blog_handle)
    return products, articles, provenance
