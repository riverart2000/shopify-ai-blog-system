"""Template renderer: RenderRequest → branded images (and videos via video.py)."""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
from dataclasses import dataclass
from typing import Any

from PIL import Image

import db

from ..errors import NotFoundError, ValidationError
from ..settings import BrandKit, load_settings
from .base import RenderedMedia, RenderProduct, RenderRequest
from .images import ImageFetcher, encode_jpeg
from .layout import LayoutContext, ProductInfo, render_spec
from .library import get_builtin
from .prepare import Prepared, fetch_images, pick_template, prepare_request, spec_urls
from .samples import SAMPLE_TEXTS, ensure_sample_images, sample_products, sample_scene_path
from .storage import save_media_bytes
from .templates import FRAMES, TemplateDef
from . import video as video_renderer

logger = logging.getLogger("ai_blog_server.social_engine.creatives")

JPEG_QUALITY = 90
DEFAULT_CAROUSEL_SLIDES = 5
MAX_CAROUSEL_SLIDES = 10


@dataclass
class Slide:
    fmt: str
    position: int
    role: str
    products: list[ProductInfo]
    image_offset: int
    slide_index: int = 0
    slide_count: int = 1


def render_image_sync(ctx: LayoutContext, spec: dict, *, quality: int = JPEG_QUALITY) -> tuple[bytes, list[dict]]:
    """Render a spec to JPEG bytes (CPU-bound; call via asyncio.to_thread)."""
    canvas = render_spec(ctx, spec)
    return encode_jpeg(canvas, quality=quality), list(ctx.report)


def _formats(template: TemplateDef, requested: list[str]) -> list[str]:
    wanted = [f for f in dict.fromkeys(requested or []) if f]
    if not wanted:
        return [template.formats[0]]
    supported = [f for f in wanted if f in template.formats and f in FRAMES]
    skipped = [f for f in wanted if f not in supported]
    if skipped:
        logger.warning("Template '%s' skips unsupported format(s): %s", template.key, ", ".join(skipped))
    if not supported:
        raise ValidationError(
            f"Template '{template.key}' does not support format(s) {', '.join(wanted)} "
            f"(supported: {', '.join(template.formats)})."
        )
    return supported


def _carousel_mode(prepared: Prepared) -> str:
    mode = prepared.template.carousel or ("auto" if prepared.options.get("carousel") else "")
    if mode != "auto":
        return ""
    return "products" if len(prepared.products) > 1 else "images"


def _plan_slides(prepared: Prepared, formats: list[str]) -> list[Slide]:
    request = prepared.request
    mode = _carousel_mode(prepared)
    offset = int(request.image_offset or 0)
    slides: list[Slide] = []
    if not mode:
        for fmt in formats:
            slides.append(Slide(fmt, 0, "primary", prepared.products, offset))
        return slides
    limit = int(prepared.options.get("max_images") or DEFAULT_CAROUSEL_SLIDES)
    limit = max(1, min(MAX_CAROUSEL_SLIDES, limit))
    if mode == "products":
        count = min(len(prepared.products), limit, max(1, prepared.template.max_products))
    else:
        first = prepared.products[0] if prepared.products else None
        count = min(len(first.image_urls) if first else 1, limit) or 1
    for fmt in formats:
        for index in range(count):
            if mode == "products":
                rotated = prepared.products[index:] + prepared.products[:index]
                slides.append(Slide(fmt, index, "carousel", rotated, offset, index, count))
            else:
                slides.append(Slide(fmt, index, "carousel", prepared.products, offset + index, index, count))
    return slides


