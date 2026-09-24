"""Shopify product and published-content audits for SEO Growth."""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

import shopify_client
from config import StoreConfig


_SEO_LABEL = re.compile(r"\b(?:seo\s+)?(?:keywords?|hashtags?)\s*:\s*", re.IGNORECASE)
_HASHTAG_RUN = re.compile(r"(?:#[A-Za-z][\w-]*\s*){4,}")
_HEALTH_CLAIM = re.compile(
    r"\b(?:treats?|cures?|prevents?|clinically proven|study (?:shows?|found)|research (?:shows?|proves?))\b",
    re.IGNORECASE,
)
_CITATION = re.compile(r"https?://|doi\.org|pubmed|\[[0-9]+\]", re.IGNORECASE)


def _words(value: str) -> set[str]:
    return {
        word for word in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(word) > 2 and word not in {"the", "and", "for", "with", "from"}
    }


def _similarity(left: str, right: str) -> float:
    a, b = _words(left), _words(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def audit_articles(articles: list[shopify_client.ShopifyArticle]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    duplicate_pairs: set[tuple[int, int]] = set()
    for article in articles:
        soup = BeautifulSoup(article.body_html or "", "html.parser")
        text = soup.get_text(" ", strip=True)
        page = article.article_url
        if _SEO_LABEL.search(text) or _HASHTAG_RUN.search(text):
            issues.append({
                "key": f"article-keyword-dump:{article.id}", "category": "quality",
                "severity": "high", "score": 92,
                "title": f"Remove visible SEO keyword blocks from ‘{article.title}’",
                "evidence": "The published article contains a visible Keywords/Hashtags label or a run of four or more hashtags.",
                "action": "Remove the public keyword/hashtag block while retaining planning terms inside the app. Review the live page after publishing.",
                "page_url": page, "source": "shopify", "metrics": {"article_id": article.id},
            })
        if not article.image_url and not soup.find("img"):
            issues.append({
                "key": f"article-missing-image:{article.id}", "category": "content",
                "severity": "medium", "score": 65,
                "title": f"Add a useful image to ‘{article.title}’",
                "evidence": "No featured image or inline image is present on the published Shopify article.",
                "action": "Add an original, product-relevant image with concise descriptive alt text, then verify it renders on Shopify.",
                "page_url": page, "source": "shopify", "metrics": {"article_id": article.id},
            })
        word_count = len(re.findall(r"\b\w+\b", text))
        if word_count < 350:
            issues.append({
                "key": f"article-thin:{article.id}", "category": "content",
                "severity": "low", "score": 40,
                "title": f"Review thin article ‘{article.title}’",
                "evidence": f"The live article contains approximately {word_count} words.",
                "action": "Expand it only if Search Console shows demand; otherwise merge it into a stronger related guide or leave it unchanged.",
                "page_url": page, "source": "shopify", "metrics": {"word_count": word_count},
            })
        if _HEALTH_CLAIM.search(text) and not _CITATION.search(article.body_html or ""):
            issues.append({
                "key": f"article-health-citations:{article.id}", "category": "quality",
                "severity": "high", "score": 88,
                "title": f"Verify health claims in ‘{article.title}’",
                "evidence": "Health or research language was detected, but no visible URL, DOI or numbered citation was found.",
                "action": "Have a human verify each claim, link to primary evidence and soften or remove unsupported therapeutic wording.",
                "page_url": page, "source": "shopify", "metrics": {"article_id": article.id},
            })

    for index, left in enumerate(articles):
        for right in articles[index + 1:]:
            similarity = _similarity(left.title, right.title)
            if similarity < 0.72:
                continue
            pair = tuple(sorted((left.id, right.id)))
            if pair in duplicate_pairs:
                continue
            duplicate_pairs.add(pair)
            issues.append({
                "key": f"article-overlap:{pair[0]}:{pair[1]}", "category": "cannibalisation",
                "severity": "medium", "score": 72,
                "title": "Review two articles with strongly overlapping titles",
                "evidence": f"‘{left.title}’ and ‘{right.title}’ have {similarity:.0%} title-term overlap.",
                "action": "Use Search Console query data to decide whether to differentiate their intent or consolidate into the stronger URL. Do not redirect automatically.",
                "page_url": left.article_url, "source": "shopify",
                "metrics": {"similarity": round(similarity, 3), "other_url": right.article_url},
            })
    return {
        "articles": len(articles),
        "with_featured_images": sum(bool(article.image_url) for article in articles),
        "visible_keyword_blocks": sum(issue["key"].startswith("article-keyword-dump:") for issue in issues),
        "health_claim_warnings": sum(issue["key"].startswith("article-health-citations:") for issue in issues),
        "overlap_groups": len(duplicate_pairs),
    }, issues


async def _products(store: StoreConfig) -> list[dict[str, Any]]:
    query = """
      query SeoGrowthProducts {
        products(first: 250, sortKey: TITLE) {
          nodes {
            id legacyResourceId title handle status vendor productType description totalInventory
            onlineStoreUrl seo { title description }
            featuredImage { url altText }
            variants(first: 100) { nodes { sku barcode price availableForSale } }
          }
        }
      }
    """
    data = await shopify_client.graphql_request(store, query)
    return list(((data.get("products") or {}).get("nodes") or []))


def audit_products(products: list[dict[str, Any]], storefront: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    active = [item for item in products if item.get("status") == "ACTIVE"]
    for product in active:
        handle = str(product.get("handle") or "")
        page = str(product.get("onlineStoreUrl") or f"{storefront}/products/{handle}")
        title = str(product.get("title") or handle)
        variants = list((product.get("variants") or {}).get("nodes") or [])
        seo = product.get("seo") or {}
        missing: list[str] = []
        if not str(product.get("vendor") or "").strip():
            missing.append("brand/vendor")
        if not str(product.get("productType") or "").strip():
            missing.append("product type")
        if not product.get("featuredImage"):
            missing.append("featured image")
        if not str(product.get("description") or "").strip():
            missing.append("description")
        if not any(str(v.get("barcode") or "").strip() for v in variants):
            missing.append("GTIN/barcode")
        if missing:
            severity = "high" if "featured image" in missing or "description" in missing else "medium"
            issues.append({
                "key": f"merchant-fields:{product.get('legacyResourceId') or product.get('id')}",
                "category": "merchant", "severity": severity,
                "score": 86 if severity == "high" else 68,
                "title": f"Complete Google product data for ‘{title}’",
                "evidence": "Missing: " + ", ".join(missing) + ".",
                "action": "Complete the genuine product identifiers and merchant fields in Shopify, then check Google Merchant Center diagnostics. Never invent a GTIN.",
                "page_url": page, "source": "shopify", "metrics": {"missing": missing},
            })
        if not str(seo.get("description") or "").strip() and str(product.get("description") or "").strip():
            issues.append({
                "key": f"product-seo-fallback:{product.get('legacyResourceId') or product.get('id')}",
                "category": "metadata", "severity": "low", "score": 38,
                "title": f"Consider a custom search description for ‘{title}’",
                "evidence": "Shopify can fall back to the product description, but no explicit SEO description is set.",
                "action": "Only replace the fallback if a concise, accurate search snippet would improve the result. This is not treated as missing content.",
                "page_url": page, "source": "shopify", "metrics": {"automatic_fallback": True},
            })

    duplicate_groups: list[list[dict[str, Any]]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for index, left in enumerate(active):
        left_words = _words(str(left.get("title") or ""))
        for right in active[index + 1:]:
            right_words = _words(str(right.get("title") or ""))
            containment = (
                len(left_words & right_words) / min(len(left_words), len(right_words))
                if left_words and right_words else 0
            )
            if containment < 0.72 or len(left_words & right_words) < 3:
                continue
            pair = tuple(sorted((str(left.get("id") or ""), str(right.get("id") or ""))))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            duplicate_groups.append([left, right])
    for group in duplicate_groups:
        titles = [str(item.get("title") or "") for item in group]
        urls = [str(item.get("onlineStoreUrl") or f"{storefront}/products/{item.get('handle', '')}") for item in group]
        issues.append({
            "key": "product-duplicate:" + ":".join(sorted(str(item.get("legacyResourceId") or item.get("id")) for item in group)),
            "category": "cannibalisation", "severity": "high", "score": 90,
            "title": "Differentiate products with effectively identical titles",
            "evidence": f"{len(group)} active products share most of the shorter title's significant terms: " + "; ".join(titles),
            "action": "Confirm search demand first, then give each variant a distinct customer intent or consolidate it. No automatic redirects will be made.",
            "page_url": urls[0], "source": "shopify", "metrics": {"urls": urls},
        })
    return {
        "products": len(products), "active_products": len(active),
        "custom_seo_descriptions": sum(bool(str((p.get("seo") or {}).get("description") or "").strip()) for p in active),
        "automatic_seo_fallbacks": sum(
            not str((p.get("seo") or {}).get("description") or "").strip()
            and bool(str(p.get("description") or "").strip()) for p in active
        ),
        "missing_gtins": sum(
            not any(str(v.get("barcode") or "").strip() for v in (p.get("variants") or {}).get("nodes", []))
            for p in active
        ),
        "duplicate_title_groups": len(duplicate_groups),
    }, issues


async def collect_shopify_audit(store: StoreConfig) -> dict[str, Any]:
    products = await _products(store)
    articles = await shopify_client.fetch_store_articles(store, limit_per_blog=250)
    storefront = (store.custom_domain or store.myshopify_domain).strip().rstrip("/")
    if not storefront.startswith(("https://", "http://")):
        storefront = f"https://{storefront}"
    product_summary, product_issues = audit_products(products, storefront)
    article_summary, article_issues = audit_articles(articles)
    return {
        "products": product_summary,
        "articles": article_summary,
        "issues": product_issues + article_issues,
    }
