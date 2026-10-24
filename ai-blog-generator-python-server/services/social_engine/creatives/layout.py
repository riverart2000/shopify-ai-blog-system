"""A small declarative layout engine for social creatives.

A spec is ``{"layers": [layer, ...]}``. Every layer has a ``type`` and a ``box``
``[x, y, w, h]`` in RELATIVE canvas coordinates (0–1 of width/height). Sizes
(`size`, `radius`, `gap`, ...) are fractions of the canvas WIDTH, so one spec
renders identically at any resolution.

Common keys:
    when       condition (or list, all must hold): "on_sale", "show_price",
               "has_logo", "has:<text key>", "products>=N", "images>=N",
               "slides>1"; prefix "!" negates.
    product    which product the layer is about (default 0).
    align      left | center | right (also "opposite_logo").
    valign     top | middle | bottom.

Layer types: background, shape, divider, scrim (shape with gradient), image,
grid, text, footer, price, button, coupon, badge, sticker, stars, choices,
logo, botanical, dots, spacer, stack (vertical flow) and row (horizontal flow).

Colour tokens: primary, text, background, cta, accent, secondary, white, ink,
muted, line, surface, tint, "<token>@0.2" (blend toward background),
"auto_on:<token>" (ink or white for WCAG contrast) or "#rrggbb".

Text is bound to ``{placeholders}`` (texts, product fields, brand fields) and is
guaranteed to stay inside its box: it shrinks to `min_size`, then truncates
with an ellipsis.
"""
from __future__ import annotations

import functools
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from .brand import (
    RGB, ResolvedBrand, contrast_ratio, display_website, font_has_glyph, load_font, mix, on_color, readable_on,
    relative_luminance, resolve_color, sanitize_text,
)
from .images import (
    drop_shadow, ellipse_mask, fit_contain, fit_cover, fit_cover_soft, has_plain_background, plain_background_color,
    rounded_mask, trim_white,
)

Box = tuple[int, int, int, int]
ELLIPSIS = "…"
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.]+)\}")
_NUMBER = re.compile(r"[-+]?\d[\d,.\s]*")


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class ProductInfo:
    index: int
    product_id: str
    title: str
    price: str = ""
    compare_at: str = ""
    discount_pct: float = 0.0
    image_urls: list[str] = field(default_factory=list)
    description: str = ""
    collections: list[str] = field(default_factory=list)
    url: str = ""

    @property
    def on_sale(self) -> bool:
        return bool(self.compare_at) and self.compare_at.strip() != self.price.strip()

    @property
    def discount_label(self) -> str:
        return f"-{int(round(self.discount_pct))}%" if self.on_sale and self.discount_pct >= 1 else ""


@dataclass
class LayoutContext:
    width: int
    height: int
    brand: ResolvedBrand
    texts: dict[str, str] = field(default_factory=dict)
    products: list[ProductInfo] = field(default_factory=list)
    images: dict[str, Image.Image] = field(default_factory=dict)
    image_offset: int = 0
    logo: Image.Image | None = None
    options: dict[str, Any] = field(default_factory=dict)
    slide_index: int = 0
    slide_count: int = 1
    report: list[dict] = field(default_factory=list)
    logo_box: Box | None = None
    background: RGB = (247, 244, 236)
    background_image: Image.Image | None = None     # options["background_image"] scene

    def sx(self, rel: float) -> int:
        """Relative size (fraction of canvas width) → pixels."""
        return int(round(float(rel) * self.width))

    def box(self, rel: Iterable[float] | None) -> Box:
        x, y, w, h = list(rel or (0, 0, 1, 1))[:4]
        x0, y0 = round(x * self.width), round(y * self.height)
        return x0, y0, x0 + max(0, round(w * self.width)), y0 + max(0, round(h * self.height))

    def color(self, token: str | None, default: str = "text") -> RGB:
        return resolve_color(token or default, self.brand)

    def product(self, index: int) -> ProductInfo | None:
        if 0 <= index < len(self.products):
            return self.products[index]
        return None

    @property
    def show_price(self) -> bool:
        return bool(self.options.get("show_price", True))

    @property
    def show_logo(self) -> bool:
        return bool(self.options.get("show_logo", True))


def parse_amount(value: str) -> float | None:
    match = _NUMBER.search(value or "")
    if not match:
        return None
    raw = match.group(0).replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        raw = f"{head.replace(',', '')}.{tail}" if len(tail) == 2 else raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def format_money(amount: float, symbol: str) -> str:
    text = f"{amount:,.2f}"
    if text.endswith(".00") and amount >= 100:
        text = text[:-3]
    return f"{symbol}{text}"


def image_url_for(product: ProductInfo | None, image_index: int, offset: int) -> str | None:
    if product is None or not product.image_urls:
        return None
    return product.image_urls[(int(image_index) + int(offset)) % len(product.image_urls)]


def first_sentence(text: str, limit: int = 180) -> str:
    clean = re.sub(r"<[^>]+>", " ", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""
    match = re.match(r"(.+?[.!?])(\s|$)", clean)
    sentence = match.group(1) if match else clean
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rsplit(" ", 1)[0] + ELLIPSIS


def base_values(ctx: LayoutContext) -> dict[str, str]:
    brand = ctx.brand
    texts = {k: str(v) for k, v in ctx.texts.items() if v is not None}
    values: dict[str, str] = {
        "store_name": brand.business_name,
        "website": brand.website,
        "cta": brand.cta_text,
        "slogan": brand.slogan,
        "product_count": str(len(ctx.products)),
        "slide_number": str(ctx.slide_index + 1),
        "slide_count": str(ctx.slide_count),
    }
    values.update({k: v.strip() for k, v in texts.items()})
    if texts.get("website"):
        values["website"] = display_website(texts["website"])
    label = values.get("countdown_label", "")
    match = re.match(r"^\s*(\d{1,3})\s+(.+)$", label)
    values["countdown_number"] = match.group(1) if match else ""
    values["countdown_unit"] = match.group(2) if match else label
    if not values.get("collection"):
        values["collection"] = values.get("collection_title", "") or next(
            (p.collections[0] for p in ctx.products if p.collections), ""
        )
    return values


def values_for(ctx: LayoutContext, product_index: int = 0) -> dict[str, str]:
    values = base_values(ctx)
    for idx, product in enumerate(ctx.products):
        prefix = f"p{idx}."
        values[prefix + "title"] = product.title
        values[prefix + "price"] = product.price
        values[prefix + "compare_at"] = product.compare_at if product.on_sale else ""
        values[prefix + "discount"] = product.discount_label
    product = ctx.product(product_index)
    if product is not None:
        values.setdefault("title", product.title)
        if not ctx.texts.get("title"):
            values["title"] = product.title
        values["price"] = product.price
        values["compare_at"] = product.compare_at if product.on_sale else ""
        values["discount"] = product.discount_label
        values["discount_pct"] = str(int(round(product.discount_pct))) if product.discount_label else ""
        values["description"] = first_sentence(product.description)
        if not values.get("collection") and product.collections:
            values["collection"] = product.collections[0]
    else:
        for key in ("title", "price", "compare_at", "discount", "discount_pct", "description"):
            values.setdefault(key, "")
    if not values.get("offer"):
        values["offer"] = f"{values['discount_pct']}% off" if values.get("discount_pct") else (values.get("headline") or "Sale")
    if not values.get("offer_kicker"):
        values["offer_kicker"] = "Limited-time offer" if values.get("discount_pct") else "Shop now"
    return values


def resolve_text(ctx: LayoutContext, template: str, product_index: int = 0) -> str:
    if not template:
        return ""
    values = values_for(ctx, product_index)

    def repl(match: re.Match) -> str:
        return values.get(match.group(1), "")

    return re.sub(r"\s+", " ", _PLACEHOLDER.sub(repl, str(template))).strip()


def condition_true(ctx: LayoutContext, condition: Any, product_index: int = 0) -> bool:
    if condition in (None, "", True):
        return True
    if isinstance(condition, (list, tuple)):
        return all(condition_true(ctx, item, product_index) for item in condition)
    text = str(condition).strip()
    if text.startswith("!"):
        return not condition_true(ctx, text[1:], product_index)
    product = ctx.product(product_index)
    if text == "on_sale":
        return bool(product and product.on_sale)
    if text == "show_price":
        return ctx.show_price and bool(product and product.price)
    if text == "has_logo":
        return ctx.show_logo and ctx.logo is not None
    if text == "slides>1":
        return ctx.slide_count > 1
    if text.startswith("has:"):
        values = values_for(ctx, product_index)
        return any(values.get(key, "") for key in text[4:].split("|"))
    match = re.match(r"^(products|images)\s*(>=|<=|==|>|<)\s*(\d+)$", text)
    if match:
        kind, op, number = match.group(1), match.group(2), int(match.group(3))
        value = len(ctx.products) if kind == "products" else len(product.image_urls if product else [])
        return {">=": value >= number, "<=": value <= number, "==": value == number,
                ">": value > number, "<": value < number}[op]
    return bool(ctx.options.get(text, False))


# ---------------------------------------------------------------------------
# Low-level drawing helpers
# ---------------------------------------------------------------------------

def composite(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
    """Alpha-composite `layer` at (x, y), clipping to the canvas."""
    x, y = int(round(x)), int(round(y))
    left, top = max(0, -x), max(0, -y)
    right = min(layer.width, canvas.width - x)
    bottom = min(layer.height, canvas.height - y)
    if right <= left or bottom <= top:
        return
    if (left, top, right, bottom) != (0, 0, layer.width, layer.height):
        layer = layer.crop((left, top, right, bottom))
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")
    canvas.alpha_composite(layer, dest=(x + left, y + top))


def fill_mask(canvas: Image.Image, mask: Image.Image, color: RGB, x: int, y: int, opacity: float = 1.0) -> None:
    if opacity < 1.0:
        mask = mask.point(lambda v: int(v * max(0.0, opacity)))
    layer = Image.new("RGBA", mask.size, (*color, 0))
    layer.putalpha(mask)
    composite(canvas, layer, x, y)


def polygon_mask(size: tuple[int, int], polygons: list[list[tuple[float, float]]], *, scale: int = 4) -> Image.Image:
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    big = Image.new("L", (w * scale, h * scale), 0)
    draw = ImageDraw.Draw(big)
    for points in polygons:
        draw.polygon([(px * scale, py * scale) for px, py in points], fill=255)
    return big.resize((w, h), Image.Resampling.LANCZOS)


def star_points(cx: float, cy: float, outer: float, inner_ratio: float = 0.47, points: int = 5) -> list[tuple[float, float]]:
    result = []
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else outer * inner_ratio
        angle = -math.pi / 2 + i * math.pi / points
        result.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return result


def _bezier(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], steps: int = 24) -> list[tuple[float, float]]:
    pts = []
    for i in range(steps + 1):
        t = i / steps
        a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
        pts.append((a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]))
    return pts


def leaf_polygon(base: tuple[float, float], angle: float, length: float, width_ratio: float = 0.36) -> list[tuple[float, float]]:
    """Almond-shaped leaf from `base`, pointing at `angle` (radians)."""
    ca, sa = math.cos(angle), math.sin(angle)

    def to_world(u: float, v: float) -> tuple[float, float]:
        return base[0] + u * ca - v * sa, base[1] + u * sa + v * ca

    half = length * width_ratio
    upper = _bezier((0, 0), (length * 0.45, -half * 1.35), (length, 0))
    lower = _bezier((length, 0), (length * 0.55, half * 1.35), (0, 0))
    return [to_world(u, v) for u, v in upper + lower[1:]]


# ---------------------------------------------------------------------------
# Text fitting
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1024)
def _font_metrics(path: str, size: int, variation: str) -> tuple[int, int, int]:
    font = load_font(path, size, variation)
    ascent = -font.getbbox("Hdlkfb", anchor="ls")[1]
    descent = max(font.getbbox("gjpqy", anchor="ls")[3], int(size * 0.2))
    cap = -font.getbbox("H", anchor="ls")[1]
    return int(ascent), int(descent), int(cap)


@dataclass
class TextBlock:
    lines: list[str]
    font: ImageFont.FreeTypeFont
    size: int
    pitch: float
    ascent: int
    descent: int
    widths: list[float]
    tracking: float
    truncated: bool = False

    @property
    def height(self) -> int:
        if not self.lines:
            return 0
        return int(math.ceil(self.ascent + self.pitch * (len(self.lines) - 1) + self.descent))

    @property
    def width(self) -> int:
        return int(math.ceil(max(self.widths))) if self.widths else 0