class TemplateRenderer:
    """Renders built-in templates locally with Pillow (images) and ffmpeg (videos)."""

    def __init__(self, *, jpeg_quality: int = JPEG_QUALITY, correlation_id: str = "") -> None:
        self.jpeg_quality = jpeg_quality
        self.correlation_id = correlation_id

    async def render(self, request: RenderRequest) -> list[RenderedMedia]:
        template = pick_template(request)
        prepared = await prepare_request(request, template)
        formats = _formats(template, request.formats)
        async with ImageFetcher(store_id=request.store_id, correlation_id=self.correlation_id) as fetcher:
            if template.kind == "video":
                return await video_renderer.render_video(prepared, formats, fetcher)
            return await self._render_images(prepared, formats, fetcher)

    async def _render_images(self, prepared: Prepared, formats: list[str], fetcher: ImageFetcher) -> list[RenderedMedia]:
        template = prepared.template
        slides = _plan_slides(prepared, formats)
        urls: list[str] = []
        for slide in slides:
            urls += spec_urls(prepared, template.spec(slide.fmt), products=slide.products, image_offset=slide.image_offset)
        await fetch_images(prepared, fetcher, urls)
        media: list[RenderedMedia] = []
        for slide in slides:
            ctx = prepared.context(slide.fmt, products=slide.products, image_offset=slide.image_offset,
                                   slide_index=slide.slide_index, slide_count=slide.slide_count)
            data, _report = await asyncio.to_thread(render_image_sync, ctx, template.spec(slide.fmt),
                                                    quality=self.jpeg_quality)
            stored = await asyncio.to_thread(
                save_media_bytes, prepared.request.store_id, prepared.request.post_id, slide.fmt, slide.position, "jpg", data,
            )
            media.append(RenderedMedia(
                path=stored.path, kind="image", format=slide.fmt, mime="image/jpeg", width=ctx.width,
                height=ctx.height, bytes=stored.bytes, sha256=stored.sha256, role=slide.role,
                position=slide.position, source=f"template:{template.key}",
            ))
        logger.info("Rendered %d image(s) with template %s for post %s", len(media), template.key,
                    prepared.request.post_id)
        return media


def decode_logo_setting(value: str) -> bytes | None:
    """Store setting `logo_data` (raw base64 or data URI) → bytes."""
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[1] if "," in raw else ""
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        return None
    return data or None


async def load_store_logo(store_id: str) -> bytes | None:
    """The store's logo bytes (or None) from store setting `logo_data`."""
    value = await db.get_store_setting(store_id, "logo_data", "")
    data = decode_logo_setting(value)
    if data is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
    except Exception:
        logger.warning("Store %s has an unreadable logo_data setting; ignoring it", store_id)
        return None
    return data


def sample_request(
    store_id: str, template: TemplateDef, fmt: str, *, brand: BrandKit | None = None, logo: bytes | None = None,
    sample: dict[str, Any] | None = None, post_id: str = "_preview",
) -> RenderRequest:
    """A RenderRequest filled with the built-in sample products and copy."""
    sample = sample or {}
    count = template.max_products if template.max_products > 1 else max(1, template.min_products)
    count = min(4, max(count, template.min_products, 1))
    if template.carousel:
        count = 4
    products: list[RenderProduct] = sample.get("products") or sample_products(count)
    texts = dict(SAMPLE_TEXTS)
    texts.update(template.sample_texts)
    if template.key.startswith("blog_article"):
        texts.setdefault("headline", "")
        texts["headline"] = "How magnesium supports deeper, more restful sleep"
        texts["subheadline"] = "What the research says — and how to choose the right form for you."
    if template.key.startswith("holiday"):
        texts["headline"] = "Season's greetings"
        texts["subheadline"] = "Thank you for being part of our community this year."
    texts.update(sample.get("texts") or {})
    options = dict(sample.get("options") or {})
    if template.style == "seasonal" and "background_image" not in options:
        options["background_image"] = sample_scene_path("festive")
    return RenderRequest(
        store_id=store_id, post_id=post_id, content_type=template.primary_content_type, template_key=template.key,
        formats=[fmt], products=products, texts=texts, brand=brand or BrandKit(), logo_png=logo,
        options=options,
    )


async def render_preview(
    store_id: str, template_key: str, fmt: str, *, sample: dict[str, Any] | None = None, video: bool = False,
) -> RenderedMedia:
    """Render one template/format with sample products (works offline).

    For video templates the thumbnail is returned unless `video=True`.
    """
    template = get_builtin(template_key)
    if template is None:
        raise NotFoundError(f"Template '{template_key}' does not exist.")
    if fmt not in template.formats:
        raise ValidationError(
            f"Template '{template_key}' does not support format '{fmt}' (supported: {', '.join(template.formats)})."
        )
    settings = await load_settings(store_id)
    brand = settings.brand
    logo = await load_store_logo(store_id) if brand.use_store_logo else None
    request = sample_request(store_id, template, fmt, brand=brand, logo=logo, sample=sample)
    await ensure_sample_images(request.products)
    if template.kind == "video" and not video:
        prepared = await prepare_request(request, template)
        async with ImageFetcher(store_id=store_id) as fetcher:
            return await video_renderer.render_thumbnail(prepared, fmt, fetcher)
    media = await TemplateRenderer().render(request)
    primary = [m for m in media if m.role != "thumbnail"]
    return (primary or media)[0]
