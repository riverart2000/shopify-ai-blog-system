"""User actions on posts (approval queue, studio, calendar).

Every action is a guarded state transition plus an activity row, and returns
a small dict for the API. Long work is queued as jobs, never done inline.
"""
from __future__ import annotations

import time
from typing import Any

from db.social_engine import config_store
from db.social_engine import posts as post_store

from ..activity import record
from ..context import load_store_context
from ..errors import NotFoundError, ValidationError
from ..jobs.queue import enqueue
from ..models import EDITABLE_POST_STATUSES, ContentType
from ..scheduling.slots import SlotPlanner
from .publish import refresh_post_status


async def _require_post(store_id: str, post_id: str) -> dict:
    post = await post_store.get_post(store_id, post_id)
    if not post:
        raise NotFoundError(f"Post {post_id} was not found.")
    return post


async def approve(store_id: str, post_id: str, *, by: str = "merchant") -> dict[str, Any]:
    post = await _require_post(store_id, post_id)
    if not [t for t in post["targets"] if t["status"] in ("pending", "failed")]:
        raise ValidationError("This post has no channel versions ready to publish; regenerate it first.")
    ok = await post_store.transition_post(
        store_id, post_id, from_statuses=("pending_approval", "failed"), to_status="approved",
        approved_at=int(time.time()), approved_by=by, error_type="", error_message="",
    )
    if not ok:
        raise ValidationError(f"Only posts waiting for approval can be approved (status: {post['status']}).")
    for target in post["targets"]:
        if target["status"] == "failed":
            await post_store.transition_target(target["id"], from_statuses=("failed",), to_status="pending",
                                               error_type="", error_message="")
    await record(store_id, "post", post_id, f"Approved by {by}", stage="approve")
    job_id, _ = await enqueue("post.publish", {"post_id": post_id}, store_id=store_id,
                              dedupe_key=f"publish:{post_id}", correlation_id=post_id)
    return {"ok": True, "status": "approved", "job_id": job_id}


async def approve_many(store_id: str, post_ids: list[str], *, by: str = "merchant") -> dict[str, Any]:
    results = {"approved": [], "errors": {}}
    for post_id in post_ids:
        try:
            await approve(store_id, post_id, by=by)
            results["approved"].append(post_id)
        except Exception as exc:
            results["errors"][post_id] = str(exc)
    return results


async def reject(store_id: str, post_id: str, *, reason: str = "", by: str = "merchant") -> dict[str, Any]:
    ok = await post_store.transition_post(
        store_id, post_id, from_statuses=("planned", "pending_approval", "approved", "failed"),
        to_status="rejected", rejected_reason=reason[:1000],
    )
    if not ok:
        post = await _require_post(store_id, post_id)
        raise ValidationError(f"This post can't be rejected while it is '{post['status']}'.")
    await record(store_id, "post", post_id, f"Rejected by {by}" + (f": {reason}" if reason else ""), stage="approve")
    return {"ok": True, "status": "rejected"}


async def regenerate(store_id: str, post_id: str, *, brief: str | None = None, angle: str | None = None) -> dict:
    post = await _require_post(store_id, post_id)
    if post["status"] not in EDITABLE_POST_STATUSES and post["status"] != "rejected":
        raise ValidationError(f"Posts that are '{post['status']}' can't be regenerated.")
    fields: dict[str, Any] = {}
    if brief is not None:
        fields["brief"] = brief[:2000]
    if angle is not None:
        fields["angle"] = angle[:500]
    await post_store.transition_post(
        store_id, post_id, from_statuses=("planned", "pending_approval", "approved", "failed", "rejected"),
        to_status="planned", **fields,
    )
    job_id, _ = await enqueue("post.generate", {"post_id": post_id, "force": True}, store_id=store_id,
                              dedupe_key=f"generate:{post_id}", correlation_id=post_id)
    await record(store_id, "post", post_id, "Regeneration requested", stage="generate")
    return {"ok": True, "job_id": job_id}


