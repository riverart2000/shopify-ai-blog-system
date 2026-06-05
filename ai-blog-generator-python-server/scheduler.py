"""
scheduler.py — Standalone scheduled blog generation process.

Run with:  python scheduler.py

Loads .env, initialises the DB, then runs a polling loop every 60 seconds.
For each due job it spawns an asyncio task that calls the full publish pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# .env loading — must happen before any module that reads os.environ
# ---------------------------------------------------------------------------

from dotenv import load_dotenv

_here = Path(__file__).parent
for _env_path in [_here / ".env", _here.parent / ".env"]:
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=False)
        break

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_PATH = Path("logs/scheduler.log")
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_fmt = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_h = logging.handlers.RotatingFileHandler(
    str(_LOG_PATH), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
)
_file_h.setFormatter(_fmt)
_stdout_h = logging.StreamHandler(sys.stdout)
_stdout_h.setFormatter(_fmt)

_root = logging.getLogger("ai_blog_server")
_root.setLevel(logging.INFO)
_root.addHandler(_file_h)
_root.addHandler(_stdout_h)

logger = logging.getLogger("ai_blog_server.scheduler")

# ---------------------------------------------------------------------------
# Now safe to import app modules
# ---------------------------------------------------------------------------

import db
from providers import AllModelsFailedError
from services import blog_scope
from services import publish_service
from services.schedule_time import get_next_run_at
from services import title_service
from services.keyword_service import fetch_keywords
import shopify_client
from config import StoreConfig
from services.quality_service import QualityGateError

_POLL_INTERVAL = 60  # seconds between ticks
_DB_PATH = os.environ.get("DB_PATH", "data/ai_blog_server.db")


# ---------------------------------------------------------------------------
# croniter helper
# ---------------------------------------------------------------------------

def _get_next_run(cron_expr: str, timezone: str = "UTC") -> int | None:
    return get_next_run_at(cron_expr, timezone)


# ---------------------------------------------------------------------------
# Job processor
# ---------------------------------------------------------------------------

async def _process_job(job: dict) -> None:
    job_id = job["id"]
    store_id = job["store_id"]
    name = job.get("name", job_id)
    cron_expr = job.get("cron_expr", "")

    logger.info("Running job '%s' (store=%s)", name, store_id)

    store_row = await db.get_store(store_id)
    if not store_row:
        logger.error("Job '%s': store '%s' not found — skipping", name, store_id)
        return
    store_cfg = StoreConfig(
        id=store_row["id"],
        name=store_row["name"],
        myshopify_domain=store_row["myshopify_domain"],
        custom_domain=store_row.get("custom_domain", ""),
        client_id=store_row["client_id"],
        client_secret=store_row["client_secret"],
        default_blog_handle=store_row.get("default_blog_handle", "news"),
        default_author=store_row.get("default_author", "Store Team"),
    )
    configured_blog_handle = (job.get("blog_handle", "") or "").strip()
    preselected_title_row = None

    auto_route_from_title_pool = (
        not job.get("is_product_blog")
        and (not configured_blog_handle or blog_scope.is_auto_blog_handle(configured_blog_handle))
    )

    if auto_route_from_title_pool:
        fallback_scope = await blog_scope.resolve_blog_scope(
            store_id,
            store_cfg,
            store_cfg.default_blog_handle,
        )
        preselected_title_row, scope = await title_service.pop_blog_title_for_auto_scope(
            store_id,
            await blog_scope.get_blog_scopes(store_id, store_cfg),
            fallback_scope,
        )
        resolved_blog_handle = getattr(scope, "handle", "") or store_cfg.default_blog_handle
        if preselected_title_row:
            logger.info(
                "Job '%s': auto-routed pooled title %r to blog handle '%s'",
                name,
                preselected_title_row["title"],
                resolved_blog_handle,
            )
        else:
            logger.info(
                "Job '%s': auto blog-handle mode found no pooled title — using %s",
                name,
                resolved_blog_handle,
            )
    else:
        resolved_blog_handle = configured_blog_handle or store_cfg.default_blog_handle
        scope = await blog_scope.resolve_blog_scope(
            store_id,
            store_cfg,
            resolved_blog_handle,
        )

    # Look up the prompt text
    prompts = await db.get_prompts(store_id)
    prompt = next((p for p in prompts if p["id"] == job["prompt_id"]), None)
    if not prompt:
        logger.error(
            "Job '%s': prompt '%s' not found — skipping", name, job["prompt_id"]
        )
        return

    # If job has use_keyword_pool set, pop a keyword and inject it into the prompt
    if job.get("use_keyword_pool"):
        keyword_row = await blog_scope.pop_scoped_keyword(store_id, scope)
        if keyword_row:
            kw = keyword_row["keyword"]
            kw_content = keyword_row.get("content", "")
            logger.info("Job '%s': using pooled keyword %r", name, kw)
        else:
            # Pool empty — fetch fresh; immediate keyword is used, extras banked
            niche = await db.get_store_setting(store_id, "keyword_niche", "")
            max_pool = int(await db.get_store_setting(store_id, "keyword_max_pool", "100"))
            kw_result = await fetch_keywords(store_id, niche, max_pool=max_pool)
            kw = kw_result.get("immediate") or ""
            kw_content = kw_result.get("immediate_content", "")
            if kw and not blog_scope.is_candidate_compatible(f"{kw} {kw_content}", scope):
                logger.info(
                    "Job '%s': fetched keyword %r does not match blog scope handle=%s — ignoring",
                    name,
                    kw,
                    getattr(scope, "handle", ""),
                )
                kw = ""
                kw_content = ""
            if kw:
                logger.info("Job '%s': fetched fresh keyword %r (source=%s)", name, kw, kw_result.get("source"))
            else:
                logger.warning("Job '%s': keyword pool empty and fetch returned nothing — running without keyword", name)
        if kw:
            kw_block = f"\n\nFocus keyword for this article: {kw}"
            if kw_content:
                kw_block += f"\n\nWhat people are currently discussing about this topic (use as context, do not quote directly):\n{kw_content[:600]}"
            prompt_text = f"{prompt['text']}{kw_block}"
        else:
            prompt_text = prompt["text"]
    else:
        prompt_text = prompt["text"]

    # If this is a product blog job, pick a random product
    product_url = ""
    product_title = ""
    if job.get("is_product_blog"):
        try:
            products = await shopify_client.fetch_products(store_cfg)
            if products:
                import random
                picked = random.choice(products)
                product_url = picked.url
                product_title = picked.title
                logger.info(
                    "Job '%s': random product selected | %s (%s)",
                    name, product_title, product_url,
                )
            else:
                logger.warning("Job '%s': is_product_blog=1 but no products found — running as normal blog", name)
        except Exception as exc:
            logger.warning("Job '%s': failed to fetch products (%s) — running as normal blog", name, exc)

    try:
        result = await publish_service.run(
            store_id=store_id,
            prompt_text=prompt_text,
            blog_handle=resolved_blog_handle,
            author=job.get("author", ""),
            prompt_id=job["prompt_id"],
            product_url=product_url,
            product_title=product_title,
            scheduled_job_id=job_id,
            preselected_title_row=preselected_title_row,
        )
        logger.info(
            "Job '%s' completed | title=%r article_id=%s",
            name, result.title, result.article_id,
        )
    except AllModelsFailedError as exc:
        logger.error("Job '%s' failed (all models failed): %s", name, exc)
    except QualityGateError as exc:
        logger.warning("Job '%s' blocked by quality checks: %s", name, exc)
    except Exception as exc:
        logger.exception("Job '%s' failed with unexpected error: %s", name, exc)
    finally:
        import time
        now = int(time.time())
        next_run = _get_next_run(cron_expr, job.get("timezone", "UTC"))
        await db.update_job_run_times(job_id, now, next_run)
        logger.debug("Job '%s' next_run_at=%s", name, next_run)


# ---------------------------------------------------------------------------
# Startup: ensure jobs that have never run get a next_run_at
# ---------------------------------------------------------------------------

async def _initialise_next_run_times() -> None:
    jobs = await db.get_all_active_jobs()
    updated = 0
    for job in jobs:
        if job.get("next_run_at") is None:
            next_run = _get_next_run(job["cron_expr"], job.get("timezone", "UTC"))
            if next_run:
                await db.update_job_run_times(job["id"], job.get("last_run_at", 0), next_run)
                updated += 1
    if updated:
        logger.info("Initialised next_run_at for %d job(s)", updated)


# ---------------------------------------------------------------------------
# Main tick
# ---------------------------------------------------------------------------

async def _tick() -> None:
    due = await db.get_due_jobs()
    if not due:
        return
    logger.info("Tick: %d due job(s)", len(due))
    tasks = [asyncio.create_task(_process_job(job)) for job in due]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for job, res in zip(due, results):
        if isinstance(res, Exception):
            logger.error("Unhandled exception in job '%s': %s", job.get("name"), res)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    db.set_db_path(_DB_PATH)
    await db.init_db()
    logger.info("Scheduler started | db=%s poll_interval=%ds", _DB_PATH, _POLL_INTERVAL)

    await _initialise_next_run_times()

    while True:
        try:
            await _tick()
        except Exception as exc:
            logger.exception("Unexpected error in scheduler tick: %s", exc)
        await asyncio.sleep(_POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
