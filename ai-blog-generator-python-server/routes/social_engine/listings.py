"""Directory listings: the profile, the catalogue, Chrome prefill, status and upkeep."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from db.social_engine import listings as store
from db.social_engine import products as product_store
from services.social_engine.context import load_store_context
from services.social_engine.errors import ValidationError
from services.social_engine.jobs.queue import enqueue
from services.social_engine.listings.catalogue import KINDS, NEEDS_LABELS, get_directory, ranked
from services.social_engine.listings.checks import check_listing, last_presence
from services.social_engine.listings.fill import directory_row, relay_for
from services.social_engine.listings.profile import COPY_FIELDS, missing_fields, seeded_profile, suggest_copy
from services.social_engine.settings import ListingProfile, save_settings

from .common import require_api_key, resolve_store_id

router = APIRouter(prefix="/api/social-engine", dependencies=[Depends(require_api_key)])

STATUS_LABELS = {
    "todo": "To do", "filled": "Filled in Chrome, submit it", "submitted": "Submitted, waiting for the site",
    "verifying": "Verification pending", "live": "Live", "broken": "Needs attention", "skipped": "Skipped",
}


async def _page(sid: str) -> dict[str, Any]:
    sctx = await load_store_context(sid)
    profile = seeded_profile(sctx)
    gaps = missing_fields(profile)
    rows = await store.list_listings(sid)
    ready = not gaps["required"]
    has_address = bool(profile.address_line1 and profile.postcode)
    directories = [directory_row(d, rows.get(d.id), ready, has_address) for d in ranked()]
    for row in directories:
        row["status_label"] = STATUS_LABELS.get(row["status"], row["status"])
        row["kind_label"] = KINDS.get(row["kind"], row["kind"])
        row["needs_labels"] = [NEEDS_LABELS.get(n, n) for n in row["needs"]]
    counts: dict[str, int] = {}
    for row in directories:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "store_id": sid, "profile": profile.model_dump(), "missing": gaps, "profile_ready": ready,
        "directories": directories, "counts": counts, "kinds": KINDS, "status_labels": STATUS_LABELS,
        "presence": await last_presence(sid), "settings": sctx.settings.listings.model_dump(exclude={"profile"}),
    }


@router.get("/listings")
async def list_listings(store_id: str = "") -> dict[str, Any]:
    return await _page(await resolve_store_id(store_id))


class ProfileRequest(BaseModel):
    store_id: str = ""
    profile: dict[str, Any] = Field(default_factory=dict)


@router.post("/listings/profile")
async def save_profile(payload: ProfileRequest) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    try:
        profile = ListingProfile.model_validate(payload.profile)
    except Exception as exc:
        raise ValidationError(f"Profile not saved: {exc}") from exc
    await save_settings(sid, {"listings": {"profile": profile.model_dump()}})
    return {"ok": True, **await _page(sid)}


class SuggestRequest(BaseModel):
    store_id: str = ""
    overwrite: bool = False


@router.post("/listings/profile/suggest")
async def suggest_profile_copy(payload: SuggestRequest) -> dict[str, Any]:
    """Gemma drafts the descriptions; blanks are filled (everything, with overwrite) and saved for editing."""
    sid = await resolve_store_id(payload.store_id)
    sctx = await load_store_context(sid, correlation_id="listings:copy")
    products = await product_store.list_publishable_products(sid, in_stock_only=False, on_sale_only=False)
    result = await suggest_copy(sctx, products=products[:40])
    current = seeded_profile(sctx).model_dump()
    changed: list[str] = []
    for field in COPY_FIELDS:
        value = result["copy"].get(field)
        if not value:
            continue
        if payload.overwrite or not current.get(field):
            current[field] = value
            changed.append(field)
    if changed:
        await save_settings(sid, {"listings": {"profile": ListingProfile.model_validate(current).model_dump()}})
    return {"ok": True, "changed": changed, "warnings": result["warnings"], "model": result["model"], **await _page(sid)}


class FillRequest(BaseModel):
    store_id: str = ""
    tab: str = ""


@router.post("/listings/{directory_id}/fill")
async def fill_listing(directory_id: str, payload: FillRequest) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    directory = get_directory(directory_id)
    if not directory:
        raise ValidationError("Unknown directory.")
    if directory.fill != "relay":
        raise ValidationError(f"{directory.name} is not filled through Chrome; follow its note instead.")
    sctx = await load_store_context(sid)
    gaps = missing_fields(seeded_profile(sctx))
    if gaps["required"]:
        raise ValidationError("Fill in the profile first: " + ", ".join(gaps["required"]) + ".")
    await store.save_listing(sid, directory.id, fill={"state": "queued"})
    job_id, created = await enqueue("listings.fill", {"directory_id": directory.id, "tab": payload.tab}, store_id=sid,
                                    dedupe_key=f"listings:fill:{sid}:{directory.id}", correlation_id=f"listing:{directory.id}",
                                    priority=8)
    return {"ok": True, "job_id": job_id, "already_queued": not created}


class StatusRequest(BaseModel):
    store_id: str = ""
    status: str
    listing_url: str = Field("", max_length=500)
    notes: str = Field("", max_length=1000)


@router.post("/listings/{directory_id}/status")
async def set_listing_status(directory_id: str, payload: StatusRequest) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    if not get_directory(directory_id):
        raise ValidationError("Unknown directory.")
    if payload.status not in store.STATUSES:
        raise ValidationError(f"Unknown status '{payload.status}'.")
    url = payload.listing_url.strip()
    if url and not url.startswith(("http://", "https://")):
        raise ValidationError("The listing link must start with https://")
    if payload.status == "live" and not url:
        raise ValidationError("Paste the link to the live listing so it can be checked later.")
    fields: dict[str, Any] = {"status": payload.status, "notes": payload.notes.strip()}
    if url:
        fields["listing_url"] = url
    row = await store.save_listing(sid, directory_id, **fields)
    return {"ok": True, "listing": row}


@router.post("/listings/{directory_id}/check")
async def check_now(directory_id: str, store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    listing = await store.get_listing(sid, directory_id)
    if not listing or not listing.get("listing_url"):
        raise ValidationError("Mark the listing live with its link first.")
    sctx = await load_store_context(sid)
    result = await check_listing(sctx, listing)
    status = listing["status"]
    if result["status_code"] in (404, 410):
        status = "broken"
    elif result["ok"]:
        status = "live"
    row = await store.save_listing(sid, directory_id, status=status, check=result, checked_at=result["checked_at"])
    return {"ok": True, "listing": row, "check": result}


@router.post("/listings/presence")
async def presence_now(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    job_id, created = await enqueue("listings.presence", {}, store_id=sid, dedupe_key=f"listings:presence:{sid}",
                                    correlation_id="listings:presence", priority=7)
    return {"ok": True, "job_id": job_id, "already_queued": not created}


@router.get("/listings/relay")
async def relay_status(store_id: str = "") -> dict[str, Any]:
    """Is the Chrome relay reachable? (Spawns the relay once; a few seconds.)"""
    sid = await resolve_store_id(store_id)
    sctx = await load_store_context(sid)
    relay = relay_for(sctx)
    try:
        tabs = await relay.tabs()
    except Exception as exc:
        return {"ok": False, "error": str(getattr(exc, "message", exc)), "relay_dir": str(relay.relay_dir)}
    return {"ok": True, "tabs": len(tabs), "relay_dir": str(relay.relay_dir),
            "open": [{"tab": t.get("tabId"), "title": str(t.get("title", ""))[:80], "url": str(t.get("url", ""))[:120]} for t in tabs[:12]]}
