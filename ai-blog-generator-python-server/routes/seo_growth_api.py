"""Authenticated JSON API for the embedded SEO Growth dashboard."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import db
from routes.api import _resolve_generation_store, _verify_backend_api_key
from services.seo_growth.orchestrator import launch_background, run_audit
from services.seo_growth.search_console import SearchConsoleError, normalise_site_url

router = APIRouter(prefix="/api/seo-growth")
_tasks: set[asyncio.Task] = set()


class SeoRunRequest(BaseModel):
    store_id: str = ""
    period_days: int = 90


class SeoSettingsRequest(BaseModel):
    store_id: str = ""
    gsc_site_url: str = ""
    gsc_service_account_json: str = ""
    clear_gsc_credentials: bool = False
    use_ga4_credentials: bool = True
    auto_enabled: bool = False
    period_days: int = 90


class OpportunityStatusRequest(BaseModel):
    store_id: str = ""
    opportunity_id: str
    status: Literal["open", "planned", "in_progress", "done", "dismissed"]


class BacklinkRequest(BaseModel):
    store_id: str = ""
    id: str = ""
    domain: str = ""
    prospect_url: str = ""
    contact_name: str = ""
    contact_email: str = ""
    target_url: str = ""
    outreach_angle: str = ""
    relationship_type: Literal["earned", "outreach", "partner", "paid", "reciprocal"] = "earned"
    status: Literal["prospect", "contacted", "replied", "won", "lost", "rejected"] = "prospect"
    link_url: str = ""
    link_rel: str = ""
    notes: str = ""


class BacklinkDeleteRequest(BaseModel):
    store_id: str = ""
    prospect_id: str


async def _store_id(requested: str) -> str:
    store = await _resolve_generation_store(requested)
    return str(store["id"])


def _credentials_are_valid(value: str) -> None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid service-account JSON: {exc}") from exc
    if not parsed.get("client_email") or not parsed.get("private_key"):
        raise HTTPException(
            status_code=400,
            detail="Service-account JSON must contain client_email and private_key.",
        )


def _clean_url(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if not value.startswith(("https://", "http://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail=f"{field} must be a valid HTTP or HTTPS URL.")
    return value


@router.get("")
async def seo_growth_data(request: Request, store_id: str = ""):
    _verify_backend_api_key(request)
    sid = await _store_id(store_id)
    history = await db.get_seo_growth_runs(sid, limit=10)
    latest = history[0] if history else None
    result_run = next((item for item in history if item.get("status") == "complete"), None)
    opportunities = await db.get_seo_growth_opportunities(
        sid, result_run["id"] if result_run else "", status="", limit=250,
    ) if result_run else []
    prospects = await db.list_backlink_prospects(sid)
    site_url = await db.get_store_setting(sid, "seo_gsc_site_url", "")
    gsc_credentials = await db.get_store_setting(sid, "seo_gsc_service_account_json", "")
    ga4_credentials = await db.get_store_setting(sid, "ga4_service_account_json", "")
    try:
        period_days = int(await db.get_store_setting(sid, "seo_growth_period_days", "90"))
    except ValueError:
        period_days = 90
    return {
        "store_id": sid,
        "latest": latest,
        "results_run_id": result_run["id"] if result_run else "",
        "opportunities": opportunities,
        "history": history,
        "backlinks": prospects,
        "settings": {
            "gsc_site_url": site_url,
            "gsc_credentials_saved": bool(gsc_credentials),
            "ga4_credentials_available": bool(ga4_credentials),
            "use_ga4_credentials": await db.get_store_setting(sid, "seo_use_ga4_credentials", "1") == "1",
            "auto_enabled": await db.get_store_setting(sid, "seo_growth_auto_enabled", "0") == "1",
            "period_days": min(max(period_days, 28), 365),
        },
    }


@router.post("/run")
async def start_seo_growth_run(request: Request, payload: SeoRunRequest):
    _verify_backend_api_key(request)
    sid = await _store_id(payload.store_id)
    active = await db.get_active_seo_growth_run(sid)
    if active:
        return {"ok": True, "already_running": True, "run_id": active["id"]}
    days = min(max(int(payload.period_days), 28), 365)
    await db.set_store_settings(sid, {"seo_growth_period_days": str(days)})
    run_id = await db.create_seo_growth_run(sid, days, "manual")
    if not run_id:
        active = await db.get_active_seo_growth_run(sid)
        return {"ok": True, "already_running": True, "run_id": (active or {}).get("id", "")}
    launch_background(run_audit(run_id, sid, days), _tasks)
    return {"ok": True, "run_id": run_id, "status": "queued"}


@router.post("/settings")
async def save_seo_growth_settings(request: Request, payload: SeoSettingsRequest):
    _verify_backend_api_key(request)
    sid = await _store_id(payload.store_id)
    try:
        site_url = normalise_site_url(payload.gsc_site_url) if payload.gsc_site_url.strip() else ""
    except SearchConsoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    credentials = payload.gsc_service_account_json.strip()
    if credentials:
        _credentials_are_valid(credentials)
    days = min(max(int(payload.period_days), 28), 365)
    values = {
        "seo_gsc_site_url": site_url,
        "seo_use_ga4_credentials": "1" if payload.use_ga4_credentials else "0",
        "seo_growth_auto_enabled": "1" if payload.auto_enabled else "0",
        "seo_growth_period_days": str(days),
    }
    if credentials:
        values["seo_gsc_service_account_json"] = credentials
    elif payload.clear_gsc_credentials:
        values["seo_gsc_service_account_json"] = ""
    await db.set_store_settings(sid, values)
    return {"ok": True}


@router.post("/opportunity-status")
async def opportunity_status(request: Request, payload: OpportunityStatusRequest):
    _verify_backend_api_key(request)
    sid = await _store_id(payload.store_id)
    changed = await db.set_seo_opportunity_status(sid, payload.opportunity_id, payload.status)
    if not changed:
        raise HTTPException(status_code=404, detail="SEO opportunity was not found for this store.")
    return {"ok": True}


@router.post("/backlinks")
async def save_backlink(request: Request, payload: BacklinkRequest):
    _verify_backend_api_key(request)
    sid = await _store_id(payload.store_id)
    prospect_url = _clean_url(payload.prospect_url, "Prospect URL")
    target_url = _clean_url(payload.target_url, "Target URL")
    link_url = _clean_url(payload.link_url, "Live link URL")
    domain = payload.domain.strip().lower()
    if not domain and prospect_url:
        domain = str(urlparse(prospect_url).hostname or "").removeprefix("www.")
    domain = re.sub(r"^https?://", "", domain).strip("/")
    domain = str(urlparse(f"https://{domain}").hostname or "").removeprefix("www.")
    if not domain:
        raise HTTPException(status_code=400, detail="Backlink prospect domain is required.")
    if payload.contact_email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", payload.contact_email):
        raise HTTPException(status_code=400, detail="Contact email address is invalid.")
    if payload.link_rel not in {"", "nofollow", "sponsored", "ugc"}:
        raise HTTPException(status_code=400, detail="Link rel must be normal, nofollow, sponsored or ugc.")
    prospect_id = await db.upsert_backlink_prospect(sid, {
        **payload.model_dump(), "domain": domain, "prospect_url": prospect_url,
        "target_url": target_url, "link_url": link_url,
    })
    warning = ""
    if payload.relationship_type == "reciprocal":
        warning = "Reciprocal link schemes can violate Google spam policies. This record is flagged for human review."
    elif payload.relationship_type == "paid" and payload.link_rel not in {"sponsored", "nofollow"}:
        warning = "Paid links should normally use rel=sponsored or rel=nofollow."
    return {"ok": True, "id": prospect_id, "warning": warning}


@router.post("/backlinks/delete")
async def remove_backlink(request: Request, payload: BacklinkDeleteRequest):
    _verify_backend_api_key(request)
    sid = await _store_id(payload.store_id)
    changed = await db.delete_backlink_prospect(sid, payload.prospect_id)
    if not changed:
        raise HTTPException(status_code=404, detail="Backlink prospect was not found for this store.")
    return {"ok": True}
