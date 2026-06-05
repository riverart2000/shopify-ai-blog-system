"""services/logo_service.py — Pillow-based image compositing.

• stamp_photo(image_url, title, logo_b64)   → JPEG data URI
    Photo with a dark gradient title bar at the bottom + logo badge at bottom-right.

• stamp_infographic(image_url, logo_b64)    → JPEG data URI
    Infographic with a logo badge at bottom-right.

Both fall back to returning the original URL unchanged if Pillow is not
installed or any processing step fails, so image generation never blocks
publishing.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import textwrap
from typing import Optional

import httpx

logger = logging.getLogger("ai_blog_server")

_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def _load_font(size: int):
    """Try system TrueType fonts; fall back to PIL default."""
    try:
        from PIL import ImageFont
        for path in _FONT_CANDIDATES:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        try:
            return ImageFont.load_default(size=size)   # Pillow ≥ 10.1
        except TypeError:
            return ImageFont.load_default()
    except Exception:
        return None


async def _fetch_bytes(url: str) -> Optional[bytes]:
    # Data URIs: decode the base64 payload directly instead of HTTP fetching
    if url.startswith("data:"):
        try:
            _, b64data = url.split(",", 1)
            return base64.b64decode(b64data)
        except Exception as exc:
            logger.warning("logo_service: failed to decode data URI: %s", exc)
            return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            data = resp.content
            logger.debug(
                "logo_service: received image from %s — %d bytes, content-type=%s",
                url[:80], len(data), resp.headers.get("content-type", "unknown"),
            )
            return data
    except Exception as exc:
        logger.warning("logo_service: failed to fetch image %s … %s", url[:80], exc)
        return None


def _logo_bytes_from_b64(logo_b64: str) -> Optional[bytes]:
    """Accept raw base64 or data URI (data:image/...;base64,<data>)."""
    if not logo_b64:
        return None
    try:
        if logo_b64.startswith("data:"):
            _, payload = logo_b64.split(",", 1)
            logo_b64 = payload
        return base64.b64decode(logo_b64)
    except Exception:
        return None


def _to_data_uri(img_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode()


def _top_has_text_overlay(img) -> bool:
    """Heuristic: detect if the top ~20% already has a rendered text/title overlay.

    Checks for a dark band (mean brightness < 90) with significant edge activity
    (edge mean > 18), which is characteristic of white text on a dark gradient.
    Returns False on any error so compositing is never blocked.
    """
    try:
        from PIL import ImageFilter, ImageStat
        w, h = img.size
        band_h = max(1, h // 5)
        top = img.crop((0, 0, w, band_h)).convert("L")
        brightness = ImageStat.Stat(top).mean[0]
        if brightness >= 90:
            return False  # too bright to be a dark overlay
        edge_density = ImageStat.Stat(top.filter(ImageFilter.FIND_EDGES)).mean[0]
        has_overlay = edge_density > 18
        if has_overlay:
            logger.debug("logo_service: top-text detected (brightness=%.1f edges=%.1f) — skipping title bar", brightness, edge_density)
        return has_overlay
    except Exception:
        return False


def _top_has_text_overlay(img) -> bool:
    """Heuristic: detect if the top ~20% already has a rendered text/title overlay.

    Checks for a dark band (mean brightness < 90) with significant edge activity
    (edge mean > 18), which is characteristic of white text on a dark gradient.
    Returns False on any error so compositing is never blocked.
    """
    try:
        from PIL import ImageFilter, ImageStat
        w, h = img.size
        band_h = max(1, h // 5)
        top = img.crop((0, 0, w, band_h)).convert("L")
        brightness = ImageStat.Stat(top).mean[0]
        if brightness >= 90:
            return False  # too bright to be a dark overlay
        edge_density = ImageStat.Stat(top.filter(ImageFilter.FIND_EDGES)).mean[0]
        has_overlay = edge_density > 18
        if has_overlay:
            logger.debug(
                "logo_service: top-text detected (brightness=%.1f edges=%.1f) — skipping title bar",
                brightness, edge_density,
            )
        return has_overlay
    except Exception:
        return False


def _add_title_bar(img, title: str) -> None:
    try:
        from PIL import Image, ImageDraw

        w, h = img.size
        font_size = max(48, h // 8)   # large H1-scale text
        padding = 24

        font = _load_font(font_size)
        draw_tmp = ImageDraw.Draw(img)

        # Wrap title to fit width
        max_chars = max(10, int((w - padding * 2) / (font_size * 0.55)))
        lines = textwrap.wrap(title, width=max_chars)[:3]

        line_h = int(font_size * 1.3)
        total_h = len(lines) * line_h
        bar_h = total_h + padding * 2

        # Draw gradient overlay (opaque black at top → transparent)
        overlay = Image.new("RGBA", (w, bar_h), (0, 0, 0, 0))
        draw_ov = ImageDraw.Draw(overlay)
        for y in range(bar_h):
            alpha = int(210 * (1 - y / bar_h))
            draw_ov.rectangle([(0, y), (w, y + 1)], fill=(0, 0, 0, alpha))
        img.paste(overlay, (0, 0), overlay)

        draw = ImageDraw.Draw(img)
        y_text = padding

        for line in lines:
            # Measure text width to centre it
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                text_w = bbox[2] - bbox[0]
            except AttributeError:
                text_w = len(line) * font_size * 0.55
            x = max(padding, (w - text_w) // 2)
            draw.text((x, y_text), line, font=font, fill=(255, 255, 255, 255))
            y_text += line_h
    except Exception as exc:
        logger.warning("logo_service: _add_title_bar failed — %s", exc)


def _add_logo_badge(img, logo_bytes: bytes) -> None:
    """Paste the store logo at the bottom-right corner with a white background pill."""
    try:
        from PIL import Image

        w, h = img.size
        padding = max(8, w // 60)
        max_logo_h = max(32, h // 10)

        logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        scale = max_logo_h / logo.height
        new_w = max(1, int(logo.width * scale))
        logo = logo.resize((new_w, max_logo_h), Image.LANCZOS)

        bg_w = new_w + padding * 2
        bg_h = max_logo_h + padding * 2
        bg = Image.new("RGBA", (bg_w, bg_h), (255, 255, 255, 210))

        x = w - bg_w - padding
        y = h - bg_h - padding
        img.paste(bg, (x, y), bg)
        img.paste(logo, (x + padding, y + padding), logo)
    except Exception as exc:
        logger.warning("logo_service: _add_logo_badge failed — %s", exc)


async def stamp_photo(image_url: str, title: str, logo_b64: str = "") -> str:
    """Add title bar + optional logo badge to the photo image.

    Returns a JPEG data URI on success, or the original URL on any failure.
    """
    try:
        from PIL import Image

        raw = await _fetch_bytes(image_url)
        if not raw:
            return image_url

        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        if not _top_has_text_overlay(img):
            _add_title_bar(img, title)
        else:
            logger.info("logo_service: skipping title bar — image already has text overlay")

        if logo_b64:
            logo_bytes = _logo_bytes_from_b64(logo_b64)
            if logo_bytes:
                _add_logo_badge(img, logo_bytes)

        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=90)
        logger.info("logo_service: stamped photo with title bar")
        return _to_data_uri(out.getvalue())
    except ImportError:
        logger.debug("logo_service: Pillow not installed, skipping compositing")
        return image_url
    except Exception as exc:
        logger.warning("logo_service: stamp_photo failed — %s", exc)
        return image_url


async def stamp_infographic(image_url: str, logo_b64: str = "") -> str:
    """Add logo badge to the infographic image.

    Returns a JPEG data URI on success, or the original URL on any failure / no logo.
    """
    if not logo_b64:
        return image_url
    try:
        from PIL import Image

        raw = await _fetch_bytes(image_url)
        if not raw:
            return image_url

        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        logo_bytes = _logo_bytes_from_b64(logo_b64)
        if logo_bytes:
            _add_logo_badge(img, logo_bytes)

        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=90)
        logger.info("logo_service: stamped infographic with logo badge")
        return _to_data_uri(out.getvalue())
    except ImportError:
        logger.debug("logo_service: Pillow not installed, skipping compositing")
        return image_url
    except Exception as exc:
        logger.warning("logo_service: stamp_infographic failed — %s", exc)
        return image_url


def _crop_to_ratio(img, target_w: int, target_h: int):
    """Centre-crop ``img`` to the target aspect ratio, then resize to target size."""
    from PIL import Image

    src_w, src_h = img.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        # Source is wider — crop the sides.
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        box = (left, 0, left + new_w, src_h)
    else:
        # Source is taller — crop top/bottom.
        new_h = int(src_w / target_ratio)
        top = (src_h - new_h) // 2
        box = (0, top, src_w, top + new_h)
    return img.crop(box).resize((target_w, target_h), Image.LANCZOS)


async def stamp_pin(
    image_url: str,
    title: str,
    logo_b64: str = "",
    pin_width: int = 1000,
    pin_height: int = 1500,
) -> str:
    """Build a vertical 2:3 Pinterest pin (default 1000x1500) from an image:
    centre-cropped to ratio, with a title bar + optional logo badge.

    Returns a JPEG data URI on success, or the original URL on any failure.
    """
    try:
        from PIL import Image

        raw = await _fetch_bytes(image_url)
        if not raw:
            return image_url

        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        img = _crop_to_ratio(img, pin_width, pin_height)

        if not _top_has_text_overlay(img):
            _add_title_bar(img, title)

        if logo_b64:
            logo_bytes = _logo_bytes_from_b64(logo_b64)
            if logo_bytes:
                _add_logo_badge(img, logo_bytes)

        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=90)
        logger.info("logo_service: built %dx%d Pinterest pin", pin_width, pin_height)
        return _to_data_uri(out.getvalue())
    except ImportError:
        logger.debug("logo_service: Pillow not installed, skipping pin compositing")
        return image_url
    except Exception as exc:
        logger.warning("logo_service: stamp_pin failed — %s", exc)
        return image_url

