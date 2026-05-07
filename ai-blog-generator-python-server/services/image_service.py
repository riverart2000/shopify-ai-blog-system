"""services/image_service.py — Image generation with per-store model failover.
Images are always optional — failures return [] so blogs still publish.
"""
from __future__ import annotations

import logging

import db
import providers

logger = logging.getLogger("ai_blog_server")


def _build_photo_prompt(title: str, summary: str, prompt: str) -> str:
    return (
        f"Professional high-quality photograph for blog article: {title}. "
        f"{summary} Context: {prompt[:200]}. "
        "No text overlays, no titles, no captions, no watermarks, clean photo only."
    )


def _build_infographic_prompt(title: str, summary: str, prompt: str) -> str:
    return (
        f"Clean modern infographic illustrating key points of: {title}. "
        f"Data visualisation, icons, bold typography, white background, "
        f"professional business style. Topic: {summary[:200]}"
    )


async def _generate_one(
    store_id: str,
    image_prompt: str,
    label: str,
) -> str | None:
    """Try active image models until one returns a URL. Returns None on all failures."""
    rows = await db.get_active_image_models(store_id)
    if not rows:
        return None

    skip_providers: set[str] = set()
    for row in rows:
        model = providers.ModelRecord.from_dict(row)
        if model.provider in skip_providers:
            continue
        try:
            provider = providers.get_image_provider(model)
            urls = await provider.generate_images(image_prompt, 1)
            if urls:
                logger.info(
                    "%s image generated via %s model=%s store=%s",
                    label, model.provider, model.model_name, store_id,
                )
                return urls[0]
        except providers.ProviderError as exc:
            err_msg = str(exc)
            logger.warning("Image provider %s failed (%s): %s", model.name, label, err_msg)
            await db.log_model_error(store_id, model.id, model.provider, "image_error", err_msg)
            if not exc.retryable:
                skip_providers.add(model.provider)
        except Exception as exc:
            err_msg = f"Unexpected error: {exc}"
            logger.exception("Unexpected error from image provider %s (%s)", model.name, label)
            await db.log_model_error(store_id, model.id, model.provider, "unexpected_error", err_msg)

    logger.warning("All image models failed for %s store=%s", label, store_id)
    return None


async def generate_images(
    store_id: str,
    title: str,
    summary: str,
    prompt: str,
) -> list[str]:
    """Generate one photo and one infographic. Returns list of up to 2 URLs (may be empty)."""
    photo_url = await _generate_one(
        store_id, _build_photo_prompt(title, summary, prompt), "photo"
    )
    infographic_url = await _generate_one(
        store_id, _build_infographic_prompt(title, summary, prompt), "infographic"
    )
    return [u for u in [photo_url, infographic_url] if u is not None]
