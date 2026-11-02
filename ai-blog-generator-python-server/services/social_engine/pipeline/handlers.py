"""Job handler registrations. Importing this module registers every job type.

Each handler is a thin adapter: read the payload, call the module that does the
work, return a small summary dict (stored on the job for debugging).
"""
from __future__ import annotations

from db.social_engine import observability
from db.social_engine import posts as post_store

from ..activity import record
from ..jobs.queue import enqueue
from ..jobs.registry import JobContext, job
from ..jobs.worker import on_final_failure


# --------------------------------------------------------------------- catalog

@job("catalog.sync", lane="shopify", max_attempts=4, timeout_seconds=900, backoff_base=60)
async def catalog_sync(ctx: JobContext) -> dict:
    """Incremental Shopify catalogue sync (products changed since the last cursor)."""
    from ..catalog import sync_catalog

    summary = await sync_catalog(ctx.store_id, full=bool(ctx.payload.get("full")), correlation_id=ctx.id)
    if any((summary.get("events") or {}).values()):
        await enqueue("campaigns.evaluate", {"reason": "catalog_events"}, store_id=ctx.store_id,
                      dedupe_key=f"periodic:evaluate_campaigns:{ctx.store_id}", priority=5)
    return summary


@job("catalog.photo_audit", lane="llm", max_attempts=2, timeout_seconds=1500, backoff_base=300)
async def catalog_photo_audit(ctx: JobContext) -> dict:
    """Check a few never-audited products' photos for printed claims (vision model)."""
    from ..ai.photos import audit_pending

    return await audit_pending(ctx.store_id, max_products=int(ctx.payload.get("max_products") or 5))


@job("catalog.sync_full", lane="shopify", max_attempts=3, timeout_seconds=1800, backoff_base=120)
async def catalog_sync_full(ctx: JobContext) -> dict:
    """Full Shopify catalogue sync, including collections and deletions."""
    from ..catalog import sync_catalog

    return await sync_catalog(ctx.store_id, full=True, correlation_id=ctx.id)


