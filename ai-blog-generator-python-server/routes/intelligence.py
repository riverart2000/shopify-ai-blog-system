"""Customer Intelligence dashboard and controls."""
from __future__ import annotations

import json
import logging
from typing import Annotated, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import db
import state
from services import intelligence_service

router = APIRouter(prefix="/intelligence")
logger = logging.getLogger("ai_blog_server.intelligence.routes")


def _store_id(request: Request) -> Optional[str]:
    value = request.session.get("store_id", "")
    return None if not value or value == "__admin__" else value


@router.get("", response_class=HTMLResponse)
async def intelligence_page(request: Request, saved: str = "", error: str = ""):
    store_id = _store_id(request)
    if not store_id:
        return RedirectResponse("/setup", status_code=303)
    store = await db.get_store(store_id)
    latest = await db.get_latest_intelligence_run(store_id)
    recommendations = await db.get_run_recommendations(latest["id"]) if latest else []
    history = await db.get_intelligence_runs(store_id, limit=8)
    property_id = await db.get_store_setting(store_id, "ga4_property_id", "")
    credentials_saved = bool(await db.get_store_setting(store_id, "ga4_service_account_json", ""))
    auto_enabled = await db.get_store_setting(store_id, "intelligence_auto_enabled", "1") == "1"
    period_days = await db.get_store_setting(store_id, "intelligence_period_days", "90")
    return state.templates.TemplateResponse(request, "intelligence.html", {
        "store": store,
        "latest": latest,
        "summary": latest.get("summary", {}) if latest else {},
        "recommendations": recommendations,
        "history": history,
        "property_id": property_id,
        "credentials_saved": credentials_saved,
        "auto_enabled": auto_enabled,
        "period_days": period_days,
        "saved": saved,
        "error": error,
    })


@router.post("/run", response_class=HTMLResponse)
async def run_intelligence(
    request: Request,
    period_days: Annotated[str, Form()] = "90",
):
    store_id = _store_id(request)
    if not store_id:
        return RedirectResponse("/setup", status_code=303)
    try:
        days = min(max(int(period_days), 30), 365)
        auto_value = await db.get_store_setting(store_id, "intelligence_auto_enabled", "1")
        await db.set_store_settings(store_id, {
            "intelligence_period_days": str(days),
            "intelligence_auto_enabled": auto_value,
        })
        await intelligence_service.run_analysis(store_id, days, "manual")
        return RedirectResponse("/intelligence?saved=analysis", status_code=303)
    except Exception as exc:
        logger.exception("Manual intelligence analysis failed store=%s", store_id)
        return RedirectResponse(
            "/intelligence?" + urlencode({"error": str(exc)[:300]}),
            status_code=303,
        )


@router.post("/settings", response_class=HTMLResponse)
async def save_intelligence_settings(
    request: Request,
    ga4_property_id: Annotated[str, Form()] = "",
    ga4_service_account_json: Annotated[str, Form()] = "",
    clear_ga4_credentials: Annotated[str, Form()] = "",
    auto_enabled: Annotated[str, Form()] = "",
    period_days: Annotated[str, Form()] = "90",
):
    store_id = _store_id(request)
    if not store_id:
        return RedirectResponse("/setup", status_code=303)

    property_id = ga4_property_id.strip().removeprefix("properties/")
    if property_id and not property_id.isdigit():
        return RedirectResponse(
            "/intelligence?error=GA4+property+ID+must+contain+numbers+only",
            status_code=303,
        )
    credentials = ga4_service_account_json.strip()
    if credentials:
        try:
            parsed = json.loads(credentials)
            if not parsed.get("client_email") or not parsed.get("private_key"):
                raise ValueError("missing client_email or private_key")
        except (json.JSONDecodeError, ValueError) as exc:
            return RedirectResponse(
                "/intelligence?" + urlencode({"error": f"Invalid service account JSON: {exc}"}),
                status_code=303,
            )
    try:
        days = min(max(int(period_days), 30), 365)
    except ValueError:
        days = 90
    pairs = {
        "ga4_property_id": property_id,
        "intelligence_auto_enabled": "1" if auto_enabled in ("1", "on", "true", "yes") else "0",
        "intelligence_period_days": str(days),
    }
    if credentials:
        pairs["ga4_service_account_json"] = credentials
    elif clear_ga4_credentials in ("1", "on", "true", "yes"):
        pairs["ga4_service_account_json"] = ""
    await db.set_store_settings(store_id, pairs)
    return RedirectResponse("/intelligence?saved=settings", status_code=303)


@router.post("/recommendations/dismiss", response_class=HTMLResponse)
async def dismiss_recommendation(
    request: Request,
    recommendation_id: Annotated[str, Form()],
):
    store_id = _store_id(request)
    if not store_id:
        return RedirectResponse("/setup", status_code=303)
    await db.dismiss_recommendation(store_id, recommendation_id)
    return RedirectResponse("/intelligence?saved=dismissed", status_code=303)
