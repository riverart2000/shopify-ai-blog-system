"""Social Engine creatives: storage, images, layout engine, templates, renderer, video.

No network: product images are served by an httpx MockTransport injected as
TRANSPORT_OVERRIDES["cdn"] and generated with Pillow.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import re
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw, ImageStat

import db
from db.social_engine import config_store
from services.social_engine import http as se_http
from services.social_engine.creatives import storage
from services.social_engine.creatives.base import RenderProduct, RenderRequest
from services.social_engine.creatives.brand import (
    build_palette, contrast_ratio, hex_to_rgb, resolve_brand, resolve_color,
)
from services.social_engine.creatives.images import ImageFetcher, sized_url
from services.social_engine.creatives.layout import fit_text, render_spec
from services.social_engine.creatives.library import (
    get_builtin, list_available_templates, seed_builtin_templates, templates_for,
)
from services.social_engine.creatives.prepare import (
    fetch_images, make_products, pick_template, prepare_request, spec_urls,
)
from services.social_engine.creatives.render import (
    TemplateRenderer, load_store_logo, render_preview,
)
from services.social_engine.creatives.templates import FRAMES, all_templates
from services.social_engine.creatives import video as video_mod
from services.social_engine.errors import ConfigurationError, ExternalServiceError, SocialEngineError, ValidationError
from services.social_engine.models import FORMAT_SIZES, ContentType
from services.social_engine.settings import BrandColors, BrandKit

CDN = "https://cdn.shopify.com/s/files/1/0001/files"
IMAGE_TEMPLATES = [t.key for t in all_templates() if t.kind == "image"]
FULL_TEXTS = {
    "headline": "Autumn wellness sale", "subheadline": "Sleep deeper, wake brighter.",
    "promo_text": "Up to 20% off our best sellers", "coupon_code": "GLOW20", "quote": "Rest is a skill.",
    "quote_author": "BioluxeLab", "countdown_label": "2 days left", "sticker": "Big Sale",
    "review_text": "Best magnesium I have tried.", "review_author": "Sam, verified buyer", "fact": "Magnesium matters.",
    "question": "Tea or coffee?", "option_a": "Tea", "option_b": "Coffee", "option_c": "Both",
}
LONG_TITLE = (
    "Ultra Premium Organic Triple-Action Magnesium Glycinate, Bisglycinate & Malate Complex with "
    "Vitamin B6, Zinc and Ashwagandha KSM-66 for Deep Restorative Sleep, Calm Focus and Muscle Recovery"
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def make_photo(seed: int, *, size: tuple[int, int] = (900, 900), plain: bool = False, fmt: str = "JPEG") -> bytes:
    """A deterministic 'product photo': a coloured jar on a backdrop."""
    palette = [(91, 52, 24), (31, 70, 56), (184, 146, 69), (120, 80, 140), (40, 90, 160)]
    color = palette[seed % len(palette)]
    w, h = size
    img = Image.new("RGB", size, (255, 255, 255) if plain else (226, 218, 200))
    draw = ImageDraw.Draw(img)
    if not plain:
        for y in range(h):
            shade = int(30 * y / h)
            draw.line((0, y, w, y), fill=(226 - shade, 218 - shade, 200 - shade))
    draw.rounded_rectangle((w * 0.32, h * 0.25, w * 0.68, h * 0.8), radius=w * 0.05, fill=color)
    draw.rectangle((w * 0.32, h * 0.42, w * 0.68, h * 0.62), fill=(247, 244, 236))
    out = io.BytesIO()
    img.save(out, format=fmt)
    return out.getvalue()


class FakeCDN:
    """MockTransport handler serving generated product photos; records requests."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.overrides: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        name = request.url.path.rsplit("/", 1)[-1]
        if name in self.overrides:
            return self.overrides[name]
        match = re.match(r"p(\d+)_(\d+)\.(jpg|png)$", name)
        if not match:
            return httpx.Response(404, text="not found")
        seed = int(match.group(1)) * 3 + int(match.group(2))
        if match.group(3) == "png":
            return httpx.Response(200, content=make_photo(seed, plain=True, fmt="PNG"),
                                  headers={"content-type": "image/png"})
        return httpx.Response(200, content=make_photo(seed, plain=int(match.group(2)) % 2 == 1),
                              headers={"content-type": "image/jpeg"})


@pytest.fixture
def cdn(se_db):
    fake = FakeCDN()
    se_http.TRANSPORT_OVERRIDES["cdn"] = httpx.MockTransport(fake)
    return fake


def product(index: int, *, images: int = 3, title: str = "", price: str = "£24.99", compare: str = "",
            discount: float = 0.0) -> RenderProduct:
    return RenderProduct(
        product_id=f"gid-{index}", title=title or f"Calm Magnesium Blend {index}", handle=f"calm-{index}",
        url=f"https://bioluxelab.com/products/calm-{index}", price=price, compare_at=compare, discount_pct=discount,
        image_urls=[f"{CDN}/p{index}_{j}.jpg?v=1" for j in range(images)],
        description="Gentle magnesium for restful sleep. Third-party tested.", collections=["Sleep & Recovery"],
    )


