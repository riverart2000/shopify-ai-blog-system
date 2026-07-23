"""Admin and storefront APIs for product and store reviews."""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import db
import shopify_client
from config import StoreConfig
from routes.api import _resolve_generation_store, _verify_backend_api_key
from services import review_service

router = APIRouter(prefix="/api/reviews", tags=["reviews"])
logger = logging.getLogger("ai_blog_server.reviews")


class ReviewSubmitRequest(BaseModel):
    shop: str = ""
    store_id: str = ""
    review_type: Literal["product", "store"] = "product"
    product_id: str = Field(default="", max_length=100)
    product_handle: str = Field(default="", max_length=255)
    product_title: str = Field(default="", max_length=255)
    rating: int = Field(ge=1, le=5)
    review_title: str = Field(min_length=2, max_length=120)
    review_body: str = Field(min_length=10, max_length=3000)
    reviewer_name: str = Field(min_length=2, max_length=80)
    reviewer_email: str = Field(min_length=5, max_length=254)
    photo_data: str = Field(default="", max_length=3_000_000)
    consent: bool = False
    website: str = Field(default="", max_length=200)
    source_path: str = Field(default="", max_length=1000)
    client_ip: str = Field(default="", max_length=100)


class ReviewModerateRequest(BaseModel):
    store_id: str = ""
    shop: str = ""
    review_id: str
    status: Literal["pending", "published", "rejected", "spam", "hidden"]
    merchant_reply: str = Field(default="", max_length=2000)
    moderation_note: str = Field(default="", max_length=1000)


class ReviewDeleteRequest(BaseModel):
    store_id: str = ""
    shop: str = ""
    review_id: str


class ReviewCacheSyncRequest(BaseModel):
    store_id: str = ""
    shop: str = ""


class ExternalReviewImportRequest(BaseModel):
    store_id: str = ""
    shop: str = ""
    source: Literal["facebook"] = "facebook"
    reviewer_name: str = Field(min_length=2, max_length=80)
    review_body: str = Field(min_length=2, max_length=3000)
    recommendation: Literal["recommends", "does_not_recommend"] = "recommends"
    source_url: str = Field(min_length=12, max_length=1000)
    review_date: str = Field(default="", max_length=10)
    confirmed_complete: bool = False


async def _store_for(store_id: str = "", shop: str = "") -> dict:
    if store_id.strip():
        return await _resolve_generation_store(store_id)
    if shop.strip():
        store = await db.get_store_by_domain(shop.strip())
        if store:
            return store
        raise HTTPException(status_code=404, detail="This shop is not connected to the Reviews system.")
    return await _resolve_generation_store("")


def _store_config(row: dict) -> StoreConfig:
    return StoreConfig(
        id=row["id"], name=row["name"], myshopify_domain=row["myshopify_domain"],
        custom_domain=row.get("custom_domain", ""), client_id=row["client_id"],
        client_secret=row["client_secret"],
        default_blog_handle=row.get("default_blog_handle", "news"),
        default_author=row.get("default_author", "Store Team"),
    )


def _public_review(item: dict) -> dict:
    public = {
        key: item.get(key)
        for key in (
            "id", "review_type", "product_handle", "product_title", "rating",
            "review_title", "review_body", "reviewer_name", "merchant_reply",
            "photo_url", "verified_purchase", "source", "created_at", "published_at",
        )
    }
    public["source_url"] = item.get("source_path", "") if item.get("source") == "facebook" else ""
    return public


async def _shopify_cache(store: dict, review_type: str, product_handle: str = "") -> dict:
    sid = str(store["id"])
    rows, _ = await db.list_reviews(
        sid, public=True, review_type=review_type,
        product_handle=product_handle if review_type == "product" else "",
        limit=10, sort="newest",
    )
    summary = await db.get_review_summary(
        sid, product_handle=product_handle if review_type == "product" else "",
        review_type=review_type,
    )
    return {"summary": summary, "reviews": [_public_review(item) for item in rows], "cached_at": int(time.time())}


@router.get("")
async def review_admin_list(
    request: Request, store_id: str = "", shop: str = "", status: str = "",
    review_type: str = "", limit: int = 100, offset: int = 0,
):
    _verify_backend_api_key(request)
    store = await _store_for(store_id, shop)
    rows, total = await db.list_reviews(
        str(store["id"]), status=status, review_type=review_type,
        limit=limit, offset=offset,
    )
    return {
        "store_id": str(store["id"]), "shop": store["myshopify_domain"],
        "summary": await db.get_admin_summary(str(store["id"])),
        "reviews": rows, "total": total,
    }


