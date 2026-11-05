"""Shoppable bio-link page: where it is, what's on it, and "refresh now"."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from services.social_engine import shoppable
from services.social_engine.context import load_store_context

from .common import require_api_key, resolve_store_id

router = APIRouter(prefix="/api/social-engine", dependencies=[Depends(require_api_key)])


@router.get("/shoppable")
async def shoppable_status(store_id: str = "") -> dict:
    sctx = await load_store_context(await resolve_store_id(store_id))
    return await shoppable.status(sctx)


@router.post("/shoppable/refresh")
async def shoppable_refresh(store_id: str = "") -> dict:
    """Rebuild the page now (it also refreshes by itself every hour)."""
    sctx = await load_store_context(await resolve_store_id(store_id))
    return {"result": await shoppable.refresh(sctx), **await shoppable.status(sctx)}