def brand() -> BrandKit:
    return BrandKit(business_name="BioluxeLab", website="https://bioluxelab.com")


def request_for(key: str, formats: list[str], *, products: list[RenderProduct] | None = None,
                texts: dict | None = None, **kwargs) -> RenderRequest:
    template = get_builtin(key)
    assert template is not None
    if products is None:
        count = 4 if template.max_products > 1 or template.carousel else 1
        products = [product(i, compare="£29.99" if i == 0 else "") for i in range(count)]
    return RenderRequest(
        store_id="store_1", post_id="post_1", content_type=template.primary_content_type, template_key=key,
        formats=formats, products=products, texts=dict(FULL_TEXTS if texts is None else texts), brand=brand(),
        **kwargs,
    )


async def layout_report(req: RenderRequest, fmt: str) -> tuple[Image.Image, list[dict]]:
    """Render one format through the layout engine directly and return the placement report."""
    template = pick_template(req)
    prepared = await prepare_request(req, template)
    async with ImageFetcher(store_id=req.store_id) as fetcher:
        await fetch_images(prepared, fetcher, spec_urls(prepared, template.spec(fmt)))
    ctx = prepared.context(fmt)
    image = render_spec(ctx, template.spec(fmt))
    return image, list(ctx.report)


def assert_no_overflow(report: list[dict], width: int, height: int) -> None:
    texts = [r for r in report if r["type"] in {"text", "price", "button", "coupon"} and r["bbox"][2] > r["bbox"][0]]
    for item in texts:
        x0, y0, x1, y1 = item["bbox"]
        bx0, by0, bx1, by1 = item["box"]
        assert 0 <= x0 and x1 <= width and 0 <= y0 and y1 <= height, item
        assert x0 >= bx0 - 1 and x1 <= bx1 + 1 and y0 >= by0 - 1 and y1 <= by1 + 1, item
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            overlap_w = min(a["bbox"][2], b["bbox"][2]) - max(a["bbox"][0], b["bbox"][0])
            overlap_h = min(a["bbox"][3], b["bbox"][3]) - max(a["bbox"][1], b["bbox"][1])
            assert overlap_w <= 1 or overlap_h <= 1, (a, b)


def assert_media_file(media, fmt: str) -> Image.Image:
    path = Path(media.path)
    assert path.is_file()
    data = path.read_bytes()
    assert media.bytes == len(data)
    assert media.sha256 == hashlib.sha256(data).hexdigest()
    img = Image.open(path)
    assert img.size == FORMAT_SIZES[fmt] == (media.width, media.height)
    stat = ImageStat.Stat(img.convert("L"))
    assert stat.stddev[0] > 12, "render looks blank"
    return img


# ---------------------------------------------------------------------------
# Templates × formats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("key", IMAGE_TEMPLATES)
async def test_every_image_template_renders_every_format(cdn, key):
    template = get_builtin(key)
    req = request_for(key, list(template.formats))
    media = await TemplateRenderer().render(req)
    formats_seen = {m.format for m in media}
    assert formats_seen == set(template.formats)
    for item in media:
        assert item.kind == "image" and item.mime == "image/jpeg"
        assert item.source == f"template:{key}"
        assert_media_file(item, item.format)
        assert item.role == ("carousel" if template.carousel else "primary")
    for fmt in template.formats:
        _, report = await layout_report(req, fmt)
        assert_no_overflow(report, *FORMAT_SIZES[fmt])


@pytest.mark.asyncio
async def test_video_template_previews_render_thumbnails(se_db):
    for key in ("classic_video", "classic_video_multi"):
        for fmt in get_builtin(key).formats:
            media = await render_preview(se_db, key, fmt)
            assert media.role == "thumbnail"
            assert_media_file(media, fmt)


@pytest.mark.asyncio
async def test_concurrent_renders_in_worker_threads(cdn):
    keys = ["product_classic", "sale_bold", "collage_grid", "brand_quiz", "product_editorial", "countdown_classic"]
    results = await asyncio.gather(*(TemplateRenderer().render(request_for(k, ["square", "story"])) for k in keys))
    hashes = set()
    for media in results:
        assert len(media) == 2
        for item in media:
            assert_media_file(item, item.format)
            hashes.add(item.sha256)
    assert len(hashes) == len(keys) * 2


def test_templates_cover_every_content_type():
    for content_type in ContentType:
        keys = templates_for(content_type)
        if content_type in {ContentType.AI_IMAGE_AD, ContentType.AI_VIDEO}:
            assert keys == []
        else:
            assert keys, f"no template for {content_type}"
    product_styles = {get_builtin(k).style for k in templates_for(ContentType.SINGLE_PRODUCT)}
    assert {"classic", "editorial"} <= product_styles
    for key in ("sale_classic", "sale_bold", "collage_grid", "collage_single_product", "countdown_classic",
                "collection_roundup", "review_spotlight", "brand_fact", "brand_poll", "brand_quiz",
                "blog_article_classic", "carousel_classic", "classic_video", "classic_video_multi"):
        assert get_builtin(key) is not None
    for template in all_templates():
        if template.kind == "image":
            assert set(template.formats) == set(FORMAT_SIZES)
            for fmt in template.formats:
                assert template.spec(fmt)["layers"], (template.key, fmt)