async def edit(
    store_id: str,
    post_id: str,
    *,
    target_captions: dict[str, str] | None = None,
    first_comments: dict[str, str] | None = None,
    titles: dict[str, str] | None = None,
    target_settings: dict[str, dict] | None = None,
    scheduled_at: int | None = None,
    remove_channel_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Edit captions/settings/time before publishing (Outfy: "edit before it goes out")."""
    post = await _require_post(store_id, post_id)
    if post["status"] not in ("pending_approval", "approved", "failed"):
        raise ValidationError(f"Posts that are '{post['status']}' can't be edited here.")
    if scheduled_at is not None and scheduled_at < int(time.time()) + 120:
        raise ValidationError("Pick a time at least two minutes from now.")
    for target in post["targets"]:
        if target["status"] not in ("pending", "failed"):
            continue
        cid = target["channel_id"]
        changes: dict[str, Any] = {}
        if target_captions and cid in target_captions:
            caption = target_captions[cid].strip()
            if not caption:
                raise ValidationError(f"The {target['provider']} caption can't be empty.")
            changes["caption"] = caption
        if first_comments and cid in first_comments:
            changes["first_comment"] = first_comments[cid].strip()
        if titles and cid in titles:
            changes["title"] = titles[cid].strip()
        if target_settings and cid in target_settings:
            changes["settings"] = {**(target.get("settings") or {}), **target_settings[cid]}
        if scheduled_at is not None:
            changes["scheduled_at"] = int(scheduled_at)
        if remove_channel_ids and cid in remove_channel_ids:
            await post_store.transition_target(target["id"], from_statuses=("pending", "failed"), to_status="cancelled")
            continue
        if changes:
            await post_store.update_target(target["id"], **changes)
    if scheduled_at is not None:
        await post_store.update_post(store_id, post_id, scheduled_at=int(scheduled_at))
    await record(store_id, "post", post_id, "Edited before publishing", stage="edit")
    return {"ok": True}


async def cancel(store_id: str, post_id: str, *, by: str = "merchant") -> dict[str, Any]:
    """Cancel a post; anything already scheduled in Postiz is deleted there too."""
    from ..publishers import get_publisher

    post = await _require_post(store_id, post_id)
    if post["status"] in ("published", "cancelled"):
        raise ValidationError(f"Post is already {post['status']}.")
    remote = [t for t in post["targets"] if t.get("external_id") and t["status"] in ("scheduled", "draft")]
    if remote:
        sctx = await load_store_context(store_id, correlation_id=post_id)
        publisher = get_publisher(sctx, correlation_id=post_id)
        try:
            # Each channel version is its own Postiz group, so delete them one by one.
            for target in remote:
                await publisher.delete_post(target["external_id"])
        finally:
            await publisher.aclose()
    for target in post["targets"]:
        if target["status"] != "published":
            await post_store.update_target(target["id"], status="cancelled")
    await post_store.update_post(store_id, post_id, status="cancelled")
    from db.social_engine import jobs as job_store

    await job_store.cancel_for_correlation(post_id, ["post.generate", "post.publish"])
    await record(store_id, "post", post_id, f"Cancelled by {by}", stage="cancel")
    return {"ok": True, "status": await refresh_post_status(store_id, post_id) or "cancelled"}


async def publish_now(store_id: str, post_id: str) -> dict[str, Any]:
    post = await _require_post(store_id, post_id)
    if post["status"] not in ("pending_approval", "approved", "failed"):
        raise ValidationError(f"Posts that are '{post['status']}' can't be published now.")
    await post_store.update_post(store_id, post_id, publish_mode="now")
    if post["status"] == "approved":
        job_id, _ = await enqueue("post.publish", {"post_id": post_id}, store_id=store_id,
                                  dedupe_key=f"publish:{post_id}", correlation_id=post_id)
        return {"ok": True, "job_id": job_id}
    return await approve(store_id, post_id)


async def retry_failed(store_id: str, post_id: str) -> dict[str, Any]:
    post = await _require_post(store_id, post_id)
    failed = [t for t in post["targets"] if t["status"] == "failed"]
    if not failed:
        if post["status"] == "failed":  # generation failed before any channel version existed
            return await regenerate(store_id, post_id)
        raise ValidationError("There is nothing to retry on this post.")
    for target in failed:
        await post_store.transition_target(target["id"], from_statuses=("failed",), to_status="pending",
                                           error_type="", error_message="")
    await post_store.update_post(store_id, post_id, status="approved", error_type="", error_message="")
    job_id, _ = await enqueue("post.publish", {"post_id": post_id}, store_id=store_id,
                              dedupe_key=f"publish:{post_id}", correlation_id=post_id)
    await record(store_id, "post", post_id, f"Retrying {len(failed)} failed channel version(s)", stage="publish")
    return {"ok": True, "job_id": job_id}


async def create_manual_post(
    store_id: str,
    *,
    content_type: str,
    product_ids: list[str],
    channel_ids: list[str],
    template_key: str = "",
    scheduled_at: int | None = None,
    brief: str = "",
    angle: str = "",
    texts: dict[str, str] | None = None,
    caption_overrides: dict | None = None,
    approval_mode: str = "manual",
    publish_mode: str = "schedule",
    link_url: str = "",
    channel_settings: dict[str, dict] | None = None,
    theme_key: str = "",
    ai_background: bool = False,
    postiz_media_search: str = "",
) -> dict[str, Any]:
    """Studio "create a post": queue generation now; time = given or next free slot."""
    try:
        ContentType(content_type)
    except ValueError as exc:
        raise ValidationError(f"Unknown content type '{content_type}'.") from exc
    if not channel_ids:
        raise ValidationError("Choose at least one channel.")
    sctx = await load_store_context(store_id)
    channels = [c for c in await config_store.list_channels(store_id, usable_only=True)
                if str(c["channel_id"]) in set(channel_ids)]
    if not channels:
        raise ValidationError("None of the chosen channels are connected and enabled.")
    channel_times: dict[str, int] = {}
    if publish_mode == "now":
        scheduled_at = int(time.time())
    if scheduled_at:
        channel_times = {str(c["channel_id"]): int(scheduled_at) for c in channels}
    else:
        now = int(time.time())
        existing = await post_store.list_targets(
            store_id, statuses=("pending", "publishing", "uncertain", "scheduled", "published", "draft"),
            scheduled_from=now - 86400, scheduled_to=now + 22 * 86400,
        )
        existing += await post_store.planned_channel_times(
            store_id, scheduled_from=now - 86400, scheduled_to=now + 22 * 86400,
        )
        planner = SlotPlanner(sctx.settings.schedule, channels, existing)
        for when, cids in planner.plan([str(c["channel_id"]) for c in channels]):
            channel_times.update({cid: int(when.timestamp()) for cid in cids})
        if not channel_times:
            raise ValidationError("No free posting slot in the next three weeks for these channels.")
    post_id, _ = await post_store.create_post(store_id, {
        "rule_type": "manual", "content_type": content_type, "template_key": template_key,
        "product_ids": product_ids, "brief": brief, "angle": angle, "status": "planned",
        "approval_mode": approval_mode, "publish_mode": publish_mode,
        "scheduled_at": min(channel_times.values()),
        "options": {k: v for k, v in {
            "channel_ids": list(channel_times), "channel_times": channel_times,
            "texts": texts or {}, "caption_overrides": caption_overrides or {},
            "link_override": link_url.strip(), "channel_settings": channel_settings or {},
            "theme_key": theme_key, "ai_background": ai_background,
            "postiz_media_search": postiz_media_search.strip(),
        }.items() if v not in ("", None, {}) or k in ("texts", "caption_overrides")},
    })
    await enqueue("post.generate", {"post_id": post_id}, store_id=store_id,
                  dedupe_key=f"generate:{post_id}", correlation_id=post_id, priority=10)
    await record(store_id, "post", post_id, "Created in Studio", stage="plan")
    return {"ok": True, "post_id": post_id, "scheduled_at": min(channel_times.values())}
