"""Authenticated JSON API used by the embedded Shopify Intelligence page."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import db
from routes.api import _resolve_generation_store, _verify_backend_api_key
from services import intelligence_service

router = APIRouter(prefix="/api/intelligence")


class IntelligenceRunRequest(BaseModel):
    store_id: str = ""
    period_days: int = 90


class IntelligenceSettingsRequest(BaseModel):
    store_id: str = ""
    ga4_property_id: str = ""
    ga4_service_account_json: str = ""
    clear_ga4_credentials: bool = False
    auto_enabled: bool = True
    period_days: int = 90


class IntelligenceDismissRequest(BaseModel):
    store_id: str = ""
    recommendation_id: str


async def _resolved_store_id(requested: str) -> str:
    store = await _resolve_generation_store(requested)
    return str(store["id"])


@router.get("")
async def intelligence_data(request: Request, store_id: str = ""):
    _verify_backend_api_key(request)
    sid = await _resolved_store_id(store_id)
    latest = await db.get_latest_intelligence_run(sid)
    recommendations = await db.get_run_recommendations(latest["id"]) if latest else []
    history = await db.get_intelligence_runs(sid, limit=8)
    property_id = await db.get_store_setting(sid, "ga4_property_id", "")
    credentials_saved = bool(await db.get_store_setting(sid, "ga4_service_account_json", ""))
    auto_enabled = await db.get_store_setting(sid, "intelligence_auto_enabled", "1") == "1"
    period_days = int(await db.get_store_setting(sid, "intelligence_period_days", "90"))
    return {
        "store_id": sid,
        "latest": latest,
        "recommendations": recommendations,
        "history": history,
        "settings": {
            "ga4_property_id": property_id,
            "ga4_credentials_saved": credentials_saved,
            "auto_enabled": auto_enabled,
            "period_days": period_days,
        },
    }


@router.post("/run")
async def intelligence_run(request: Request, payload: IntelligenceRunRequest):
    _verify_backend_api_key(request)
    sid = await _resolved_store_id(payload.store_id)
    days = min(max(int(payload.period_days), 30), 365)
    auto_value = await db.get_store_setting(sid, "intelligence_auto_enabled", "1")
    await db.set_store_settings(sid, {
        "intelligence_period_days": str(days),
        "intelligence_auto_enabled": auto_value,
    })
    run_id = await intelligence_service.run_analysis(sid, days, "manual")
    return {"ok": True, "run_id": run_id}


@router.post("/settings")
async def intelligence_settings(request: Request, payload: IntelligenceSettingsRequest):
    _verify_backend_api_key(request)
    sid = await _resolved_store_id(payload.store_id)
    property_id = payload.ga4_property_id.strip().removeprefix("properties/")
    if property_id and not property_id.isdigit():
        raise HTTPException(status_code=400, detail="GA4 property ID must contain numbers only.")
    credentials = payload.ga4_service_account_json.strip()
    if credentials:
        try:
            parsed = json.loads(credentials)
            if not parsed.get("client_email") or not parsed.get("private_key"):
                raise ValueError("missing client_email or private_key")
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid service account JSON: {exc}") from exc
    days = min(max(int(payload.period_days), 30), 365)
    pairs = {
        "ga4_property_id": property_id,
        "intelligence_auto_enabled": "1" if payload.auto_enabled else "0",
        "intelligence_period_days": str(days),
    }
    if credentials:
        pairs["ga4_service_account_json"] = credentials
    elif payload.clear_ga4_credentials:
        pairs["ga4_service_account_json"] = ""
    await db.set_store_settings(sid, pairs)
    return {"ok": True}


@router.post("/dismiss")
async def intelligence_dismiss(request: Request, payload: IntelligenceDismissRequest):
    _verify_backend_api_key(request)
    sid = await _resolved_store_id(payload.store_id)
    await db.dismiss_recommendation(sid, payload.recommendation_id)
    return {"ok": True}