@pytest.mark.asyncio
async def test_ai_content_types_are_not_rendered_locally(se_db):
    req = RenderRequest(store_id="store_1", post_id="p", content_type=ContentType.AI_IMAGE_AD, template_key="",
                        formats=["square"], products=[product(0)])
    with pytest.raises(ValidationError, match="AI provider"):
        await TemplateRenderer().render(req)


@pytest.mark.asyncio
async def test_unknown_template_falls_back_to_content_type_default(cdn):
    req = request_for("product_classic", ["square"])
    req.template_key = "does_not_exist"
    media = await TemplateRenderer().render(req)
    assert media[0].source == f"template:{templates_for(ContentType.SINGLE_PRODUCT)[0]}"


# ---------------------------------------------------------------------------
# Text fitting
# ---------------------------------------------------------------------------

def test_fit_text_shrinks_then_ellipsizes():
    path = resolve_brand(BrandKit()).heading_font
    short = fit_text("Calm", path, "", 600, 200, 80, min_size=40, max_lines=2)
    assert short.size == 80 and short.lines == ["Calm"] and not short.truncated
    shrunk = fit_text("Magnesium Glycinate Complex", path, "", 500, 200, 80, min_size=30, max_lines=1)
    assert shrunk.size < 80 and len(shrunk.lines) == 1 and shrunk.width <= 500
    clipped = fit_text(LONG_TITLE, path, "", 500, 120, 80, min_size=40, max_lines=2)
    assert clipped.truncated and clipped.lines[-1].endswith("…")
    assert clipped.width <= 500 and clipped.height <= 120 and len(clipped.lines) <= 2
    unbreakable = fit_text("Supercalifragilisticexpialidocious" * 3, path, "", 200, 60, 60, min_size=30, max_lines=1)
    assert unbreakable.width <= 200 and unbreakable.height <= 60


@pytest.mark.asyncio
@pytest.mark.parametrize("key", IMAGE_TEMPLATES)
async def test_long_titles_never_overflow(cdn, key):
    long_texts = {k: (v + " ") * 6 for k, v in FULL_TEXTS.items()}
    long_texts["coupon_code"] = "WELCOMEBACKAUTUMN2026"
    products = [product(i, title=LONG_TITLE, price="£1,249.99", compare="£1,499.99") for i in range(4)]
    req = request_for(key, [], products=products, texts=long_texts)
    for fmt in get_builtin(key).formats:
        _, report = await layout_report(req, fmt)
        assert_no_overflow(report, *FORMAT_SIZES[fmt])
        titles = [r for r in report if r["type"] == "text" and "Ultra Premium" in r["text"]]
        for item in titles:
            assert item["truncated"] or len(item["lines"]) >= 1


@pytest.mark.asyncio
async def test_long_title_gets_ellipsis_in_product_card(cdn):
    req = request_for("product_classic", ["square"], products=[product(0, title=LONG_TITLE)])
    _, report = await layout_report(req, "square")
    title = next(r for r in report if r["type"] == "text" and r["text"].startswith("Ultra Premium"))
    assert title["truncated"] and title["lines"][-1].endswith("…")
    assert len(title["lines"]) <= 2


# ---------------------------------------------------------------------------
# Prices and badges
# ---------------------------------------------------------------------------

def test_price_normalisation():
    resolved = resolve_brand(BrandKit(currency_symbol="£"))
    items = make_products([
        RenderProduct("a", "A", price="£24.99", compare_at="£29.99"),
        RenderProduct("b", "B", price="£24.99", compare_at="£19.99"),       # lower: not a sale
        RenderProduct("c", "C", price="", price_value=19.5),
        RenderProduct("d", "D", price="£10.00", compare_at="£10.00"),
        RenderProduct("e", "E", price="€1.299,00", compare_at="€1.599,00"),
        RenderProduct("f", "F", price="£30", compare_at="£40", discount_pct=30),
    ], resolved)
    a, b, c, d, e, f = items
    assert a.on_sale and a.discount_label == "-17%"
    assert not b.on_sale and b.compare_at == "" and b.discount_label == ""
    assert c.price == "£19.50" and not c.on_sale
    assert not d.on_sale
    assert e.on_sale and e.discount_label == "-19%"
    assert f.discount_label == "-30%"


