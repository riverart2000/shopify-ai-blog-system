"""Reusable backfill workflow for cleaning heading-marker noise from existing
Shopify article titles (e.g. "H2: ...", "## ...", '"Quoted"').
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

import db
import shopify_client
from config import StoreConfig
from utils import clean_title


@dataclass
class ArticleTitleBackfillResult:
    article_id: int
    blog_id: int
    blog_handle: str
    old_title: str
    new_title: str
    article_url: str
    status: str
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "blog_id": self.blog_id,
            "blog_handle": self.blog_handle,
            "old_title": self.old_title,
            "new_title": self.new_title,
            "article_url": self.article_url,
            "status": self.status,
            "error": self.error,
        }


@dataclass
class ArticleTitleBackfillSummary:
    store_id: str
    myshopify_domain: str
    scanned: int
    candidates: int
    processed: int
    updated: int
    failed: int
    dry_run: bool
    results: list[ArticleTitleBackfillResult]

    def to_dict(self) -> dict:
        return {
            "store_id": self.store_id,
            "myshopify_domain": self.myshopify_domain,
            "scanned": self.scanned,
            "candidates": self.candidates,
            "processed": self.processed,
            "updated": self.updated,
            "failed": self.failed,
            "dry_run": self.dry_run,
            "results": [result.to_dict() for result in self.results],
        }


ProgressCallback = Callable[[dict], None]


def _store_config_from_row(store_row: dict) -> StoreConfig:
    return StoreConfig(
        id=store_row["id"],
        name=store_row["name"],
        myshopify_domain=store_row["myshopify_domain"],
        custom_domain=store_row.get("custom_domain", ""),
        client_id=store_row.get("client_id", ""),
        client_secret=store_row.get("client_secret", ""),
        default_blog_handle=store_row.get("default_blog_handle", "news"),
        default_author=store_row.get("default_author", "Store Team"),
    )


async def _resolve_store(store_id: str = "") -> StoreConfig:
    requested_store_id = store_id.strip()
    if requested_store_id:
        row = await db.get_store(requested_store_id)
        if not row:
            raise ValueError(f"Unknown store: {requested_store_id}")
        return _store_config_from_row(row)

    stores = await db.get_stores()
    if not stores:
        raise ValueError("No stores are configured in the database.")
    return _store_config_from_row(stores[0])


def _filter_candidates(
    articles: Iterable[shopify_client.ShopifyArticle],
    article_ids: set[int],
    limit: int,
) -> list[tuple[shopify_client.ShopifyArticle, str]]:
    candidates: list[tuple[shopify_client.ShopifyArticle, str]] = []
    for article in articles:
        cleaned = clean_title(article.title)
        if cleaned and cleaned != (article.title or "").strip():
            candidates.append((article, cleaned))
    if article_ids:
        candidates = [c for c in candidates if c[0].id in article_ids]
    if limit > 0:
        candidates = candidates[:limit]
    return candidates


async def backfill_article_titles(
    *,
    store_id: str = "",
    article_ids: Iterable[int] = (),
    limit: int = 0,
    limit_per_blog: int = 250,
    dry_run: bool = False,
    progress_every: int = 10,
    progress_callback: ProgressCallback | None = None,
) -> ArticleTitleBackfillSummary:
    store = await _resolve_store(store_id)
    requested_ids = {int(a) for a in article_ids}

    articles = await shopify_client.fetch_store_articles(store, limit_per_blog=limit_per_blog)
    candidates = _filter_candidates(articles, requested_ids, limit)

    results: list[ArticleTitleBackfillResult] = []
    updated = 0
    failed = 0
    processed = 0

    for article, cleaned in candidates:
        if dry_run:
            results.append(
                ArticleTitleBackfillResult(
                    article_id=article.id,
                    blog_id=article.blog_id,
                    blog_handle=article.blog_handle,
                    old_title=article.title,
                    new_title=cleaned,
                    article_url=article.article_url,
                    status="would_update",
                )
            )
            continue

        processed += 1
        try:
            new_title = await shopify_client.update_article_title(
                store, article.blog_id, article.id, cleaned
            )
            updated += 1
            results.append(
                ArticleTitleBackfillResult(
                    article_id=article.id,
                    blog_id=article.blog_id,
                    blog_handle=article.blog_handle,
                    old_title=article.title,
                    new_title=new_title,
                    article_url=article.article_url,
                    status="updated",
                )
            )
        except Exception as exc:  # noqa: BLE001 — record and continue
            failed += 1
            results.append(
                ArticleTitleBackfillResult(
                    article_id=article.id,
                    blog_id=article.blog_id,
                    blog_handle=article.blog_handle,
                    old_title=article.title,
                    new_title=cleaned,
                    article_url=article.article_url,
                    status="failed",
                    error=str(exc),
                )
            )
        if progress_callback and progress_every > 0 and processed % progress_every == 0:
            progress_callback(
                {
                    "processed": processed,
                    "total": len(candidates),
                    "updated": updated,
                    "failed": failed,
                }
            )

    return ArticleTitleBackfillSummary(
        store_id=store.id,
        myshopify_domain=store.myshopify_domain,
        scanned=len(articles),
        candidates=len(candidates),
        processed=processed,
        updated=updated,
        failed=failed,
        dry_run=dry_run,
        results=results,
    )