def text_width(font: ImageFont.FreeTypeFont, text: str, tracking: float = 0.0) -> float:
    if not text:
        return 0.0
    return font.getlength(text) + tracking * max(0, len(text) - 1)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: float, tracking: float = 0.0) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if text_width(font, candidate, tracking) <= max_w:
                current = candidate
                continue
            if current:
                lines.append(current)
            if text_width(font, word, tracking) <= max_w:
                current = word
                continue
            chunk = ""
            for char in word:
                if text_width(font, chunk + char, tracking) <= max_w or not chunk:
                    chunk += char
                else:
                    lines.append(chunk)
                    chunk = char
            current = chunk
        if current:
            lines.append(current)
    return lines


def _ellipsize(line: str, font: ImageFont.FreeTypeFont, max_w: float, tracking: float) -> str:
    candidate = line.rstrip(" ,;:-–—.") + ELLIPSIS
    while text_width(font, candidate, tracking) > max_w and len(candidate) > 1:
        body = candidate[:-1].rstrip()
        if " " in body:
            body = body.rsplit(" ", 1)[0]
        else:
            body = body[:-1]
        candidate = body.rstrip(" ,;:-–—.") + ELLIPSIS
    return candidate if text_width(font, candidate, tracking) <= max_w else ""


def _block(lines: list[str], path: str, variation: str, size: int, line_height: float, tracking_em: float,
           truncated: bool = False, tight: bool = False) -> TextBlock:
    font = load_font(path, size, variation)
    ascent, descent, _ = _font_metrics(path, size, variation)
    tracking = tracking_em * size
    if lines and tight:
        # Decorative glyphs (e.g. a big quote mark): use the real ink extents only.
        ascent = -font.getbbox(lines[0], anchor="ls")[1]
        descent = font.getbbox(lines[-1], anchor="ls")[3]
    elif lines:
        # Accented capitals reach above the usual ascender: reserve their real height.
        ascent = max(ascent, -font.getbbox(lines[0], anchor="ls")[1])
        descent = max(descent, font.getbbox(lines[-1], anchor="ls")[3])
    widths = [text_width(font, line, tracking) for line in lines]
    return TextBlock(lines, font, size, size * line_height, ascent, descent, widths, tracking, truncated)


def fit_text(
    text: str,
    path: str,
    variation: str,
    max_w: int,
    max_h: int,
    size: int,
    *,
    min_size: int | None = None,
    max_lines: int = 3,
    line_height: float = 1.15,
    tracking_em: float = 0.0,
    tight: bool = False,
) -> TextBlock:
    """Largest block (≤ `size`) that fits `max_w`×`max_h`; ellipsis as a last resort."""
    size = max(6, int(size))
    min_size = max(6, min(size, int(min_size or size * 0.55)))
    max_lines = max(1, int(max_lines))
    if not text or max_w <= 0 or max_h <= 0:
        return _block([], path, variation, size, line_height, tracking_em)
    current = size
    words = text.split()
    while True:
        font = load_font(path, current, variation)
        lines = wrap_text(text, font, max_w, tracking_em * current)
        block = _block(lines, path, variation, current, line_height, tracking_em, tight=tight)
        breaks_word = any(text_width(font, word, tracking_em * current) > max_w for word in words)
        if (len(lines) <= max_lines and block.height <= max_h and block.width <= max_w
                and (not breaks_word or current <= min_size)):
            return block
        if current <= min_size:
            break
        current = max(min_size, int(current * 0.93))
    # Shrinking alone was not enough: keep the lines that fit and add an ellipsis.
    while True:
        font = load_font(path, current, variation)
        tracking = tracking_em * current
        ascent, descent, _ = _font_metrics(path, current, variation)
        pitch = current * line_height
        allowed = max_lines
        while allowed > 1 and ascent + pitch * (allowed - 1) + descent > max_h:
            allowed -= 1
        lines = wrap_text(text, font, max_w, tracking)
        kept = lines[:allowed]
        if len(lines) > allowed and kept:
            kept[-1] = _ellipsize(kept[-1], font, max_w, tracking)
            kept = [line for line in kept if line]
        block = _block(kept, path, variation, current, line_height, tracking_em, truncated=True)
        if (block.height <= max_h and block.width <= max_w) or current <= 6:
            if block.height > max_h or block.width > max_w:
                return _block([], path, variation, current, line_height, tracking_em, truncated=True)
            return block
        current = max(6, int(current * 0.9))


def draw_text_block(
    canvas: Image.Image, block: TextBlock, x: int, y: int, w: int, align: str, color: RGB, opacity: float = 1.0,
) -> Box:
    """Draw a fitted block with its top-left at (x, y) inside width `w`. Returns the bbox."""
    if not block.lines:
        return (x, y, x, y)
    target = canvas
    if opacity < 1.0:
        target = Image.new("RGBA", canvas.size, (*color, 0))
    draw = ImageDraw.Draw(target)
    fill = (*color, 255)
    baseline = y + block.ascent
    min_x, max_x = x + w, x
    for line, line_w in zip(block.lines, block.widths):
        if align == "center":
            lx = x + (w - line_w) / 2
        elif align == "right":
            lx = x + w - line_w
        else:
            lx = x
        if block.tracking:
            cursor = lx
            for char in line:
                draw.text((cursor, baseline), char, font=block.font, fill=fill, anchor="ls")
                cursor += block.font.getlength(char) + block.tracking
        else:
            draw.text((lx, baseline), line, font=block.font, fill=fill, anchor="ls")
        min_x, max_x = min(min_x, lx), max(max_x, lx + line_w)
        baseline += block.pitch
    if opacity < 1.0:
        alpha = target.getchannel("A").point(lambda v: int(v * opacity))
        target.putalpha(alpha)
        canvas.alpha_composite(target)
    return int(math.floor(min_x)), y, int(math.ceil(max_x)), y + block.height


# ---------------------------------------------------------------------------
# Element measuring and drawing
# ---------------------------------------------------------------------------

@dataclass
class Measured:
    w: int
    h: int
    data: Any = None


def _product_index(layer: dict, inherited: int = 0) -> int:
    return int(layer.get("product", inherited))


def _text_style(ctx: LayoutContext, layer: dict) -> tuple[str, str, float]:
    role = layer.get("role", "body")
    path, variation = ctx.brand.font_spec(role, layer.get("weight", ""))
    default_lh = {"heading": 1.06, "italic": 1.12, "label": 1.25}.get(role, 1.32)
    return path, variation, float(layer.get("line_height", default_lh))


def _prepare_text(ctx: LayoutContext, layer: dict, raw: str) -> str:
    path, variation, _ = _text_style(ctx, layer)
    text = sanitize_text(raw, path, variation)
    if layer.get("uppercase"):
        text = text.upper()
    return text


def resolve_with_fallback(ctx: LayoutContext, layer: dict, pidx: int, default: str = "") -> str:
    """Resolve `text`, then each `fallback` (string or list) until one is non-empty."""
    raw = resolve_text(ctx, layer.get("text", default), pidx)
    fallbacks = layer.get("fallback") or []
    for candidate in [fallbacks] if isinstance(fallbacks, str) else fallbacks:
        if raw:
            break
        raw = resolve_text(ctx, candidate, pidx)
    return raw


