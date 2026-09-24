"""Shopify product and published-content audits for SEO Growth."""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

import shopify_client
from config import StoreConfig
from services.content_claims import find_claims, text_with_link_targets
from .managed_content import RULE_KEY, has_managed_keyword_blocks


_SEO_LABEL = re.compile(r"\b(?:seo\s+)?(?:keywords?|hashtags?)\s*:\s*", re.IGNORECASE)
_HASHTAG_RUN = re.compile(r"(?:#[A-Za-z][\w-]*\s*){4,}")
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
    duplicate_pairs: list[tuple[int, int, float]] = []
    managed_block_articles: list[shopify_client.ShopifyArticle] = []
    unmanaged_keyword_articles: list[shopify_client.ShopifyArticle] = []
    claim_articles: list[tuple[shopify_client.ShopifyArticle, list[Any]]] = []
    for article in articles:
        soup = BeautifulSoup(article.body_html or "", "html.parser")
        text = soup.get_text(" ", strip=True)
        page = article.article_url
        if has_managed_keyword_blocks(article.body_html or ""):
            managed_block_articles.append(article)
        elif _SEO_LABEL.search(text) or _HASHTAG_RUN.search(text):
            unmanaged_keyword_articles.append(article)
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
        claim_findings = [
            finding for finding in find_claims(text_with_link_targets(article.body_html or ""))
            if finding.kind in {"medical_claim", "clinical_claim"} or not finding.cited
        ]
        if claim_findings:
            claim_articles.append((article, claim_findings))

    for left_index, left in enumerate(articles):
        for right_index in range(left_index + 1, len(articles)):
            right = articles[right_index]
            similarity = _similarity(left.title, right.title)
            if similarity < 0.72:
                continue
            duplicate_pairs.append((left_index, right_index, similarity))

    # Collapse pairwise matches into connected title families. One repetitive
    # family should produce one useful opportunity, not dozens of cards.
    adjacency: dict[int, set[int]] = {}
    pair_similarity: dict[tuple[int, int], float] = {}
    for left_index, right_index, similarity in duplicate_pairs:
        adjacency.setdefault(left_index, set()).add(right_index)
        adjacency.setdefault(right_index, set()).add(left_index)
        pair_similarity[(left_index, right_index)] = similarity

    overlap_groups: list[list[int]] = []
    visited: set[int] = set()
    for start in sorted(adjacency):
        if start in visited:
            continue
        stack = [start]
        group: list[int] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            group.append(current)
            stack.extend(adjacency.get(current, set()) - visited)
        if len(group) > 1:
            overlap_groups.append(sorted(group))

    for group in overlap_groups:
        group_articles = [articles[index] for index in group]
        urls = [article.article_url for article in group_articles]
        titles = [article.title for article in group_articles]
        similarities = [
            similarity for (left_index, right_index), similarity in pair_similarity.items()
            if left_index in group and right_index in group
        ]
        max_similarity = max(similarities, default=0.0)
        sample = "; ".join(f"‘{title}’" for title in titles[:4])
        if len(titles) > 4:
            sample += f"; and {len(titles) - 4} more"
        group_ids = ":".join(str(article.id) for article in group_articles)
        issues.append({
            "key": f"article-overlap-group:{group_ids}", "category": "cannibalisation",
            "severity": "medium", "score": 72,
            "title": f"Review {len(group_articles)} articles with overlapping title intent",
            "evidence": f"The related title group reaches {max_similarity:.0%} term overlap: {sample}.",
            "action": "Use Search Console query and conversion data to decide whether each page serves a distinct intent. Consolidate only where the evidence supports it; do not redirect automatically.",
            "page_url": urls[0], "source": "shopify",
            "metrics": {"urls": urls, "titles": titles, "max_similarity": round(max_similarity, 3)},
        })

    claim_groups = [
        (
            "research",
            "Verify unsupported research wording",
            "Add a directly supporting primary-source URL beside each accurate statement, or soften/remove the statement. Never invent a citation.",
            lambda finding: finding.kind == "research_claim" and not finding.cited,
        ),
        (
            "medical",
            "Review potential product or routine medical claims",
            "Have a human verify the exact wording and soften or remove unsupported diagnosis, treatment, cure or prevention claims.",
            lambda finding: finding.kind in {"medical_claim", "clinical_claim"},
        ),
    ]
    for group_key, title, action, predicate in claim_groups:
        affected_pages: list[dict[str, Any]] = []
        for article, findings in claim_articles:
            matching = [finding for finding in findings if predicate(finding)]
            if not matching:
                continue
            affected_pages.append({
                "article_id": article.id,
                "title": article.title,
                "url": article.article_url,
                "findings": [finding.as_dict() for finding in matching[:10]],
            })
        if not affected_pages:
            continue
        samples: list[str] = []
        for page_data in affected_pages[:3]:
            first_finding = page_data["findings"][0]
            samples.append(f"‘{page_data['title']}’: “{first_finding['excerpt']}”")
        issues.append({
            "key": f"article-claim-group:{group_key}", "category": "quality",
            "severity": "high", "score": 88 if group_key == "research" else 90,
            "title": f"{title} in {len(affected_pages):,} articles",
            "evidence": (
                f"The audit found exact sentence-level matches in {len(affected_pages):,} published articles. "
                f"Examples: {'; '.join(samples)}."
            ),
            "action": action,
            "page_url": affected_pages[0]["url"], "source": "shopify",
            "metrics": {
                "affected_count": len(affected_pages),
                "affected_pages": affected_pages,
            },
        })

    if managed_block_articles:
        count = len(managed_block_articles)
        issues.append({
            "key": f"safe-repair:{RULE_KEY}", "kind": "safe_repair",
            "category": "quality", "severity": "high", "score": 96,
            "title": f"Remove app-generated SEO blocks from {count:,} articles",
            "evidence": (
                f"{count:,} published articles contain the exact visible or hidden "
                "keyword-block signature previously emitted by this app."
            ),
            "action": (
                "Use Safe SEO repair to preview, back up and remove these exact "
                "app-owned blocks. No merchant-authored article text is targeted."
            ),
            "page_url": managed_block_articles[0].article_url,
            "source": "shopify",
            "metrics": {
                "affected_count": count,
                "autofix_rule": RULE_KEY,
                "sample_urls": [item.article_url for item in managed_block_articles[:10]],
            },
        })

    if unmanaged_keyword_articles:
        count = len(unmanaged_keyword_articles)
        issues.append({
            "key": "article-unmanaged-keyword-blocks", "category": "quality",
            "severity": "medium", "score": 70,
            "title": f"Review possible keyword blocks in {count:,} other articles",
            "evidence": (
                f"{count:,} articles contain a Keywords/Hashtags label or a run of "
                "four or more hashtags, but not the app's exact managed signature."
            ),
            "action": (
                "Review these pages manually. They are deliberately excluded from "
                "automatic repair because their ownership and intent are uncertain."
            ),
            "page_url": unmanaged_keyword_articles[0].article_url,
            "source": "shopify",
            "metrics": {
                "affected_count": count,
                "sample_urls": [item.article_url for item in unmanaged_keyword_articles[:10]],
            },
        })
    return {
        "articles": len(articles),
        "with_featured_images": sum(bool(article.image_url) for article in articles),
        "visible_keyword_blocks": len(managed_block_articles) + len(unmanaged_keyword_articles),
        "managed_keyword_blocks": len(managed_block_articles),
        "unmanaged_keyword_blocks": len(unmanaged_keyword_articles),
        "health_claim_warnings": len(claim_articles),
        "overlap_groups": len(overlap_groups),
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

    duplicate_groups: list[tuple[list[dict[str, Any]], bool, float]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for index, left in enumerate(active):
        left_words = _words(str(left.get("title") or ""))
        for right in active[index + 1:]:
            right_words = _words(str(right.get("title") or ""))
            containment = (
                len(left_words & right_words) / min(len(left_words), len(right_words))
                if left_words and right_words else 0
            )
            jaccard = _similarity(str(left.get("title") or ""), str(right.get("title") or ""))
            exact = bool(left_words) and left_words == right_words
            if not exact and (containment < 0.90 or jaccard < 0.75 or len(left_words & right_words) < 4):
                continue
            pair = tuple(sorted((str(left.get("id") or ""), str(right.get("id") or ""))))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            duplicate_groups.append(([left, right], exact, jaccard))
    for group, exact, similarity in duplicate_groups:
        titles = [str(item.get("title") or "") for item in group]
        urls = [str(item.get("onlineStoreUrl") or f"{storefront}/products/{item.get('handle', '')}") for item in group]
        issues.append({
            "key": "product-duplicate:" + ":".join(sorted(str(item.get("legacyResourceId") or item.get("id")) for item in group)),
            "category": "cannibalisation", "severity": "high" if exact else "medium",
            "score": 90 if exact else 66,
            "title": "Resolve duplicate product titles" if exact else "Review two unusually similar product titles",
            "evidence": (
                "The active products have the same significant title terms: " if exact
                else f"The active product titles have {similarity:.0%} term overlap: "
            ) + "; ".join(titles),
            "action": "Check Search Console demand and confirm the products are genuinely distinct before changing titles. No automatic rename or redirect will be made.",
            "page_url": urls[0], "source": "shopify", "metrics": {"urls": urls, "similarity": round(similarity, 3)},
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
    articles = await shopify_client.fetch_store_articles(store, limit_per_blog=0)
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