@pytest.mark.asyncio
async def test_sale_template_shows_price_compare_badge_and_coupon(cdn):
    req = request_for("sale_classic", ["square"], products=[product(0, price="£24.99", compare="£29.99")])
    _, report = await layout_report(req, "square")
    price = next(r for r in report if r["type"] == "price")
    assert price["price"] == "£24.99" and price["compare_at"] == "£29.99"
    badges = [r["text"] for r in report if r["type"] == "badge"]
    assert "-17%" in badges
    assert any(r["type"] == "coupon" and r["text"] == "GLOW20" for r in report)
    assert any(r["type"] == "text" and r["text"] == "Autumn wellness sale".upper() for r in report)


@pytest.mark.asyncio
async def test_no_sale_hides_compare_badge_and_price_toggle(cdn):
    req = request_for("sale_classic", ["square"], products=[product(0, price="£24.99")])
    _, report = await layout_report(req, "square")
    assert next(r for r in report if r["type"] == "price")["compare_at"] == ""
    assert not any(r["type"] == "badge" and "%" in r["text"] for r in report)
    req.options["show_price"] = False
    _, report = await layout_report(req, "square")
    assert not any(r["type"] == "price" for r in report)


# ---------------------------------------------------------------------------
# Images: rotation, collages, validation, downloads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_image_rotation_offset_picks_next_image(cdn):
    req = request_for("product_classic", ["square"], products=[product(0, images=3)], image_offset=1)
    _, report = await layout_report(req, "square")
    urls = [r["url"] for r in report if r["type"] == "image"]
    assert urls == [f"{CDN}/p0_1.jpg?v=1"]
    req.image_offset = 3  # wraps around
    _, report = await layout_report(req, "square")
    assert [r["url"] for r in report if r["type"] == "image"] == [f"{CDN}/p0_0.jpg?v=1"]
    cdn.requests.clear()
    req.image_offset = 2
    await TemplateRenderer().render(req)
    assert len(cdn.requests) == 1 and "p0_2.jpg" in cdn.requests[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [2, 3, 4])
async def test_collage_with_2_3_4_products(cdn, count):
    products = [product(i) for i in range(count)]
    req = request_for("collage_grid", ["square", "portrait", "landscape"], products=products)
    media = await TemplateRenderer().render(req)
    assert len(media) == 3
    for item in media:
        assert_media_file(item, item.format)
    for fmt in ("square", "story"):
        _, report = await layout_report(req, fmt)
        images = [r for r in report if r["type"] == "image"]
        assert sorted(r["product"] for r in images) == list(range(count))
        boxes = [r["box"] for r in images]
        for i, a in enumerate(boxes):  # grid cells never overlap
            for b in boxes[i + 1:]:
                assert min(a[2], b[2]) <= max(a[0], b[0]) or min(a[3], b[3]) <= max(a[1], b[1])
        assert_no_overflow(report, *FORMAT_SIZES[fmt])


@pytest.mark.asyncio
async def test_single_product_multi_image_collage_and_mosaic(cdn):
    req = request_for("collage_single_product", ["square"], products=[product(0, images=4)])
    _, report = await layout_report(req, "square")
    assert len({r["url"] for r in report if r["type"] == "image"}) == 4
    req = request_for("collage_mosaic", ["portrait"], products=[product(i) for i in range(3)])
    _, report = await layout_report(req, "portrait")
    assert len([r for r in report if r["type"] == "image"]) == 3


@pytest.mark.asyncio
async def test_carousel_renders_one_slide_per_product_and_per_image(cdn):
    req = request_for("carousel_classic", ["square", "portrait"], products=[product(i) for i in range(3)])
    media = await TemplateRenderer().render(req)
    assert [(m.format, m.position) for m in media] == [
        ("square", 0), ("square", 1), ("square", 2), ("portrait", 0), ("portrait", 1), ("portrait", 2)]
    assert {m.role for m in media} == {"carousel"}
    single = request_for("carousel_editorial", ["square"], products=[product(0, images=2)])
    media = await TemplateRenderer().render(single)
    assert [m.position for m in media] == [0, 1]
    assert media[0].sha256 != media[1].sha256


@pytest.mark.asyncio
async def test_missing_images_and_products_raise_validation_errors(cdn):
    no_images = RenderProduct("gid-9", "Empty Tin", price="£5.00")
    with pytest.raises(ValidationError, match=r"Product 'Empty Tin' \(gid-9\) has no images to render\."):
        await TemplateRenderer().render(request_for("product_classic", ["square"], products=[no_images]))
    with pytest.raises(ValidationError, match="needs at least 2 product"):
        await TemplateRenderer().render(request_for("collage_grid", ["square"], products=[product(0)]))
    with pytest.raises(ValidationError, match="needs text 'quote'"):
        await TemplateRenderer().render(request_for("quote_classic", ["square"], products=[], texts={}))
    with pytest.raises(ValidationError, match="does not support format"):
        await TemplateRenderer().render(request_for("classic_video", ["landscape"]))


