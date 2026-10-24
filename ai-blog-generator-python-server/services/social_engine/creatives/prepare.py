"""Turn a RenderRequest into everything the layout engine needs.

Shared by the image renderer (render.py) and the video renderer (video.py):
template choice, validation, product/price normalisation, text merging,
logo decoding and up-front image fetching.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from ..errors import ValidationError
from ..models import ContentType
from .base import RenderProduct, RenderRequest
from .brand import ResolvedBrand, resolve_brand
from .images import ImageFetcher, decode_image
from .layout import LayoutContext, ProductInfo, format_money, image_requests, parse_amount
from .library import get_builtin, templates_for
from .storage import media_root
from .templates import FRAMES, TemplateDef

logger = logging.getLogger("ai_blog_server.social_engine.creatives")

AI_CONTENT_TYPES = frozenset({ContentType.AI_IMAGE_AD, ContentType.AI_VIDEO})
OPTION_TEXT_OVERRIDES = ("sticker", "badge")     # promotion themes set these through request.options
MAX_BACKGROUND_BYTES = 30 * 1024 * 1024


def pick_template(request: RenderRequest) -> TemplateDef:
    """The requested template, else the first built-in for the content type."""
    key = (request.template_key or "").strip()
    if key:
        found = get_builtin(key)
        if found is not None:
            return found
        logger.warning("Unknown template '%s'; falling back to the default for %s", key, request.content_type)
    keys = templates_for(request.content_type)
    if not keys:
        if request.content_type in AI_CONTENT_TYPES:
            raise ValidationError(
                f"Content type '{request.content_type}' is produced by an AI provider, not a built-in template."
            )
        raise ValidationError(f"No built-in template renders content type '{request.content_type}'.")
    found = get_builtin(keys[0])
    assert found is not None
    return found


def make_products(products: Iterable[RenderProduct], brand: ResolvedBrand) -> list[ProductInfo]:
    """Normalise display prices: compare-at only when it is really higher; discount computed if missing."""
    result: list[ProductInfo] = []
    for index, product in enumerate(products):
        price = (product.price or "").strip()
        if not price and product.price_value:
            price = format_money(float(product.price_value), brand.currency_symbol)
        compare = (product.compare_at or "").strip()
        price_amount = parse_amount(price) if price else None
        if price_amount is None and product.price_value:
            price_amount = float(product.price_value)
        compare_amount = parse_amount(compare) if compare else None
        if compare and (compare == price or (compare_amount is not None and price_amount is not None
                                             and compare_amount <= price_amount)):
            compare = ""
        discount = float(product.discount_pct or 0.0)
        if compare and discount <= 0 and compare_amount and price_amount is not None and compare_amount > 0:
            discount = (compare_amount - price_amount) / compare_amount * 100
        if not compare:
            discount = 0.0
        result.append(ProductInfo(
            index=index, product_id=str(product.product_id), title=(product.title or "").strip(), price=price,
            compare_at=compare, discount_pct=max(0.0, min(99.0, discount)),
            image_urls=[u for u in (product.image_urls or []) if u and u.strip()],
            description=product.description or "", collections=list(product.collections or []),
            url=product.url or "",
        ))
    return result


def merge_texts(template: TemplateDef, request: RenderRequest) -> dict[str, str]:
    """Template defaults < request.texts < request.options["sticker"/"badge"]."""
    texts = dict(template.defaults)
    for key, value in (request.texts or {}).items():
        if value is None:
            continue
        value = str(value).strip()
        if value:
            texts[key] = value
    for key in OPTION_TEXT_OVERRIDES:
        value = str((request.options or {}).get(key) or "").strip()
        if value:
            texts[key] = value
    return texts


def load_background_image(path: Any) -> Image.Image | None:
    """Decode options["background_image"] (a local file). Never raises: a missing or
    unreadable file is logged and the template's normal background is used."""
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = media_root() / candidate
    try:
        size = candidate.stat().st_size
        if size > MAX_BACKGROUND_BYTES:
            raise ValueError(f"file is too large ({size / 1_048_576:.1f} MB)")
        return decode_image(candidate.read_bytes(), source=str(candidate), max_edge=2400)
    except Exception as exc:
        logger.warning("Background image %s is unusable; rendering the normal background (%s)", raw, exc)
        return None


