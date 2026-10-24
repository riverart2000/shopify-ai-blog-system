"""Publish an approved post to Postiz.

Steps (each safe to repeat):
1. Reconcile targets left "uncertain" by an earlier ambiguous create call.
2. Upload media not yet in Postiz (uploads are reused across retries).
3. One create request per distinct scheduled time (Postiz takes one date per
   request), inside the hourly create budget.
4. Record external ids; refresh the post's overall status.

Never re-sends a create call whose outcome is unknown — it reconciles first.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from db.social_engine import config_store
from db.social_engine import posts as post_store
from db.social_engine import products as product_store

from ..activity import record
from ..context import load_store_context
from ..errors import (
    Deferred, NotFoundError, RateLimited, SocialEngineError, UncertainOutcome, ValidationError, describe,
    is_retryable,
)
from ..jobs.queue import enqueue
from ..models import CONTENT_TYPE_LABELS, profile_for

logger = logging.getLogger("ai_blog_server.social_engine.pipeline")

_MIN_LEAD_SECONDS = 180
_PUBLISHABLE_POST = ("approved", "publishing", "partially_published", "scheduled")
AI_CONTENT_TYPES = {"ai_image_ad", "ai_video"}


def _utc(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


async def refresh_post_status(store_id: str, post_id: str) -> str:
    """Derive the post status from its targets (the single place this happens)."""
    post = await post_store.get_post(store_id, post_id)
    if not post:
        return ""
    statuses = [t["status"] for t in post["targets"]]
    if not statuses or post["status"] in ("cancelled", "rejected", "pending_approval", "planned", "generating"):
        return post["status"]
    live = [s for s in statuses if s != "cancelled"]
    if not live:
        new = "cancelled"
    elif all(s == "published" for s in live):
        new = "published"
    elif any(s == "failed" for s in live) and any(s in ("published", "scheduled", "draft") for s in live):
        new = "partially_published"
    elif all(s == "failed" for s in live):
        new = "failed"
    elif any(s in ("pending", "publishing", "uncertain") for s in live):
        new = "publishing"
    elif any(s == "published" for s in live):
        new = "partially_published" if any(s == "failed" for s in live) else "scheduled"
    else:
        new = "scheduled"
    if new != post["status"]:
        fields: dict[str, Any] = {}
        if new == "published":
            published = [t.get("published_at") for t in post["targets"] if t.get("published_at")]
            fields["published_at"] = max(published) if published else int(time.time())
        if new == "failed":
            errors = "; ".join(sorted({t["error_message"] for t in post["targets"] if t.get("error_message")}))
            fields["error_type"] = "PublishFailed"
            fields["error_message"] = errors[:2000]
        await post_store.update_post(store_id, post_id, status=new, **fields)
    return new


async def _reconcile_uncertain(publisher, post: dict) -> int:
    resolved = 0
    for target in post["targets"]:
        if target["status"] != "uncertain":
            continue
        found = await publisher.find_existing_post(
            target["channel_id"], _utc(target["scheduled_at"] or int(time.time())), target["caption"],
        )
        if found:
            await post_store.transition_target(
                target["id"], from_statuses=("uncertain",), to_status="scheduled",
                external_id=found.external_id, external_group=found.group or post["id"],
                error_type="", error_message="",
            )
        else:
            # Confirmed absent in Postiz → safe to create again.
            await post_store.transition_target(
                target["id"], from_statuses=("uncertain",), to_status="pending",
                error_type="", error_message="",
            )
        resolved += 1
    return resolved


async def _upload_assets(publisher, store_id: str, asset_ids: set[str], post_id: str):
    from ..publishers.base import OutboundMedia

    uploaded: dict[str, OutboundMedia] = {}
    processing: list[str] = []
    for asset in await post_store.get_assets(sorted(asset_ids)):
        media = OutboundMedia(asset_id=asset["id"], path=asset["path"], mime=asset["mime"], kind=asset["kind"],
                              external_id=asset.get("external_id", ""), external_path=asset.get("external_path", ""))
        status = "ready"
        if not media.external_id:
            result = await publisher.upload_media(media)
            await post_store.set_asset_uploaded(asset["id"], external_id=result.external_id,
                                                external_path=result.external_path)
            media.external_id, media.external_path = result.external_id, result.external_path
            status = result.status
            if result.warning:
                await record(store_id, "post", post_id, f"Postiz media warning: {result.warning}",
                             level="warning", stage="publish")
        elif media.kind == "video" and hasattr(publisher, "media_status"):
            # Uploaded on an earlier attempt: Postiz may still be adjusting the video.
            current = await publisher.media_status(media.external_id)
            status = current.status
            if current.external_path:
                media.external_path = current.external_path
        if status == "failed":
            raise ValidationError(f"Postiz could not process {asset['kind']} {asset['id']}.")
        if status == "processing":
            processing.append(asset["id"])
        uploaded[asset["id"]] = media
    if processing:
        raise Deferred(f"Postiz is still processing {len(processing)} video(s); checking again shortly.",
                       run_at=int(time.time()) + 90)
    return uploaded


async def publish_post(store_id: str, post_id: str) -> dict[str, Any]:
    from ..publishers import get_publisher
    from ..publishers.base import OutboundPost, OutboundTarget

    post = await post_store.get_post(store_id, post_id)
    if not post:
        raise NotFoundError(f"Post {post_id} was not found.")
    if post["status"] not in _PUBLISHABLE_POST:
        return {"skipped": True, "reason": f"status is {post['status']}"}
    if post["status"] == "approved":
        await post_store.transition_post(store_id, post_id, from_statuses=("approved",), to_status="publishing")

    sctx = await load_store_context(store_id, correlation_id=post_id)
    publisher = get_publisher(sctx, correlation_id=post_id)
    try:
        await _reconcile_uncertain(publisher, post)
        post = await post_store.get_post(store_id, post_id) or post
        pending = [t for t in post["targets"] if t["status"] == "pending"]
        if not pending:
            status = await refresh_post_status(store_id, post_id)
            return {"status": status, "created": 0}

        from .freshness import apply_verdict, check_post

        verdict, reason = await check_post(store_id, post)
        if verdict != "ok":
            await apply_verdict(store_id, post, verdict, reason)
            return {"status": "stale", "verdict": verdict, "reason": reason}

        now = int(time.time())
        mode = post.get("publish_mode") or "schedule"
        for target in pending:
            when = int(target.get("scheduled_at") or 0)
            if mode == "now":
                when = now
            elif when < now + _MIN_LEAD_SECONDS:
                when = now + _MIN_LEAD_SECONDS
            if when != target.get("scheduled_at"):
                await post_store.update_target(target["id"], scheduled_at=when)
            target["scheduled_at"] = when

        asset_ids = {aid for t in pending for aid in t.get("media_ids") or []}
        media = await _upload_assets(publisher, store_id, asset_ids, post_id)

        campaign = await config_store.get_campaign(store_id, post["campaign_id"]) if post.get("campaign_id") else None
        tags = [CONTENT_TYPE_LABELS.get(post["content_type"], post["content_type"])]
        if campaign:
            tags.insert(0, campaign["name"])

        groups: dict[int, list[dict]] = defaultdict(list)
        for target in pending:
            groups[int(target["scheduled_at"])].append(target)

        channel_names = {c["channel_id"]: c["name"] for c in await config_store.list_channels(store_id)}
        ai_made = post["content_type"] in AI_CONTENT_TYPES
        created = 0
        for when, group in sorted(groups.items()):
            outbound = []
            for target in group:
                profile = profile_for(target["provider"])
                comments = [target["first_comment"]] if target.get("first_comment") and profile.supports_comments else []
                settings = dict(target.get("settings") or {})
                settings.setdefault("channel_name", channel_names.get(target["channel_id"], target["channel_id"]))
                if ai_made:  # honest AI-content disclosure where networks support it
                    settings.setdefault("made_with_ai", True)
                    settings.setdefault("video_made_with_ai", True)
                outbound.append(OutboundTarget(
                    target_id=target["id"], channel_id=target["channel_id"], provider=target["provider"],
                    caption=target["caption"], scheduled_at=_utc(when),
                    media=[media[aid] for aid in target.get("media_ids") or [] if aid in media],
                    comments=comments, settings=settings,
                    title=target.get("title", ""), link_url=target.get("link_url", ""),
                ))
            request = OutboundPost(
                post_id=post_id, group=post_id, mode=mode if mode in ("schedule", "now", "draft") else "schedule",
                targets=outbound, tags=tags, short_links=sctx.settings.postiz.short_links,
            )
            for target in group:
                await post_store.transition_target(target["id"], from_statuses=("pending",), to_status="publishing")
            try:
                results = await publisher.create_posts(request)
            except UncertainOutcome as exc:
                for target in group:
                    await post_store.transition_target(
                        target["id"], from_statuses=("publishing",), to_status="uncertain",
                        error_type="UncertainOutcome", error_message=exc.message,
                    )
                await record(store_id, "post", post_id, f"Postiz outcome unknown; will reconcile: {exc.message}",
                             level="warning", stage="publish")
                await enqueue("post.publish", {"post_id": post_id}, store_id=store_id, run_in=120,
                              dedupe_key=f"publish-reconcile:{post_id}", correlation_id=post_id)
                continue
            except RateLimited:
                for target in group:
                    await post_store.transition_target(target["id"], from_statuses=("publishing",), to_status="pending")
                raise
            except SocialEngineError as exc:
                details = exc.details or {}
                published = details.get("published") or []
                done = {item.get("target_id") for item in published if isinstance(item, dict)}
                for item in published:
                    if isinstance(item, dict) and item.get("target_id"):
                        await post_store.transition_target(
                            item["target_id"], from_statuses=("publishing",),
                            to_status="draft" if mode == "draft" else "scheduled",
                            external_id=str(item.get("external_id", "")),
                            external_group=str(item.get("external_group") or post_id),
                        )
                        created += 1
                if details.get("reconcile"):
                    # Postiz answered but didn't confirm every channel: look them up, never re-send blindly.
                    for target in group:
                        if target["id"] not in done:
                            await post_store.transition_target(
                                target["id"], from_statuses=("publishing",), to_status="uncertain",
                                error_type=type(exc).__name__, error_message=exc.message,
                            )
                    await enqueue("post.publish", {"post_id": post_id}, store_id=store_id, run_in=120,
                                  dedupe_key=f"publish-reconcile:{post_id}", correlation_id=post_id)
                    continue
                retryable = is_retryable(exc)
                for target in group:
                    if target["id"] in done:
                        continue
                    await post_store.transition_target(
                        target["id"], from_statuses=("publishing",),
                        to_status="pending" if retryable else "failed",
                        error_type=type(exc).__name__, error_message=exc.message,
                    )
                if retryable:
                    raise
                await record(store_id, "post", post_id, f"Publishing failed: {exc.message}",
                             level="error", stage="publish")
                continue

            by_target = {r.target_id: r for r in results}
            for target in group:
                result = by_target.get(target["id"])
                if result is None:
                    continue
                await post_store.transition_target(
                    target["id"], from_statuses=("publishing",),
                    to_status="draft" if mode == "draft" else "scheduled",
                    external_id=result.external_id, external_group=result.external_group or post_id,
                    error_type="", error_message="",
                )
                created += 1

        status = await refresh_post_status(store_id, post_id)
        if created:
            await product_store.mark_posted(store_id, post.get("product_ids") or [])
            await record(store_id, "post", post_id,
                         f"Sent {created} channel version(s) to Postiz ({mode})", stage="publish")
        return {"status": status, "created": created}
    finally:
        await publisher.aclose()


async def mark_publish_failed(store_id: str, post_id: str, exc: BaseException) -> None:
    """Final-failure hook: make the post's state honest after retries ran out."""
    error_type, message = describe(exc)
    post = await post_store.get_post(store_id, post_id)
    if not post:
        return
    for target in post["targets"]:
        if target["status"] in ("pending", "publishing"):
            await post_store.transition_target(
                target["id"], from_statuses=("pending", "publishing"), to_status="failed",
                error_type=error_type, error_message=message,
            )
    await refresh_post_status(store_id, post_id)
    await record(store_id, "post", post_id, f"Publishing gave up: {error_type}: {message}",
                 level="error", stage="publish")
