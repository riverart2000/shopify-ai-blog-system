"""Campaigns (auto-pilot rules) and templates."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from db.social_engine import config_store, observability
from db.social_engine import posts as post_store
from services.social_engine.campaigns.base import rule_catalog
from services.social_engine.campaigns.engine import evaluate_campaign, validate_campaign_config
from services.social_engine.errors import NotFoundError, ValidationError
from services.social_engine.jobs.queue import enqueue

from .common import require_api_key, resolve_store_id

router = APIRouter(prefix="/api/social-engine", dependencies=[Depends(require_api_key)])


@router.get("/campaigns")
async def campaigns(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    rows = await config_store.list_campaigns(sid)
    counts: dict[str, dict] = {}
    for row in rows:
        posts = await post_store.list_posts(sid, campaign_id=row["id"], limit=500)
        tally: dict[str, int] = {}
        for post in posts:
            tally[post["status"]] = tally.get(post["status"], 0) + 1
        counts[row["id"]] = tally
        row["state"] = await observability.get_state(f"campaign_state:{row['id']}", {}) or {}
        row["state"] = {k: v for k, v in row["state"].items() if k in ("event_cursor", "promo_texts", "collection_index")}
    return {
        "campaigns": [row | {"post_counts": counts.get(row["id"], {})} for row in rows],
        "rules": rule_catalog(),
    }


class CampaignRequest(BaseModel):
    store_id: str = ""
    id: str = ""
    name: str = Field(..., min_length=1, max_length=120)
    rule_type: str
    status: Literal["active", "paused", "archived"] = "paused"
    config: dict[str, Any] = Field(default_factory=dict)
    product_filter: dict[str, Any] = Field(default_factory=dict)
    channel_ids: list[str] = Field(default_factory=list)
    caption: dict[str, Any] = Field(default_factory=dict)
    content: dict[str, Any] | None = None
    approval_mode: Literal["manual", "ai_review", "auto"] = "manual"


@router.post("/campaigns")
async def save_campaign(payload: CampaignRequest) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    schedule = (payload.config or {}).get("schedule")
    config = validate_campaign_config(payload.rule_type, payload.config)
    if schedule:
        config["schedule"] = schedule
    existing = await config_store.get_campaign(sid, payload.id) if payload.id else None
    if payload.id and not existing:
        raise NotFoundError("Campaign not found.")
    data = payload.model_dump(exclude={"store_id"})
    if data.get("content") is None:
        data["content"] = (existing or {}).get("content") or {}
    campaign_id = await config_store.save_campaign(sid, {**data, "config": config})
    return {"ok": True, "id": campaign_id}


class CampaignStatus(BaseModel):
    store_id: str = ""
    status: Literal["active", "paused", "archived"]


@router.post("/campaigns/{campaign_id}/status")
async def set_status(campaign_id: str, payload: CampaignStatus) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    if not await config_store.set_campaign_status(sid, campaign_id, payload.status):
        raise NotFoundError("Campaign not found.")
    if payload.status == "active":
        await enqueue("campaigns.evaluate", {"campaign_id": campaign_id}, store_id=sid,
                      dedupe_key=f"campaign:{campaign_id}", correlation_id=campaign_id, priority=8)
    return {"ok": True}


@router.post("/campaigns/{campaign_id}/preview")
async def preview(campaign_id: str, store_id: str = "") -> dict[str, Any]:
    """Dry run: what would this campaign plan right now? Nothing is saved."""
    return await evaluate_campaign(await resolve_store_id(store_id), campaign_id, dry_run=True)


@router.post("/campaigns/{campaign_id}/run")
async def run_now(campaign_id: str, store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    job_id, created = await enqueue("campaigns.evaluate", {"campaign_id": campaign_id}, store_id=sid,
                                    dedupe_key=f"campaign:{campaign_id}", correlation_id=campaign_id, priority=8)
    return {"ok": True, "job_id": job_id, "already_queued": not created}


@router.post("/campaigns/{campaign_id}/delete")
async def delete_campaign(campaign_id: str, store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    if await post_store.list_posts(sid, campaign_id=campaign_id, statuses=("publishing", "scheduled"), limit=1):
        raise ValidationError("This campaign has scheduled posts. Pause it (or cancel those posts) instead.")
    if not await config_store.delete_campaign(sid, campaign_id):
        raise NotFoundError("Campaign not found.")
    return {"ok": True}


# ---------------------------------------------------------------- templates

@router.get("/templates")
async def templates(store_id: str = "", content_type: str = "") -> dict[str, Any]:
    from services.social_engine.creatives import seed_builtin_templates

    sid = await resolve_store_id(store_id)
    rows = await config_store.list_templates(sid, content_type=content_type)
    if not rows:
        await seed_builtin_templates(sid)
        rows = await config_store.list_templates(sid, content_type=content_type)
    return {"templates": [{k: v for k, v in row.items() if k != "spec"} for row in rows]}


class TemplateActive(BaseModel):
    store_id: str = ""
    active: bool


@router.post("/templates/{key}/active")
async def template_active(key: str, payload: TemplateActive) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    if not await config_store.set_template_active(sid, key, payload.active):
        raise NotFoundError("Template not found.")
    return {"ok": True}


@router.post("/templates/seed")
async def seed(store_id: str = "") -> dict[str, Any]:
    from services.social_engine.creatives import seed_builtin_templates

    return {"ok": True, "count": await seed_builtin_templates(await resolve_store_id(store_id))}


@router.get("/templates/{key}/preview")
async def template_preview(key: str, format: str = "square", store_id: str = ""):
    from services.social_engine.creatives import render_preview

    media = await render_preview(await resolve_store_id(store_id), key, format)
    return FileResponse(media.path, media_type=media.mime, headers={"Cache-Control": "private, max-age=300"})