@job("catalog.webhook", lane="shopify", max_attempts=4, timeout_seconds=300, backoff_base=30)
async def catalog_webhook(ctx: JobContext) -> dict:
    """Process one stored Shopify webhook delivery."""
    from ..catalog import process_shopify_webhook

    event_id = int(ctx.payload["webhook_event_id"])
    event = await observability.get_webhook(event_id)
    if not event:
        return {"skipped": "webhook event not found"}
    try:
        result = await process_shopify_webhook(ctx.store_id, event["topic"], event.get("payload") or {},
                                               correlation_id=ctx.id)
    except Exception as exc:
        await observability.mark_webhook(event_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    action = (result or {}).get("action")
    if action in ("incremental_sync", "full_sync"):
        job_type = "catalog.sync_full" if action == "full_sync" else "catalog.sync"
        await enqueue(job_type, {"reason": f"webhook:{event['topic']}"}, store_id=ctx.store_id,
                      dedupe_key=f"{job_type}:webhook:{ctx.store_id}", run_in=30)
    elif (result or {}).get("events"):
        await enqueue("campaigns.evaluate", {"reason": "webhook"}, store_id=ctx.store_id,
                      dedupe_key=f"periodic:evaluate_campaigns:{ctx.store_id}", priority=5)
    await observability.mark_webhook(event_id, status="processed")
    return result or {}


# -------------------------------------------------------------------- channels

@job("channels.sync", lane="postiz", max_attempts=3, timeout_seconds=300)
async def channels_sync(ctx: JobContext) -> dict:
    """Refresh connected channels and their posting rules from Postiz."""
    from .channels import sync_channels

    return await sync_channels(ctx.store_id)


@job("notifications.poll", lane="postiz", max_attempts=2, timeout_seconds=120)
async def notifications_poll(ctx: JobContext) -> dict:
    """Surface new Postiz notifications in the activity log / System Health."""
    from .channels import poll_notifications

    return await poll_notifications(ctx.store_id)


# ------------------------------------------------------------------- campaigns

@job("campaigns.evaluate", lane="default", max_attempts=2, timeout_seconds=900, backoff_base=60)
async def campaigns_evaluate(ctx: JobContext) -> dict:
    """Run due auto-pilot campaigns (or one campaign when campaign_id is given)."""
    from ..campaigns.autofill import ensure_autofill_campaign
    from ..campaigns.engine import evaluate_campaign, evaluate_due_campaigns
    from ..scheduling.fill import reslot_missed_approvals

    missed = await reslot_missed_approvals(ctx.store_id)
    campaign_id = ctx.payload.get("campaign_id")
    if campaign_id:
        result = await evaluate_campaign(ctx.store_id, campaign_id)
    else:
        autofill = await ensure_autofill_campaign(ctx.store_id)   # every channel keeps its daily posts
        result = await evaluate_due_campaigns(ctx.store_id)
        result["autofill"] = autofill
    result["missed_approvals"] = missed
    return result


@job("promotions.auto_occasions", lane="default", max_attempts=2, timeout_seconds=900, backoff_base=300)
async def promotions_auto_occasions(ctx: JobContext) -> dict:
    """Create the day's occasion promotions: a real Shopify code plus a running promotion."""
    from ..campaigns.auto_promotions import ensure_occasion_promotions

    return await ensure_occasion_promotions(ctx.store_id, force=bool(ctx.payload.get("force")))


# -------------------------------------------------------------- founder videos

@job("founder.scan", lane="postiz", max_attempts=2, timeout_seconds=1800, backoff_base=120)
async def founder_scan(ctx: JobContext) -> dict:
    """Pick up dropped founder videos and move clippings on (YouTube → Postiz clips → posts)."""
    from ..founder.ingest import run_cycle

    return await run_cycle(ctx.store_id, force=bool(ctx.payload.get("force")))


# ----------------------------------------------------------------------- posts

@job("listings.fill", lane="relay", max_attempts=1, timeout_seconds=600)
async def listings_fill(ctx: JobContext) -> dict:
    """Prefill one directory form in the owner's Chrome (never submits)."""
    from ..context import load_store_context
    from ..listings.fill import fill_directory

    sctx = await load_store_context(ctx.store_id, correlation_id=ctx.correlation_id)
    return await fill_directory(sctx, str(ctx.payload.get("directory_id") or ""), tab=str(ctx.payload.get("tab") or ""))


@job("listings.presence", lane="relay", max_attempts=1, timeout_seconds=600)
async def listings_presence(ctx: JobContext) -> dict:
    """Search the brand in the owner's Chrome and record what a client would find."""
    from ..context import load_store_context
    from ..listings.checks import search_presence

    sctx = await load_store_context(ctx.store_id, correlation_id=ctx.correlation_id)
    report = await search_presence(sctx)
    return report.get("summary") or report


@job("listings.work", lane="relay", max_attempts=1, timeout_seconds=1800)
async def listings_work(ctx: JobContext) -> dict:
    """The directory employee: sign up, fill, submit, verify one directory in the owner's Chrome, logging every step."""
    from ..context import load_store_context
    from ..listings.worker import work_directory

    sctx = await load_store_context(ctx.store_id, correlation_id=ctx.correlation_id)
    return await work_directory(sctx, str(ctx.payload.get("directory_id") or ""), tab=str(ctx.payload.get("tab") or ""))


@job("listings.work_next", lane="relay", max_attempts=1, timeout_seconds=1800)
async def listings_work_next(ctx: JobContext) -> dict:
    """Periodic autopilot: take the next unworked directory, if the owner has autopilot on and the profile is ready."""
    from ..context import load_store_context
    from ..listings.worker import next_directory, recover_interrupted, work_directory

    sctx = await load_store_context(ctx.store_id, correlation_id=ctx.correlation_id)
    await recover_interrupted(ctx.store_id)
    if not sctx.settings.listings.autopilot:
        return {"skipped": "autopilot off"}
    directory = await next_directory(sctx)
    if not directory:
        return {"skipped": "nothing left to work"}
    return {"directory_id": directory.id, **await work_directory(sctx, directory.id)}


@job("listings.check", lane="default", max_attempts=2, timeout_seconds=900, backoff_base=300)
async def listings_check(ctx: JobContext) -> dict:
    """Periodic: confirm live listings still exist and still show the right name and phone."""
    from ..listings.checks import check_due

    return await check_due(ctx.store_id)


@job("post.generate", lane="llm", max_attempts=3, timeout_seconds=2400, backoff_base=120)
async def post_generate(ctx: JobContext) -> dict:
    """Write captions with Gemma, render creatives, build channel versions."""
    from .generate import generate_post

    return await generate_post(ctx.store_id, ctx.payload["post_id"], force=bool(ctx.payload.get("force")))


@on_final_failure("post.generate")
async def _generate_failed(ctx: JobContext, exc: BaseException) -> None:
    from ..errors import describe

    error_type, message = describe(exc)
    post_id = ctx.payload.get("post_id", "")
    await post_store.transition_post(
        ctx.store_id, post_id, from_statuses=("planned", "generating"), to_status="failed",
        error_type=error_type, error_message=message,
    )
    await record(ctx.store_id, "post", post_id, f"Generation failed: {error_type}: {message}",
                 level="error", stage="generate")


@job("post.publish", lane="postiz", max_attempts=5, timeout_seconds=900, backoff_base=60)
async def post_publish(ctx: JobContext) -> dict:
    """Upload media and create the post in Postiz."""
    from .publish import publish_post

    return await publish_post(ctx.store_id, ctx.payload["post_id"])


@on_final_failure("post.publish")
async def _publish_failed(ctx: JobContext, exc: BaseException) -> None:
    from .publish import mark_publish_failed

    await mark_publish_failed(ctx.store_id, ctx.payload.get("post_id", ""), exc)


@job("posts.reconcile", lane="postiz", max_attempts=3, timeout_seconds=600)
async def posts_reconcile(ctx: JobContext) -> dict:
    """Sync scheduled/published state back from Postiz."""
    from .freshness import sweep_scheduled
    from .reconcile import reconcile_store

    result = await reconcile_store(ctx.store_id, post_ids=ctx.payload.get("post_ids") or None)
    if not ctx.payload.get("post_ids"):
        result["freshness"] = await sweep_scheduled(ctx.store_id)
    return result


@job("postiz.webhook", lane="default", max_attempts=3, timeout_seconds=120)
async def postiz_webhook(ctx: JobContext) -> dict:
    """A Postiz 'post published' webhook: reconcile the posts it mentions."""
    from ..publishers.webhook import extract_post_ids

    event_id = int(ctx.payload["webhook_event_id"])
    event = await observability.get_webhook(event_id)
    if not event:
        return {"skipped": "webhook event not found"}
    external_ids = extract_post_ids(event.get("payload") or {})
    post_ids = []
    for external_id in external_ids:
        target = await post_store.find_target_by_external_id(external_id)
        if target and target["store_id"] == ctx.store_id:
            post_ids.append(target["post_id"])
    if post_ids:
        await enqueue("posts.reconcile", {"post_ids": sorted(set(post_ids))}, store_id=ctx.store_id,
                      dedupe_key=f"reconcile-webhook:{ctx.store_id}:{','.join(sorted(set(post_ids)))}")
    await observability.mark_webhook(event_id, status="processed")
    return {"external_ids": external_ids, "posts": post_ids}


# ------------------------------------------------------------------- analytics

@job("analytics.collect", lane="postiz", max_attempts=2, timeout_seconds=900)
async def analytics_collect(ctx: JobContext) -> dict:
    """Pull per-post metrics from Postiz for recently published posts."""
    from ..analytics.collector import collect_post_metrics

    return await collect_post_metrics(ctx.store_id)


@job("analytics.channels", lane="postiz", max_attempts=2, timeout_seconds=900)
async def analytics_channels(ctx: JobContext) -> dict:
    """Pull channel-level analytics (followers, impressions...) from Postiz."""
    from ..analytics.collector import collect_channel_metrics

    return await collect_channel_metrics(ctx.store_id)


# ----------------------------------------------------------------- maintenance

@job("maintenance.prune", lane="default", max_attempts=1, timeout_seconds=600)
async def maintenance_prune(ctx: JobContext) -> dict:
    """Delete old logs/finished jobs and media files no post references."""
    from db.social_engine import jobs as job_store

    removed = await observability.prune()
    removed["social_jobs"] = await job_store.prune_finished(30 * 86400)
    try:
        from ..creatives.storage import prune_orphans

        known = await post_store.all_asset_paths()
        removed["media_files"] = len(await prune_orphans(known, min_age_seconds=2 * 86400))
    except Exception as exc:  # media pruning is best-effort
        removed["media_error"] = f"{type(exc).__name__}: {exc}"
    return removed
