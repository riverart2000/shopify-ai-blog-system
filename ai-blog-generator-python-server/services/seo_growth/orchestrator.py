"""SEO Growth orchestration, progress reporting and scheduled execution."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import db
from config import StoreConfig

from .opportunities import merge_and_rank, search_opportunities
from .search_console import collect_search_console
from .shopify_audit import collect_shopify_audit

logger = logging.getLogger("ai_blog_server.seo_growth")
_scheduled_tasks: set[asyncio.Task] = set()


async def _settings(store_id: str) -> dict[str, Any]:
    site_url = await db.get_store_setting(store_id, "seo_gsc_site_url", "")
    credentials = await db.get_store_setting(store_id, "seo_gsc_service_account_json", "")
    use_ga4 = await db.get_store_setting(store_id, "seo_use_ga4_credentials", "1") == "1"
    credential_source = "search_console"
    if not credentials and use_ga4:
        credentials = await db.get_store_setting(store_id, "ga4_service_account_json", "")
        credential_source = "ga4_shared"
    return {
        "site_url": site_url,
        "credentials": credentials,
        "credential_source": credential_source,
    }


async def run_audit(run_id: str, store_id: str, period_days: int) -> None:
    """Run one final, no-retry audit and persist exact progress or failure."""
    try:
        await db.update_seo_growth_run(run_id, status="running", stage="loading_store", progress=5)
        row = await db.get_store(store_id)
        if not row:
            raise RuntimeError(f"Store '{store_id}' does not exist.")
        store = StoreConfig.from_row(row)

        await db.update_seo_growth_run(run_id, status="running", stage="auditing_shopify", progress=18)
        shopify = await collect_shopify_audit(store)

        await db.update_seo_growth_run(run_id, status="running", stage="reading_search_console", progress=55)
        settings = await _settings(store_id)
        search_console: dict[str, Any]
        search_items: list[dict[str, Any]] = []
        if settings["site_url"] and settings["credentials"]:
            search_console = await collect_search_console(
                settings["site_url"], settings["credentials"], period_days,
            )
            search_console["credential_source"] = settings["credential_source"]
            latest_intelligence = await db.get_latest_intelligence_run(store_id)
            intelligence_summary = (latest_intelligence or {}).get("summary", {})
            search_items = search_opportunities(search_console, intelligence_summary)
        else:
            missing = []
            if not settings["site_url"]:
                missing.append("Search Console property")
            if not settings["credentials"]:
                missing.append("service-account credentials")
            search_console = {
                "connected": False,
                "status": "not_configured",
                "message": "Missing " + " and ".join(missing) + ". Shopify audit completed; no Google search metrics were inferred.",
                "totals": {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0, "rows": 0},
            }

        await db.update_seo_growth_run(run_id, status="running", stage="ranking_opportunities", progress=82)
        opportunities = merge_and_rank(shopify.get("issues", []), search_items)
        category_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        for item in opportunities:
            category = str(item.get("category", "other"))
            severity = str(item.get("severity", "medium"))
            category_counts[category] = category_counts.get(category, 0) + 1
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        summary = {
            "store_id": store_id,
            "period_days": period_days,
            "search_console": {
                key: value for key, value in search_console.items()
                if key not in {"current", "previous"}
            },
            "shopify": {
                "products": shopify.get("products", {}),
                "articles": shopify.get("articles", {}),
            },
            "opportunity_count": len(opportunities),
            "category_counts": category_counts,
            "severity_counts": severity_counts,
            "no_automatic_changes": True,
            "no_retries": True,
        }
        await db.complete_seo_growth_run(run_id, store_id, summary, opportunities)
        logger.info(
            "SEO Growth audit complete store=%s run=%s opportunities=%d",
            store_id, run_id, len(opportunities),
            extra={"operation": "seo_growth_audit", "store_id": store_id, "correlation_id": run_id},
        )
    except Exception as exc:
        error_type = type(exc).__name__
        message = str(exc) or repr(exc)
        await db.fail_seo_growth_run(run_id, error_type, message)
        logger.error(
            "SEO Growth audit failed store=%s run=%s type=%s error=%s; no retry attempted",
            store_id, run_id, error_type, message,
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"operation": "seo_growth_audit", "store_id": store_id, "correlation_id": run_id},
        )


async def run_scheduled_scans() -> None:
    stores = await db.get_stores_due_for_seo_growth(int(time.time()))
    for store in stores:
        store_id = str(store["id"])
        if await db.get_active_seo_growth_run(store_id):
            continue
        days_raw = await db.get_store_setting(store_id, "seo_growth_period_days", "90")
        try:
            days = min(max(int(days_raw), 28), 365)
        except ValueError:
            days = 90
        run_id = await db.create_seo_growth_run(store_id, days, "scheduled")
        if not run_id:
            continue
        launch_background(
            _run_scheduled_cycle(run_id, store_id, days),
            _scheduled_tasks,
        )


async def _run_scheduled_cycle(run_id: str, store_id: str, days: int) -> None:
    """Run the weekly audit, then only an explicitly opted-in safe repair rule."""
    await run_audit(run_id, store_id, days)
    run = await db.get_latest_seo_growth_run(store_id)
    if not run or run.get("id") != run_id or run.get("status") != "complete":
        return
    if await db.get_store_setting(store_id, "seo_auto_safe_repairs", "0") != "1":
        return
    from .repair import run_automatic_safe_repairs
    await run_automatic_safe_repairs(store_id)


def launch_background(coro: Any, task_set: set[asyncio.Task]) -> asyncio.Task:
    """Keep process-local task references until completion."""
    task = asyncio.create_task(coro)
    task_set.add(task)
    task.add_done_callback(task_set.discard)
    return task
