"""Keep supplier photos with printed claims ("Slim Legs", "Fat Burning") out of every post.

Each product photo is shown to the vision model once; the verdict is stored by
URL (`db.social_engine.photos`). Products are then rendered from their clean
photos only, and a product with no clean photo is kept out of the campaigns
until its photos change — a person is told on the Products page, not asked in
the approval queue.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from db.social_engine import observability
from db.social_engine import photos as photo_store
from db.social_engine import products as product_store

from ..context import StoreContext
from ..errors import describe
from . import prompts
from .base import LLMRequest
from .llm import OpenAICompatibleProvider

logger = logging.getLogger("ai_blog_server.social_engine.ai")

MAX_PHOTOS_PER_PRODUCT = 10


def photo_urls(product: dict, limit: int = MAX_PHOTOS_PER_PRODUCT) -> list[str]:
    urls = [str(img.get("url") or "") for img in (product.get("images") or []) if isinstance(img, dict)]
    if not urls and product.get("featured_image_url"):
        urls = [str(product["featured_image_url"])]
    return [u for u in dict.fromkeys(urls) if u][:limit]


async def _look(sctx: StoreContext, url: str, *, subject_id: str, title: str = "") -> tuple[list[str], str] | None:
    """Claims printed on the photo, or None when the model could not answer (nothing is stored)."""
    from ..creatives.images import ImageFetcher, cache_file, sized_url

    ai = sctx.settings.ai
    if not ai.vision_model:
        return None
    async with ImageFetcher(store_id=sctx.store_id, correlation_id=subject_id) as fetcher:
        try:
            await fetcher.fetch_bytes(url)
        except Exception as exc:
            logger.info("Photo audit skipped (download): %s", describe(exc)[1], extra={"store_id": sctx.store_id})
            return None
        path = cache_file(sized_url(url, fetcher.width))
    provider = OpenAICompatibleProvider(
        ai.base_url, ai.vision_model, api_key=ai.api_key(), timeout=min(float(ai.timeout_seconds), 180.0),
        name="vision", max_attempts=1, strict_json=ai.strict_json,
    )
    request = LLMRequest(system=prompts.VISION_SYSTEM, prompt=prompts.photo_audit_prompt(), images=[str(path)],
                         json_mode=True, temperature=0, max_tokens=300)
    try:
        from .review import VisionDraft, _vision_payload, not_product_name

        response = await provider.complete(request, sctx.call_context(subject_id), operation="llm.photo_audit")
        draft = VisionDraft.model_validate(_vision_payload(response.text))
    except Exception as exc:
        logger.info("Photo audit skipped: %s", describe(exc)[1], extra={"store_id": sctx.store_id})
        return None
    return not_product_name(list(draft.claims), [title]), response.model


async def audit_product(sctx: StoreContext, product: dict) -> dict[str, dict]:
    """Audit every photo of `product` that has no verdict yet; return {url: audit}."""
    urls = photo_urls(product)
    known = await photo_store.get_audits(sctx.store_id, urls)
    pid = str(product.get("product_id") or "")
    for url in urls:
        if url in known:
            continue
        looked = await _look(sctx, url, subject_id=f"photo:{pid}", title=str(product.get("title") or ""))
        if looked is None:
            continue
        claims, model = looked
        verdict = "claim" if claims else "clean"
        await photo_store.save_audit(sctx.store_id, url=url, product_id=pid, verdict=verdict, claims=claims, model=model)
        known[url] = {"url": url, "product_id": pid, "verdict": verdict, "claims": claims, "model": model}
        if claims:
            await observability.add_activity(
                store_id=sctx.store_id, entity_type="product", entity_id=pid, level="warning", stage="photo_audit",
                message=f"Photo carries a claim and will not be used: {', '.join(claims[:3])}",
                data={"url": url, "claims": claims}, correlation_id=f"photo:{pid}",
            )
    return known


async def clean_products(sctx: StoreContext, products: list[dict]) -> list[dict]:
    """Copies of `products` with only clean photos; `photo_block` set when none is clean.

    Unaudited photos (vision model down) count as clean so posting never stops
    because the checker is unavailable.
    """
    cleaned: list[dict] = []
    for product in products:
        audits = await audit_product(sctx, product)
        images = [img for img in (product.get("images") or []) if isinstance(img, dict)]
        keep = [img for img in images if (audits.get(str(img.get("url") or "")) or {}).get("verdict") != "claim"]
        dropped = len(images) - len(keep)
        copy = {**product, "images": keep, "photo_claims": dropped}
        featured = str(product.get("featured_image_url") or "")
        if featured and (audits.get(featured) or {}).get("verdict") == "claim":
            copy["featured_image_url"] = str(keep[0]["url"]) if keep else ""
        copy["photo_block"] = bool(images) and not keep
        cleaned.append(copy)
    return cleaned


async def audit_pending(store_id: str, *, max_products: int = 5) -> dict[str, Any]:
    """Periodic: audit a few publishable products that were never checked."""
    from ..context import load_store_context

    sctx = await load_store_context(store_id, correlation_id="photo_audit")
    if not sctx.settings.ai.vision_model:
        return {"skipped": "no vision model"}
    done = await photo_store.audited_product_ids(store_id)
    rows = await product_store.list_publishable_products(store_id, in_stock_only=True, on_sale_only=False)
    todo = [r for r in rows if str(r["product_id"]) not in done][:max_products]
    audited = blocked = 0
    started = time.monotonic()
    for product in todo:
        audits = await audit_product(sctx, product)
        if audits:
            audited += 1
            if all(a.get("verdict") == "claim" for a in audits.values()):
                blocked += 1
    return {"audited": audited, "blocked": blocked, "remaining": max(0, len(rows) - len(done) - audited),
            "duration_ms": int((time.monotonic() - started) * 1000)}