@pytest.mark.asyncio
async def test_quote_and_blog_templates_render(cdn):
    media = await TemplateRenderer().render(
        request_for("quote_classic", ["square"], products=[], texts={"quote": "Rest is a skill."}))
    assert_media_file(media[0], "square")
    blog = request_for("blog_article_classic", ["portrait"], products=[],
                       texts={"headline": "How to sleep better", "image_url": f"{CDN}/p7_0.jpg"})
    media = await TemplateRenderer().render(blog)
    assert_media_file(media[0], "portrait")
    with pytest.raises(ValidationError, match="featured image"):
        await TemplateRenderer().render(request_for("blog_article_classic", ["square"], products=[],
                                                    texts={"headline": "No image"}))


@pytest.mark.asyncio
async def test_cdn_download_width_cache_and_errors(cdn):
    assert sized_url(f"{CDN}/p0_0.jpg?v=1&width=300") == f"{CDN}/p0_0.jpg?v=1&width=1200"
    assert sized_url("https://example.com/a.jpg") == "https://example.com/a.jpg"
    async with ImageFetcher(store_id="store_1") as fetcher:
        img = await fetcher.get(f"{CDN}/p1_0.jpg?v=1")
        assert img.mode == "RGBA" and img.size == (900, 900)
    assert len(cdn.requests) == 1 and "width=1200" in cdn.requests[0]
    async with ImageFetcher(store_id="store_1") as fetcher:
        await fetcher.get(f"{CDN}/p1_0.jpg?v=1")
    assert len(cdn.requests) == 1, "second fetch must come from the disk cache"
    cdn.overrides["html.jpg"] = httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
    cdn.overrides["huge.jpg"] = httpx.Response(200, content=b"x" * 2048, headers={"content-type": "image/jpeg"})
    cdn.overrides["broken.jpg"] = httpx.Response(200, content=b"not an image", headers={"content-type": "image/jpeg"})
    async with ImageFetcher(store_id="store_1", max_bytes=1024) as fetcher:
        with pytest.raises(ExternalServiceError, match="expected an image"):
            await fetcher.get(f"{CDN}/html.jpg")
        with pytest.raises(ExternalServiceError, match="too large"):
            await fetcher.get(f"{CDN}/huge.jpg")
        with pytest.raises(ExternalServiceError, match="HTTP 404"):
            await fetcher.get(f"{CDN}/missing.jpg")
    async with ImageFetcher(store_id="store_1") as fetcher:
        with pytest.raises(ExternalServiceError, match="not a readable image"):
            await fetcher.get(f"{CDN}/broken.jpg")


@pytest.mark.asyncio
async def test_exif_orientation_and_png_alpha(cdn):
    img = Image.new("RGB", (300, 100), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW on display
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)
    cdn.overrides["rotated.jpg"] = httpx.Response(200, content=out.getvalue(), headers={"content-type": "image/jpeg"})
    async with ImageFetcher() as fetcher:
        rotated = await fetcher.get(f"{CDN}/rotated.jpg")
        assert rotated.size == (100, 300)
        png = await fetcher.get(f"{CDN}/p2_0.png")
        assert png.mode == "RGBA"


# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------

def test_palette_builder_and_contrast_tokens():
    palette = build_palette("#285746")
    BrandColors(**palette)  # valid hex for every key
    assert set(palette) == {"primary", "text", "background", "cta", "accent", "secondary"}
    assert contrast_ratio(hex_to_rgb(palette["text"]), hex_to_rgb(palette["background"])) >= 7
    for primary in ("#ff6600", "#f2e14c", "#1a1aff", "#000000"):
        derived = build_palette(primary)
        assert contrast_ratio(hex_to_rgb(derived["primary"]), hex_to_rgb(derived["background"])) >= 3
    resolved = resolve_brand(BrandKit())
    assert resolve_color("auto_on:primary", resolved) == (255, 255, 255)
    assert resolve_color("auto_on:background", resolved) == hex_to_rgb("#15251c")
    assert resolve_color("auto_on:cta", resolved) == hex_to_rgb("#15251c")
    assert resolve_color("#123456", resolved) == (0x12, 0x34, 0x56)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_storage_layout_digest_and_path_safety(se_db, tmp_path):
    root = storage.media_root()
    assert root == (tmp_path / "media").resolve()
    stored = storage.save_media_bytes("store_1", "post_1", "square", 2, "jpg", b"hello")
    rel = Path(stored.path).relative_to(root).as_posix()
    assert re.fullmatch(r"store_1/\d{4}/\d{2}/post_1/square_2_[0-9a-f]{10}\.jpg", rel)
    assert stored.sha256 == hashlib.sha256(b"hello").hexdigest() and stored.bytes == 5
    assert storage.resolve_media_path(stored.path) == Path(stored.path)
    assert storage.resolve_media_path(rel) == Path(stored.path)
    for bad in ("../../etc/passwd", "/etc/passwd", str(root / ".." / "outside.txt"), ""):
        with pytest.raises(ValidationError):
            storage.resolve_media_path(bad)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    link = root / "store_1" / "link.txt"
    os.symlink(outside, link)
    with pytest.raises(ValidationError):
        storage.resolve_media_path(link)
    assert storage.safe_segment("../../x") == "x"
    traversal = storage.media_path("../evil", "../../post", "square", 0, "jpg")
    assert traversal.resolve().is_relative_to(root)

    assert await storage.delete_files([stored.path, str(outside)]) == 1
    assert not Path(stored.path).exists() and outside.exists()
    assert not (root / "store_1" / rel.split("/")[1]).exists(), "empty directories are cleaned up"


