"""Reusable backfill workflow for existing Shopify article images."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

import db
import shopify_client
from config import StoreConfig

from .image_service import generate_feature_image
from .quality_service import html_to_review_text


@dataclass
class ArticleImageBackfillResult:
    article_id: int
    blog_id: int
    blog_handle: str
    title: str
    article_url: str
    status: str
    generated_image_url: str = ""
    updated_image_src: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "blog_id": self.blog_id,
            "blog_handle": self.blog_handle,
            "title": self.title,
            "article_url": self.article_url,
            "status": self.status,
            "generated_image_url": self.generated_image_url,
            "updated_image_src": self.updated_image_src,
            "error": self.error,
        }


@dataclass
class ArticleImageBackfillSummary:
    store_id: str
    myshopify_domain: str
    scanned: int
    candidates: int
    processed: int
    updated: int
    failed: int
    dry_run: bool
    remaining_missing: int
    results: list[ArticleImageBackfillResult]

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
            "remaining_missing": self.remaining_missing,
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


def _article_context(article: shopify_client.ShopifyArticle) -> tuple[str, str]:
    summary = html_to_review_text(article.summary_html).strip() or article.title
    content_text = html_to_review_text(article.body_html).strip()
    prompt = content_text[:600] if content_text else article.title
    return summary[:240], prompt


def _filter_candidates(
    articles: Iterable[shopify_client.ShopifyArticle],
    article_ids: set[int],
    limit: int,
) -> list[shopify_client.ShopifyArticle]:
    candidates = [article for article in articles if not (article.image_url or "").strip()]
    if article_ids:
        candidates = [article for article in candidates if article.id in article_ids]
    if limit > 0:
        candidates = candidates[:limit]
    return candidates


async def backfill_missing_article_images(
    *,
    store_id: str = "",
    article_ids: Iterable[int] | None = None,
    limit: int = 0,
    limit_per_blog: int = 250,
    dry_run: bool = False,
    progress_every: int = 10,
    progress_callback: ProgressCallback | None = None,
) -> ArticleImageBackfillSummary:
    store = await _resolve_store(store_id)
    selected_article_ids = {int(article_id) for article_id in (article_ids or [])}
    progress_step = max(1, progress_every)

    articles = await shopify_client.fetch_store_articles(store, limit_per_blog=limit_per_blog)
    candidates = _filter_candidates(articles, selected_article_ids, limit)

    if dry_run:
        results = [
            ArticleImageBackfillResult(
                article_id=article.id,
                blog_id=article.blog_id,
                blog_handle=article.blog_handle,
                title=article.title,
                article_url=article.article_url,
                status="candidate",
            )
            for article in candidates
        ]
        return ArticleImageBackfillSummary(
            store_id=store.id,
            myshopify_domain=store.myshopify_domain,
            scanned=len(articles),
            candidates=len(candidates),
            processed=0,
            updated=0,
            failed=0,
            dry_run=True,
            remaining_missing=len([article for article in articles if not (article.image_url or "").strip()]),
            results=results,
        )

    results: list[ArticleImageBackfillResult] = []
    updated = 0
    failed = 0

    for index, article in enumerate(candidates, start=1):
        summary, prompt = _article_context(article)
        generated_image_url = ""
        updated_image_src = ""
        error = ""
        status = "updated"
        try:
            generated_image_url = await generate_feature_image(store.id, article.title, summary, prompt) or ""
            if not generated_image_url:
                status = "failed"
                error = "no_image_generated"
                failed += 1
            else:
                updated_image_src = await shopify_client.update_article_image(
                    store=store,
                    blog_id=article.blog_id,
                    article_id=article.id,
                    title=article.title,
                    image_url=generated_image_url,
                )
                updated += 1
        except Exception as exc:
            status = "failed"
            error = str(exc)[:300]
            failed += 1

        results.append(
            ArticleImageBackfillResult(
                article_id=article.id,
                blog_id=article.blog_id,
                blog_handle=article.blog_handle,
                title=article.title,
                article_url=article.article_url,
                status=status,
                generated_image_url=generated_image_url,
                updated_image_src=updated_image_src,
                error=error,
            )
        )

        if progress_callback and (index % progress_step == 0 or index == len(candidates)):
            progress_callback(
                {
                    "processed": index,
                    "total": len(candidates),
                    "updated": updated,
                    "failed": failed,
                }
            )

    refreshed_articles = await shopify_client.fetch_store_articles(store, limit_per_blog=limit_per_blog)
    remaining_missing = len([article for article in refreshed_articles if not (article.image_url or "").strip()])
    return ArticleImageBackfillSummary(
        store_id=store.id,
        myshopify_domain=store.myshopify_domain,
        scanned=len(articles),
        candidates=len(candidates),
        processed=len(candidates),
        updated=updated,
        failed=failed,
        dry_run=False,
        remaining_missing=remaining_missing,
        results=results,
    )