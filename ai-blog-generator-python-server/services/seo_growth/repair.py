"""Backed-up, deterministic repairs for explicitly safe SEO findings."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import db
import shopify_client
from config import StoreConfig

from .managed_content import (
    RULE_KEY,
    has_managed_keyword_blocks,
    remove_managed_keyword_blocks,
)

logger = logging.getLogger("ai_blog_server.seo_growth.repair")


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


async def _store(store_id: str) -> StoreConfig:
    row = await db.get_store(store_id)
    if not row:
        raise RuntimeError(f"Store '{store_id}' does not exist.")
    return StoreConfig.from_row(row)


async def scan_managed_keyword_blocks(job_id: str, store_id: str) -> None:
    """Create a dry-run snapshot. No Shopify content is changed."""
    try:
        await db.update_seo_repair_job(
            job_id, status="running", stage="reading_articles", progress=5,
            message="Reading every published Shopify article. No changes are being made.",
        )
        store = await _store(store_id)
        articles = await shopify_client.fetch_store_articles(store, limit_per_blog=0)
        await db.update_seo_repair_job(
            job_id, status="running", stage="building_preview", progress=55,
            total_items=len(articles), processed_items=0,
            message="Checking only exact HTML signatures previously created by this app.",
        )

        repairs: list[dict[str, Any]] = []
        unsafe_empty = 0
        for article in articles:
            original = article.body_html or ""
            cleaned = remove_managed_keyword_blocks(original)
            if not cleaned.changed:
                continue
            if not cleaned.html.strip():
                unsafe_empty += 1
                continue
            repairs.append({
                "resource_type": "article",
                "resource_id": str(article.id),
                "parent_id": str(article.blog_id),
                "title": article.title,
                "page_url": article.article_url,
                "original_html": original,
                "repaired_html": cleaned.html,
                "original_hash": _hash(original),
                "repaired_hash": _hash(cleaned.html),
            })

        await db.replace_seo_repair_items(job_id, store_id, RULE_KEY, repairs)
        if not repairs:
            message = "No app-generated keyword blocks were found. Nothing needs changing."
            if unsafe_empty:
                message += f" {unsafe_empty} empty-body candidate(s) were excluded for safety."
            await db.update_seo_repair_job(
                job_id, status="complete", stage="complete", progress=100,
                total_items=0, processed_items=0, changed_items=0,
                skipped_items=unsafe_empty, failed_items=0, message=message, complete=True,
            )
            return

        message = (
            f"Dry run complete: {len(repairs):,} article(s) can be repaired using the "
            "app's exact historical HTML signature. Originals are retained for rollback."
        )
        if unsafe_empty:
            message += f" {unsafe_empty} unsafe empty-body candidate(s) were excluded."
        await db.update_seo_repair_job(
            job_id, status="awaiting_approval", stage="preview_ready", progress=100,
            total_items=len(repairs), processed_items=0, changed_items=0,
            skipped_items=unsafe_empty, failed_items=0, message=message,
        )
    except Exception as exc:
        await db.fail_seo_repair_job(job_id, type(exc).__name__, str(exc) or repr(exc))
        logger.error(
            "SEO repair preview failed store=%s job=%s; no retry attempted",
            store_id, job_id, exc_info=True,
            extra={"operation": "seo_repair_preview", "store_id": store_id, "correlation_id": job_id},
        )


async def apply_managed_keyword_blocks(
    job_id: str,
    store_id: str,
    *,
    refresh_audit: bool = True,
) -> None:
    """Apply a previously approved preview one article at a time, without retries."""
    try:
        store = await _store(store_id)
        items = await db.list_seo_repair_items(
            store_id, job_id, limit=50000, include_html=True,
        )
        total = len(items)
        changed = skipped = failed = 0
        if not total:
            await db.update_seo_repair_job(
                job_id, status="complete", stage="complete", progress=100,
                total_items=0, processed_items=0,
                message="No approved repair items were present. Nothing was changed.", complete=True,
            )
            return

        for index, item in enumerate(items, start=1):
            article_id = int(item["resource_id"])
            blog_id = int(item["parent_id"])
            try:
                current = await shopify_client.fetch_article_body_html(
                    store, blog_id, article_id,
                )
                current_hash = _hash(current)
                if current_hash != item["original_hash"]:
                    if current_hash == item["repaired_hash"] or not has_managed_keyword_blocks(current):
                        skipped += 1
                        await db.set_seo_repair_item_result(
                            item["id"], status="already_clean",
                            error_message="The managed block was already absent when apply began.",
                        )
                    else:
                        skipped += 1
                        await db.set_seo_repair_item_result(
                            item["id"], status="skipped_changed",
                            error_message=(
                                "The article changed after the preview. It was not overwritten; "
                                "run a new preview to reassess it."
                            ),
                        )
                else:
                    stored = await shopify_client.update_article_body_html(
                        store, blog_id, article_id, item["repaired_html"],
                    )
                    if has_managed_keyword_blocks(stored):
                        raise RuntimeError(
                            "Shopify accepted the update but the managed keyword signature remains."
                        )
                    changed += 1
                    await db.set_seo_repair_item_applied(
                        item["id"], stored, _hash(stored),
                    )
            except Exception as exc:
                failed += 1
                await db.set_seo_repair_item_result(
                    item["id"], status="failed", error_message=str(exc) or repr(exc),
                )
                logger.error(
                    "SEO repair item failed store=%s job=%s article=%s; no retry attempted",
                    store_id, job_id, article_id, exc_info=True,
                    extra={"operation": "seo_repair_apply", "store_id": store_id, "correlation_id": job_id},
                )

            progress = 5 + int(index / total * 90)
            await db.update_seo_repair_job(
                job_id, status="applying", stage="updating_shopify", progress=progress,
                total_items=total, processed_items=index, changed_items=changed,
                skipped_items=skipped, failed_items=failed,
                message=f"Processed {index:,} of {total:,} articles. No failed item is retried.",
            )
            # Shopify REST updates are intentionally sequential and rate-limited.
            if index < total:
                await asyncio.sleep(1.05)

        status = "partial" if failed or skipped else "complete"
        message = (
            f"Repair finished: {changed:,} changed, {skipped:,} safely skipped and "
            f"{failed:,} failed. Every original body is retained for rollback."
        )
        await db.update_seo_repair_job(
            job_id, status=status, stage="complete", progress=100,
            total_items=total, processed_items=total, changed_items=changed,
            skipped_items=skipped, failed_items=failed, message=message, complete=True,
        )

        if refresh_audit:
            period_raw = await db.get_store_setting(store_id, "seo_growth_period_days", "90")
            try:
                period_days = min(max(int(period_raw), 28), 365)
            except ValueError:
                period_days = 90
            audit_id = await db.create_seo_growth_run(store_id, period_days, "post_repair")
            if audit_id:
                from .orchestrator import run_audit
                await run_audit(audit_id, store_id, period_days)
    except Exception as exc:
        await db.fail_seo_repair_job(job_id, type(exc).__name__, str(exc) or repr(exc))
        logger.error(
            "SEO repair apply failed store=%s job=%s; no retry attempted",
            store_id, job_id, exc_info=True,
            extra={"operation": "seo_repair_apply", "store_id": store_id, "correlation_id": job_id},
        )


async def restore_managed_keyword_blocks(job_id: str, store_id: str) -> None:
    """Restore originals only where the repaired version is still unchanged."""
    try:
        store = await _store(store_id)
        items = await db.list_seo_repair_items(
            store_id, job_id, limit=50000, include_html=True,
        )
        candidates = [item for item in items if item["status"] in {"changed", "restored"}]
        total = len(candidates)
        restored = skipped = failed = 0
        for index, item in enumerate(candidates, start=1):
            article_id = int(item["resource_id"])
            blog_id = int(item["parent_id"])
            try:
                current = await shopify_client.fetch_article_body_html(store, blog_id, article_id)
                current_hash = _hash(current)
                if current_hash == item["original_hash"]:
                    restored += 1
                    await db.set_seo_repair_item_result(
                        item["id"], status="restored", restored=True,
                    )
                elif current_hash != item["repaired_hash"]:
                    skipped += 1
                    await db.set_seo_repair_item_result(
                        item["id"], status="restore_skipped_changed",
                        error_message=(
                            "The article changed after repair. Rollback did not overwrite the newer content."
                        ),
                    )
                else:
                    stored = await shopify_client.update_article_body_html(
                        store, blog_id, article_id, item["original_html"],
                    )
                    if _hash(stored) != item["original_hash"]:
                        raise RuntimeError(
                            "Shopify returned content that does not match the saved original backup."
                        )
                    restored += 1
                    await db.set_seo_repair_item_result(
                        item["id"], status="restored", restored=True,
                    )
            except Exception as exc:
                failed += 1
                await db.set_seo_repair_item_result(
                    item["id"], status="restore_failed", error_message=str(exc) or repr(exc),
                )
            progress = 5 + int(index / max(total, 1) * 90)
            await db.update_seo_repair_job(
                job_id, status="restoring", stage="restoring_shopify", progress=progress,
                total_items=total, processed_items=index, changed_items=restored,
                skipped_items=skipped, failed_items=failed,
                message=f"Restored {index:,} of {total:,} eligible backups.",
            )
            if index < total:
                await asyncio.sleep(1.05)

        status = "restore_partial" if skipped or failed else "restored"
        await db.update_seo_repair_job(
            job_id, status=status, stage="complete", progress=100,
            total_items=total, processed_items=total, changed_items=restored,
            skipped_items=skipped, failed_items=failed,
            message=(
                f"Rollback finished: {restored:,} restored, {skipped:,} safely skipped "
                f"and {failed:,} failed. No failed item was retried."
            ),
            complete=True,
        )
    except Exception as exc:
        await db.fail_seo_repair_job(job_id, type(exc).__name__, str(exc) or repr(exc))
        logger.error(
            "SEO repair rollback failed store=%s job=%s; no retry attempted",
            store_id, job_id, exc_info=True,
            extra={"operation": "seo_repair_restore", "store_id": store_id, "correlation_id": job_id},
        )


async def run_automatic_safe_repairs(store_id: str) -> None:
    """Scan and apply the single allow-listed rule for an opted-in store."""
    job_id = await db.create_seo_repair_job(store_id, RULE_KEY)
    if not job_id:
        return
    await scan_managed_keyword_blocks(job_id, store_id)
    job = await db.get_seo_repair_job(store_id, job_id)
    if not job or job["status"] != "awaiting_approval":
        return
    claimed = await db.begin_seo_repair_phase(
        store_id, job_id, expected_status="awaiting_approval",
        status="applying", stage="starting_apply",
    )
    if claimed:
        await apply_managed_keyword_blocks(job_id, store_id, refresh_audit=False)
