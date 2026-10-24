"""Product image download/cache and Pillow image utilities.

Downloads go through `ServiceClient("cdn", "")` so every fetch is ledgered in
`social_api_calls` (tests inject `TRANSPORT_OVERRIDES["cdn"]`). Raw bytes are
cached on disk under `{media_root}/_cache`, keyed by the sha256 of the URL.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import os
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

from ..errors import ExternalServiceError, ValidationError
from ..http import CallContext, ServiceClient
from . import storage

logger = logging.getLogger("ai_blog_server.social_engine.creatives")

RGB = tuple[int, int, int]

DEFAULT_MAX_BYTES = 15 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
DEFAULT_WIDTH = 1200
MAX_SOURCE_PIXELS = 60_000_000
MAX_WORKING_EDGE = 2400
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF", "MPO", "BMP", "TIFF", "AVIF", "HEIF"})


# ---------------------------------------------------------------------------
# URLs and cache
# ---------------------------------------------------------------------------

def is_shopify_cdn(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return host.endswith("cdn.shopify.com") or parsed.path.startswith("/cdn/shop/") or "/s/files/" in parsed.path


def sized_url(url: str, width: int = DEFAULT_WIDTH) -> str:
    """Ask Shopify's CDN for a `width`-pixel rendition (replaces any width/height)."""
    if not url.lower().startswith(("http://", "https://")) or not is_shopify_cdn(url):
        return url
    parsed = urlparse(url)
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in {"width", "height"}]
    params.append(("width", str(int(width))))
    return urlunparse(parsed._replace(query=urlencode(params)))


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cache_file(url: str) -> Path:
    key = cache_key(url)
    return storage.cache_dir() / key[:2] / key


def prime_cache(url: str, data: bytes) -> Path:
    """Store bytes for `url` in the cache (used for offline sample images)."""
    path = cache_file(url)
    storage.write_bytes_atomic(path, data)
    return path


def _read_cache(url: str) -> bytes | None:
    path = cache_file(url)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        os.utime(path, None)
    except OSError:
        pass
    return data or None


def _decode_data_uri(url: str) -> bytes:
    try:
        header, payload = url.split(",", 1)
    except ValueError as exc:
        raise ValidationError("Image data URI is malformed.") from exc
    if ";base64" not in header:
        raise ValidationError("Image data URI must be base64 encoded.")
    try:
        return base64.b64decode(payload, validate=False)
    except ValueError as exc:
        raise ValidationError("Image data URI is not valid base64.") from exc


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def decode_image(data: bytes, *, source: str = "", max_edge: int = MAX_WORKING_EDGE) -> Image.Image:
    """Bytes → RGBA image (EXIF orientation applied, downscaled to `max_edge`)."""
    try:
        img = Image.open(io.BytesIO(data))
        fmt = (img.format or "").upper()
        if fmt and fmt not in ALLOWED_FORMATS:
            raise ValidationError(f"Unsupported image format {fmt} for {source or 'image'}.")
        width, height = img.size
        if width * height > MAX_SOURCE_PIXELS:
            raise ValidationError(f"Image is too large ({width}x{height} px): {source or 'image'}")
        if fmt == "JPEG":
            img.draft("RGB", (max_edge, max_edge))
        img.load()
    except ValidationError:
        raise
    except Exception as exc:
        raise ExternalServiceError(
            "cdn", f"downloaded file is not a readable image ({type(exc).__name__}): {source}",
            retryable=False,
        ) from exc
    img = ImageOps.exif_transpose(img) or img
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    img = img.convert("RGBA")
    if max(img.size) > max_edge:
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return img


