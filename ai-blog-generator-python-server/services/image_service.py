"""services/image_service.py — Image generation with per-store model failover.
Images are always optional — failures return [] so blogs still publish.
"""
from __future__ import annotations

import asyncio
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


def _build_secondary_photo_prompt(title: str, summary: str, prompt: str) -> str:
    return (
        f"Lifestyle editorial photograph related to the blog article: {title}. "
        f"Show a different scene or angle than a hero shot — people, hands, or "
        f"environment in natural use. {summary[:160]} "
        "Bright natural light, premium magazine style. "
        "No text overlays, no titles, no captions, no watermarks."
    )


def _build_detail_photo_prompt(title: str, summary: str, prompt: str) -> str:
    return (
        f"Close-up detail photograph supporting the blog article: {title}. "
        f"Macro or product-detail framing highlighting texture and quality. "
        f"Topic: {summary[:160]}. Soft shallow depth of field, clean background. "
        "No text overlays, no titles, no captions, no watermarks."
    )


# Ordered plan for a multi-image blog: hero first (used as featured image),
# then an infographic and two supporting photos. Each entry is
# (type, human label, prompt builder).
_IMAGE_PLAN = [
    ("hero_photo", "Hero Photo", _build_photo_prompt),
    ("infographic", "Infographic", _build_infographic_prompt),
    ("secondary_photo", "Lifestyle Photo", _build_secondary_photo_prompt),
    ("detail_photo", "Detail Photo", _build_detail_photo_prompt),
]


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


async def generate_typed_images(
    store_id: str,
    title: str,
    summary: str,
    prompt: str,
    max_images: int = 4,
) -> tuple[list[str], list[str], list[str]]:
    """Generate up to ``max_images`` typed images (hero photo, infographic,
    lifestyle photo, detail photo) concurrently.

    Returns ``(urls, types, labels)`` parallel lists containing only the images
    that were generated successfully. The hero photo is always first when present
    so callers can use it as the Shopify featured image.
    """
    plan = _IMAGE_PLAN[: max(1, min(max_images, len(_IMAGE_PLAN)))]

    tasks = [
        _generate_one(store_id, builder(title, summary, prompt), label)
        for (_type, label, builder) in plan
    ]
    results = await asyncio.gather(*tasks)

    urls: list[str] = []
    types: list[str] = []
    labels: list[str] = []
    for (img_type, label, _builder), url in zip(plan, results):
        if url is not None:
            urls.append(url)
            types.append(img_type)
            labels.append(label)
    return urls, types, labels


async def generate_images(
    store_id: str,
    title: str,
    summary: str,
    prompt: str,
) -> list[str]:
    """Backward-compatible wrapper: return only the image URLs (3-4 typed images)."""
    urls, _types, _labels = await generate_typed_images(store_id, title, summary, prompt)
    return urls


async def generate_feature_image(
    store_id: str,
    title: str,
    summary: str,
    prompt: str,
) -> str | None:
    """Generate one featured image, falling back to simpler prompts if needed."""
    prompts = [
        _build_photo_prompt(title, summary, prompt),
        (
            f"Editorial wellness lifestyle photograph illustrating: {title}. "
            "Bright home interior, natural light, premium brand photography."
        ),
        (
            f"Professional blog hero image for an article titled '{title}'. "
            "Clean composition, realistic people or props when appropriate, "
            "premium wellness editorial style."
        ),
    ]

    for image_prompt in prompts:
        image_url = await _generate_one(store_id, image_prompt, "photo")
        if image_url:
            return image_url
    return None
