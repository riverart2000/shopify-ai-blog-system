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


def _build_hero_photo_prompt(title: str, summary: str, prompt: str) -> str:
    return _build_photo_prompt(title, summary, prompt)


def _build_infographic_prompt(title: str, summary: str, prompt: str) -> str:
    return (
        f"Clean modern infographic illustrating key points of: {title}. "
        f"Data visualisation, icons, bold typography, white background, "
        f"professional business style. Topic: {summary[:200]}"
    )


def _build_step_card_prompt(title: str, summary: str, prompt: str) -> str:
    return (
        f"Create a clean modern step-by-step visual card for the blog article '{title}'. "
        "Show 3 to 5 numbered steps, short actionable text, clear hierarchy, bold headings, "
        "editorial infographic layout, highly legible typography, premium ecommerce brand style. "
        f"Topic: {summary[:200]}. Context: {prompt[:200]}. "
        "No watermarks, no device mockups, no photo collage."
    )


def _build_checklist_card_prompt(title: str, summary: str, prompt: str) -> str:
    return (
        f"Create a clean modern checklist or tips card for the blog article '{title}'. "
        "Show 4 to 6 concise checklist or tip items with checkmarks or bullets, clean spacing, "
        "strong visual hierarchy, editorial infographic layout, premium ecommerce brand style. "
        f"Topic: {summary[:200]}. Context: {prompt[:200]}. "
        "No watermarks, no screenshots, no product grid."
    )


_TYPED_IMAGE_SPECS = (
    ("hero_photo", "Hero Photo", _build_hero_photo_prompt, "hero photo"),
    ("infographic", "Infographic", _build_infographic_prompt, "infographic"),
    ("step_card", "Step-by-Step Visual Card", _build_step_card_prompt, "step card"),
    ("checklist_card", "Checklist/Tips Card", _build_checklist_card_prompt, "checklist card"),
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
    """Generate the default image set for a blog post.

    Returns the raw image URLs only, preserving the older public API used by the
    scheduler and publish pipeline.
    """
    urls, _image_types, _labels = await generate_typed_images(store_id, title, summary, prompt)
    return urls


async def generate_typed_images(
    store_id: str,
    title: str,
    summary: str,
    prompt: str,
) -> tuple[list[str], list[str], list[str]]:
    """Generate up to four typed images for a blog post.

    The returned tuples stay index-aligned:
    - image URLs
    - image types
    - display labels
    """
    image_urls: list[str] = []
    image_types: list[str] = []
    image_labels: list[str] = []

    for image_type, image_label, prompt_builder, generation_label in _TYPED_IMAGE_SPECS:
        url = await _generate_one(
            store_id,
            prompt_builder(title, summary, prompt),
            generation_label,
        )
        if url is None:
            continue
        image_urls.append(url)
        image_types.append(image_type)
        image_labels.append(image_label)

    return image_urls, image_types, image_labels


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