class ImageFetcher:
    """Downloads (or reads from cache) product images for one render.

    Use as an async context manager; images are memoised per URL.
    """

    def __init__(
        self,
        *,
        store_id: str = "",
        correlation_id: str = "",
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float = DEFAULT_TIMEOUT,
        width: int = DEFAULT_WIDTH,
        concurrency: int = 4,
    ) -> None:
        self.ctx = CallContext(store_id=store_id, correlation_id=correlation_id)
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.width = width
        self._client: ServiceClient | None = None
        self._memo: dict[str, Image.Image] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(max(1, concurrency))

    async def __aenter__(self) -> ImageFetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> ServiceClient:
        if self._client is None:
            self._client = ServiceClient("cdn", "", timeout=self.timeout, max_attempts=2, backoff_base=0.5)
        return self._client

    async def fetch_bytes(self, url: str) -> bytes:
        url = (url or "").strip()
        if not url:
            raise ValidationError("Image URL is empty.")
        if url.startswith("data:"):
            return _decode_data_uri(url)
        target = sized_url(url, self.width)
        cached = await asyncio.to_thread(_read_cache, target)
        if cached is not None:
            return cached
        if not target.lower().startswith(("http://", "https://")):
            raise ValidationError(f"Unsupported image URL (only http/https/data URIs): {url}")
        async with self._semaphore:
            response = await self._http().request(
                "GET", target, operation="download_image", ctx=self.ctx, timeout=self.timeout,
            )
        declared = response.headers.get("content-length", "")
        size = len(response.content)
        if size > self.max_bytes or (declared.isdigit() and int(declared) > self.max_bytes):
            raise ExternalServiceError(
                "cdn",
                f"image is too large ({max(size, int(declared or 0)) / 1_048_576:.1f} MB > "
                f"{self.max_bytes / 1_048_576:.0f} MB): {url}",
                status_code=response.status_code, retryable=False,
            )
        if not size:
            raise ExternalServiceError("cdn", f"image download returned no data: {url}", retryable=False)
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not content_type.startswith(("image/", "application/octet-stream", "binary/")):
            raise ExternalServiceError(
                "cdn", f"expected an image but got '{content_type}': {url}",
                status_code=response.status_code, retryable=False,
            )
        data = response.content
        await asyncio.to_thread(storage.write_bytes_atomic, cache_file(target), data)
        return data

    async def get(self, url: str) -> Image.Image:
        if url in self._memo:
            return self._memo[url]
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            if url in self._memo:
                return self._memo[url]
            data = await self.fetch_bytes(url)
            image = await asyncio.to_thread(decode_image, data, source=url)
            self._memo[url] = image
            return image

    async def get_many(self, urls: Iterable[str]) -> dict[str, Image.Image]:
        unique = list(dict.fromkeys(u for u in urls if u))
        images = await asyncio.gather(*(self.get(url) for url in unique))
        return dict(zip(unique, images))


# ---------------------------------------------------------------------------
# Pillow utilities
# ---------------------------------------------------------------------------