def resolved_options(request: RenderRequest) -> dict[str, Any]:
    options = dict(request.options or {})
    options.setdefault("show_price", request.brand.show_price)
    options.setdefault("show_logo", request.brand.use_store_logo)
    return options


def validate_request(template: TemplateDef, products: list[ProductInfo], texts: dict[str, str]) -> list[ProductInfo]:
    """Raise ValidationError with an exact, human message; returns the products used."""
    used = products[: max(template.max_products, 1)] if template.max_products else products
    if len(products) < template.min_products:
        raise ValidationError(
            f"Template '{template.key}' needs at least {template.min_products} product(s); got {len(products)}."
        )
    if template.needs_images:
        for product in used:
            if not product.image_urls:
                raise ValidationError(f"Product '{product.title}' ({product.product_id}) has no images to render.")
    elif template.key.startswith("blog_article") and not (texts.get("image_url") or (used and used[0].image_urls)):
        raise ValidationError("Blog article has no featured image (texts['image_url'] or products[0].image_urls).")
    if template.key.startswith("blog_article") and not (texts.get("headline") or (used and used[0].title)):
        raise ValidationError("Blog article has no title (texts['headline'] or products[0].title).")
    for requirement in template.required_texts:
        options = requirement.split("|")
        if not any(texts.get(option) for option in options):
            raise ValidationError(
                f"Template '{template.key}' needs text '{' or '.join(options)}'."
            )
    return used


def decode_logo(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        return decode_image(data, source="store logo", max_edge=1200)
    except Exception as exc:
        logger.warning("Store logo could not be decoded; rendering without it: %s", exc)
        return None


@dataclass
class Prepared:
    request: RenderRequest
    template: TemplateDef
    brand: ResolvedBrand
    products: list[ProductInfo]
    texts: dict[str, str]
    options: dict[str, Any]
    logo: Image.Image | None = None
    images: dict[str, Image.Image] = field(default_factory=dict)
    background_image: Image.Image | None = None

    def context(
        self,
        fmt: str,
        *,
        products: list[ProductInfo] | None = None,
        texts: dict[str, str] | None = None,
        image_offset: int | None = None,
        slide_index: int = 0,
        slide_count: int = 1,
        size: tuple[int, int] | None = None,
    ) -> LayoutContext:
        frame = FRAMES[fmt]
        width, height = size or (frame.W, frame.H)
        merged = dict(self.texts)
        if texts:
            merged.update({k: v for k, v in texts.items() if v is not None})
        return LayoutContext(
            width=width, height=height, brand=self.brand, texts=merged,
            products=list(products if products is not None else self.products), images=self.images,
            image_offset=self.request.image_offset if image_offset is None else image_offset,
            logo=self.logo, options=self.options, slide_index=slide_index, slide_count=slide_count,
            background_image=self.background_image,
        )


def prepare(request: RenderRequest, template: TemplateDef) -> Prepared:
    brand = resolve_brand(request.brand)
    options = resolved_options(request)
    if options.get("show_store_name") is False:
        brand = replace(brand, business_name="")
    products = make_products(request.products, brand)
    texts = merge_texts(template, request)
    if options.get("show_store_name") is False:
        texts["store_name"] = ""
    validate_request(template, products, texts)
    return Prepared(
        request=request, template=template, brand=brand, products=products, texts=texts, options=options,
        logo=decode_logo(request.logo_png) if options.get("show_logo", True) else None,
    )


async def prepare_request(request: RenderRequest, template: TemplateDef) -> Prepared:
    """`prepare` plus the (thread-offloaded) background scene decode."""
    prepared = prepare(request, template)
    if prepared.options.get("background_image"):
        prepared.background_image = await asyncio.to_thread(load_background_image, prepared.options["background_image"])
    return prepared


async def fetch_images(prepared: Prepared, fetcher: ImageFetcher, urls: Iterable[str]) -> None:
    missing = [u for u in dict.fromkeys(urls) if u and u not in prepared.images]
    if missing:
        prepared.images.update(await fetcher.get_many(missing))


def spec_urls(prepared: Prepared, spec: dict, *, products: list[ProductInfo] | None = None,
              image_offset: int | None = None) -> list[str]:
    return image_requests(
        spec, products if products is not None else prepared.products,
        prepared.request.image_offset if image_offset is None else image_offset, texts=prepared.texts,
    )
