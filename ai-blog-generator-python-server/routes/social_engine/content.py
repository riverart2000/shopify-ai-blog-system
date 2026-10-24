"""Products, posts (approval queue / studio), calendar and media previews."""
from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from db.social_engine import config_store, jobs as job_store, observability
from db.social_engine import posts as post_store
from db.social_engine import products as product_store
from services.social_engine.errors import NotFoundError, ValidationError
from services.social_engine.jobs.queue import enqueue
from services.social_engine.pipeline import actions
from services.social_engine.settings import load_settings, save_settings

from .common import require_api_key, resolve_store_id

router = APIRouter(prefix="/api/social-engine", dependencies=[Depends(require_api_key)])


# ----------------------------------------------------------------- products

@router.get("/products")
async def products(
    store_id: str = "", search: str = "", collection_id: str = "", available_only: bool = False,
    on_sale_only: bool = False, order: str = "title", page: int = 1, page_size: int = 48,
) -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    page_size = max(1, min(page_size, 200))
    rows = await product_store.list_products(
        sid, search=search, collection_id=collection_id, available_only=available_only,
        on_sale_only=on_sale_only, order=order, limit=page_size, offset=(max(page, 1) - 1) * page_size,
    )
    excluded = set((await load_settings(sid)).content.excluded_product_ids)
    for row in rows:
        row["excluded"] = str(row["product_id"]) in excluded
        row.pop("description", None)
        row["variants"] = len(row.get("variants") or [])
    return {
        "products": rows, "page": page, "page_size": page_size,
        "counts": await product_store.count_products(sid),
        "collections": [{"id": c["collection_id"], "title": c["title"], "products_count": c["products_count"]}
                        for c in await product_store.list_collections(sid)],
    }