@router.get("/public")
async def review_public_list(
    request: Request, shop: str, review_type: Literal["product", "store"] = "product",
    product_handle: str = "", rating: int = 0, sort: str = "newest", page: int = 1,
):
    _verify_backend_api_key(request)
    store = await _store_for(shop=shop)
    if review_type == "product" and not product_handle.strip():
        raise HTTPException(status_code=400, detail="product_handle is required for product reviews")
    if rating not in {0, 1, 2, 3, 4, 5}:
        raise HTTPException(status_code=400, detail="rating must be between 1 and 5")
    per_page = 10
    rows, total = await db.list_reviews(
        str(store["id"]), public=True, review_type=review_type,
        product_handle=product_handle.strip() if review_type == "product" else "",
        rating=rating, sort=sort, limit=per_page, offset=(max(page, 1) - 1) * per_page,
    )
    summary = await db.get_review_summary(
        str(store["id"]),
        product_handle=product_handle.strip() if review_type == "product" else "",
        review_type=review_type,
    )
    return {
        "ok": True, "summary": summary,
        "reviews": [_public_review(item) for item in rows],
        "page": max(page, 1), "pages": max(1, (total + per_page - 1) // per_page),
    }


@router.post("/submit")
async def review_submit(request: Request, payload: ReviewSubmitRequest):
    _verify_backend_api_key(request)
    if payload.website.strip():
        # Honeypot: respond as though accepted without storing bot content.
        return {"ok": True, "message": "Thank you. Your review was submitted for approval."}
    if not payload.consent:
        raise HTTPException(status_code=400, detail="Consent is required to submit a review.")
    store = await _store_for(payload.store_id, payload.shop)
    sid = str(store["id"])
    name = review_service.clean_text(payload.reviewer_name, 80)
    title = review_service.clean_text(payload.review_title, 120)
    body = review_service.clean_text(payload.review_body, 3000, multiline=True)
    email = review_service.validate_email(payload.reviewer_email)
    if len(name) < 2 or len(title) < 2 or len(body) < 10:
        raise HTTPException(status_code=400, detail="Name, review title and a meaningful review are required.")
    product_handle = review_service.clean_text(payload.product_handle, 255)
    product_title = review_service.clean_text(payload.product_title, 255)
    product_id = review_service.clean_text(payload.product_id, 100)
    if payload.review_type == "product":
        if not product_handle:
            raise HTTPException(status_code=400, detail="A product is required for a product review.")
        details = await shopify_client.fetch_product_details(_store_config(store), product_handle)
        if not details:
            raise HTTPException(status_code=404, detail="That product could not be verified in this store.")
        product_title = str(details.get("title") or product_title)[:255]
    else:
        product_handle = ""
        product_title = ""
        product_id = ""
    ip_hash = review_service.hash_ip(payload.client_ip)
    if await db.rate_limit_count(sid, ip_hash, int(time.time()) - 3600) >= 3:
        raise HTTPException(status_code=429, detail="Too many reviews were submitted from this connection. Please try again later.")
    if await db.duplicate_count(sid, email, payload.review_type, product_handle, int(time.time()) - 86400):
        raise HTTPException(status_code=409, detail="A review for this item was already submitted with this email recently.")
    try:
        photo_data = review_service.normalize_photo(payload.photo_data)
    except review_service.ReviewValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    flags = review_service.moderation_flags(title, body, name)
    item = await db.create_review(sid, {
        "review_type": payload.review_type, "product_id": product_id,
        "product_handle": product_handle, "product_title": product_title,
        "rating": payload.rating, "review_title": title, "review_body": body,
        "reviewer_name": name, "reviewer_email": email, "photo_data": photo_data,
        "moderation_flags": flags, "source_path": payload.source_path,
        "ip_hash": ip_hash,
    })
    logger.info("Review submitted id=%s type=%s product=%s flags=%s", item.get("id"), payload.review_type, product_handle, flags)
    return {"ok": True, "review_id": item.get("id"), "message": "Thank you. Your review was submitted for approval."}


@router.post("/import-external")
async def review_import_external(request: Request, payload: ExternalReviewImportRequest):
    """Import a genuine public recommendation without blending it into site ratings."""
    _verify_backend_api_key(request)
    if not payload.confirmed_complete:
        raise HTTPException(
            status_code=400,
            detail="Confirm that every Facebook recommendation is being imported, including negative feedback.",
        )
    store = await _store_for(payload.store_id, payload.shop)
    sid = str(store["id"])
    name = review_service.clean_text(payload.reviewer_name, 80)
    body = review_service.clean_text(payload.review_body, 3000, multiline=True)
    if len(name) < 2 or len(body) < 2:
        raise HTTPException(status_code=400, detail="Reviewer name and Facebook review text are required.")
    try:
        source_url = review_service.validate_facebook_review_url(payload.source_url)
    except review_service.ReviewValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    created_at = int(time.time())
    if payload.review_date:
        try:
            created_at = int(datetime.strptime(payload.review_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Facebook review date must be YYYY-MM-DD.") from exc
        if created_at > int(time.time()) + 86400:
            raise HTTPException(status_code=400, detail="Facebook review date cannot be in the future.")
    if await db.external_review_duplicate_count(sid, "facebook", name, body):
        raise HTTPException(status_code=409, detail="This Facebook review has already been imported.")
    recommends = payload.recommendation == "recommends"
    item = await db.create_review(sid, {
        "review_type": "store",
        "rating": 5 if recommends else 1,
        "review_title": "Recommended on Facebook" if recommends else "Facebook recommendation",
        "review_body": body,
        "reviewer_name": name,
        "reviewer_email": "",
        "source": "facebook",
        "source_path": source_url,
        "created_at": created_at,
        "moderation_flags": [],
    })
    await db.moderate_review(
        sid, item["id"], status="published",
        moderation_note="Imported from the public BioLuxeLab Facebook Reviews page.",
    )
    try:
        await shopify_client.set_store_review_cache(
            _store_config(store), await _shopify_cache(store, "store")
        )
    except Exception as exc:
        await db.delete_review(sid, item["id"])
        logger.exception("Facebook review import rolled back after Shopify cache failure")
        raise HTTPException(
            status_code=502,
            detail=f"Facebook review was not imported because Shopify cache sync failed: {type(exc).__name__}: {exc}",
        ) from exc
    return {
        "ok": True,
        "review_id": item["id"],
        "message": "Facebook recommendation imported and published to the store review block.",
    }


@router.post("/moderate")
async def review_moderate(request: Request, payload: ReviewModerateRequest):
    _verify_backend_api_key(request)
    store = await _store_for(payload.store_id, payload.shop)
    sid = str(store["id"])
    current = await db.get_review(sid, payload.review_id)
    if not current:
        raise HTTPException(status_code=404, detail="Review not found.")
    reply = review_service.clean_text(payload.merchant_reply, 2000, multiline=True)
    note = review_service.clean_text(payload.moderation_note, 1000, multiline=True)
    photo_url: str | None = None
    clear_photo = payload.status in {"published", "rejected", "spam"}
    if payload.status == "published" and current.get("photo_data") and not current.get("photo_url"):
        photo_url = await shopify_client.upload_image_to_shopify(
            _store_config(store), current["photo_data"], f"customer_review_{current['id']}.webp"
        )
        if not photo_url:
            raise HTTPException(status_code=502, detail="Review approval stopped: Shopify did not confirm the customer photograph upload.")
    updated = await db.moderate_review(
        sid, payload.review_id, status=payload.status, merchant_reply=reply,
        moderation_note=note, photo_url=photo_url, clear_photo_data=clear_photo,
    )
    if current.get("review_type") == "product" and current.get("product_handle"):
        try:
            cache = await _shopify_cache(store, "product", current["product_handle"])
            summary = cache["summary"]
            await shopify_client.set_product_review_metafields(
                _store_config(store), current["product_handle"], summary["average"], summary["count"], cache
            )
        except Exception as exc:
            logger.exception("Product review aggregate sync failed review=%s", payload.review_id)
            await db.moderate_review(
                sid, payload.review_id, status=current["status"],
                merchant_reply=current.get("merchant_reply", ""),
                moderation_note=current.get("moderation_note", ""),
            )
            raise HTTPException(
                status_code=502,
                detail=f"Review change was not completed because Shopify rating sync failed: {type(exc).__name__}: {exc}",
            ) from exc
    elif current.get("review_type") == "store":
        try:
            await shopify_client.set_store_review_cache(_store_config(store), await _shopify_cache(store, "store"))
        except Exception as exc:
            logger.exception("Store review Shopify cache sync failed review=%s", payload.review_id)
            await db.moderate_review(
                sid, payload.review_id, status=current["status"],
                merchant_reply=current.get("merchant_reply", ""),
                moderation_note=current.get("moderation_note", ""),
            )
            raise HTTPException(status_code=502, detail=f"Review change was not completed because Shopify cache sync failed: {type(exc).__name__}: {exc}") from exc
    return {"ok": True, "review": updated, "message": f"Review marked {payload.status}. Shopify storefront cache refreshed."}


@router.post("/delete")
async def review_delete(request: Request, payload: ReviewDeleteRequest):
    _verify_backend_api_key(request)
    store = await _store_for(payload.store_id, payload.shop)
    sid = str(store["id"])
    current = await db.get_review(sid, payload.review_id)
    if not current:
        raise HTTPException(status_code=404, detail="Review not found.")
    # Remove it from the Shopify storefront cache first. If Shopify is
    # unavailable, preserve the record and report the exact failure instead of
    # leaving a deleted review visible in cached Liquid.
    await db.moderate_review(sid, payload.review_id, status="hidden", merchant_reply=current.get("merchant_reply", ""))
    if current.get("review_type") == "product" and current.get("product_handle"):
        try:
            cache = await _shopify_cache(store, "product", current["product_handle"])
            summary = cache["summary"]
            await shopify_client.set_product_review_metafields(
                _store_config(store), current["product_handle"], summary["average"], summary["count"], cache
            )
        except Exception:
            await db.moderate_review(sid, payload.review_id, status=current["status"], merchant_reply=current.get("merchant_reply", ""), moderation_note=current.get("moderation_note", ""))
            raise
    elif current.get("review_type") == "store":
        try:
            await shopify_client.set_store_review_cache(_store_config(store), await _shopify_cache(store, "store"))
        except Exception:
            await db.moderate_review(sid, payload.review_id, status=current["status"], merchant_reply=current.get("merchant_reply", ""), moderation_note=current.get("moderation_note", ""))
            raise
    if not await db.delete_review(sid, payload.review_id):
        raise HTTPException(status_code=404, detail="Review not found.")
    return {"ok": True, "message": "Review permanently deleted."}


@router.get("/export.csv")
async def review_export(request: Request, store_id: str = "", shop: str = ""):
    _verify_backend_api_key(request)
    store = await _store_for(store_id, shop)
    content = await db.export_reviews_csv(str(store["id"]))
    return Response(
        content=content, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=bioluxelab-reviews.csv"},
    )


@router.post("/sync-cache")
async def review_sync_cache(request: Request, payload: ReviewCacheSyncRequest):
    """Rebuild Shopify-hosted caches without putting storefront rendering at risk."""
    _verify_backend_api_key(request)
    store = await _store_for(payload.store_id, payload.shop)
    sid = str(store["id"])
    handles_set: set[str] = set()
    offset = 0
    while True:
        rows, total = await db.list_reviews(
            sid, status="published", review_type="product", limit=250, offset=offset
        )
        handles_set.update(str(item.get("product_handle") or "") for item in rows if item.get("product_handle"))
        offset += len(rows)
        if not rows or offset >= total:
            break
    handles = sorted(handles_set)
    errors: list[str] = []
    for handle in handles:
        try:
            cache = await _shopify_cache(store, "product", handle)
            summary = cache["summary"]
            await shopify_client.set_product_review_metafields(
                _store_config(store), handle, summary["average"], summary["count"], cache
            )
        except Exception as exc:
            errors.append(f"{handle}: {type(exc).__name__}: {exc}")
    try:
        await shopify_client.set_store_review_cache(_store_config(store), await _shopify_cache(store, "store"))
    except Exception as exc:
        errors.append(f"store reviews: {type(exc).__name__}: {exc}")
    if errors:
        raise HTTPException(status_code=502, detail="Shopify review cache refresh failed — " + " | ".join(errors))
    return {"ok": True, "products": len(handles), "message": f"Shopify review cache refreshed for {len(handles)} products and the store review block."}
