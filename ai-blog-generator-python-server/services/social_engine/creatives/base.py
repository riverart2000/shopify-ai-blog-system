"""Creative rendering contract.

The pipeline builds a `RenderRequest` (products, texts, brand, formats) and the
renderer returns local media files. Renderers never touch the database; the
pipeline stores the returned `RenderedMedia` rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..settings import BrandKit


@dataclass
class RenderProduct:
    product_id: str
    title: str
    handle: str = ""
    url: str = ""
    price: str = ""                 # display string incl. symbol, e.g. "£24.99"
    price_value: float = 0.0
    compare_at: str = ""            # display string or "" when not on sale
    discount_pct: float = 0.0
    image_urls: list[str] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)


@dataclass
class RenderRequest:
    store_id: str
    post_id: str
    content_type: str
    template_key: str
    formats: list[str]                          # MediaFormat values
    products: list[RenderProduct] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)
    # Known text keys: headline, subheadline, badge, cta, promo_text, coupon_code,
    # quote, quote_author, countdown_label, sticker, store_name, website, review_text,
    # review_author.
    brand: BrandKit = field(default_factory=BrandKit)
    logo_png: bytes | None = None
    image_offset: int = 0                       # rotate product images between reposts
    options: dict[str, Any] = field(default_factory=dict)
    # Known options: show_price, show_logo, show_store_name, max_images, duration_seconds,
    # music_path, background ("solid"|"gradient"|"pattern"), carousel (bool).


@dataclass
class RenderedMedia:
    path: str
    kind: str                       # image | video
    format: str
    mime: str
    width: int
    height: int
    bytes: int
    sha256: str
    duration_ms: int = 0
    role: str = "primary"           # primary | carousel | thumbnail
    position: int = 0
    source: str = ""                # e.g. "template:product_card", "ffmpeg:classic_video"


class Renderer(Protocol):
    async def render(self, request: RenderRequest) -> list[RenderedMedia]: ...
