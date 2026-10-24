"""Promotions tab (Outfy PromoQ) and Postiz creative tools."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from db.social_engine import config_store
from db.social_engine import posts as post_store
from services.social_engine.activity import record
from services.social_engine.campaigns.base import model_defaults
from services.social_engine.campaigns.engine import evaluate_campaign, validate_campaign_config
from services.social_engine.campaigns.rules.promotion import PromotionConfig, promotion_status
from services.social_engine.campaigns.themes import CATEGORIES, THEMES, get_theme, upcoming_calendar
from services.social_engine.context import load_store_context
from services.social_engine.errors import NotFoundError, ValidationError
from services.social_engine.jobs.queue import enqueue
from services.social_engine.settings import load_settings

from .common import require_api_key, resolve_store_id

router = APIRouter(prefix="/api/social-engine", dependencies=[Depends(require_api_key)])

_NOT_SENT = ("planned", "generating", "pending_approval", "approved")


async def _today(store_id: str) -> date:
    settings = await load_settings(store_id)
    return datetime.now(ZoneInfo(settings.schedule.timezone)).date()


async def _promotion_rows(store_id: str, today: date) -> list[dict]:
    rows = []
    for campaign in await config_store.list_campaigns(store_id):
        if campaign["rule_type"] != "promotion":
            continue
        config = campaign.get("config") or {}
        posts = await post_store.list_posts(store_id, campaign_id=campaign["id"], limit=500)
        counts: dict[str, int] = {}
        for post in posts:
            counts[post["status"]] = counts.get(post["status"], 0) + 1
        theme = get_theme(str(config.get("theme_key", "custom")))
        start = config.get("start_date", "")
        try:
            end = (date.fromisoformat(start) + timedelta(days=int(config.get("duration_days", 1)) - 1)).isoformat()
        except (TypeError, ValueError):
            end = ""
        rows.append({
            "id": campaign["id"], "name": campaign["name"], "status": promotion_status(config, campaign["status"], today),
            "campaign_status": campaign["status"], "theme_key": theme.key, "theme_label": theme.label,
            "emoji": theme.emoji, "category": theme.category, "start_date": start, "end_date": end,
            "coupon_code": config.get("coupon_code", ""), "posts_per_day": config.get("posts_per_day", 1),
            "channel_ids": campaign.get("channel_ids") or [], "approval_mode": campaign.get("approval_mode"),
            "post_counts": counts, "config": config, "product_filter": campaign.get("product_filter") or {},
            "last_error": campaign.get("last_error", ""),
        })
    order = {"running": 0, "upcoming": 1, "paused": 2, "ended": 3}
    return sorted(rows, key=lambda r: (order.get(r["status"], 9), r["start_date"]))


@router.get("/promotions")
async def promotions(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    today = await _today(sid)
    themes: dict[str, list[dict]] = {key: [] for key in CATEGORIES}
    for theme in THEMES.values():
        themes.setdefault(theme.category, []).append(theme.as_dict())
    budget: dict[str, Any] = {}
    try:
        from services.social_engine.publishers.postiz_tools import get_creative_tools

        tools = get_creative_tools(await load_store_context(sid))
        try:
            budget = await tools.budget_status()
        finally:
            await tools.aclose()
    except Exception as exc:  # budgets are informational on this page
        budget = {"error": str(exc)}
    defaults = model_defaults(PromotionConfig)
    defaults["start_date"] = (today + timedelta(days=1)).isoformat()
    return {
        "today": today.isoformat(),
        "categories": CATEGORIES,
        "themes": themes,
        "calendar": upcoming_calendar(today, days=365),
        "promotions": await _promotion_rows(sid, today),
        "defaults": defaults,
        "postiz_budget": budget,
    }


class PromotionRequest(BaseModel):
    store_id: str = ""
    id: str = ""
    name: str = Field("", max_length=120)
    config: dict[str, Any]
    channel_ids: list[str] = Field(default_factory=list)
    approval_mode: Literal["manual", "ai_review", "auto"] = "manual"
    status: Literal["active", "paused"] = "active"
    product_filter: dict[str, Any] = Field(default_factory=dict)


@router.post("/promotions")
async def save_promotion(payload: PromotionRequest) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    schedule = (payload.config or {}).get("schedule")
    config = validate_campaign_config("promotion", payload.config)
    if schedule:
        config["schedule"] = schedule
    parsed = PromotionConfig.model_validate(config)
    if parsed.end_date < await _today(sid):
        raise ValidationError("This promotion would already be over — pick a start date that isn't in the past.")
    if payload.id and not await config_store.get_campaign(sid, payload.id):
        raise NotFoundError("Promotion not found.")
    name = payload.name.strip() or f"{parsed.title} {parsed.start_date.year}"
    campaign_id = await config_store.save_campaign(sid, {
        "id": payload.id, "name": name, "rule_type": "promotion", "status": payload.status, "config": config,
        "channel_ids": payload.channel_ids, "approval_mode": payload.approval_mode,
        "product_filter": payload.product_filter,
    })
    if payload.status == "active":
        await enqueue("campaigns.evaluate", {"campaign_id": campaign_id}, store_id=sid,
                      dedupe_key=f"campaign:{campaign_id}", correlation_id=campaign_id, priority=8)
    await record(sid, "campaign", campaign_id, f"Promotion '{name}' saved ({parsed.start_date} for "
                 f"{parsed.duration_days} day(s))", stage="promotion")
    return {"ok": True, "id": campaign_id}


class PromotionAction(BaseModel):
    store_id: str = ""


@router.post("/promotions/{campaign_id}/stop")
async def stop_promotion(campaign_id: str, payload: PromotionAction) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    if not await config_store.set_campaign_status(sid, campaign_id, "paused"):
        raise NotFoundError("Promotion not found.")
    return {"ok": True}


@router.post("/promotions/{campaign_id}/resume")
async def resume_promotion(campaign_id: str, payload: PromotionAction) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    if not await config_store.set_campaign_status(sid, campaign_id, "active"):
        raise NotFoundError("Promotion not found.")
    await enqueue("campaigns.evaluate", {"campaign_id": campaign_id}, store_id=sid,
                  dedupe_key=f"campaign:{campaign_id}", correlation_id=campaign_id, priority=8)
    return {"ok": True}


@router.post("/promotions/{campaign_id}/remove")
async def remove_promotion(campaign_id: str, payload: PromotionAction) -> dict[str, Any]:
    """Outfy "Remove": stop it and cancel posts not yet sent; anything already
    scheduled in Postiz is cancelled there too. Published posts are kept."""
    from services.social_engine.pipeline.actions import cancel

    sid = await resolve_store_id(payload.store_id)
    campaign = await config_store.get_campaign(sid, campaign_id)
    if not campaign:
        raise NotFoundError("Promotion not found.")
    await config_store.set_campaign_status(sid, campaign_id, "archived")
    cancelled = 0
    for post in await post_store.list_posts(sid, campaign_id=campaign_id, limit=500):
        if post["status"] in (*_NOT_SENT, "publishing", "scheduled"):
            await cancel(sid, post["id"], by="promotion removed")
            cancelled += 1
    await record(sid, "campaign", campaign_id, f"Promotion removed; {cancelled} unpublished post(s) cancelled",
                 stage="promotion")
    return {"ok": True, "cancelled": cancelled}


@router.post("/promotions/{campaign_id}/preview")
async def preview_promotion(campaign_id: str, store_id: str = "") -> dict[str, Any]:
    return await evaluate_campaign(await resolve_store_id(store_id), campaign_id, dry_run=True)


class PromoTextRequest(BaseModel):
    store_id: str = ""
    theme_key: str
    discount_text: str = ""
    coupon_code: str = ""
    product_ids: list[str] = Field(default_factory=list)


@router.post("/promotions/suggest-texts")
async def suggest_texts(payload: PromoTextRequest) -> dict[str, Any]:
    """Promo lines written by Gemma for a theme (falls back to the theme's own lines)."""
    from db.social_engine import products as product_store
    from services.social_engine.ai.decisions import generate_promo_texts

    sid = await resolve_store_id(payload.store_id)
    theme = get_theme(payload.theme_key)
    products = await product_store.get_products(sid, payload.product_ids) if payload.product_ids else \
        await product_store.list_products(sid, available_only=True, order="newest", limit=8)
    try:
        texts = await generate_promo_texts(
            await load_store_context(sid), theme=theme.label, products=products[:8],
            discount_text=payload.discount_text, coupon_code=payload.coupon_code, count=6,
        )
        source = "gemma"
    except Exception as exc:
        texts, source = list(theme.promo_texts), f"theme ({type(exc).__name__})"
    return {"texts": texts or list(theme.promo_texts), "source": source}


# ------------------------------------------------------------------ Postiz tools

@router.get("/postiz/tools")
async def postiz_tools(store_id: str = "") -> dict[str, Any]:
    from services.social_engine.publishers.guard import live_publishing_enabled
    from services.social_engine.publishers.postiz_tools import get_creative_tools

    sid = await resolve_store_id(store_id)
    sctx = await load_store_context(sid)
    tools = get_creative_tools(sctx)
    try:
        return {
            "live_publishing": live_publishing_enabled(),
            "budget": await tools.budget_status(),
            "video_generators": await tools.video_generators(),
            "voices": [{"id": v.get("id"), "name": v.get("name"), "preview_url": v.get("preview_url")}
                       for v in await tools.voices()],
            "settings": {k: v for k, v in sctx.settings.postiz.model_dump().items()
                         if k not in ("webhook_token", "api_key_env")},
            "ui_only_features": [
                {"name": "Design editor templates", "how": "Design in Postiz → it lands in the media library; "
                 "pick it here with 'Use a Postiz design' (Studio) or the promotion's design search."},
                {"name": "Signatures & Sets", "how": "Configure in Postiz → Settings; used when you finish a draft in Postiz."},
                {"name": "Plugs (auto-repost / auto-comment)", "how": "Postiz → Plugs for X, LinkedIn Page, Bluesky, Threads."},
                {"name": "Preview links for approval", "how": "Send posts as Postiz drafts, then share the preview link."},
                {"name": "RSS auto-post", "how": "Postiz → RSS; the landing-page feed /publar/rss-landingpages works here."},
            ],
        }
    finally:
        await tools.aclose()


@router.get("/postiz/media")
async def postiz_media(store_id: str = "", search: str = "", page: int = 1) -> dict[str, Any]:
    from services.social_engine.publishers.postiz_tools import get_creative_tools

    tools = get_creative_tools(await load_store_context(await resolve_store_id(store_id)))
    try:
        return await tools.media_library(search=search, page=page)
    finally:
        await tools.aclose()


@router.get("/postiz/budget")
async def postiz_budget(store_id: str = "") -> dict[str, Any]:
    from services.social_engine.publishers.postiz_tools import get_creative_tools

    tools = get_creative_tools(await load_store_context(await resolve_store_id(store_id)))
    try:
        return {"budget": await tools.budget_status(), "checked_at": int(time.time())}
    finally:
        await tools.aclose()