@pytest.mark.asyncio
async def test_prune_orphans_keeps_known_young_and_cache(se_db):
    keep = storage.save_media_bytes("store_1", "post_1", "square", 0, "jpg", b"keep")
    orphan = storage.save_media_bytes("store_1", "post_2", "square", 0, "jpg", b"orphan")
    young = storage.save_media_bytes("store_1", "post_3", "square", 0, "jpg", b"young")
    cached = storage.write_bytes_atomic(storage.cache_dir() / "ab" / "abc", b"cache")
    old = 1_600_000_000
    for path in (keep.path, orphan.path, cached.path):
        os.utime(path, (old, old))
    dry = await storage.prune_orphans([keep.path], dry_run=True)
    assert dry == [orphan.path] and Path(orphan.path).exists()
    removed = await storage.prune_orphans([keep.path], store_id="store_1")
    assert removed == [orphan.path]
    assert Path(keep.path).exists() and Path(young.path).exists() and Path(cached.path).exists()
    assert await storage.prune_cache(max_age_seconds=3600) == 1


# ---------------------------------------------------------------------------
# Template library (DB)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seeding_is_idempotent_and_preserves_is_active(se_db):
    total = len(all_templates())
    assert await seed_builtin_templates(se_db) == total
    rows = await config_store.list_templates(se_db)
    assert len(rows) == total and all(r["is_builtin"] and r["is_active"] for r in rows)
    row = await config_store.get_template(se_db, "sale_bold")
    assert row["content_type"] == ContentType.SALE_PROMO and set(row["formats"]) == set(FORMAT_SIZES)
    assert row["spec"]["formats"]["square"]["layers"]
    assert await config_store.set_template_active(se_db, "sale_bold", False)
    assert await seed_builtin_templates(se_db) == total
    rows = await config_store.list_templates(se_db)
    assert len(rows) == total
    assert (await config_store.get_template(se_db, "sale_bold"))["is_active"] is False

    sale = await list_available_templates(se_db, ContentType.SALE_PROMO)
    assert [t["key"] for t in sale] == ["sale_classic", "seasonal_scene"]
    sale_all = await list_available_templates(se_db, ContentType.SALE_PROMO, include_inactive=True)
    assert {t["key"]: t["is_active"] for t in sale_all} == {
        "sale_classic": True, "sale_bold": False, "seasonal_scene": True}
    collage = [t["key"] for t in await list_available_templates(se_db, ContentType.COLLAGE)]
    assert collage[0] == "collage_grid" and "classic_video_multi" in collage


@pytest.mark.asyncio
async def test_listing_before_seeding_and_custom_rows(se_db):
    available = await list_available_templates(se_db)
    assert len(available) == len(all_templates())
    await config_store.upsert_template(se_db, {
        "key": "custom_card", "name": "Custom", "content_type": ContentType.SINGLE_PRODUCT, "is_builtin": False,
        "spec": {"content_types": [ContentType.SINGLE_PRODUCT]},
    })
    keys = [t["key"] for t in await list_available_templates(se_db, ContentType.SINGLE_PRODUCT)]
    assert "custom_card" in keys and keys[0] == "product_classic"


# ---------------------------------------------------------------------------
# Logo + previews
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_logo_and_offline_preview(se_db):
    assert await load_store_logo(se_db) is None
    logo = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rounded_rectangle((10, 10, 390, 110), radius=30, fill=(40, 87, 70, 255))
    out = io.BytesIO()
    logo.save(out, format="PNG")
    await db.set_store_settings(se_db, {"logo_data": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()})
    data = await load_store_logo(se_db)
    assert data == out.getvalue()
    media = await render_preview(se_db, "product_editorial", "portrait")
    assert_media_file(media, "portrait")
    req = request_for("product_classic", ["square"], products=[product(0)], logo_png=data)
    se_http.TRANSPORT_OVERRIDES["cdn"] = httpx.MockTransport(FakeCDN())
    _, report = await layout_report(req, "square")
    assert any(r["type"] == "logo" and r["kind"] == "image" for r in report)
    await db.set_store_settings(se_db, {"logo_data": "bm90IGFuIGltYWdl"})
    assert await load_store_logo(se_db) is None


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------

requires_ffmpeg = pytest.mark.skipif(not video_mod.ffmpeg_available(), reason="ffmpeg/ffprobe not installed")


