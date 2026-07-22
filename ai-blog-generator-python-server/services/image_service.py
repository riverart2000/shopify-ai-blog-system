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
EXPECTED_TYPED_IMAGE_TYPES = tuple(spec[0] for spec in _TYPED_IMAGE_SPECS)


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


def use_product_featured_image(
    product_image_url: str | None,
    image_urls: list[str],
    image_types: list[str],
    image_labels: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Build a complete image set for product blogs without wasting generations.

    Preferred order:
    1) Shopify product image first (featured)
    2) Non-hero generated support images
    3) Generated hero image after the supporting visuals

    Product blogs add the Shopify product image; it must not replace or discard
    any of the up-to-four images that were already paid for and generated.
    """

    target_count = 1 + len(image_urls) if product_image_url else len(image_urls)
    merged: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()

    def _append(url: str, image_type: str, label: str) -> None:
        normalized_url = (url or "").strip()
        if not normalized_url or normalized_url in seen_urls:
            return
        merged.append((normalized_url, image_type, label))
        seen_urls.add(normalized_url)

    normalized_entries: list[tuple[str, str, str]] = []
    for index, url in enumerate(image_urls):
        image_type = image_types[index] if index < len(image_types) else "generated"
        if index < len(image_labels):
            label = image_labels[index]
        else:
            label = image_type.replace("_", " ").title()
        normalized_entries.append((url, image_type, label))

    if product_image_url:
        _append(product_image_url, "product", "Product Image")

    non_hero_entries = [
        (url, image_type, label)
        for url, image_type, label in normalized_entries
        if image_type not in ("photo", "hero_photo")
    ]
    hero_entries = [
        (url, image_type, label)
        for url, image_type, label in normalized_entries
        if image_type in ("photo", "hero_photo")
    ]

    for url, image_type, label in non_hero_entries:
        _append(url, image_type, label)

    for url, image_type, label in hero_entries:
        _append(url, image_type, label)

    # Final safety net for malformed type arrays: include remaining raw URLs.
    if len(merged) < target_count:
        for url, image_type, label in normalized_entries:
            _append(url, image_type, label)
            if len(merged) >= target_count:
                break

    if not merged:
        return [], [], []

    merged = merged[:target_count]
    merged_urls = [url for url, _image_type, _label in merged]
    merged_types = [image_type for _url, image_type, _label in merged]
    merged_labels = [label for _url, _image_type, label in merged]
    return merged_urls, merged_types, merged_labels


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