@router.get("/products/{product_id}")
async def product_detail(product_id: str, store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    product = await product_store.get_product(sid, product_id)
    if not product:
        raise NotFoundError(f"Product {product_id} is not synced.")
    posts = await post_store.list_posts(sid, product_id=str(product_id), order="newest", limit=50)
    events = await product_store.list_events(sid, product_id=str(product_id), limit=50)
    return {"product": product, "posts": posts, "events": events}


class SyncRequest(BaseModel):
    store_id: str = ""
    full: bool = False


@router.post("/products/sync")
async def products_sync(payload: SyncRequest) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    job_type = "catalog.sync_full" if payload.full else "catalog.sync"
    job_id, created = await enqueue(job_type, {"reason": "manual", "full": payload.full}, store_id=sid,
                                    dedupe_key=f"{job_type}:manual:{sid}", priority=10)
    return {"ok": True, "job_id": job_id, "already_queued": not created}


class ExcludeRequest(BaseModel):
    store_id: str = ""
    product_id: str
    excluded: bool


@router.post("/products/exclude")
async def exclude_product(payload: ExcludeRequest) -> dict[str, Any]:
    """Outfy's per-product "Exclude from automation"."""
    sid = await resolve_store_id(payload.store_id)
    settings = await load_settings(sid)
    excluded = [p for p in settings.content.excluded_product_ids if p != payload.product_id]
    if payload.excluded:
        excluded.append(payload.product_id)
    await save_settings(sid, {"content": {"excluded_product_ids": excluded}})
    return {"ok": True, "excluded": payload.excluded}


# -------------------------------------------------------------------- posts

# Options the admin may show on a post: what was requested, not internals.
_SUMMARY_OPTION_KEYS = ("channel_ids", "texts", "decided_by", "decision_reason", "theme_key", "ai_background",
                        "postiz_media_search", "channel_settings", "link_override")


def _post_summary(post: dict, targets: list[dict], media: list[dict], *,
                  products: dict[str, dict] | None = None, channels: dict[str, dict] | None = None) -> dict:
    preview = next((m for m in media if m["kind"] == "image" and m["role"] != "thumbnail"), None) \
        or next((m for m in media if m["role"] == "thumbnail"), None)
    return {
        **{k: v for k, v in post.items() if k not in ("generation", "options")},
        "options": {k: v for k, v in (post.get("options") or {}).items() if k in _SUMMARY_OPTION_KEYS},
        "warnings": (post.get("generation") or {}).get("warnings", []),
        "model": (post.get("generation") or {}).get("model", ""),
        "preview_asset_id": preview["id"] if preview else "",
        "media": [{"id": m["id"], "kind": m["kind"], "format": m["format"], "role": m["role"],
                   "width": m["width"], "height": m["height"], "duration_ms": m["duration_ms"]} for m in media],
        "targets": [
            {k: v for k, v in t.items() if k not in ("settings",)} | {
                "settings": t.get("settings") or {},
                "channel_name": ((channels or {}).get(t["channel_id"]) or {}).get("name", t["channel_id"]),
                "channel_picture": ((channels or {}).get(t["channel_id"]) or {}).get("picture", ""),
            }
            for t in targets
        ],
        "products": [
            {"product_id": pid, "title": p.get("title", ""), "image": p.get("featured_image_url", ""),
             "url": p.get("url", ""), "available": bool(p.get("available"))}
            for pid in post.get("product_ids") or [] if (p := (products or {}).get(str(pid)))
        ],
        "postiz_tools": (post.get("generation") or {}).get("postiz_tools", []),
    }


@router.get("/posts")
async def list_posts(
    store_id: str = "", status: str = "", campaign_id: str = "", product_id: str = "", content_type: str = "",
    scheduled_from: int | None = None, scheduled_to: int | None = None, search: str = "",
    order: str = "scheduled", page: int = 1, page_size: int = 25,
) -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    statuses = [s for s in status.split(",") if s] if status else []
    page_size = max(1, min(page_size, 100))
    rows = await post_store.list_posts(
        sid, statuses=statuses, campaign_id=campaign_id, product_id=product_id, content_type=content_type,
        scheduled_from=scheduled_from, scheduled_to=scheduled_to, search=search, order=order,
        limit=page_size, offset=(max(page, 1) - 1) * page_size,
    )
    product_ids = sorted({str(pid) for row in rows for pid in row.get("product_ids") or []})
    products = {p["product_id"]: p for p in await product_store.get_products(sid, product_ids)}
    channels = {c["channel_id"]: c for c in await config_store.list_channels(sid)}
    items = []
    for row in rows:
        items.append(_post_summary(row, await post_store.list_targets_for_post(row["id"]),
                                   await post_store.list_assets(row["id"]), products=products, channels=channels))
    return {"posts": items, "counts": await post_store.count_posts_by_status(sid), "page": page, "page_size": page_size}


@router.get("/posts/{post_id}")
async def post_detail(post_id: str, store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    post = await post_store.get_post(sid, post_id)
    if not post:
        raise NotFoundError(f"Post {post_id} was not found.")
    products = await product_store.get_products(sid, post.get("product_ids") or [])
    channels = {c["channel_id"]: c for c in await config_store.list_channels(sid)}
    detail = _post_summary(post, post["targets"], post["media"],
                           products={p["product_id"]: p for p in products}, channels=channels)
    return {
        "post": detail,
        "generation": post.get("generation") or {},
        "review": post.get("review") or {},
        "products": [{"product_id": p["product_id"], "title": p["title"], "url": p["url"],
                      "price_min": p["price_min"], "featured_image_url": p["featured_image_url"]} for p in products],
        "timeline": await observability.list_activity(entity_type="post", entity_id=post_id, limit=100),
        "jobs": await job_store.list_jobs(correlation_id=post_id, limit=30),
        "api_calls": await observability.list_api_calls(correlation_id=post_id, limit=40),
        "decisions": await observability.list_decisions(subject_id=post_id, limit=10),
        "metrics": await post_store.latest_metrics_for_post(sid, post_id),
    }


class CreatePostRequest(BaseModel):
    store_id: str = ""
    content_type: str
    product_ids: list[str] = Field(default_factory=list)
    channel_ids: list[str]
    template_key: str = ""
    scheduled_at: int | None = None
    brief: str = Field("", max_length=2000)
    angle: str = Field("", max_length=500)
    texts: dict[str, str] = Field(default_factory=dict)
    caption_overrides: dict[str, Any] = Field(default_factory=dict)
    approval_mode: Literal["manual", "ai_review", "auto"] = "manual"
    publish_mode: Literal["schedule", "now", "draft"] = "schedule"
    link_url: str = Field("", max_length=1000)
    channel_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    theme_key: str = ""
    ai_background: bool = False
    postiz_media_search: str = Field("", max_length=80)


@router.post("/posts")
async def create_post(payload: CreatePostRequest) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    return await actions.create_manual_post(sid, **payload.model_dump(exclude={"store_id"}))


class PostAction(BaseModel):
    store_id: str = ""
    reason: str = ""
    brief: str | None = None
    angle: str | None = None


@router.post("/posts/{post_id}/approve")
async def approve(post_id: str, payload: PostAction) -> dict[str, Any]:
    return await actions.approve(await resolve_store_id(payload.store_id), post_id)


class BulkApprove(BaseModel):
    store_id: str = ""
    post_ids: list[str]


@router.post("/posts/approve-bulk")
async def approve_bulk(payload: BulkApprove) -> dict[str, Any]:
    return await actions.approve_many(await resolve_store_id(payload.store_id), payload.post_ids[:200])


@router.post("/posts/{post_id}/reject")
async def reject(post_id: str, payload: PostAction) -> dict[str, Any]:
    return await actions.reject(await resolve_store_id(payload.store_id), post_id, reason=payload.reason)


@router.post("/posts/{post_id}/regenerate")
async def regenerate(post_id: str, payload: PostAction) -> dict[str, Any]:
    return await actions.regenerate(await resolve_store_id(payload.store_id), post_id,
                                    brief=payload.brief, angle=payload.angle)


@router.post("/posts/{post_id}/cancel")
async def cancel(post_id: str, payload: PostAction) -> dict[str, Any]:
    return await actions.cancel(await resolve_store_id(payload.store_id), post_id)


@router.post("/posts/{post_id}/publish-now")
async def publish_now(post_id: str, payload: PostAction) -> dict[str, Any]:
    return await actions.publish_now(await resolve_store_id(payload.store_id), post_id)


@router.post("/posts/{post_id}/retry")
async def retry(post_id: str, payload: PostAction) -> dict[str, Any]:
    return await actions.retry_failed(await resolve_store_id(payload.store_id), post_id)


class EditRequest(BaseModel):
    store_id: str = ""
    captions: dict[str, str] | None = None
    first_comments: dict[str, str] | None = None
    titles: dict[str, str] | None = None
    target_settings: dict[str, dict] | None = None
    scheduled_at: int | None = None
    remove_channel_ids: list[str] | None = None


@router.post("/posts/{post_id}/edit")
async def edit(post_id: str, payload: EditRequest) -> dict[str, Any]:
    return await actions.edit(
        await resolve_store_id(payload.store_id), post_id, target_captions=payload.captions,
        first_comments=payload.first_comments, titles=payload.titles, target_settings=payload.target_settings,
        scheduled_at=payload.scheduled_at, remove_channel_ids=payload.remove_channel_ids,
    )


@router.get("/calendar")
async def calendar(store_id: str = "", start: int | None = None, end: int | None = None) -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    now = int(time.time())
    start = start or now - 7 * 86400
    end = end or now + 35 * 86400
    if end - start > 120 * 86400:
        raise ValidationError("Calendar range is limited to 120 days.")
    targets = await post_store.list_targets(sid, scheduled_from=start, scheduled_to=end, limit=5000)
    channels = {c["channel_id"]: c for c in await config_store.list_channels(sid)}
    return {
        "start": start, "end": end,
        "items": [
            {"target_id": t["id"], "post_id": t["post_id"], "channel_id": t["channel_id"],
             "channel_name": (channels.get(t["channel_id"]) or {}).get("name", ""), "provider": t["provider"],
             "status": t["status"], "post_status": t["post_status"], "scheduled_at": t["scheduled_at"],
             "content_type": t["content_type"], "title": t["post_title"], "release_url": t["release_url"],
             "error": t["error_message"]}
            for t in targets
        ],
    }


@router.get("/media/{asset_id}")
async def media(asset_id: str):
    """Serve a rendered asset (the embedded app proxies this with a signed URL)."""
    from services.social_engine.creatives.storage import resolve_media_path

    asset = await post_store.get_asset(asset_id)
    if not asset:
        raise NotFoundError("Media not found.")
    path = resolve_media_path(asset["path"])
    return FileResponse(path, media_type=asset.get("mime") or None,
                        headers={"Cache-Control": "private, max-age=3600"})
