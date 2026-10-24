"""Overview, settings, health and channels."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import db
from db.social_engine import config_store, jobs as job_store, observability
from db.social_engine import posts as post_store
from db.social_engine import products as product_store
from services.social_engine.campaigns.base import rule_catalog
from services.social_engine.errors import ValidationError
from services.social_engine.health import run_doctor
from services.social_engine.jobs.queue import enqueue
from services.social_engine.jobs.worker import worker_health
from services.social_engine.models import CONTENT_TYPE_LABELS, FORMAT_SIZES, PROVIDER_PROFILES, profile_for
from services.social_engine.scheduling.fill import daily_fill_status
from services.social_engine.settings import (
    ensure_webhook_token, load_settings, public_settings, save_settings,
)

from .common import require_api_key, resolve_store_id

router = APIRouter(prefix="/api/social-engine", dependencies=[Depends(require_api_key)])


def _channel_view(channel: dict) -> dict:
    profile = profile_for(channel["provider"])
    return {
        **{k: v for k, v in channel.items() if k != "settings_schema"},
        "network": {
            "label": profile.label, "max_length": profile.max_length, "media": profile.media,
            "video_only": profile.video_only, "link_policy": profile.link_policy,
            "image_formats": list(profile.image_formats), "video_format": profile.video_format,
            "has_post_analytics": profile.has_post_analytics, "title_max": profile.title_max,
        },
        "needs_setup": channel["provider"] == "pinterest" and not (channel.get("default_settings") or {}).get("board"),
    }


@router.get("/overview")
async def overview(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    settings = await load_settings(sid)
    now = int(time.time())
    upcoming = await post_store.list_targets(sid, statuses=("scheduled", "pending", "draft"),
                                             scheduled_from=now - 3600, scheduled_to=now + 14 * 86400, limit=12)
    doctor = await observability.get_state(f"doctor:{sid}") or await run_doctor(sid, live=False)
    return {
        "store_id": sid,
        "settings": public_settings(settings),
        "health": doctor,
        "worker": await worker_health(),
        "posts": await post_store.count_posts_by_status(sid),
        "products": await product_store.count_products(sid),
        "channels": [_channel_view(c) for c in await config_store.list_channels(sid)],
        "campaigns": [
            {"id": c["id"], "name": c["name"], "rule_type": c["rule_type"], "status": c["status"],
             "last_evaluated_at": c["last_evaluated_at"], "next_evaluation_at": c["next_evaluation_at"],
             "last_error": c["last_error"]}
            for c in await config_store.list_campaigns(sid)
        ],
        "jobs": await job_store.counts_by_status(sid),
        "active_jobs": len([j for j in await job_store.list_jobs(store_id=sid, status="queued", limit=500)
                            if j["run_at"] <= now]) + (await job_store.counts_by_status(sid)).get("running", 0),
        "daily_fill": await daily_fill_status(sid, days=3),
        "upcoming": upcoming,
        "activity": await observability.list_activity(store_id=sid, limit=25),
    }


@router.post("/doctor")
async def doctor(store_id: str = "") -> dict[str, Any]:
    return await run_doctor(await resolve_store_id(store_id), live=True)


@router.get("/settings")
async def get_settings(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    settings = await load_settings(sid)
    text_models = [
        {"id": m["id"], "name": m["name"], "provider": m["provider"], "model_name": m.get("model_name", "")}
        for m in await db.get_models(sid) if m.get("model_type") == "text"
    ]
    return {
        "store_id": sid,
        "settings": public_settings(settings),
        "content_types": CONTENT_TYPE_LABELS,
        "formats": {k: list(v) for k, v in FORMAT_SIZES.items()},
        "providers": {k: {"label": p.label, "max_length": p.max_length} for k, p in PROVIDER_PROFILES.items()},
        "rules": rule_catalog(),
        "fallback_models": text_models,
        "has_logo": bool(await db.get_store_setting(sid, "logo_data", "")),
    }


class SettingsPatch(BaseModel):
    store_id: str = ""
    patch: dict[str, Any] = Field(default_factory=dict)


@router.post("/settings")
async def update_settings(payload: SettingsPatch) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    patch = dict(payload.patch)
    (patch.get("postiz") or {}).pop("webhook_token", None)  # rotated only via its own endpoint
    try:
        settings = await save_settings(sid, patch)
    except Exception as exc:
        raise ValidationError(f"Settings not saved: {exc}") from exc
    return {"ok": True, "settings": public_settings(settings)}


class EnableRequest(BaseModel):
    store_id: str = ""
    enabled: bool


@router.post("/settings/enable")
async def enable_engine(payload: EnableRequest) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    settings = await save_settings(sid, {"enabled": payload.enabled})
    if payload.enabled:
        for job_type in ("channels.sync", "catalog.sync"):
            await enqueue(job_type, {"reason": "enabled"}, store_id=sid, dedupe_key=f"{job_type}:enable:{sid}")
        from services.social_engine.creatives import seed_builtin_templates

        await seed_builtin_templates(sid)
    return {"ok": True, "enabled": settings.enabled}


@router.post("/settings/webhook-token")
async def webhook_token(store_id: str = "", rotate: bool = False) -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    if rotate:
        await save_settings(sid, {"postiz": {"webhook_token": ""}})
    token = await ensure_webhook_token(sid)
    return {"ok": True, "path": f"/webhooks/postiz/{sid}/{token}"}


@router.get("/channels")
async def channels(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    last_sync, synced_at = await observability.get_state_with_time(f"channels:last_sync:{sid}")
    return {"channels": [_channel_view(c) for c in await config_store.list_channels(sid)],
            "last_sync": {**(last_sync or {}), "synced_at": synced_at}}


@router.post("/channels/sync")
async def channels_sync(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    job_id, created = await enqueue("channels.sync", {"reason": "manual"}, store_id=sid,
                                    dedupe_key=f"channels.sync:manual:{sid}", priority=10)
    return {"ok": True, "job_id": job_id, "already_queued": not created}


class ChannelUpdate(BaseModel):
    store_id: str = ""
    enabled: bool | None = None
    cadence: dict[str, Any] | None = None
    default_settings: dict[str, Any] | None = None


@router.post("/channels/{channel_id}")
async def update_channel(channel_id: str, payload: ChannelUpdate) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    if payload.cadence is not None:
        cadence = {}
        for key in ("max_per_day", "min_gap_minutes"):
            if payload.cadence.get(key) not in (None, ""):
                value = int(payload.cadence[key])
                if value < 0 or value > (1440 if key == "min_gap_minutes" else 100):
                    raise ValidationError(f"{key} is out of range")
                cadence[key] = value
        payload.cadence = cadence
    changed = await config_store.update_channel_preferences(
        sid, channel_id, enabled=payload.enabled, cadence=payload.cadence, default_settings=payload.default_settings,
    )
    if not changed:
        raise ValidationError("Channel not found or nothing to change.")
    channel = await config_store.get_channel(sid, channel_id)
    return {"ok": True, "channel": _channel_view(channel) if channel else None}


class ChannelTool(BaseModel):
    store_id: str = ""
    method: str
    data: dict[str, Any] = Field(default_factory=dict)


@router.post("/channels/{channel_id}/tool")
async def channel_tool(channel_id: str, payload: ChannelTool) -> dict[str, Any]:
    """Run a Postiz provider tool, e.g. list Pinterest boards or search Instagram audio."""
    from services.social_engine.context import load_store_context
    from services.social_engine.publishers import get_publisher

    sid = await resolve_store_id(payload.store_id)
    publisher = get_publisher(await load_store_context(sid), correlation_id=f"tool:{channel_id}")
    try:
        rules = await publisher.channel_rules(channel_id)
        result = await publisher.run_channel_tool(channel_id, payload.method, payload.data) if payload.method else None
    finally:
        await publisher.aclose()
    return {"ok": True, "tools": rules.tools, "result": result}