def measure_text(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    raw = resolve_with_fallback(ctx, layer, pidx)
    text = _prepare_text(ctx, layer, raw)
    path, variation, line_height = _text_style(ctx, layer)
    size = ctx.sx(layer.get("size", 0.032))
    min_size = ctx.sx(layer["min_size"]) if "min_size" in layer else None
    block = fit_text(
        text, path, variation, max_w, max_h, size, min_size=min_size,
        max_lines=int(layer.get("max_lines", 3)), line_height=line_height,
        tracking_em=float(layer.get("tracking", 0.0)), tight=bool(layer.get("tight")),
    )
    return Measured(block.width, block.height, {"block": block, "text": text})


def draw_text(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    block: TextBlock = m.data["block"]
    color = ctx.color(layer.get("color"), "text")
    bbox = draw_text_block(canvas, block, box[0], box[1], box[2] - box[0], align, color, float(layer.get("opacity", 1.0)))
    ctx.report.append({
        "type": "text", "layer": layer.get("_index"), "id": layer.get("id", ""), "text": m.data["text"],
        "lines": list(block.lines), "size": block.size, "truncated": block.truncated,
        "box": list(slot), "bbox": list(bbox), "color": list(color),
    })


def _price_parts(ctx: LayoutContext, layer: dict, pidx: int) -> tuple[str, str]:
    product = ctx.product(pidx)
    if product is None or not ctx.show_price or not product.price:
        return "", ""
    compare = product.compare_at if (product.on_sale and layer.get("show_compare", True)) else ""
    return product.price, compare


def measure_price(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    price, compare = _price_parts(ctx, layer, pidx)
    if not price:
        return Measured(0, 0, None)
    role = layer.get("role", "price")
    path, variation = ctx.brand.font_spec(role, layer.get("weight", ""))
    cpath, cvar = ctx.brand.font_spec("body", "Regular")
    size = ctx.sx(layer.get("size", 0.044))
    scale = float(layer.get("compare_scale", 0.68))
    while True:
        font = load_font(path, size, variation)
        cfont = load_font(cpath, max(6, int(size * scale)), cvar)
        price_w = font.getlength(price)
        gap = size * 0.42 if compare else 0
        compare_w = cfont.getlength(compare) if compare else 0
        ascent, descent, _cap = _font_metrics(path, size, variation)
        total_w = int(math.ceil(price_w + gap + compare_w))
        height = ascent + descent
        if (total_w <= max_w and height <= max_h) or size <= 8:
            break
        size = int(size * 0.92)
    if total_w > max_w or height > max_h:
        return Measured(0, 0, None)
    return Measured(total_w, height, {
        "price": price, "compare": compare, "font": font, "cfont": cfont, "ascent": ascent,
        "price_w": price_w, "gap": gap, "size": size,
    })


def draw_price(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    if not data:
        return
    draw = ImageDraw.Draw(canvas)
    x = box[0]
    baseline = box[1] + data["ascent"]
    color = ctx.color(layer.get("color"), "text")
    draw.text((x, baseline), data["price"], font=data["font"], fill=(*color, 255), anchor="ls")
    if data["compare"]:
        ccolor = ctx.color(layer.get("compare_color"), "muted")
        cx = x + data["price_w"] + data["gap"]
        draw.text((cx, baseline), data["compare"], font=data["cfont"], fill=(*ccolor, 255), anchor="ls")
        cbox = data["cfont"].getbbox(data["compare"], anchor="ls")
        cap = -data["cfont"].getbbox("0", anchor="ls")[1]
        y = baseline - cap * 0.42
        thickness = max(2, int(round(data["size"] * 0.045)))
        draw.line((cx + cbox[0] - 2, y, cx + cbox[2] + 2, y), fill=(*ccolor, 255), width=thickness)
    ctx.report.append({
        "type": "price", "layer": layer.get("_index"), "price": data["price"], "compare_at": data["compare"],
        "box": list(slot), "bbox": [box[0], box[1], box[0] + m.w, box[1] + m.h], "color": list(color),
    })


def _label_font(ctx: LayoutContext, layer: dict, size: int) -> tuple[ImageFont.FreeTypeFont, str, str, int]:
    role = layer.get("role", "button")
    path, variation = ctx.brand.font_spec(role, layer.get("weight", ""))
    cap = _font_metrics(path, size, variation)[2]
    return load_font(path, size, variation), path, variation, cap


def measure_button(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    raw = resolve_text(ctx, layer.get("text", "{cta}"), pidx) or ctx.brand.cta_text
    text = _prepare_text(ctx, {"role": layer.get("role", "button"), "uppercase": layer.get("uppercase", True)}, raw)
    if layer.get("arrow") and text:
        path, variation = ctx.brand.font_spec(layer.get("role", "button"))
        text = f"{text}  →" if font_has_glyph(path, variation, "→") else text
    size = ctx.sx(layer.get("size", 0.026))
    floor = max(10, int(size * float(layer.get("min_scale", 0.7))))
    tracking_em = float(layer.get("tracking", 0.1))
    while True:
        font, path, variation, cap = _label_font(ctx, layer, size)
        tracking = tracking_em * size
        height = int(round(size * float(layer.get("height_em", 2.75))))
        pad = int(round(size * float(layer.get("pad_em", 1.9))))
        width = int(math.ceil(text_width(font, text, tracking))) + pad * 2
        if layer.get("stretch"):
            width = max_w
        width = max(width, ctx.sx(layer.get("min_w", 0)))
        if (width <= max_w and height <= max_h) or size <= floor:
            break
        size = max(floor, int(size * 0.92))
    if width > max_w or height > max_h:
        return Measured(0, 0, None)  # never draw a crushed, illegible button
    return Measured(width, height, {"text": text, "font": font, "tracking": tracking, "cap": cap, "size": size})


def _draw_label_centered(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, tracking: float,
                         cx: float, cy: float, cap: int, color: RGB) -> None:
    width = text_width(font, text, tracking)
    x = cx - width / 2
    baseline = cy + cap / 2
    if tracking:
        for char in text:
            draw.text((x, baseline), char, font=font, fill=(*color, 255), anchor="ls")
            x += font.getlength(char) + tracking
    else:
        draw.text((x, baseline), text, font=font, fill=(*color, 255), anchor="ls")


def draw_button(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    if not data:
        return
    x0, y0 = box[0], box[1]
    w, h = m.w, m.h
    radius = h / 2 if layer.get("radius") is None else ctx.sx(layer["radius"])
    fill_token = layer.get("fill", "cta")
    outline = layer.get("outline")
    if layer.get("shadow"):
        shadow, pad = drop_shadow((w, h), radius=radius, blur=ctx.sx(0.012), opacity=0.16, color=ctx.color("ink"))
        composite(canvas, shadow, x0 - pad, y0 - pad + ctx.sx(0.006))
    if fill_token and fill_token != "none":
        fill_mask(canvas, rounded_mask((w, h), radius), ctx.color(fill_token), x0, y0)
        text_color = ctx.color(layer.get("color", f"auto_on:{fill_token}"))
    else:
        text_color = ctx.color(layer.get("color", "text"))
    if outline:
        stroke = max(2, ctx.sx(layer.get("stroke", 0.0022)))
        outer = rounded_mask((w, h), radius)
        inner = Image.new("L", (w, h), 0)
        inner.paste(rounded_mask((w - stroke * 2, h - stroke * 2), max(0, radius - stroke)), (stroke, stroke))
        ring = Image.composite(Image.new("L", (w, h), 0), outer, inner)
        fill_mask(canvas, ring, ctx.color(outline), x0, y0)
    draw = ImageDraw.Draw(canvas)
    _draw_label_centered(draw, data["text"], data["font"], data["tracking"], x0 + w / 2, y0 + h / 2, data["cap"], text_color)
    ctx.report.append({
        "type": "button", "layer": layer.get("_index"), "text": data["text"],
        "box": list(slot), "bbox": [x0, y0, x0 + w, y0 + h],
    })


def measure_coupon(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    code = resolve_text(ctx, layer.get("text", "{coupon_code}"), pidx)
    if not code:
        return Measured(0, 0, None)
    label_raw = resolve_text(ctx, layer.get("label", "Use code"), pidx)
    size = ctx.sx(layer.get("size", 0.03))
    coupon_floor = max(9, int(size * 0.7))
    while True:
        code_font, _, _, cap = _label_font(ctx, {"role": "label", "weight": "Bold"}, size)
        label_size = max(8, int(size * 0.62))
        label_font, _, _, label_cap = _label_font(ctx, {"role": "label", "weight": "Medium"}, label_size)
        code_text = _prepare_text(ctx, {"role": "label", "uppercase": True}, code)
        label_text = _prepare_text(ctx, {"role": "label", "uppercase": True}, label_raw)
        code_tr, label_tr = size * 0.12, label_size * 0.14
        pad = int(size * 1.1)
        gap = int(size * 0.7) if label_text else 0
        width = int(text_width(label_font, label_text, label_tr) + gap + text_width(code_font, code_text, code_tr)) + pad * 2
        height = int(size * 2.5)
        if (width <= max_w and height <= max_h) or size <= coupon_floor:
            break
        size = max(coupon_floor, int(size * 0.92))
    if width > max_w or height > max_h:
        return Measured(0, 0, None)
    return Measured(width, height, {
        "code": code_text, "label": label_text, "code_font": code_font, "label_font": label_font,
        "code_tr": code_tr, "label_tr": label_tr, "cap": cap, "label_cap": label_cap, "pad": pad, "gap": gap,
        "size": size,
    })


def draw_coupon(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    if not data:
        return
    x0, y0, w, h = box[0], box[1], m.w, m.h
    radius = ctx.sx(layer.get("radius", 0.012))
    fill = layer.get("fill", "surface")
    surface: RGB | None = None
    if fill and fill != "none":
        surface = ctx.color(fill)
        fill_mask(canvas, rounded_mask((w, h), radius), surface, x0, y0)
    border = ctx.color(layer.get("border", "primary"))
    scale = 3
    big = Image.new("L", (w * scale, h * scale), 0)
    bdraw = ImageDraw.Draw(big)
    stroke = max(2, int(data["size"] * 0.07)) * scale
    dash, gap = data["size"] * 0.5 * scale, data["size"] * 0.32 * scale
    r = radius * scale
    inset = stroke / 2
    # Dashed rounded rectangle: straight dashed edges + solid corner arcs.
    edges = [
        ((r, inset), (w * scale - r, inset)), ((r, h * scale - inset), (w * scale - r, h * scale - inset)),
        ((inset, r), (inset, h * scale - r)), ((w * scale - inset, r), (w * scale - inset, h * scale - r)),
    ]
    for (ax, ay), (bx, by) in edges:
        length = math.hypot(bx - ax, by - ay)
        steps = max(1, int(length // (dash + gap)))
        spare = (length - steps * (dash + gap) + gap) / 2
        for i in range(steps):
            t0 = (spare + i * (dash + gap)) / length
            t1 = min(1.0, (spare + i * (dash + gap) + dash) / length)
            bdraw.line((ax + (bx - ax) * t0, ay + (by - ay) * t0, ax + (bx - ax) * t1, ay + (by - ay) * t1),
                       fill=255, width=int(stroke))
    for cx, cy, start in ((r, r, 180), (w * scale - r, r, 270), (w * scale - r, h * scale - r, 0), (r, h * scale - r, 90)):
        bdraw.arc((cx - r + inset, cy - r + inset, cx + r - inset, cy + r - inset), start, start + 90, fill=255, width=int(stroke))
    fill_mask(canvas, big.resize((w, h), Image.Resampling.LANCZOS), border, x0, y0)
    draw = ImageDraw.Draw(canvas)
    baseline = y0 + h / 2 + data["cap"] / 2
    x = x0 + data["pad"]
    label_color = ctx.color(layer.get("label_color", "muted"))
    code_color = ctx.color(layer.get("color", "text"))
    if surface is not None:
        if contrast_ratio(code_color, surface) < 4.5:
            code_color = on_color(surface, ctx.brand.colors)
        if contrast_ratio(label_color, surface) < 3.0:
            label_color = mix(code_color, surface, 0.35)
    for char in data["label"]:
        draw.text((x, baseline), char, font=data["label_font"], fill=(*label_color, 255), anchor="ls")
        x += data["label_font"].getlength(char) + data["label_tr"]
    if data["label"]:
        x += data["gap"] - data["label_tr"]
    for char in data["code"]:
        draw.text((x, baseline), char, font=data["code_font"], fill=(*code_color, 255), anchor="ls")
        x += data["code_font"].getlength(char) + data["code_tr"]
    ctx.report.append({"type": "coupon", "layer": layer.get("_index"), "text": data["code"],
                       "box": list(slot), "bbox": [x0, y0, x0 + w, y0 + h]})


def measure_stars(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    count = int(layer.get("count", 5))
    size = ctx.sx(layer.get("size", 0.04))
    gap = int(size * float(layer.get("gap_em", 0.22)))
    while size > 6 and (count * size + (count - 1) * gap > max_w or size > max_h):
        size = int(size * 0.9)
        gap = int(size * float(layer.get("gap_em", 0.22)))
    return Measured(count * size + (count - 1) * gap, size, {"size": size, "gap": gap, "count": count})


def draw_stars(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    size, gap, count = data["size"], data["gap"], data["count"]
    raw = resolve_text(ctx, str(layer.get("rating", "{rating}")), 0)
    try:
        rating = float(raw) if raw else float(layer.get("default_rating", 5))
    except ValueError:
        rating = 5.0
    rating = max(0.0, min(float(count), rating))
    full = polygon_mask((m.w, m.h), [star_points(i * (size + gap) + size / 2, size / 2 + size * 0.04, size / 2) for i in range(count)])
    empty_color = ctx.color(layer.get("empty_color", "line"))
    fill_mask(canvas, full, empty_color, box[0], box[1])
    filled_w = 0.0
    for i in range(count):
        part = max(0.0, min(1.0, rating - i))
        if part > 0:
            filled_w = i * (size + gap) + size * part
    if filled_w > 0:
        clip = Image.new("L", full.size, 0)
        clip.paste(full.crop((0, 0, int(round(filled_w)), m.h)), (0, 0))
        fill_mask(canvas, clip, ctx.color(layer.get("color", "cta")), box[0], box[1])


def measure_choices(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    keys = layer.get("options", ["option_a", "option_b", "option_c", "option_d"])
    values = values_for(ctx, pidx)
    items = [(chr(ord("A") + i), values.get(key, "")) for i, key in enumerate(keys)]
    items = [(letter, text) for letter, text in items if text]
    if not items:
        return Measured(0, 0, None)
    size = ctx.sx(layer.get("size", 0.034))
    gap = ctx.sx(layer.get("gap", 0.022))
    direction = layer.get("direction", "column")
    path, variation = ctx.brand.font_spec("body", "Medium")
    while True:
        row_h = int(size * 2.9)
        circle = int(row_h * 0.62)
        pad = int((row_h - circle) / 2)
        if direction == "row":
            cell_w = int((max_w - gap * (len(items) - 1)) / len(items))
        else:
            cell_w = max_w
        text_w = cell_w - pad * 2 - circle - int(size * 0.7)
        blocks = [fit_text(_prepare_text(ctx, {"role": "body"}, text), path, variation, text_w, int(row_h * 0.86),
                           size, min_size=int(size * 0.7), max_lines=2, line_height=1.12) for _, text in items]
        heights = [max(row_h, b.height + pad * 2) for b in blocks]
        total = (max(heights) if direction == "row" else sum(heights) + gap * (len(items) - 1))
        if total <= max_h or size <= 10:
            break
        size = int(size * 0.92)
        gap = int(gap * 0.9)
    return Measured(max_w, min(total, max_h), {
        "items": items, "blocks": blocks, "heights": heights, "circle": circle, "pad": pad, "gap": gap,
        "size": size, "cell_w": cell_w, "direction": direction,
    })


def draw_choices(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    if not data:
        return
    fill = ctx.color(layer.get("fill", "surface"))
    border = ctx.color(layer.get("border", "line"))
    letter_fill = ctx.color(layer.get("letter_fill", "primary"))
    letter_color = readable_on(letter_fill, ink=ctx.color("ink"))
    text_color = ctx.color(layer.get("color", "text"))
    letter_font, _, _, cap = _label_font(ctx, {"role": "label", "weight": "SemiBold"}, int(data["size"] * 0.82))
    x, y = box[0], box[1]
    for (letter, _), block, height in zip(data["items"], data["blocks"], data["heights"]):
        w = data["cell_w"]
        radius = height / 2 if height < data["size"] * 3.6 else data["size"] * 0.9
        fill_mask(canvas, rounded_mask((w, height), radius), border, x, y)
        stroke = max(2, int(data["size"] * 0.06))
        fill_mask(canvas, rounded_mask((w - stroke * 2, height - stroke * 2), max(1, radius - stroke)), fill, x + stroke, y + stroke)
        circle = data["circle"]
        cx0 = x + data["pad"]
        cy0 = y + (height - circle) // 2
        fill_mask(canvas, ellipse_mask((circle, circle)), letter_fill, cx0, cy0)
        _draw_label_centered(ImageDraw.Draw(canvas), letter, letter_font, 0, cx0 + circle / 2, cy0 + circle / 2, cap, letter_color)
        tx = cx0 + circle + int(data["size"] * 0.7)
        ty = y + (height - block.height) // 2
        bbox = draw_text_block(canvas, block, tx, ty, w - (tx - x) - data["pad"], "left", text_color)
        ctx.report.append({"type": "text", "layer": layer.get("_index"), "text": " ".join(block.lines),
                           "lines": list(block.lines), "size": block.size, "truncated": block.truncated,
                           "box": [x, y, x + w, y + height], "bbox": list(bbox), "color": list(text_color)})
        if data["direction"] == "row":
            x += w + data["gap"]
        else:
            y += height + data["gap"]


def measure_badge(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    raw = resolve_with_fallback(ctx, layer, pidx, "{badge}")
    if not raw:
        return Measured(0, 0, None)
    shape = layer.get("shape", "pill")
    upper = layer.get("uppercase", True)
    role = layer.get("role", "label")
    text = _prepare_text(ctx, {"role": role, "uppercase": upper}, raw)
    if shape == "circle":
        diameter = min(max_w, max_h, ctx.sx(layer["diameter"]) if "diameter" in layer else min(max_w, max_h))
        return Measured(diameter, diameter, {"text": text, "shape": shape})
    if shape == "ribbon":
        height = min(max_h, ctx.sx(layer.get("height", 0.075)))
        return Measured(max_w, height, {"text": text, "shape": shape})
    size = ctx.sx(layer.get("size", 0.022))
    tracking_em = float(layer.get("tracking", 0.14))
    while True:
        font, _path, _variation, cap = _label_font(ctx, {"role": role, "weight": layer.get("weight", "SemiBold")}, size)
        tracking = tracking_em * size
        height = int(size * float(layer.get("height_em", 2.3)))
        width = int(text_width(font, text, tracking)) + int(size * float(layer.get("pad_em", 1.25))) * 2
        if (width <= max_w and height <= max_h) or size <= 8:
            break
        size = int(size * 0.9)
    if width > max_w or height > max_h:
        return Measured(0, 0, None)
    return Measured(width, height, {"text": text, "shape": shape, "font": font, "tracking": tracking, "cap": cap})


def draw_badge(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    if not data:
        return
    fill_token = layer.get("fill", "cta")
    fill = ctx.color(fill_token)
    color = ctx.color(layer.get("color", f"auto_on:{fill_token}"))
    x0, y0, w, h = box[0], box[1], m.w, m.h
    shape = data["shape"]
    if shape == "circle":
        if layer.get("shadow", True):
            shadow, pad = drop_shadow((w, h), blur=ctx.sx(0.01), opacity=0.18, shape="ellipse", color=ctx.color("ink"))
            composite(canvas, shadow, x0 - pad, y0 - pad + ctx.sx(0.005))
        fill_mask(canvas, ellipse_mask((w, h)), fill, x0, y0)
        ring_inset = int(w * 0.06)
        if layer.get("ring", True) and w > 60:
            ring = ellipse_mask((w - ring_inset * 2, w - ring_inset * 2))
            inner = Image.new("L", ring.size, 0)
            stroke = max(1, int(w * 0.008))
            inner.paste(ellipse_mask((ring.width - stroke * 2, ring.height - stroke * 2)), (stroke, stroke))
            ring = Image.composite(Image.new("L", ring.size, 0), ring, inner)
            fill_mask(canvas, ring, color, x0 + ring_inset, y0 + ring_inset, opacity=0.55)
        inner_w = int(w * 0.72)
        role = layer.get("role", "heading")
        path, variation = ctx.brand.font_spec(role, layer.get("weight", ""))
        block = fit_text(data["text"], path, variation, inner_w, int(h * 0.56), int(h * float(layer.get("text_scale", 0.36))),
                         min_size=int(h * 0.12), max_lines=2, line_height=0.98,
                         tracking_em=float(layer.get("tracking", 0.02 if role != "heading" else 0)))
        ty = y0 + (h - block.height) // 2
        bbox = draw_text_block(canvas, block, x0 + (w - inner_w) // 2, ty, inner_w, "center", color)
    elif shape == "ribbon":
        notch = int(h * 0.42)
        mask = polygon_mask((w, h), [[(0, 0), (w, 0), (w - notch, h / 2), (w, h), (0, h)]])
        if layer.get("flip"):
            mask = mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        fill_mask(canvas, mask, fill, x0, y0)
        size = int(h * 0.34)
        font, path, variation, cap = _label_font(ctx, {"role": "label", "weight": "SemiBold"}, size)
        tracking = size * float(layer.get("tracking", 0.16))
        avail = w - notch - int(h * 0.6)
        while size > 8 and text_width(font, data["text"], tracking) > avail:
            size = int(size * 0.92)
            font, path, variation, cap = _label_font(ctx, {"role": "label", "weight": "SemiBold"}, size)
            tracking = size * float(layer.get("tracking", 0.16))
        text_left = x0 + (notch if layer.get("flip") else 0)
        _draw_label_centered(ImageDraw.Draw(canvas), data["text"], font, tracking,
                             text_left + (w - notch) / 2, y0 + h / 2, cap, color)
        bbox = (x0, y0, x0 + w, y0 + h)
    else:
        radius = h / 2 if layer.get("radius") is None else ctx.sx(layer["radius"])
        fill_mask(canvas, rounded_mask((w, h), radius), fill, x0, y0)
        _draw_label_centered(ImageDraw.Draw(canvas), data["text"], data["font"], data["tracking"],
                             x0 + w / 2, y0 + h / 2, data["cap"], color)
        bbox = (x0, y0, x0 + w, y0 + h)
    ctx.report.append({"type": "badge", "layer": layer.get("_index"), "text": data["text"],
                       "box": list(slot), "bbox": list(bbox)})


STICKER_STYLES = {"buy now": "tag", "big sale": "burst", "best offer": "seal", "deal of the day": "banner"}


def measure_sticker(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    raw = resolve_with_fallback(ctx, layer, pidx, "{sticker}")
    if not raw:
        return Measured(0, 0, None)
    style = layer.get("style") or STICKER_STYLES.get(raw.strip().lower(), "seal")
    if style == "banner":
        width = min(max_w, int(max_h / 0.3))
        return Measured(width, int(width * 0.3), {"text": raw, "style": style})
    if style == "tag":
        width = min(max_w, int(max_h / 0.42))
        return Measured(width, int(width * 0.42), {"text": raw, "style": style})
    side = min(max_w, max_h)
    return Measured(side, side, {"text": raw, "style": style})


def draw_sticker(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    if not data:
        return
    w, h = m.w, m.h
    style = data["style"]
    fill_token = layer.get("fill", "cta")
    fill = ctx.color(fill_token)
    color = ctx.color(layer.get("color", f"auto_on:{fill_token}"))
    layer_img = Image.new("RGBA", (w, h), (*fill, 0))
    text = _prepare_text(ctx, {"role": "label", "uppercase": True}, data["text"])
    path, variation = ctx.brand.font_spec("label", "Bold")
    if style == "burst":
        spikes = 18
        pts = star_points(w / 2, h / 2, w / 2 * 0.98, inner_ratio=0.84, points=spikes)
        mask = polygon_mask((w, h), [pts])
        fill_mask(layer_img, mask, fill, 0, 0)
        inner_w, inner_h = int(w * 0.62), int(h * 0.5)
    elif style == "seal":
        fill_mask(layer_img, ellipse_mask((w, h)), fill, 0, 0)
        inset = int(w * 0.07)
        ring = ellipse_mask((w - inset * 2, h - inset * 2))
        stroke = max(1, int(w * 0.012))
        inner = Image.new("L", ring.size, 0)
        inner.paste(ellipse_mask((ring.width - stroke * 2, ring.height - stroke * 2)), (stroke, stroke))
        ring = Image.composite(Image.new("L", ring.size, 0), ring, inner)
        fill_mask(layer_img, ring, color, inset, inset, opacity=0.6)
        inner_w, inner_h = int(w * 0.66), int(h * 0.5)
    elif style == "tag":
        notch = int(h * 0.45)
        mask = polygon_mask((w, h), [[(notch, 0), (w, 0), (w, h), (notch, h), (0, h / 2)]])
        fill_mask(layer_img, mask, fill, 0, 0)
        hole = int(h * 0.16)
        punch = Image.new("L", (w, h), 0)
        punch.paste(ellipse_mask((hole, hole)), (int(notch * 0.62), (h - hole) // 2))
        alpha = layer_img.getchannel("A")
        layer_img.putalpha(Image.composite(Image.new("L", (w, h), 0), alpha, punch))
        inner_w, inner_h = int(w - notch - h * 0.35), int(h * 0.56)
        inner_offset = notch // 2
    else:  # banner (ribbon with folded ends)
        tail = int(h * 0.36)
        body = [(tail, 0), (w - tail, 0), (w - tail, h * 0.78), (tail, h * 0.78)]
        left = [(0, h * 0.22), (tail * 1.4, h * 0.22), (tail * 1.4, h), (0, h), (tail * 0.55, h * 0.61)]
        right = [(w, h * 0.22), (w - tail * 1.4, h * 0.22), (w - tail * 1.4, h), (w, h), (w - tail * 0.55, h * 0.61)]
        darker = mix(fill, (0, 0, 0), 0.25)
        fill_mask(layer_img, polygon_mask((w, h), [left, right]), darker, 0, 0)
        fill_mask(layer_img, polygon_mask((w, h), [body]), fill, 0, 0)
        inner_w, inner_h = int(w - tail * 2 - h * 0.3), int(h * 0.5)
    block = fit_text(text, path, variation, inner_w, inner_h, int(h * (0.2 if style in {"burst", "seal"} else 0.3)),
                     min_size=8, max_lines=2 if style in {"burst", "seal"} else 1, line_height=1.0, tracking_em=0.06)
    if style == "banner":
        ty = int(h * 0.39 - block.height / 2)
        tx = (w - inner_w) // 2
    elif style == "tag":
        ty = (h - block.height) // 2
        tx = (w - inner_w) // 2 + inner_offset
    else:
        ty = (h - block.height) // 2
        tx = (w - inner_w) // 2
    draw_text_block(layer_img, block, tx, ty, inner_w, "center", color)
    rotation = float(layer.get("rotation", -8 if style in {"burst", "seal", "tag"} else 0))
    if rotation:
        rotated = layer_img.rotate(rotation, resample=Image.Resampling.BICUBIC, expand=True)
        dx, dy = (rotated.width - w) // 2, (rotated.height - h) // 2
        if layer.get("shadow", True):
            alpha = rotated.getchannel("A").filter(ImageFilter.GaussianBlur(ctx.sx(0.008)))
            shadow = Image.new("RGBA", rotated.size, (*ctx.color("ink"), 0))
            shadow.putalpha(alpha.point(lambda v: int(v * 0.2)))
            composite(canvas, shadow, box[0] - dx, box[1] - dy + ctx.sx(0.005))
        composite(canvas, rotated, box[0] - dx, box[1] - dy)
    else:
        composite(canvas, layer_img, box[0], box[1])
    ctx.report.append({"type": "sticker", "layer": layer.get("_index"), "text": text,
                       "box": list(slot), "bbox": [box[0], box[1], box[0] + w, box[1] + h]})


def measure_divider(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    length = min(max_w, ctx.sx(layer["length"])) if "length" in layer else max_w
    thickness = max(1, ctx.sx(layer.get("thickness", 0.0018)))
    return Measured(length, min(max_h, thickness), {"thickness": thickness})


def draw_divider(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    color = ctx.color(layer.get("color", "line"))
    overlay = Image.new("RGBA", (m.w, max(1, m.h)), (*color, int(255 * float(layer.get("opacity", 1.0)))))
    composite(canvas, overlay, box[0], box[1])


def measure_dots(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    count = ctx.slide_count
    if count <= 1:
        return Measured(0, 0, None)
    size = ctx.sx(layer.get("size", 0.011))
    gap = int(size * 1.2)
    return Measured(min(max_w, count * size + (count - 1) * gap + size * 2), min(max_h, size), {"size": size, "gap": gap})


def draw_dots(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    if not data:
        return
    size, gap = data["size"], data["gap"]
    x = box[0]
    active = ctx.color(layer.get("color", "primary"))
    idle = ctx.color(layer.get("idle_color", "line"))
    for i in range(ctx.slide_count):
        if i == ctx.slide_index:
            width = size * 3
            fill_mask(canvas, rounded_mask((width, size), size / 2), active, x, box[1])
            x += width + gap
        else:
            fill_mask(canvas, ellipse_mask((size, size)), idle, x, box[1])
            x += size + gap


def measure_thumb(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    url = _image_source(ctx, layer, pidx)
    img = _get_image(ctx, url)
    if img is None:
        return Measured(0, 0, None)
    w, h = min(max_w, ctx.sx(layer.get("w", 0.1))), min(max_h, ctx.sx(layer.get("h", layer.get("w", 0.1))))
    return Measured(w, h, {"img": img, "url": url, "pidx": pidx})


def draw_thumb(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    if not data:
        return
    paste_product_image(ctx, canvas, data["img"], box, fit=layer.get("fit", "auto"), radius=ctx.sx(layer.get("radius", 0.01)),
                        padding=float(layer.get("padding", 0.06)), card=layer.get("card", "auto"),
                        shadow=bool(layer.get("shadow", False)))
    ctx.report.append({"type": "image", "layer": layer.get("_index"), "url": data["url"], "product": data["pidx"],
                       "box": list(box), "bbox": list(box)})


def measure_spacer(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    return Measured(0, min(max_h, ctx.sx(layer.get("h", 0.02))), None)


def _noop_draw(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    return None


# -- flow containers --------------------------------------------------------

def _visible(ctx: LayoutContext, layer: dict, pidx: int) -> bool:
    return condition_true(ctx, layer.get("when"), pidx)


def measure_stack(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    children = [c for c in layer.get("children", []) if _visible(ctx, c, _product_index(c, pidx))]
    default_gap = ctx.sx(layer.get("gap", 0.02))
    gaps = [ctx.sx(c["gap_before"]) if "gap_before" in c else default_gap for c in children]
    if gaps:
        gaps[0] = 0

    def run(limits: list[int]) -> list[tuple[dict, Measured]]:
        out = []
        for child, limit in zip(children, limits):
            child_pidx = _product_index(child, pidx)
            child_w = min(max_w, ctx.sx(child["w"])) if "w" in child else max_w
            out.append((child, measure(ctx, child, child_w, max(0, limit), child_pidx)))
        return out

    limits = [min(max_h, round(c["h"] * ctx.height)) if "h" in c else max_h for c in children]
    measured = run(limits)

    def total_of(items: list[tuple[dict, Measured]]) -> int:
        return sum(m.h for _, m in items) + sum(g for g, (_, m) in zip(gaps, items) if m.h)

    total = total_of(measured)
    # 1) Drop optional children (last first) before shrinking anything.
    while total > max_h:
        optional = [i for i, (c, m) in enumerate(measured) if c.get("optional") and m.h]
        if not optional:
            break
        idx = optional[-1]
        measured[idx] = (measured[idx][0], Measured(0, 0, None))
        limits[idx] = 0
        total = total_of(measured)
    # 2) Shrink flexible text in proportion to its natural height.
    if total > max_h:
        flexible = [i for i, (c, m) in enumerate(measured) if c.get("type") in {"text", "footer", "choices"} and m.h]
        fixed = sum(m.h for i, (_, m) in enumerate(measured) if i not in flexible)
        gap_total = sum(g for g, (_, m) in zip(gaps, measured) if m.h)
        room = max(0, max_h - fixed - gap_total)
        flex_total = sum(measured[i][1].h for i in flexible) or 1
        for i in flexible:
            limits[i] = int(room * measured[i][1].h / flex_total)
        dropped = {i for i, (_, m) in enumerate(measured) if m.h == 0}
        measured = run(limits)
        measured = [(c, Measured(0, 0, None)) if i in dropped else (c, m) for i, (c, m) in enumerate(measured)]
        total = total_of(measured)
    width = max((m.w for _, m in measured), default=0)
    return Measured(width, min(total, max_h) if total <= max_h else total, {"items": measured, "gaps": gaps})


def draw_stack(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    y = box[1]
    x0, x1 = slot[0], slot[2]
    first = True
    for (child, cm), gap in zip(m.data["items"], m.data["gaps"]):
        if cm.h <= 0 and child.get("type") != "spacer":
            continue
        if not first:
            y += gap
        first = False
        child_align = _resolve_align(ctx, child, child.get("align", align), (x0, y, x1, y + cm.h))
        child_w = min(x1 - x0, ctx.sx(child["w"])) if "w" in child else x1 - x0
        if child.get("type") in {"text", "footer"}:
            cx = _align_x(x0, x1, child_w, child_align)
            draw_element(ctx, canvas, child, (cx, y, cx + child_w, y + cm.h), cm, child_align, (cx, y, cx + child_w, y + cm.h))
        else:
            cx = _align_x(x0, x1, cm.w, child_align)
            draw_element(ctx, canvas, child, (cx, y, cx + cm.w, y + cm.h), cm, child_align, (x0, y, x1, y + cm.h))
        y += cm.h


ROW_PRIORITY = {"button": 3, "price": 2, "coupon": 1}


def measure_row(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int) -> Measured:
    """Horizontal flow. Children are measured by priority (button, price, coupon...);
    whatever does not fit on the first line wraps onto a second line."""
    children = [c for c in layer.get("children", []) if _visible(ctx, c, _product_index(c, pidx))]
    gap = ctx.sx(layer.get("gap", 0.025))
    order = sorted(range(len(children)), key=lambda i: -int(children[i].get("priority", ROW_PRIORITY.get(children[i].get("type", ""), 0))))
    natural: dict[int, Measured] = {}
    first_line: list[int] = []
    overflow: list[int] = []
    remaining = max_w
    for i in order:
        child = children[i]
        child_w = min(max_w, ctx.sx(child["w"])) if "w" in child else max_w
        m = measure(ctx, child, child_w, max_h, _product_index(child, pidx))
        if not m.w or not m.h:
            continue
        needed = m.w + (gap if first_line else 0)
        if needed <= remaining and not overflow:
            natural[i] = m
            first_line.append(i)
            remaining -= needed
        else:
            natural[i] = m
            overflow.append(i)
    lines = [sorted(first_line)]
    if overflow and layer.get("wrap", True):
        lines.append(sorted(overflow))
        lines.sort(key=lambda line: min(line) if line else 99)  # e.g. price stays above the button
    line_gap = ctx.sx(layer.get("line_gap", 0.018))
    heights = [max((natural[i].h for i in line), default=0) for line in lines if line]
    widths = [sum(natural[i].w for i in line) + gap * max(0, len(line) - 1) for line in lines if line]
    height = sum(heights) + line_gap * max(0, len(heights) - 1)
    if height > max_h and len(heights) > 1:
        lines, heights, widths = lines[:1], heights[:1], widths[:1]
        height = heights[0]
    width = max(widths, default=0)
    if layer.get("justify", "start") == "between" and lines and len(lines[0]) > 1:
        width = max_w
    items = [[(children[i], natural[i]) for i in line] for line in lines if line]
    return Measured(width, height, {"lines": items, "heights": heights, "gap": gap, "line_gap": line_gap})


def draw_row(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    data = m.data
    if not data or not data["lines"]:
        return
    justify = layer.get("justify", "start")
    y = box[1]
    for number, (items, line_h) in enumerate(zip(data["lines"], data["heights"])):
        gap = data["gap"]
        content_w = sum(cm.w for _, cm in items) + gap * (len(items) - 1)
        x: float = box[0]
        if justify == "between" and len(items) > 1 and number == 0:
            gap = (m.w - sum(cm.w for _, cm in items)) / (len(items) - 1)
        elif justify == "center" or (justify == "between" and align == "center"):
            x = box[0] + (m.w - content_w) / 2
        for child, cm in items:
            cy = y + (line_h - cm.h) // 2
            rect = (int(x), cy, int(x) + cm.w, cy + cm.h)
            draw_element(ctx, canvas, child, rect, cm, "left", rect)
            x += cm.w + gap
        y += line_h + data["line_gap"]


MEASURERS: dict[str, Callable[..., Measured]] = {
    "text": measure_text, "footer": measure_text, "price": measure_price, "button": measure_button,
    "coupon": measure_coupon, "stars": measure_stars, "choices": measure_choices, "badge": measure_badge,
    "sticker": measure_sticker, "divider": measure_divider, "dots": measure_dots, "spacer": measure_spacer,
    "stack": measure_stack, "row": measure_row, "thumb": measure_thumb,
}
DRAWERS: dict[str, Callable[..., None]] = {
    "text": draw_text, "footer": draw_text, "price": draw_price, "button": draw_button, "coupon": draw_coupon,
    "stars": draw_stars, "choices": draw_choices, "badge": draw_badge, "sticker": draw_sticker,
    "divider": draw_divider, "dots": draw_dots, "spacer": _noop_draw, "stack": draw_stack, "row": draw_row,
    "thumb": draw_thumb,
}

FOOTER_DEFAULTS = {"text": "{website}", "role": "body", "size": 0.021, "color": "muted", "max_lines": 1,
                   "align": "center", "tracking": 0.04}


def _with_defaults(layer: dict) -> dict:
    if layer.get("type") == "footer":
        merged = dict(FOOTER_DEFAULTS)
        merged.update(layer)
        return merged
    return layer


def measure(ctx: LayoutContext, layer: dict, max_w: int, max_h: int, pidx: int = 0) -> Measured:
    layer = _with_defaults(layer)
    func = MEASURERS.get(layer.get("type", ""))
    if func is None or max_w <= 0 or max_h <= 0:
        return Measured(0, 0, None)
    return func(ctx, layer, max_w, max_h, pidx)


def draw_element(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, m: Measured, align: str, slot: Box) -> None:
    layer = _with_defaults(layer)
    func = DRAWERS.get(layer.get("type", ""))
    if func is not None and m.h > 0:
        func(ctx, canvas, layer, box, m, align, slot)


def _align_x(x0: int, x1: int, w: int, align: str) -> int:
    if align == "center":
        return x0 + (x1 - x0 - w) // 2
    if align == "right":
        return x1 - w
    return x0


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _resolve_align(ctx: LayoutContext, layer: dict, align: str, box: Box) -> str:
    if align != "opposite_logo":
        return align or "left"
    if ctx.logo_box and _overlaps(ctx.logo_box, (0, box[1], ctx.width, box[3])):
        return "right" if (ctx.logo_box[0] + ctx.logo_box[2]) / 2 < ctx.width / 2 else "left"
    return layer.get("align_default", "center")


def _avoid_logo(ctx: LayoutContext, layer: dict, box: Box) -> Box:
    if not layer.get("avoid_logo", layer.get("align") == "opposite_logo") or not ctx.logo_box:
        return box
    logo = ctx.logo_box
    if not _overlaps(logo, box):
        return box
    gap = ctx.sx(0.03)
    if (logo[0] + logo[2]) / 2 < (box[0] + box[2]) / 2:
        return (max(box[0], logo[2] + gap), box[1], box[2], box[3])
    return (box[0], box[1], min(box[2], logo[0] - gap), box[3])


def render_flow_layer(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box) -> None:
    box = _avoid_logo(ctx, layer, box)
    w, h = box[2] - box[0], box[3] - box[1]
    if w <= 0 or h <= 0:
        return
    pidx = _product_index(layer)
    m = measure(ctx, layer, w, h, pidx)
    if m.h <= 0:
        return
    align = _resolve_align(ctx, layer, layer.get("align", "left"), box)
    valign = layer.get("valign", "top")
    if valign == "middle":
        y = box[1] + (h - m.h) // 2
    elif valign == "bottom":
        y = box[3] - m.h
    else:
        y = box[1]
    if layer.get("type") in {"text", "footer", "stack"}:
        draw_element(ctx, canvas, layer, (box[0], y, box[2], y + m.h), m, align, box)
    else:
        x = _align_x(box[0], box[2], m.w, align)
        draw_element(ctx, canvas, layer, (x, y, x + m.w, y + m.h), m, align, box)


# ---------------------------------------------------------------------------
# Box-only layers
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=32)
def _leaf_tile(tile: int, color: RGB, opacity: float, style: str) -> Image.Image:
    img = Image.new("RGBA", (tile, tile), (*color, 0))
    polys = []
    length = tile * 0.2
    for (cx, cy, angle) in ((0.25, 0.27, -0.7), (0.75, 0.77, 2.4), (0.75, 0.25, 0.9), (0.25, 0.75, -2.3)):
        base = (cx * tile - math.cos(angle) * length / 2, cy * tile - math.sin(angle) * length / 2)
        polys.append(leaf_polygon(base, angle, length, 0.32))
    if style == "dots":
        mask = Image.new("L", (tile, tile), 0)
        dot = int(tile * 0.035)
        for cx, cy in ((0.25, 0.25), (0.75, 0.75)):
            mask.paste(ellipse_mask((dot, dot)), (int(cx * tile), int(cy * tile)))
    else:
        mask = polygon_mask((tile, tile), polys)
    fill_mask(img, mask, color, 0, 0, opacity=opacity)
    return img


def _scene_contrast(pixels: list[RGB], scrim: RGB, text: RGB, alpha: float) -> float:
    """5th-percentile WCAG contrast of `text` over the scene blended with `scrim` at `alpha`."""
    text_lum = relative_luminance(text)
    ratios = []
    for px in pixels:
        blended = (px[0] + (scrim[0] - px[0]) * alpha, px[1] + (scrim[1] - px[1]) * alpha,
                   px[2] + (scrim[2] - px[2]) * alpha)
        lum = relative_luminance(blended)  # type: ignore[arg-type]
        hi, lo = max(lum, text_lum), min(lum, text_lum)
        ratios.append((hi + 0.05) / (lo + 0.05))
    ratios.sort()
    return ratios[max(0, int(len(ratios) * 0.05) - 1)] if ratios else 21.0


def draw_scene(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box, color: RGB) -> None:
    """Full-bleed AI scene (options["background_image"]) under a brand-colour scrim.

    The scrim opacity is the smallest that gives the text colour meant for this
    surface (`on:<colour>`) ≥ `scene_contrast` (default 4.5:1) and its muted text
    ≥ `scene_muted_contrast` (3:1) over 95% of the scene, unless the layer fixes it
    with `scene_scrim` (for layouts whose text sits on solid panels). The scrim is a gentle vertical gradient (+8% at the bottom).
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    scene = fit_cover(ctx.background_image, (w, h)).convert("RGB")  # type: ignore[arg-type]
    token = layer.get("color", "background")
    text = resolve_color(f"on:{token}", ctx.brand)
    muted = resolve_color("muted" if token == "background" else f"soft:{token}", ctx.brand)
    sample = scene.resize((40, 40), Image.Resampling.BILINEAR)
    pixels: list[RGB] = [sample.getpixel((x, y)) for y in range(40) for x in range(40)]  # type: ignore[misc]
    target = float(layer.get("scene_contrast", 4.5))
    muted_target = float(layer.get("scene_muted_contrast", 3.0))

    def legible(value: float) -> bool:
        return (_scene_contrast(pixels, color, text, value) >= target
                and _scene_contrast(pixels, color, muted, value) >= muted_target)

    if layer.get("scene_scrim") is not None:
        alpha = max(0.0, min(0.95, float(layer["scene_scrim"])))
    else:
        alpha = max(0.0, min(0.92, float(layer.get("scene_min", 0.3))))
        while alpha < 0.92 and not legible(alpha):
            alpha = min(0.92, alpha + 0.04)
    achieved = _scene_contrast(pixels, color, text, alpha)
    ramp = Image.linear_gradient("L").resize((w, h), Image.Resampling.BICUBIC)
    top, bottom = alpha, min(0.96, alpha + 0.08)
    mask = ramp.point(lambda v: int(255 * (top + (bottom - top) * v / 255.0)))
    scrim = Image.new("RGBA", (w, h), (*color, 0))
    scrim.putalpha(mask)
    flat = scene.convert("RGBA")
    flat.alpha_composite(scrim)
    composite(canvas, flat, x0, y0)
    ctx.report.append({"type": "background", "layer": layer.get("_index"), "scene": True, "scrim": round(alpha, 3),
                       "contrast_p05": round(achieved, 2),
                       "muted_contrast_p05": round(_scene_contrast(pixels, color, muted, alpha), 2),
                       "text_color": list(text), "box": list(box)})


def draw_background(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box) -> None:
    style = layer.get("style", "solid")
    if layer.get("allow_override", True) and ctx.options.get("background") in {"solid", "gradient", "pattern"}:
        style = ctx.options["background"]
    color = ctx.color(layer.get("color", "background"))
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    if ctx.background_image is not None and layer.get("scene", True):
        draw_scene(ctx, canvas, layer, box, color)
        if box == (0, 0, ctx.width, ctx.height):
            ctx.background = color
        return
    if style == "gradient":
        if layer.get("color2"):
            end = ctx.color(layer["color2"])
        elif sum(color) > 380:
            end = mix(color, (0, 0, 0), 0.07)
        else:
            end = mix(color, (255, 255, 255), 0.1)
        gradient = Image.linear_gradient("L").resize((w, h), Image.Resampling.BICUBIC)
        top = Image.new("RGBA", (w, h), (*color, 255))
        bottom = Image.new("RGBA", (w, h), (*end, 255))
        composite(canvas, Image.composite(bottom, top, gradient), x0, y0)
    else:
        composite(canvas, Image.new("RGBA", (w, h), (*color, 255)), x0, y0)
    if style == "pattern":
        pattern_token = layer.get("pattern_color", "primary")
        pcolor = ctx.color(pattern_token)
        tile = max(48, ctx.sx(layer.get("tile", 0.16)))
        opacity = float(layer.get("pattern_opacity", 0.07 if sum(color) > 380 else 0.12))
        sprite = _leaf_tile(tile, pcolor, opacity, layer.get("pattern", "leaves"))
        overlay = Image.new("RGBA", (w, h), (*pcolor, 0))
        for ty in range(0, h, tile):
            for tx in range(0, w, tile):
                overlay.paste(sprite, (tx, ty))
        composite(canvas, overlay, x0, y0)
    if box == (0, 0, ctx.width, ctx.height):
        ctx.background = color


def draw_shape(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    shape = layer.get("shape", "rect")
    opacity = float(layer.get("opacity", 1.0))
    radius = ctx.sx(layer.get("radius", 0))
    if layer.get("shadow"):
        shadow, pad = drop_shadow((w, h), radius=radius, blur=ctx.sx(0.02), opacity=0.14, color=ctx.color("ink"),
                                  shape="ellipse" if shape == "ellipse" else "rounded")
        composite(canvas, shadow, x0 - pad, y0 - pad + ctx.sx(0.01))
    if shape == "ellipse":
        mask = ellipse_mask((w, h))
    elif shape == "line":
        mask = Image.new("L", (w, h), 255)
    else:
        mask = rounded_mask((w, h), radius) if radius else Image.new("L", (w, h), 255)
    gradient = layer.get("gradient")
    if gradient:
        start, end = float(gradient.get("from", 0.0)), float(gradient.get("to", 1.0))
        ramp = Image.linear_gradient("L").resize((w, h), Image.Resampling.BICUBIC)
        if gradient.get("direction") == "up":
            ramp = ramp.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        ramp = ramp.point(lambda v: int(255 * (start + (end - start) * (v / 255.0) ** float(gradient.get("ease", 1.4)))))
        mask = Image.composite(ramp, Image.new("L", (w, h), 0), mask)
    fill = layer.get("fill")
    if fill and fill != "none":
        fill_mask(canvas, mask, ctx.color(fill), x0, y0, opacity)
    outline = layer.get("outline")
    if outline:
        stroke = max(1, ctx.sx(layer.get("stroke", 0.002)))
        inner = Image.new("L", (w, h), 0)
        if shape == "ellipse":
            inner.paste(ellipse_mask((w - stroke * 2, h - stroke * 2)), (stroke, stroke))
        else:
            inner.paste(rounded_mask((w - stroke * 2, h - stroke * 2), max(0, radius - stroke)), (stroke, stroke))
        ring = Image.composite(Image.new("L", (w, h), 0), mask, inner)
        fill_mask(canvas, ring, ctx.color(outline), x0, y0, opacity)


def draw_botanical(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box) -> None:
    """A stylised sprig: curved stem with alternating leaves (line or filled)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 4 or h <= 4:
        return
    scale = 3
    W, H = w * scale, h * scale
    leaves = int(layer.get("leaves", 7))
    style = layer.get("style", "fill")
    stem = _bezier((W * 0.5, H * 0.98), (W * (0.5 + float(layer.get("bend", 0.18))), H * 0.5), (W * 0.5, H * 0.02), 60)
    mask = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(mask)
    stroke = max(2, int(min(W, H) * 0.012))
    draw.line(stem, fill=255, width=stroke, joint="curve")
    length_max = min(W * 0.5, H * 0.24)
    polys = []
    for i in range(leaves):
        t = 0.12 + 0.8 * i / max(1, leaves - 1)
        idx = min(len(stem) - 2, int(t * (len(stem) - 1)))
        px, py = stem[idx]
        nx, ny = stem[idx + 1]
        direction = math.atan2(ny - py, nx - px)
        side = 1 if i % 2 == 0 else -1
        angle = direction + side * math.radians(42)
        length = length_max * (1.0 - 0.45 * t)
        polys.append(leaf_polygon((px, py), angle, length, 0.34))
    tip_dir = math.atan2(stem[-1][1] - stem[-3][1], stem[-1][0] - stem[-3][0])
    polys.append(leaf_polygon(stem[-1], tip_dir, length_max * 0.5, 0.34))
    if style == "line":
        for poly in polys:
            draw.line(poly + [poly[0]], fill=255, width=stroke, joint="curve")
    else:
        for poly in polys:
            draw.polygon(poly, fill=255)
    small = mask.resize((w, h), Image.Resampling.LANCZOS)
    if layer.get("mirror"):
        small = small.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    rotation = float(layer.get("rotation", 0))
    color = ctx.color(layer.get("color", "primary"))
    opacity = float(layer.get("opacity", 0.16))
    if rotation:
        small = small.rotate(rotation, resample=Image.Resampling.BICUBIC, expand=True)
    fill_mask(canvas, small, color, x0 + (w - small.width) // 2, y0 + (h - small.height) // 2, opacity)


# -- images -----------------------------------------------------------------

def _get_image(ctx: LayoutContext, url: str | None) -> Image.Image | None:
    if not url:
        return None
    return ctx.images.get(url)


def paste_product_image(
    ctx: LayoutContext,
    canvas: Image.Image,
    img: Image.Image,
    box: Box,
    *,
    fit: str = "auto",
    radius: int = 0,
    padding: float = 0.08,
    card: str | None = None,
    shadow: bool = False,
    focus: tuple[float, float] = (0.5, 0.5),
    trim: bool = True,
    valign: str = "middle",
    max_crop: float = 0.24,
) -> Box:
    """Place `img` in `box` with the requested fit; returns the image's drawn box."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 2 or h <= 2:
        return box
    plain = has_plain_background(img)
    mode = fit if fit in {"contain", "cover"} else ("contain" if plain else "cover")
    card_color: RGB | None = None
    if card == "auto":
        card_color = plain_background_color(img) if plain else ctx.color("surface")
    elif card and card != "none":
        card_color = ctx.color(card)
    elif mode == "contain" and fit == "auto" and plain:
        card_color = plain_background_color(img)
    if shadow and (card_color is not None or mode == "cover"):
        sh, pad = drop_shadow((w, h), radius=radius, blur=ctx.sx(0.022), opacity=0.13, color=ctx.color("ink"))
        composite(canvas, sh, x0 - pad, y0 - pad + ctx.sx(0.012))
    if mode == "cover":
        fitted = fit_cover_soft(img, (w, h), focus, max_crop=max_crop)
        flat = Image.new("RGBA", (w, h), (*(card_color or ctx.color("surface")), 255))
        flat.alpha_composite(fitted)
        mask = rounded_mask((w, h), radius) if radius else Image.new("L", (w, h), 255)
        flat.putalpha(mask)
        composite(canvas, flat, x0, y0)
        return box
    if card_color is not None:
        mask = rounded_mask((w, h), radius) if radius else Image.new("L", (w, h), 255)
        fill_mask(canvas, mask, card_color, x0, y0)
    source = trim_white(img) if trim and plain else img
    pad = int(min(w, h) * padding)
    inner_w, inner_h = max(1, w - pad * 2), max(1, h - pad * 2)
    fitted = fit_contain(source, (inner_w, inner_h))
    px = x0 + (w - fitted.width) // 2
    if valign == "bottom":
        py = y1 - pad - fitted.height
    elif valign == "top":
        py = y0 + pad
    else:
        py = y0 + (h - fitted.height) // 2
    if radius and card_color is None:
        fitted = fitted.copy()
        fitted.putalpha(Image.composite(fitted.getchannel("A"), Image.new("L", fitted.size, 0),
                                        rounded_mask(fitted.size, radius)))
    composite(canvas, fitted, px, py)
    return (px, py, px + fitted.width, py + fitted.height)


def _image_source(ctx: LayoutContext, layer: dict, pidx: int) -> str | None:
    if layer.get("source") == "text:image_url":
        return ctx.texts.get("image_url") or image_url_for(ctx.product(pidx), int(layer.get("image", 0)), ctx.image_offset)
    index = int(layer.get("image", 0)) + (ctx.slide_index if layer.get("per_slide") else 0)
    return image_url_for(ctx.product(pidx), index, ctx.image_offset)


def draw_image(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box) -> None:
    pidx = _product_index(layer)
    url = _image_source(ctx, layer, pidx)
    img = _get_image(ctx, url)
    if img is None:
        if layer.get("placeholder", True) and layer.get("card"):
            fill_mask(canvas, rounded_mask((box[2] - box[0], box[3] - box[1]), ctx.sx(layer.get("radius", 0))),
                      ctx.color(layer.get("card") if layer.get("card") != "auto" else "surface"), box[0], box[1])
        return
    drawn = paste_product_image(
        ctx, canvas, img, box, fit=layer.get("fit", "auto"), radius=ctx.sx(layer.get("radius", 0)),
        padding=float(layer.get("padding", 0.08)), card=layer.get("card"), shadow=bool(layer.get("shadow")),
        focus=tuple(layer.get("focus", (0.5, 0.5))), trim=bool(layer.get("trim", True)),
        valign=layer.get("valign", "middle"), max_crop=float(layer.get("max_crop", 0.24)),
    )
    ctx.report.append({"type": "image", "layer": layer.get("_index"), "url": url, "product": pidx,
                       "box": list(box), "bbox": list(drawn)})


def grid_cells(box: Box, count: int, gap: int, arrangement: str, caption_h: int = 0, target_aspect: float = 1.0) -> list[Box]:
    """Cell rectangles (image + caption area) for `count` items inside `box`."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if count <= 0:
        return []
    if count == 1:
        return [box]
    if arrangement == "hero" and count == 3:
        if w >= h:
            big_w = int((w - gap) * 0.58)
            small_h = (h - gap) // 2
            return [(x0, y0, x0 + big_w, y1), (x0 + big_w + gap, y0, x1, y0 + small_h),
                    (x0 + big_w + gap, y0 + small_h + gap, x1, y1)]
        big_h = int((h - gap) * 0.58)
        small_w = (w - gap) // 2
        return [(x0, y0, x1, y0 + big_h), (x0, y0 + big_h + gap, x0 + small_w, y1),
                (x0 + small_w + gap, y0 + big_h + gap, x1, y1)]
    if arrangement == "hero" and count == 4:
        if w >= h:
            big_w = int((w - gap) * 0.56)
            small_h = (h - gap * 2) // 3
            cells = [(x0, y0, x0 + big_w, y1)]
            for i in range(3):
                top = y0 + i * (small_h + gap)
                cells.append((x0 + big_w + gap, top, x1, top + small_h))
            return cells
        big_h = int((h - gap) * 0.6)
        small_w = (w - gap * 2) // 3
        cells = [(x0, y0, x1, y0 + big_h)]
        for i in range(3):
            left = x0 + i * (small_w + gap)
            cells.append((left, y0 + big_h + gap, left + small_w, y1))
        return cells
    best: tuple[float, int, int] | None = None
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        if (cols - 1) * rows >= count:
            continue
        cell_w = (w - gap * (cols - 1)) / cols
        cell_h = (h - gap * (rows - 1)) / rows - caption_h
        if cell_w <= 0 or cell_h <= 0:
            continue
        aspect = cell_w / cell_h
        empty = cols * rows - count
        score = abs(math.log(aspect / target_aspect)) + empty * 0.35 - min(cell_w, cell_h) / max(w, h) * 0.6
        if best is None or score < best[0]:
            best = (score, cols, rows)
    if best is None:
        return [box]
    _, cols, rows = best
    cell_w = (w - gap * (cols - 1)) / cols
    cell_h = (h - gap * (rows - 1)) / rows
    cells: list[Box] = []
    for i in range(count):
        row, col = divmod(i, cols)
        in_row = min(cols, count - row * cols)
        offset = (cols - in_row) * (cell_w + gap) / 2
        left = x0 + offset + col * (cell_w + gap)
        top = y0 + row * (cell_h + gap)
        cells.append((int(round(left)), int(round(top)), int(round(left + cell_w)), int(round(top + cell_h))))
    return cells


def draw_grid(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box) -> None:
    source = layer.get("source", "products")
    start = int(layer.get("product", 0))
    max_cells = int(layer.get("max", 4))
    entries: list[tuple[int, str | None]] = []
    if source == "images":
        product = ctx.product(start)
        count = min(max_cells, len(product.image_urls) if product else 0)
        entries = [(start, image_url_for(product, i, ctx.image_offset)) for i in range(count)]
    else:
        for idx in range(start, min(len(ctx.products), start + max_cells)):
            entries.append((idx, image_url_for(ctx.products[idx], 0, ctx.image_offset)))
    if not entries:
        return
    gap = ctx.sx(layer.get("gap", 0.02))
    captions = bool(layer.get("captions")) and source == "products"
    cap_size = ctx.sx(layer.get("caption_size", 0.024))
    price_size = ctx.sx(layer.get("price_size", 0.024))
    caption_lines = int(layer.get("caption_lines", 2))
    show_prices = captions and ctx.show_price and layer.get("prices", True)
    caption_h = 0
    if captions:
        caption_h = int(cap_size * 1.2 * caption_lines + cap_size * 0.9 + (price_size * 1.6 if show_prices else 0))
    arrangement = layer.get("arrangement", "auto")
    if arrangement == "list":
        _draw_grid_list(ctx, canvas, layer, box, entries, gap)
        return
    if captions and arrangement == "hero":
        arrangement = "auto"
    cells = grid_cells(box, len(entries), gap, arrangement, caption_h, float(layer.get("aspect", 1.0)))
    radius = ctx.sx(layer.get("radius", 0.014))
    card = layer.get("card", "surface")
    align = layer.get("caption_align", "center")
    caption_rel = float(layer.get("caption_size", 0.024))
    if captions:
        # One caption size for every cell: the largest size at which all titles fit.
        title_path, title_var = ctx.brand.font_spec(layer.get("caption_role", "body"), "Medium")
        sizes = []
        for (pidx, _url), cell in zip(entries, cells):
            product = ctx.product(pidx)
            title = sanitize_text(product.title if product else "", title_path, title_var)
            block = fit_text(title, title_path, title_var, cell[2] - cell[0], int(cap_size * 1.2 * caption_lines + 2),
                             cap_size, min_size=int(cap_size * 0.75), max_lines=caption_lines, line_height=1.18)
            sizes.append(block.size)
        if sizes:
            caption_rel = min(sizes) / ctx.width
    for (pidx, url), cell in zip(entries, cells):
        img_box = (cell[0], cell[1], cell[2], cell[3] - caption_h)
        img = _get_image(ctx, url)
        if img is not None:
            paste_product_image(ctx, canvas, img, img_box, fit=layer.get("fit", "auto"), radius=radius,
                                padding=float(layer.get("padding", 0.08)), card=card,
                                shadow=bool(layer.get("shadow", False)), trim=True)
            ctx.report.append({"type": "image", "layer": layer.get("_index"), "url": url, "product": pidx,
                               "box": list(img_box), "bbox": list(img_box)})
        if captions:
            cap_box = (cell[0], cell[3] - caption_h + int(cap_size * 0.7), cell[2], cell[3])
            stack = {
                "type": "stack", "gap": 0.006, "_index": layer.get("_index"),
                "children": [
                    {"type": "text", "text": "{title}", "role": layer.get("caption_role", "body"),
                     "weight": "Medium", "size": caption_rel, "min_size": caption_rel * 0.98,
                     "max_lines": caption_lines, "line_height": 1.18, "color": layer.get("caption_color", "text"),
                     "align": align, "_index": layer.get("_index")},
                    {"type": "price", "size": layer.get("price_size", 0.024), "compare_scale": 0.8,
                     "role": "price", "color": layer.get("price_color", "primary"), "_index": layer.get("_index"),
                     "when": "show_price"} if layer.get("prices", True) else {"type": "spacer", "h": 0},
                ],
            }
            sub_m = measure(ctx, stack, cap_box[2] - cap_box[0], cap_box[3] - cap_box[1], pidx)
            draw_element(ctx, canvas, {**stack, "product": pidx}, (cap_box[0], cap_box[1], cap_box[2], cap_box[1] + sub_m.h),
                         sub_m, align, cap_box)


def _draw_grid_list(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box,
                    entries: list[tuple[int, str | None]], gap: int) -> None:
    """Editorial list: one row per product (thumbnail, name, price) with hairlines between."""
    x0, y0, x1, y1 = box
    count = len(entries)
    row_h = (y1 - y0 - gap * (count - 1)) / count
    thumb = int(min(row_h, (x1 - x0) * 0.34))
    line_color = ctx.color(layer.get("line_color", "line"))
    list_title = min(float(layer.get("list_title_size", 0.042)), row_h * 0.27 / ctx.width)
    for i, (pidx, url) in enumerate(entries):
        top = int(round(y0 + i * (row_h + gap)))
        img = _get_image(ctx, url)
        tb = (x0, top + int((row_h - thumb) / 2), x0 + thumb, top + int((row_h - thumb) / 2) + thumb)
        if img is not None:
            paste_product_image(ctx, canvas, img, tb, fit=layer.get("fit", "auto"), radius=ctx.sx(layer.get("radius", 0.014)),
                                padding=float(layer.get("padding", 0.08)), card=layer.get("card", "auto"),
                                shadow=bool(layer.get("shadow", False)))
            ctx.report.append({"type": "image", "layer": layer.get("_index"), "url": url, "product": pidx,
                               "box": list(tb), "bbox": list(tb)})
        text_x = x0 + thumb + max(gap, ctx.sx(0.035))
        cap_box = (text_x, top, x1, int(top + row_h))
        stack = {
            "type": "stack", "gap": 0, "_index": layer.get("_index"), "product": pidx,
            "children": [
                {"type": "text", "text": "{title}", "role": "heading", "size": list_title,
                 "min_size": list_title * 0.7, "max_lines": 2, "line_height": 1.06,
                 "color": layer.get("caption_color", "text"), "_index": layer.get("_index")},
                {"type": "price", "size": layer.get("price_size", 0.026), "role": "price", "compare_scale": 0.8,
                 "color": layer.get("price_color", "primary"), "gap_before": ctx_rel(ctx, 12), "when": "show_price",
                 "_index": layer.get("_index")},
            ],
        }
        sub_m = measure(ctx, stack, cap_box[2] - cap_box[0], cap_box[3] - cap_box[1], pidx)
        ty = cap_box[1] + (cap_box[3] - cap_box[1] - sub_m.h) // 2
        draw_element(ctx, canvas, stack, (cap_box[0], ty, cap_box[2], ty + sub_m.h), sub_m, "left", cap_box)
        if i < count - 1:
            ly = int(top + row_h + gap / 2)
            composite(canvas, Image.new("RGBA", (x1 - x0, max(1, ctx.sx(0.0015))), (*line_color, 255)), x0, ly)


def ctx_rel(ctx: LayoutContext, px_at_1080: float) -> float:
    """Design px (1080-wide basis) → relative size for this canvas."""
    return px_at_1080 / 1080.0


def _decode_logo_box(ctx: LayoutContext, layer: dict) -> tuple[Box | None, str, Any]:
    """Where the logo (or wordmark) goes: (box, kind, payload)."""
    if not ctx.show_logo and not layer.get("force"):
        return None, "", None
    position = layer.get("position", "brand")
    if position == "brand":
        position = ctx.brand.logo_position
    if position == "none":
        return None, "", None
    allowed = layer.get("allowed") or ["top_left", "top_right", "bottom_left", "bottom_right"]
    if position not in allowed and position not in {"center", "box"}:
        flipped = position.replace("top", "§").replace("bottom", "top").replace("§", "bottom")
        position = flipped if flipped in allowed else allowed[0]
    max_w = ctx.sx(layer.get("max_w", 0.2))
    max_h = ctx.sx(layer.get("max_h", 0.06))
    margin_x = ctx.sx(layer.get("margin_x", 0.065))
    margin_top = round(float(layer.get("margin_y", 0.06)) * ctx.height)
    margin_bottom = round(float(layer.get("margin_bottom", layer.get("margin_y", 0.06))) * ctx.height)
    if ctx.logo is not None:
        logo = ctx.logo
        bbox = logo.getchannel("A").getbbox()
        if bbox:
            logo = logo.crop(bbox)
        fitted = fit_contain(logo, (max_w, max_h))
        kind, payload, w, h = "image", fitted, fitted.width, fitted.height
    elif layer.get("fallback", "wordmark") == "wordmark" and ctx.brand.business_name:
        text = sanitize_text(ctx.brand.business_name, ctx.brand.heading_font)
        block = fit_text(text, ctx.brand.heading_font, "", max_w, max_h, int(max_h * 0.82), min_size=int(max_h * 0.4),
                         max_lines=1, line_height=1.0)
        if not block.lines:
            return None, "", None
        kind, payload, w, h = "wordmark", block, block.width, block.height
    else:
        return None, "", None
    if position in {"center", "box"}:
        bx = ctx.box(layer.get("box", [0, 0, 1, 1]))
        x = bx[0] + (bx[2] - bx[0] - w) // 2
        y = bx[1] + (bx[3] - bx[1] - h) // 2
    else:
        x = margin_x if position.endswith("left") else ctx.width - margin_x - w
        y = margin_top if position.startswith("top") else ctx.height - margin_bottom - h
        band_key = "band_top" if position.startswith("top") else "band_bottom"
        if layer.get(band_key):
            band = ctx.box(layer[band_key])
            y = band[1] + (band[3] - band[1] - h) // 2
    return (x, y, x + w, y + h), kind, payload


def draw_logo(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box) -> None:
    placed, kind, payload = _decode_logo_box(ctx, layer)
    if placed is None:
        return
    opacity = ctx.brand.logo_opacity
    on = ctx.color(layer.get("on", "background"))
    if kind == "image":
        logo: Image.Image = payload
        opaque = logo.getchannel("A").point(lambda v: 255 if v > 128 else 0)
        transparent = logo.getchannel("A").getextrema()[0] < 200
        if opaque.getbbox():
            stat = ImageStat.Stat(logo.convert("RGB"), mask=opaque)
            mean: RGB = (int(stat.mean[0]), int(stat.mean[1]), int(stat.mean[2]))
        else:
            mean = on
        plate = layer.get("plate", "auto")
        if plate == "always" or (plate == "auto" and transparent and contrast_ratio(mean, on) < 1.7):
            pad = ctx.sx(0.014)
            plate_color = ctx.color("surface") if sum(on) < 380 else ctx.color("ink")
            fill_mask(canvas, rounded_mask((logo.width + pad * 2, logo.height + pad * 2), pad * 1.4), plate_color,
                      placed[0] - pad, placed[1] - pad, 0.92)
        if opacity < 1.0:
            logo = logo.copy()
            logo.putalpha(logo.getchannel("A").point(lambda v: int(v * opacity)))
        composite(canvas, logo, placed[0], placed[1])
    else:
        if layer.get("plate") == "always":
            pad = ctx.sx(0.016)
            fill_mask(canvas, rounded_mask((placed[2] - placed[0] + pad * 2, placed[3] - placed[1] + pad * 2), pad * 1.4),
                      ctx.color("surface"), placed[0] - pad, placed[1] - pad, 0.92)
            on = ctx.color("surface")
        color = ctx.color(layer["color"]) if layer.get("color") else readable_on(on, ink=ctx.color("ink"))
        draw_text_block(canvas, payload, placed[0], placed[1], placed[2] - placed[0], "left", color, opacity)
    ctx.report.append({"type": "logo", "layer": layer.get("_index"), "kind": kind, "bbox": list(placed)})


def anchored_box(ctx: LayoutContext, parent: Box, layer: dict) -> Box:
    """Box for an overlay anchored to a corner/edge of `parent` (sizes relative to canvas width)."""
    anchor = layer.get("anchor", "top_left")
    w, h = ctx.sx(layer.get("w", 0.15)), ctx.sx(layer.get("h", layer.get("w", 0.15)))
    if layer.get("w") == "full":
        w = parent[2] - parent[0]
    ix, iy = ctx.sx(layer.get("inset_x", layer.get("inset", 0.025))), ctx.sx(layer.get("inset_y", layer.get("inset", 0.025)))
    if anchor.endswith("left"):
        x = parent[0] + ix
    elif anchor.endswith("right"):
        x = parent[2] - ix - w
    else:
        x = parent[0] + (parent[2] - parent[0] - w) // 2
    if anchor.startswith("top"):
        y = parent[1] + iy
    elif anchor.startswith("bottom"):
        y = parent[3] - iy - h
    else:
        y = parent[1] + (parent[3] - parent[1] - h) // 2
    return int(x), int(y), int(x + w), int(y + h)


def draw_overlays(ctx: LayoutContext, canvas: Image.Image, parent: Box, overlays: Iterable[dict], pidx: int) -> None:
    for overlay in overlays:
        overlay = dict(overlay, _index=overlay.get("_index"))
        overlay.setdefault("product", pidx)
        if not _visible(ctx, overlay, _product_index(overlay)):
            continue
        box = anchored_box(ctx, parent, overlay)
        kind = overlay.get("type", "")
        if kind in BOX_DRAWERS:
            BOX_DRAWERS[kind](ctx, canvas, overlay, box)
        elif kind in MEASURERS:
            render_flow_layer(ctx, canvas, overlay, box)


def draw_column(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box) -> None:
    """Vertical flex container: children with `flex` share the space left by the others."""
    children = [dict(c, _index=c.get("_index", layer.get("_index"))) for c in layer.get("children", [])
                if _visible(ctx, c, _product_index(c, _product_index(layer)))]
    if not children:
        return
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    gap = ctx.sx(layer.get("gap", 0.03))
    flex_total = sum(float(c.get("flex", 0)) for c in children)
    min_flex = round(float(layer.get("min_flex", 0.35)) * height) if flex_total else 0
    measured: list[Measured | None] = []
    for child in children:
        if child.get("flex"):
            measured.append(None)
            continue
        limit = round(child["h"] * ctx.height) if "h" in child else height
        budget = max(0, min(limit, height - min_flex))
        measured.append(measure(ctx, child, width, budget, _product_index(child, _product_index(layer))))
    shown = [m for m in measured if m is None or m.h > 0]
    fixed = sum(m.h for m in measured if m is not None)
    gaps = gap * max(0, len(shown) - 1)
    remaining = max(0, height - fixed - gaps)
    y = y0
    if not flex_total:
        valign = layer.get("valign", "top")
        content = fixed + gaps
        y = y0 + (height - content) // 2 if valign == "middle" else (y1 - content if valign == "bottom" else y0)
    first = True
    for child, m in zip(children, measured):
        if m is not None and m.h <= 0:
            continue
        if not first:
            y += gap
        first = False
        pidx = _product_index(child, _product_index(layer))
        if m is None:
            h = int(remaining * float(child["flex"]) / flex_total)
            child_box = (x0, y, x1, y + h)
            kind = child.get("type", "")
            if kind in BOX_DRAWERS:
                BOX_DRAWERS[kind](ctx, canvas, child, child_box)
            draw_overlays(ctx, canvas, child_box, child.get("overlays", []), pidx)
            y += h
            continue
        align = _resolve_align(ctx, child, child.get("align", layer.get("align", "left")), (x0, y, x1, y + m.h))
        if child.get("type") in {"text", "footer", "stack"}:
            draw_element(ctx, canvas, child, (x0, y, x1, y + m.h), m, align, (x0, y, x1, y + m.h))
        else:
            cx = _align_x(x0, x1, m.w, align)
            draw_element(ctx, canvas, child, (cx, y, cx + m.w, y + m.h), m, align, (x0, y, x1, y + m.h))
        y += m.h


def draw_box_with_overlays(kind: str) -> Callable[[LayoutContext, Image.Image, dict, Box], None]:
    def drawer(ctx: LayoutContext, canvas: Image.Image, layer: dict, box: Box) -> None:
        BASE_DRAWERS[kind](ctx, canvas, layer, box)
        if layer.get("overlays"):
            draw_overlays(ctx, canvas, box, layer["overlays"], _product_index(layer))
    return drawer


BASE_DRAWERS: dict[str, Callable[[LayoutContext, Image.Image, dict, Box], None]] = {
    "image": draw_image,
    "grid": draw_grid,
    "shape": draw_shape,
}

BOX_DRAWERS: dict[str, Callable[[LayoutContext, Image.Image, dict, Box], None]] = {
    "column": draw_column,
    "background": draw_background,
    "shape": draw_box_with_overlays("shape"),
    "scrim": draw_shape,
    "image": draw_box_with_overlays("image"),
    "grid": draw_box_with_overlays("grid"),
    "logo": draw_logo,
    "botanical": draw_botanical,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_layers(ctx: LayoutContext, layers: list[dict], *, transparent: bool = False) -> Image.Image:
    """Render a list of layers onto a new RGBA canvas (report in `ctx.report`).

    `transparent=True` starts from a fully transparent canvas (video overlays).
    """
    base = ctx.color("background")
    canvas = Image.new("RGBA", (ctx.width, ctx.height), (*base, 0 if transparent else 255))
    if not transparent:
        ctx.background = base
    ctx.logo_box = None
    indexed = [dict(layer, _index=i) for i, layer in enumerate(layers)]
    for layer in indexed:
        if layer.get("type") == "logo" and _visible(ctx, layer, _product_index(layer)):
            ctx.logo_box = _decode_logo_box(ctx, layer)[0]
            break
    for layer in indexed:
        kind = layer.get("type", "")
        if not _visible(ctx, layer, _product_index(layer)):
            continue
        box = ctx.box(layer.get("box"))
        if kind in BOX_DRAWERS:
            BOX_DRAWERS[kind](ctx, canvas, layer, box)
        elif kind in MEASURERS:
            render_flow_layer(ctx, canvas, layer, box)
    return canvas


def render_spec(ctx: LayoutContext, spec: dict) -> Image.Image:
    return render_layers(ctx, list(spec.get("layers", [])))


def image_requests(spec: dict, products: list[ProductInfo], offset: int, *, slide_index: int = 0,
                   texts: dict[str, str] | None = None) -> list[str]:
    """Every image URL a spec will draw (so they can be fetched up front)."""
    urls: list[str] = []

    def product_at(index: int) -> ProductInfo | None:
        return products[index] if 0 <= index < len(products) else None

    def walk(layers: Iterable[dict]) -> None:
        for layer in layers:
            kind = layer.get("type")
            if kind in {"image", "thumb"}:
                pidx = int(layer.get("product", 0))
                if layer.get("source") == "text:image_url" and texts and texts.get("image_url"):
                    urls.append(texts["image_url"])
                    continue
                index = int(layer.get("image", 0)) + (slide_index if layer.get("per_slide") else 0)
                url = image_url_for(product_at(pidx), index, offset)
                if url:
                    urls.append(url)
            elif kind == "grid":
                start = int(layer.get("product", 0))
                max_cells = int(layer.get("max", 4))
                if layer.get("source") == "images":
                    product = product_at(start)
                    count = min(max_cells, len(product.image_urls) if product else 0)
                    urls.extend(u for u in (image_url_for(product, i, offset) for i in range(count)) if u)
                else:
                    for idx in range(start, min(len(products), start + max_cells)):
                        url = image_url_for(products[idx], 0, offset)
                        if url:
                            urls.append(url)
            if kind in {"stack", "row", "column"}:
                walk(layer.get("children", []))
            if layer.get("overlays"):
                walk(layer["overlays"])

    walk(spec.get("layers", []))
    return list(dict.fromkeys(urls))
