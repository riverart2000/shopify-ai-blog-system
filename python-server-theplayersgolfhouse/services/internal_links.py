"""services/internal_links.py — Pick 3-4 relevant internal links (other blog
posts + store products) to embed at the end of a generated article.

Design (locked):
  • Blog candidates come from local `generations` history first, then fall back
    to a live Shopify article fetch.
  • Product candidates are always fetched live from Shopify.
  • Candidates are scored by keyword/title token overlap with the current post,
    the current post is excluded, and the result is a 3-4 link mix that prefers
    at least one product and one blog.
  • Only validated internal URLs are returned, so nothing hallucinated can leak
    into the published HTML.

All network calls soft-fail: any error returns whatever links were gathered so
publishing is never blocked.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import escape
from typing import Iterable

import db
import shopify_client
from config import StoreConfig

logger = logging.getLogger("ai_blog_server")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "by", "with", "from", "as", "is", "are", "be", "your", "you", "our", "how",
    "what", "why", "when", "best", "top", "guide", "tips", "ways", "this", "that",
    "it", "its", "can", "do", "does", "vs", "into", "about", "more", "most",
}
_MIN_TOKEN_LEN = 3


@dataclass
class InternalLink:
    url: str
    title: str
    kind: str  # "blog" | "product"


def _tokenize(*parts: object) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple, set)):
            text = " ".join(str(p) for p in part)
        else:
            text = str(part)
        for tok in _TOKEN_RE.findall(text.lower()):
            if len(tok) >= _MIN_TOKEN_LEN and tok not in _STOPWORDS:
                tokens.add(tok)
    return tokens


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


async def _blog_candidates(
    store: StoreConfig,
    store_id: str,
    current_url: str,
    current_title: str,
) -> list[tuple[InternalLink, set[str]]]:
    """Local generations first, live Shopify articles as fallback. Deduped by URL."""
    current_url_n = _normalize_url(current_url)
    current_title_n = (current_title or "").strip().lower()
    seen: set[str] = set()
    out: list[tuple[InternalLink, set[str]]] = []

    try:
        rows = await db.get_recent_generations(store_id, limit=100)
    except Exception as exc:  # noqa: BLE001
        logger.warning("internal_links: get_recent_generations failed: %s", exc)
        rows = []

    for row in rows:
        if (row.get("status") or "") != "published":
            continue
        url = (row.get("article_url") or "").strip()
        title = (row.get("title") or "").strip()
        if not url or not title:
            continue
        url_n = _normalize_url(url)
        if url_n == current_url_n or title.lower() == current_title_n:
            continue
        if url_n in seen:
            continue
        seen.add(url_n)
        tokens = _tokenize(title, row.get("keywords"), row.get("hashtags"))
        out.append((InternalLink(url=url, title=title, kind="blog"), tokens))

    # Fallback / supplement: live Shopify articles (only if we have few locally).
    if len(out) < 6:
        try:
            articles = await shopify_client.fetch_store_articles(store, limit_per_blog=50)
        except Exception as exc:  # noqa: BLE001
            logger.warning("internal_links: fetch_store_articles failed: %s", exc)
            articles = []
        for art in articles:
            url = (art.article_url or "").strip()
            title = (art.title or "").strip()
            if not url or not title:
                continue
            url_n = _normalize_url(url)
            if url_n == current_url_n or title.lower() == current_title_n:
                continue
            if url_n in seen:
                continue
            seen.add(url_n)
            tokens = _tokenize(title, art.tags)
            out.append((InternalLink(url=url, title=title, kind="blog"), tokens))

    return out


async def _product_candidates(
    store: StoreConfig,
) -> list[tuple[InternalLink, set[str]]]:
    try:
        products = await shopify_client.fetch_products(store, limit=250)
    except Exception as exc:  # noqa: BLE001
        logger.warning("internal_links: fetch_products failed: %s", exc)
        return []
    out: list[tuple[InternalLink, set[str]]] = []
    seen: set[str] = set()
    for p in products:
        url = (p.url or "").strip()
        title = (p.title or "").strip()
        if not url or not title:
            continue
        url_n = _normalize_url(url)
        if url_n in seen:
            continue
        seen.add(url_n)
        out.append((InternalLink(url=url, title=title, kind="product"), _tokenize(title)))
    return out


def _rank(
    candidates: list[tuple[InternalLink, set[str]]],
    query_tokens: set[str],
) -> list[InternalLink]:
    scored = []
    for idx, (link, tokens) in enumerate(candidates):
        score = len(tokens & query_tokens)
        scored.append((score, idx, link))
    # Higher score first; preserve original order (recency) on ties.
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [link for _score, _idx, link in scored]


async def build_internal_links(
    store: StoreConfig,
    store_id: str,
    *,
    title: str,
    keywords: Iterable[str] = (),
    long_tail_keywords: Iterable[str] = (),
    current_url: str = "",
    max_links: int = 4,
) -> list[InternalLink]:
    """Return up to ``max_links`` internal links (mix of blogs + products),
    ranked by relevance to the current post. Prefers at least one of each kind.
    """
    query_tokens = _tokenize(title, list(keywords), list(long_tail_keywords))

    blogs = _rank(await _blog_candidates(store, store_id, current_url, title), query_tokens)
    products = _rank(await _product_candidates(store), query_tokens)

    if not blogs and not products:
        return []

    selected: list[InternalLink] = []
    seen_urls: set[str] = set()

    def _take(link: InternalLink) -> bool:
        url_n = _normalize_url(link.url)
        if url_n in seen_urls:
            return False
        seen_urls.add(url_n)
        selected.append(link)
        return True

    # Guarantee at least one product and one blog when available.
    if products:
        _take(products[0])
    if blogs:
        _take(blogs[0])

    # Fill the rest, alternating product/blog for a balanced mix.
    bi, pi = (1 if blogs else 0), (1 if products else 0)
    turn_product = True
    while len(selected) < max_links and (bi < len(blogs) or pi < len(products)):
        progressed = False
        if turn_product and pi < len(products):
            if _take(products[pi]):
                progressed = True
            pi += 1
        elif not turn_product and bi < len(blogs):
            if _take(blogs[bi]):
                progressed = True
            bi += 1
        else:
            # Preferred side exhausted — take from the other.
            if pi < len(products):
                _take(products[pi]); pi += 1; progressed = True
            elif bi < len(blogs):
                _take(blogs[bi]); bi += 1; progressed = True
        turn_product = not turn_product
        if not progressed and pi >= len(products) and bi >= len(blogs):
            break

    return selected[:max_links]


def build_allow_list(links: Iterable[InternalLink]) -> set[str]:
    """Validated set of internal URLs that may appear in the article body."""
    return {_normalize_url(link.url) for link in links if link.url}


def render_related_block(links: list[InternalLink]) -> str:
    """Deterministic 'Related reading & products' HTML block. Empty if no links."""
    if not links:
        return ""
    item_parts: list[str] = []
    for link in links:
        tag = ""
        if link.kind == "product":
            tag = ' <span style="color:#6b7280;font-size:12px;">(product)</span>'
        item_parts.append(
            '<li style="margin:6px 0;">'
            f'<a href="{escape(link.url, quote=True)}" '
            'style="color:#1d4ed8;text-decoration:underline;">'
            f"{escape(link.title)}</a>{tag}</li>"
        )
    items = "".join(item_parts)
    return (
        '<div style="margin-top:36px;padding-top:20px;border-top:1px solid #e5e7eb;">'
        '<p style="margin:0 0 8px;font-size:15px;font-weight:700;color:#111827;">'
        "Related reading &amp; products</p>"
        f'<ul style="margin:0;padding-left:18px;">{items}</ul>'
        "</div>"
    )