@requires_ffmpeg
@pytest.mark.asyncio
async def test_classic_video_renders_with_thumbnail(cdn):
    req = request_for("classic_video", ["portrait"], products=[product(0, images=2, compare="£29.99")],
                      options={"duration_seconds": 8})
    req.content_type = ContentType.PRODUCT_VIDEO
    media = await TemplateRenderer().render(req)
    video = next(m for m in media if m.kind == "video")
    thumb = next(m for m in media if m.role == "thumbnail")
    assert video.mime == "video/mp4" and (video.width, video.height) == FORMAT_SIZES["portrait"]
    info = await video_mod.probe_video(video.path)
    assert (info["width"], info["height"]) == (1080, 1350)
    assert info["codec"] == "h264" and info["pix_fmt"] == "yuv420p" and info["audio_codec"] == "aac"
    assert abs(info["duration"] - 8.0) < 0.3 and abs(video.duration_ms - 8000) < 300
    assert hashlib.sha256(Path(video.path).read_bytes()).hexdigest() == video.sha256
    assert_media_file(thumb, "portrait")
    with open(video.path, "rb") as handle:
        head = handle.read(4096)
    assert b"moov" in head, "+faststart puts the moov atom first"


@requires_ffmpeg
@pytest.mark.asyncio
async def test_ffmpeg_errors_carry_stderr_tail(se_db, tmp_path):
    ffmpeg = video_mod.find_binary("ffmpeg")
    with pytest.raises(SocialEngineError, match=r"(?s)ffmpeg failed \(exit \d+\):.*nope\.png"):
        await video_mod.run_tool([ffmpeg, "-hide_banner", "-i", str(tmp_path / "nope.png"), str(tmp_path / "o.mp4")],
                                 timeout=30)


def test_missing_ffmpeg_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv("SOCIAL_ENGINE_FFMPEG", "/definitely/not/ffmpeg")
    with pytest.raises(ConfigurationError, match="SOCIAL_ENGINE_FFMPEG"):
        video_mod.find_binary("ffmpeg")


def test_video_duration_scaling_bounds():
    assert abs(sum(video_mod._scale_durations([2.6, 2.4, 2.8, 2.6], None)) - 1.5 - 8.9) < 0.01
    scaled = video_mod._scale_durations([2.6, 2.4, 2.4, 2.4, 2.4, 2.8, 2.6], 30)
    assert abs(sum(scaled) - 3.0 - 15.0) < 0.05
    scaled = video_mod._scale_durations([2.0, 2.0, 2.0], 4)
    assert abs(sum(scaled) - 1.0 - 8.0) < 0.05


def test_frames_match_format_sizes():
    for fmt, frame in FRAMES.items():
        assert (frame.W, frame.H) == FORMAT_SIZES[fmt]


# ---------------------------------------------------------------------------
# Promotions: AI background scenes, sticker/badge overrides, occasions, themed palettes
# ---------------------------------------------------------------------------

BLACK_FRIDAY = BrandColors(primary="#e11d48", text="#ffffff", background="#0b0b0c", cta="#facc15",
                           accent="#3f3f46", secondary="#18181b")


def make_scene(path: Path, *, seed: int = 1, size: tuple[int, int] = (1400, 1000)) -> Path:
    """A busy, text-free 'AI scene': random bright and dark blobs on a gradient."""
    import random

    rng = random.Random(seed)
    img = Image.new("RGB", size, (20, 60, 40))
    draw = ImageDraw.Draw(img)
    for _ in range(140):
        r = rng.randint(20, 120)
        x, y = rng.randint(0, size[0]), rng.randint(0, size[1])
        color = rng.choice([(250, 220, 150), (200, 30, 40), (10, 10, 10), (255, 255, 255), (60, 120, 80)])
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
    img.save(path, format="JPEG", quality=88)
    return path


def text_background_contrast(image: Image.Image, item: dict) -> float:
    """Contrast between a text item's colour and the dominant non-text colour behind it."""
    x0, y0, x1, y1 = item["bbox"]
    region = image.crop((x0, y0, max(x1, x0 + 1), max(y1, y0 + 1))).convert("RGB")
    text = tuple(item["color"])
    colors = sorted(region.getcolors(region.width * region.height) or [], reverse=True)
    for _, color in colors:
        if sum(abs(a - b) for a, b in zip(color, text)) > 90:
            return contrast_ratio(text, color)
    return 21.0


@pytest.mark.asyncio
@pytest.mark.parametrize("key", IMAGE_TEMPLATES)
async def test_background_image_scene_on_every_template(cdn, tmp_path, key):
    scene = make_scene(tmp_path / "scene.jpg")
    plain = request_for(key, ["square"])
    themed = request_for(key, ["square", "story"], options={"background_image": str(scene)})
    without = await TemplateRenderer().render(plain)
    media = await TemplateRenderer().render(themed)
    assert {m.format for m in media} == {"square", "story"}
    for item in media:
        assert_media_file(item, item.format)
    assert media[0].sha256 != without[0].sha256, "the scene must change the render"
    for fmt in ("square", "story"):
        _, report = await layout_report(themed, fmt)
        assert_no_overflow(report, *FORMAT_SIZES[fmt])
        scenes = [r for r in report if r["type"] == "background" and r["scene"]]
        assert scenes, "background layer must draw the scene"
        if get_builtin(key).style != "seasonal":  # seasonal keeps its copy on a solid panel
            assert scenes[0]["contrast_p05"] >= 4.5 or scenes[0]["scrim"] >= 0.92
            assert scenes[0]["muted_contrast_p05"] >= 3.0 or scenes[0]["scrim"] >= 0.92