def fit_contain(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale to fit inside `size` keeping the aspect ratio (may upscale)."""
    tw, th = max(1, size[0]), max(1, size[1])
    scale = min(tw / img.width, th / img.height)
    new = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(new, Image.Resampling.LANCZOS)


def fit_cover(img: Image.Image, size: tuple[int, int], focus: tuple[float, float] = (0.5, 0.5)) -> Image.Image:
    """Scale and crop to exactly `size`, keeping `focus` (0–1) in view."""
    tw, th = max(1, size[0]), max(1, size[1])
    scale = max(tw / img.width, th / img.height)
    sw, sh = img.width * scale, img.height * scale
    left = max(0.0, min(sw * focus[0] - tw / 2, sw - tw))
    top = max(0.0, min(sh * focus[1] - th / 2, sh - th))
    box = (left / scale, top / scale, min(img.width, (left + tw) / scale), min(img.height, (top + th) / scale))
    return img.resize((tw, th), Image.Resampling.LANCZOS, box=box)


def fit_cover_soft(
    img: Image.Image,
    size: tuple[int, int],
    focus: tuple[float, float] = (0.5, 0.5),
    *,
    max_crop: float = 0.24,
    anchor: tuple[float, float] = (0.5, 0.5),
    max_height: float = 1.0,
) -> Image.Image:
    """Cover-fit that never crops more than `max_crop` of the photo.

    When the box and photo aspect ratios differ a lot (or `max_height` < 1
    limits the photo's height), the photo is placed at `anchor` and the empty
    strips are filled by extending its own outer edge, blurred and feathered,
    so studio backdrops continue seamlessly.
    """
    tw, th = max(1, size[0]), max(1, size[1])
    iw, ih = img.size
    cover = max(tw / iw, th / ih)
    keep = max(0.01, 1 - max_crop)
    scale = min(cover, tw / (iw * keep), th / (ih * keep), th * max_height / ih)
    if scale >= cover - 1e-9:
        return fit_cover(img, size, focus)
    sw, sh = min(tw, max(1, round(iw * scale))), min(th, max(1, round(ih * scale)))
    sharp = fit_cover(img, (sw, sh), focus).convert("RGBA")
    ox, oy = int((tw - sw) * anchor[0]), int((th - sh) * anchor[1])
    fill, _ = border_stats(sharp)
    backdrop = Image.new("RGBA", (tw, th), (*fill, 255))
    backdrop.paste(sharp, (ox, oy))
    edge = max(2, int(min(sw, sh) * 0.02))
    if ox > 0:
        backdrop.paste(sharp.crop((0, 0, edge, sh)).resize((ox + edge, sh)), (0, oy))
    if tw - ox - sw > 0:
        backdrop.paste(sharp.crop((sw - edge, 0, sw, sh)).resize((tw - ox - sw + edge, sh)), (ox + sw - edge, oy))
    if oy > 0:
        backdrop.paste(sharp.crop((0, 0, sw, edge)).resize((sw, oy + edge)), (ox, 0))
    if th - oy - sh > 0:
        backdrop.paste(sharp.crop((0, sh - edge, sw, sh)).resize((sw, th - oy - sh + edge)), (ox, oy + sh - edge))
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(max(tw, th) * 0.02))
    feather = max(2, int(min(sw, sh) * 0.05))
    mask = Image.new("L", (sw, sh), 255)
    draw = ImageDraw.Draw(mask)
    for i in range(feather):
        value = int(255 * (i + 1) / (feather + 1))
        if ox > 0:
            draw.line((i, 0, i, sh), fill=value)
        if tw - ox - sw > 0:
            draw.line((sw - 1 - i, 0, sw - 1 - i, sh), fill=value)
        if oy > 0:
            draw.line((0, i, sw, i), fill=value)
        if th - oy - sh > 0:
            draw.line((0, sh - 1 - i, sw, sh - 1 - i), fill=value)
    backdrop.paste(sharp, (ox, oy), mask)
    return backdrop


def rounded_mask(size: tuple[int, int], radius: float, *, scale: int = 4) -> Image.Image:
    """Anti-aliased rounded-rectangle mask (L mode)."""
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    radius = max(0.0, min(radius, w / 2, h / 2))
    if radius <= 0:
        return Image.new("L", (w, h), 255)
    big = Image.new("L", (w * scale, h * scale), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * scale - 1, h * scale - 1), radius=radius * scale, fill=255)
    return big.resize((w, h), Image.Resampling.LANCZOS)


def ellipse_mask(size: tuple[int, int], *, scale: int = 4) -> Image.Image:
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    big = Image.new("L", (w * scale, h * scale), 0)
    ImageDraw.Draw(big).ellipse((0, 0, w * scale - 1, h * scale - 1), fill=255)
    return big.resize((w, h), Image.Resampling.LANCZOS)


def round_corners(img: Image.Image, radius: float) -> Image.Image:
    out = img.convert("RGBA")
    if radius <= 0:
        return out
    mask = rounded_mask(out.size, radius)
    alpha = ImageChops.multiply(out.getchannel("A"), mask)
    out.putalpha(alpha)
    return out


def drop_shadow(
    size: tuple[int, int],
    *,
    radius: float = 0,
    blur: float = 24,
    color: RGB = (21, 37, 28),
    opacity: float = 0.18,
    shape: str = "rounded",
) -> tuple[Image.Image, int]:
    """Soft shadow for a shape of `size`; returns (RGBA layer, padding)."""
    pad = int(blur * 3) + 2
    w, h = int(size[0]), int(size[1])
    mask = Image.new("L", (w + pad * 2, h + pad * 2), 0)
    inner = ellipse_mask((w, h)) if shape == "ellipse" else rounded_mask((w, h), radius)
    mask.paste(inner, (pad, pad))
    if blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
    mask = mask.point(lambda v: int(v * max(0.0, min(1.0, opacity))))
    layer = Image.new("RGBA", mask.size, (*color, 0))
    layer.putalpha(mask)
    return layer, pad


def border_stats(img: Image.Image, *, strip: float = 0.04) -> tuple[RGB, float]:
    """Mean colour and spread of the outer border (to detect studio packshots)."""
    rgba = img.convert("RGBA")
    small = rgba.copy()
    small.thumbnail((200, 200))
    rgb = Image.new("RGB", small.size, (255, 255, 255))
    rgb.paste(small, mask=small.getchannel("A"))
    w, h = rgb.size
    s = max(1, int(min(w, h) * strip))
    strips = [rgb.crop((0, 0, w, s)), rgb.crop((0, h - s, w, h)), rgb.crop((0, 0, s, h)), rgb.crop((w - s, 0, w, h))]
    means: list[float] = [0.0, 0.0, 0.0]
    spreads: list[float] = []
    for part in strips:
        stat = ImageStat.Stat(part)
        for i in range(3):
            means[i] += stat.mean[i] / len(strips)
        spreads.append(max(stat.stddev))
    mean_rgb = (int(means[0]), int(means[1]), int(means[2]))
    return mean_rgb, max(spreads)


def has_plain_background(img: Image.Image, *, tolerance: float = 4.0) -> bool:
    """True when the photo sits on a uniform backdrop (packshot or cut-out)."""
    if img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 250:
        return True
    _, spread = border_stats(img)
    return spread <= tolerance


def plain_background_color(img: Image.Image) -> RGB:
    if img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 250:
        return (255, 255, 255)
    color, _ = border_stats(img)
    return color


def trim_white(img: Image.Image, *, threshold: int = 244, margin: float = 0.04) -> Image.Image:
    """Crop near-white (or transparent) borders around a product, keeping a small margin."""
    rgba = img.convert("RGBA")
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    gray = flat.convert("L")
    content = gray.point(lambda v: 255 if v < threshold else 0)
    bbox = content.getbbox()
    if not bbox:
        return rgba
    x0, y0, x1, y1 = bbox
    area = (x1 - x0) * (y1 - y0)
    if area < 0.04 * rgba.width * rgba.height:
        return rgba
    if (x1 - x0) >= rgba.width * 0.97 and (y1 - y0) >= rgba.height * 0.97:
        return rgba
    pad = int(max(x1 - x0, y1 - y0) * margin)
    box = (max(0, x0 - pad), max(0, y0 - pad), min(rgba.width, x1 + pad), min(rgba.height, y1 + pad))
    return rgba.crop(box)


def dominant_colors(img: Image.Image, count: int = 5) -> list[RGB]:
    """Most common colours (most frequent first), ignoring transparent pixels."""
    rgba = img.convert("RGBA")
    rgba.thumbnail((160, 160))
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    quantized = flat.quantize(colors=max(2, count), method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = sorted(quantized.getcolors() or [], reverse=True)
    result: list[RGB] = []
    for _, index in counts:
        r, g, b = palette[index * 3: index * 3 + 3]
        result.append((r, g, b))
    return result[:count]


def dominant_color(img: Image.Image, *, ignore_light: bool = True) -> RGB:
    """The most common non-background colour of a product photo."""
    colors = dominant_colors(img, 6)
    for color in colors:
        if ignore_light and min(color) > 225:
            continue
        if max(color) < 18:
            continue
        return color
    return colors[0] if colors else (128, 128, 128)


def encode_jpeg(img: Image.Image, *, quality: int = 90) -> bytes:
    rgb = img.convert("RGB") if img.mode != "RGB" else img
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=quality, optimize=True, progressive=True, subsampling=0)
    return out.getvalue()


def encode_png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=False, compress_level=6)
    return out.getvalue()


