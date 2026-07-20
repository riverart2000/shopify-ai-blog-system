"""Authenticated JSON API for the embedded and storefront Wellness Quiz."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import db
from routes.api import _resolve_generation_store, _verify_backend_api_key
from services import wellness_quiz_service

router = APIRouter(prefix="/api/wellness-quiz", tags=["wellness_quiz"])

ALLOWED_EVENTS = {
    "started", "completed", "recommendation_clicked", "guide_clicked",
    "product_clicked", "landing_page_clicked", "routine_saved",
}


class QuizStoreRequest(BaseModel):
    store_id: str = ""
    shop: str = ""


class QuizEventRequest(QuizStoreRequest):
    session_id: str = Field(min_length=8, max_length=100)
    event_type: str = Field(min_length=2, max_length=60)
    goal: str = Field(default="", max_length=60)
    answers: dict[str, Any] = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list, max_length=10)
    product_handle: str = Field(default="", max_length=255)
    source_path: str = Field(default="", max_length=1000)


async def _store_for(store_id: str = "", shop: str = "") -> dict:
    if store_id.strip():
        store = await _resolve_generation_store(store_id)
    elif shop.strip():
        store = await db.get_store_by_domain(shop)
        if not store:
            raise HTTPException(status_code=404, detail="This shop is not connected to the AI Blog app.")
    else:
        store = await _resolve_generation_store("")
    return store


@router.get("")
async def quiz_admin_data(request: Request, store_id: str = "", shop: str = "", period_days: int = 90):
    _verify_backend_api_key(request)
    store = await _store_for(store_id, shop)
    sid = str(store["id"])
    products = await db.get_wellness_quiz_products(sid, available_only=False)
    summary = await db.get_wellness_quiz_summary(sid, min(max(int(period_days), 7), 365))
    goal_counts = {goal: 0 for goal in wellness_quiz_service.GOALS}
    for item in products:
        if not item.get("available"):
            continue
        for goal, score in item.get("goal_scores", {}).items():
            if score > 0:
                goal_counts[goal] = goal_counts.get(goal, 0) + 1
    return {
        "store_id": sid,
        "shop": store.get("myshopify_domain", ""),
        "storefront_domain": store.get("custom_domain") or store.get("myshopify_domain", ""),
        "config": wellness_quiz_service.public_config(),
        "summary": summary,
        "goal_counts": goal_counts,
        "products": products,
    }


@router.post("/sync")
async def quiz_sync(request: Request, payload: QuizStoreRequest):
    _verify_backend_api_key(request)
    store = await _store_for(payload.store_id, payload.shop)
    products = await wellness_quiz_service.sync_catalogue(str(store["id"]))
    return {
        "ok": True,
        "products": len(products),
        "available": sum(bool(item.get("available")) for item in products),
    }


@router.get("/public")
async def quiz_public(request: Request, shop: str):
    _verify_backend_api_key(request)
    store = await _store_for(shop=shop)
    products = await db.get_wellness_quiz_products(str(store["id"]), available_only=True)
    public_products = [
        {
            key: item.get(key)
            for key in (
                "handle", "title", "product_url", "landing_page_url", "guide_url",
                "guide_title", "image_url", "price", "currency", "variant_id",
                "goal_scores", "formats",
            )
        }
        for item in products
    ]
    return {
        "ok": True,
        "config": wellness_quiz_service.public_config(),
        "products": public_products,
        "catalogue_ready": bool(public_products),
    }


@router.post("/event")
async def quiz_event(request: Request, payload: QuizEventRequest):
    _verify_backend_api_key(request)
    if payload.event_type not in ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail="Unsupported quiz event.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", payload.session_id):
        raise HTTPException(status_code=400, detail="Invalid anonymous quiz session.")
    store = await _store_for(payload.store_id, payload.shop)
    allowed_answers = {
        key: str(value)[:100]
        for key, value in payload.answers.items()
        if key in {"goal", "format", "time", "budget"}
    }
    await db.record_wellness_quiz_event(str(store["id"]), {
        "session_id": payload.session_id,
        "event_type": payload.event_type,
        "goal": payload.goal,
        "answers": allowed_answers,
        "recommendations": [str(value)[:255] for value in payload.recommendations[:10]],
        "product_handle": payload.product_handle,
        "source_path": payload.source_path,
    })
    return {"ok": True}