@pytest.mark.asyncio
async def test_seasonal_scene_template_frames_product_over_scene(cdn, tmp_path):
    scene = make_scene(tmp_path / "scene.jpg", seed=4)
    req = request_for("seasonal_scene", list(FORMAT_SIZES), products=[product(0, compare="£29.99")],
                      texts={"occasion": "Christmas", "promo_text": "Gift-ready wellness", "coupon_code": "XMAS15",
                             "countdown_label": "Order by Dec 20"},
                      options={"background_image": str(scene), "sticker": "Christmas Offer"})
    media = await TemplateRenderer().render(req)
    assert len(media) == 5
    for item in media:
        assert_media_file(item, item.format)
    for fmt in FORMAT_SIZES:
        image, report = await layout_report(req, fmt)
        assert_no_overflow(report, *FORMAT_SIZES[fmt])
        texts = [r["text"] for r in report if r["type"] in {"text", "badge", "sticker", "coupon"}]
        assert "Christmas" in texts and "XMAS15" in texts and "ORDER BY DEC 20" in texts
        assert "CHRISTMAS OFFER" in texts and "-17%" in texts
        assert any(r["type"] == "image" for r in report), "the real product photo is the hero"
        for item in report:
            if item["type"] in {"text", "price"} and item["bbox"][2] > item["bbox"][0]:
                assert text_background_contrast(image, item) >= 3.0, item
    assert get_builtin("seasonal_scene").content_types == (ContentType.HOLIDAY, ContentType.SALE_PROMO)


@pytest.mark.asyncio
async def test_unusable_background_image_is_ignored_with_a_warning(cdn, tmp_path, caplog):
    baseline = (await TemplateRenderer().render(request_for("product_classic", ["square"])))[0]
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a jpeg")
    for path in (str(tmp_path / "missing.jpg"), str(broken)):
        caplog.clear()
        with caplog.at_level("WARNING", logger="ai_blog_server.social_engine.creatives"):
            media = await TemplateRenderer().render(
                request_for("product_classic", ["square"], options={"background_image": path}))
        assert media[0].sha256 == baseline.sha256
        assert any("Background image" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_promotion_overrides_and_occasion_texts(cdn):
    texts = {"occasion": "Black Friday", "promo_text": "Our biggest offer of the year", "coupon_code": "BF30",
             "countdown_label": "Ends Monday"}
    options = {"sticker": "Flash Sale", "badge": "-30%"}
    for key in ("sale_classic", "sale_bold", "countdown_bold", "holiday_classic", "seasonal_scene", "product_classic"):
        req = request_for(key, ["square"], products=[product(0, compare="£29.99")], texts=dict(texts),
                          options=dict(options))
        req.texts["sticker"] = "Ignored text sticker"  # options win over texts
        _, report = await layout_report(req, "square")
        found = {r["type"]: [] for r in report}
        for r in report:
            found[r["type"]].append(r.get("text", ""))
        assert "FLASH SALE" in found.get("sticker", []), key
        if key != "product_classic":
            assert any("BLACK FRIDAY" == t.upper() for t in found.get("text", [])), key
            assert "BF30" in found.get("coupon", []), key
        if key in {"sale_classic", "sale_bold", "seasonal_scene"}:
            assert "-30%" in found.get("badge", []) and "ENDS MONDAY" in found.get("badge", []), key
            assert "Our biggest offer of the year" in found.get("text", []), key


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["product_classic", "product_editorial", "sale_bold", "collage_mosaic",
                                 "brand_fact", "brand_quiz", "countdown_classic", "seasonal_scene"])
async def test_themed_palette_is_applied_and_legible(cdn, key):
    req = request_for(key, ["square"])
    req.brand = BrandKit(business_name="BioluxeLab", website="https://bioluxelab.com", colors=BLACK_FRIDAY)
    image, report = await layout_report(req, "square")
    assert_no_overflow(report, 1080, 1080)
    for item in report:
        if item["type"] in {"text", "price"} and item["bbox"][2] > item["bbox"][0]:
            assert text_background_contrast(image, item) >= 3.0, item
    buttons = [r for r in report if r["type"] == "button"]
    for button_item in buttons:  # CTA uses the themed cta colour, not BioluxeLab gold
        x0, y0, _x1, y1 = button_item["bbox"]
        pixel = image.getpixel((x0 + (y1 - y0) // 2 + 4, y0 + 4))
        assert sum(abs(a - b) for a, b in zip(pixel[:3], hex_to_rgb("#facc15"))) < 30
    assert image.getpixel((2, 1078))[:3] != hex_to_rgb("#f7f4ec")
