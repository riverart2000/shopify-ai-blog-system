"""Offline sample products for template previews.

Placeholder product photos are drawn with Pillow (a supplement jar, dropper
bottle, pouch or tube in a studio scene) and primed into the image cache under
`sample://` URLs, so previews work without a network connection.
"""
from __future__ import annotations

import asyncio
import functools
import math

from PIL import Image, ImageDraw, ImageFilter

from .base import RenderProduct
from .brand import RGB, hex_to_rgb, load_font, mix, SANS_FONT, SERIF_FONT, FONT_DIR
from .images import cache_file, encode_jpeg, prime_cache
from .storage import cache_dir, write_bytes_atomic

SAMPLE_PREFIX = "sample://v3/"
_SIZE = 1200

_SAMPLES: tuple[dict, ...] = (
    {
        "id": "sample-magnesium", "title": "Magnesium Glycinate Complex", "handle": "magnesium-glycinate-complex",
        "price": "£24.99", "price_value": 24.99, "compare_at": "£29.99", "discount_pct": 16.7,
        "kind": "jar", "glass": "#5a3418", "cap": "#15251c", "band": "#285746", "name": "Magnesium",
        "sub": "GLYCINATE COMPLEX", "count": "90 CAPSULES", "backdrop": "#e9e2d3", "arch": "#c9d1c3",
        "description": "Gentle, highly absorbable magnesium to support restful sleep and muscle recovery. Third-party tested.",
        "collections": ["Sleep & Recovery"], "tags": ["magnesium", "sleep"],
    },
    {
        "id": "sample-lionsmane", "title": "Lion's Mane Focus Drops", "handle": "lions-mane-focus-drops",
        "price": "£32.00", "price_value": 32.0, "compare_at": "", "discount_pct": 0.0,
        "kind": "dropper", "glass": "#1f4638", "cap": "#b89245", "band": "#15251c", "name": "Lion's Mane",
        "sub": "FOCUS DROPS", "count": "30 ML", "backdrop": "#ece5d8", "arch": "#d8c9a6",
        "description": "Dual-extracted lion's mane mushroom for clear, calm focus.",
        "collections": ["Focus & Energy"], "tags": ["mushrooms", "focus"],
    },
    {
        "id": "sample-collagen", "title": "Collagen Peptides Daily Powder", "handle": "collagen-peptides",
        "price": "£39.00", "price_value": 39.0, "compare_at": "£45.00", "discount_pct": 13.3,
        "kind": "pouch", "glass": "#efe8da", "cap": "#285746", "band": "#b89245", "name": "Collagen",
        "sub": "PEPTIDES · DAILY", "count": "300 G", "backdrop": "#dfe4dc", "arch": "#f4efe4",
        "description": "Grass-fed collagen peptides that dissolve instantly in coffee or smoothies.",
        "collections": ["Beauty"], "tags": ["collagen"],
    },
    {
        "id": "sample-ashwagandha", "title": "Ashwagandha KSM-66 Calm Capsules", "handle": "ashwagandha-calm",
        "price": "£19.50", "price_value": 19.5, "compare_at": "", "discount_pct": 0.0,
        "kind": "jar", "glass": "#f3efe6", "cap": "#285746", "band": "#a8b4a6", "name": "Ashwagandha",
        "sub": "KSM-66 · CALM", "count": "60 CAPSULES", "backdrop": "#efe9dd", "arch": "#b9c6b6",
        "description": "Clinically studied KSM-66 ashwagandha root to help the body adapt to stress.",
        "collections": ["Stress & Mood"], "tags": ["adaptogens"],
    },
)

SAMPLE_TEXTS: dict[str, str] = {
    "headline": "",
    "promo_text": "Autumn wellness sale",
    "coupon_code": "GLOW20",
    "quote": "Small daily rituals, done consistently, are what change how you feel.",
    "quote_author": "The BioluxeLab team",
    "countdown_label": "2 days left",
    "review_text": "I've tried every magnesium out there. This is the first one that actually helps me sleep through the night.",
    "review_author": "Sarah K., verified buyer",
    "rating": "5",
    "fact": "Around half of adults don't get enough magnesium from food alone.",
    "question": "What's your evening wind-down ritual?",
    "option_a": "Herbal tea & a book",
    "option_b": "Stretching or yoga",
    "option_c": "A warm bath",
}


def _font(name: str, size: int, variation: str = ""):
    return load_font(str(FONT_DIR / name), size, variation)


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], radius: float, fill: RGB) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _horizontal_shade(size: tuple[int, int], base: RGB) -> Image.Image:
    """Cylindrical shading: darker edges, soft highlight left of centre."""
    w, h = size
    strip = Image.new("RGB", (w, 1))
    for x in range(w):
        t = x / max(1, w - 1)
        light = 0.5 * (1 - math.cos(2 * math.pi * min(1.0, max(0.0, (t - 0.02) / 0.96))))
        shade = 0.72 + 0.34 * light
        highlight = math.exp(-((t - 0.3) / 0.06) ** 2) * 0.35
        r, g, b = (min(255, int(c * shade + 255 * highlight)) for c in base)
        strip.putpixel((x, 0), (r, g, b))
    return strip.resize((w, h))


def _draw_text_center(draw: ImageDraw.ImageDraw, cx: float, y: float, text: str, font, fill: RGB, tracking: float = 0) -> None:
    width = font.getlength(text) + tracking * max(0, len(text) - 1)
    x = cx - width / 2
    for char in text if tracking else [text]:
        draw.text((x, y), char, font=font, fill=fill, anchor="ls")
        x += font.getlength(char) + tracking


def _product_layer(spec: dict, scale: float) -> tuple[Image.Image, int]:
    """Draw the product (RGBA, transparent background); returns (image, floor offset)."""
    s = scale
    W, H = int(520 * s), int(760 * s)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glass = hex_to_rgb(spec["glass"])
    cap = hex_to_rgb(spec["cap"])
    band = hex_to_rgb(spec["band"])
    label_bg = (247, 244, 236) if sum(glass) < 500 else (255, 255, 255)
    ink = (21, 37, 28)
    kind = spec["kind"]
    if kind == "dropper":
        body = (W * 0.2, H * 0.36, W * 0.8, H * 0.98)
        neck = (W * 0.36, H * 0.29, W * 0.64, H * 0.37)
        cap_box = (W * 0.33, H * 0.14, W * 0.67, H * 0.3)
        bulb = (W * 0.39, H * 0.02, W * 0.61, H * 0.17)
        radius = W * 0.1
    elif kind == "pouch":
        body = (W * 0.08, H * 0.12, W * 0.92, H * 0.98)
        neck = cap_box = bulb = None
        radius = W * 0.05
    else:
        body = (W * 0.1, H * 0.3, W * 0.9, H * 0.98)
        neck = (W * 0.16, H * 0.25, W * 0.84, H * 0.31)
        cap_box = (W * 0.13, H * 0.08, W * 0.87, H * 0.27)
        bulb = None
        radius = W * 0.09
    bw, bh = int(body[2] - body[0]), int(body[3] - body[1])
    mask = Image.new("L", (W, H), 0)
    mdraw = ImageDraw.Draw(mask)
    if kind == "pouch":
        mdraw.polygon([(body[0] + W * 0.04, body[1]), (body[2] - W * 0.04, body[1]), (body[2], body[3]), (body[0], body[3])], fill=255)
        mdraw.rounded_rectangle((body[0], body[1] + H * 0.05, body[2], body[3]), radius=radius, fill=255)
    else:
        mdraw.rounded_rectangle(body, radius=radius, fill=255)
    shade = _horizontal_shade((W, H), glass)
    img.paste(shade, (0, 0), mask)
    draw = ImageDraw.Draw(img)
    if kind == "pouch":
        draw.rectangle((body[0] + W * 0.06, body[1] + H * 0.035, body[2] - W * 0.06, body[1] + H * 0.045), fill=mix(glass, (0, 0, 0), 0.12))
    if neck:
        draw.rectangle(neck, fill=mix(glass, (0, 0, 0), 0.25))
    if cap_box:
        cap_img = _horizontal_shade((int(cap_box[2] - cap_box[0]), int(cap_box[3] - cap_box[1])), cap)
        cmask = Image.new("L", cap_img.size, 0)
        ImageDraw.Draw(cmask).rounded_rectangle((0, 0, cap_img.width - 1, cap_img.height - 1), radius=W * 0.025, fill=255)
        img.paste(cap_img, (int(cap_box[0]), int(cap_box[1])), cmask)
        if kind != "dropper":
            for i in range(1, 28):
                x = cap_box[0] + (cap_box[2] - cap_box[0]) * i / 28
                draw.line((x, cap_box[1] + 6 * s, x, cap_box[3] - 6 * s), fill=(*mix(cap, (0, 0, 0), 0.3), 90), width=max(1, int(2 * s)))
    if bulb:
        bulb_img = _horizontal_shade((int(bulb[2] - bulb[0]), int(bulb[3] - bulb[1])), mix(cap, (0, 0, 0), 0.15))
        bmask = Image.new("L", bulb_img.size, 0)
        ImageDraw.Draw(bmask).rounded_rectangle((0, 0, bulb_img.width - 1, bulb_img.height - 1), radius=bulb_img.width / 2, fill=255)
        img.paste(bulb_img, (int(bulb[0]), int(bulb[1])), bmask)
    # Label
    top = body[1] + bh * (0.2 if kind != "pouch" else 0.22)
    bottom = body[1] + bh * (0.86 if kind != "pouch" else 0.8)
    label = (body[0] + (0 if kind != "pouch" else W * 0.07), top, body[2] - (0 if kind != "pouch" else W * 0.07), bottom)
    label_img = _horizontal_shade((int(label[2] - label[0]), int(label[3] - label[1])), label_bg)
    lmask = Image.new("L", label_img.size, 255)
    if kind == "pouch":
        lmask = Image.new("L", label_img.size, 0)
        ImageDraw.Draw(lmask).rounded_rectangle((0, 0, label_img.width - 1, label_img.height - 1), radius=W * 0.03, fill=255)
    img.paste(label_img, (int(label[0]), int(label[1])), lmask)
    lw, lh = label[2] - label[0], label[3] - label[1]
    cx = (label[0] + label[2]) / 2
    brand_font = _font(SANS_FONT, int(lh * 0.065), "SemiBold")
    _draw_text_center(draw, cx, label[1] + lh * 0.17, "BIOLUXE LAB", brand_font, band, tracking=lh * 0.02)
    name_size = int(lh * 0.24)
    name_font = _font(SERIF_FONT, name_size)
    while name_font.getlength(spec["name"]) > lw * 0.84 and name_size > 10:
        name_size = int(name_size * 0.92)
        name_font = _font(SERIF_FONT, name_size)
    _draw_text_center(draw, cx, label[1] + lh * 0.5, spec["name"], name_font, ink)
    sub_font = _font(SANS_FONT, int(lh * 0.06), "Medium")
    _draw_text_center(draw, cx, label[1] + lh * 0.65, spec["sub"], sub_font, ink, tracking=lh * 0.012)
    band_box = (label[0], label[1] + lh * 0.78, label[2], label[1] + lh * 0.92)
    draw.rectangle(band_box, fill=band)
    count_font = _font(SANS_FONT, int(lh * 0.05), "SemiBold")
    _draw_text_center(draw, cx, band_box[1] + (band_box[3] - band_box[1]) * 0.68, spec["count"], count_font,
                      (255, 255, 255) if sum(band) < 450 else ink, tracking=lh * 0.012)
    # Glass highlight
    hl = Image.new("L", (W, H), 0)
    ImageDraw.Draw(hl).rounded_rectangle((body[0] + bw * 0.12, body[1] + bh * 0.06, body[0] + bw * 0.17, body[3] - bh * 0.06), radius=bw * 0.03, fill=70)
    hl = hl.filter(ImageFilter.GaussianBlur(6 * s))
    white = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    white.putalpha(hl)
    img.alpha_composite(white)
    return img, int(body[3])


def _capsule(draw: ImageDraw.ImageDraw, cx: float, cy: float, length: float, angle: float, color: RGB) -> None:
    width = length * 0.38
    dx, dy = math.cos(angle) * (length - width) / 2, math.sin(angle) * (length - width) / 2
    draw.line((cx - dx, cy - dy, cx + dx, cy + dy), fill=color, width=int(width))
    for sx, sy in ((cx - dx, cy - dy), (cx + dx, cy + dy)):
        draw.ellipse((sx - width / 2, sy - width / 2, sx + width / 2, sy + width / 2), fill=color)


@functools.lru_cache(maxsize=32)
def sample_photo(index: int, variant: int) -> bytes:
    """JPEG bytes of a placeholder product photo (deterministic)."""
    spec = _SAMPLES[index % len(_SAMPLES)]
    size = _SIZE
    if variant == 1:
        canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        product, floor = _product_layer(spec, 1.05)
        x = (size - product.width) // 2
        y = int(size * 0.9) - floor
        shadow = Image.new("L", (size, size), 0)
        ImageDraw.Draw(shadow).ellipse((x + product.width * 0.1, y + floor - 18, x + product.width * 0.9, y + floor + 18), fill=70)
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        canvas.paste((210, 205, 195, 255), (0, 0), shadow)
        canvas.alpha_composite(product, (x, y))
        return encode_jpeg(canvas, quality=90)
    backdrop = hex_to_rgb(spec["backdrop"])
    if variant == 3:
        backdrop = mix(backdrop, hex_to_rgb(spec["arch"]), 0.45)
    canvas = Image.new("RGBA", (size, size), (*backdrop, 255))
    gradient = Image.linear_gradient("L").resize((size, size))
    darker = Image.new("RGBA", (size, size), (*mix(backdrop, (60, 50, 40), 0.18), 255))
    canvas = Image.composite(darker, canvas, gradient.point(lambda v: int(v * 0.6)))
    draw = ImageDraw.Draw(canvas)
    floor_y = int(size * (0.8 if variant != 2 else 0.84))
    draw.rectangle((0, floor_y, size, size), fill=mix(backdrop, (255, 255, 255), 0.25))
    arch = hex_to_rgb(spec["arch"])
    aw = size * (0.54 if variant != 3 else 0.62)
    ax = size * (0.5 if variant == 0 else 0.62 if variant == 2 else 0.4)
    draw.rounded_rectangle((ax - aw / 2, size * 0.12, ax + aw / 2, floor_y), radius=aw / 2, fill=arch)
    draw.rectangle((ax - aw / 2, size * 0.12 + aw / 2, ax + aw / 2, floor_y), fill=arch)
    scale = 0.95 if variant != 2 else 0.78
    product, floor = _product_layer(spec, scale)
    px = int((size * (0.5 if variant != 2 else 0.4)) - product.width / 2)
    py = floor_y + int(size * 0.015) - floor
    shadow = Image.new("L", (size, size), 0)
    ImageDraw.Draw(shadow).ellipse((px + product.width * 0.08, floor_y - 14, px + product.width * 0.98, floor_y + 30), fill=110)
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    canvas.paste((*mix(backdrop, (40, 35, 30), 0.45), 255), (0, 0), shadow)
    canvas.alpha_composite(product, (px, py))
    if variant == 2:
        cdraw = ImageDraw.Draw(canvas)
        gold = (196, 160, 82)
        for i, (cx, cy, ang) in enumerate(((0.7, 0.88, 0.4), (0.78, 0.9, -0.3), (0.86, 0.87, 1.2), (0.74, 0.94, 2.0), (0.62, 0.93, 2.6))):
            _capsule(cdraw, cx * size, cy * size, size * 0.075, ang, mix(gold, (255, 255, 255), 0.12 * (i % 2)))
    return encode_jpeg(canvas, quality=88)


def sample_image_url(index: int, variant: int) -> str:
    return f"{SAMPLE_PREFIX}{_SAMPLES[index % len(_SAMPLES)]['id']}/{variant}.jpg"


def sample_products(count: int = 4, *, images_per_product: int = 4) -> list[RenderProduct]:
    products = []
    for i in range(max(1, count)):
        spec = _SAMPLES[i % len(_SAMPLES)]
        products.append(RenderProduct(
            product_id=spec["id"] if i < len(_SAMPLES) else f"{spec['id']}-{i}",
            title=spec["title"], handle=spec["handle"], url=f"https://bioluxelab.com/products/{spec['handle']}",
            price=spec["price"], price_value=spec["price_value"], compare_at=spec["compare_at"],
            discount_pct=spec["discount_pct"],
            image_urls=[sample_image_url(i, v) for v in range(images_per_product)],
            description=spec["description"], tags=list(spec["tags"]), collections=list(spec["collections"]),
        ))
    return products


def _prime_sync(products: list[RenderProduct]) -> None:
    for product in products:
        for url in product.image_urls:
            if not url.startswith(SAMPLE_PREFIX) or cache_file(url).is_file():
                continue
            ident, _, variant = url[len(SAMPLE_PREFIX):].partition("/")
            index = next((i for i, s in enumerate(_SAMPLES) if s["id"] == ident), 0)
            prime_cache(url, sample_photo(index, int(variant.split(".")[0] or 0)))


async def ensure_sample_images(products: list[RenderProduct]) -> None:
    """Write the generated sample photos into the image cache (once)."""
    await asyncio.to_thread(_prime_sync, products)


def _bokeh(canvas: Image.Image, rng_seed: int, colors: list[RGB], count: int, max_r: float, area: tuple[float, float, float, float]) -> None:
    import random

    rng = random.Random(rng_seed)
    w, h = canvas.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(count):
        r = rng.uniform(max_r * 0.25, max_r)
        cx = rng.uniform(area[0], area[2]) * w
        cy = rng.uniform(area[1], area[3]) * h
        color = rng.choice(colors)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, int(rng.uniform(60, 150))))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(max_r * 0.18)))


@functools.lru_cache(maxsize=4)
def sample_scene(kind: str = "festive") -> bytes:
    """A text-free stand-in for a Postiz AI scene (festive table or Black Friday backdrop)."""
    from .layout import leaf_polygon

    w, h = 1600, 1600
    if kind == "black_friday":
        top, bottom = (24, 22, 26), (6, 6, 8)
    else:
        top, bottom = (30, 58, 44), (12, 28, 20)
    canvas = Image.new("RGBA", (w, h), (*top, 255))
    ramp = Image.linear_gradient("L").resize((w, h))
    canvas = Image.composite(Image.new("RGBA", (w, h), (*bottom, 255)), canvas, ramp)
    if kind == "black_friday":
        streaks = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(streaks)
        for i, x in enumerate((0.15, 0.42, 0.7, 0.92)):
            sdraw.polygon([(x * w, 0), (x * w + 90, 0), (x * w - 500, h), (x * w - 590, h)],
                          fill=(212, 175, 55, 46 if i % 2 else 70))
        canvas.alpha_composite(streaks.filter(ImageFilter.GaussianBlur(40)))
        _bokeh(canvas, 7, [(226, 190, 90), (255, 255, 255)], 46, 70, (0, 0, 1, 0.75))
        floor = Image.new("RGBA", (w, int(h * 0.22)), (18, 18, 20, 255))
        canvas.alpha_composite(floor, (0, int(h * 0.78)))
    else:
        _bokeh(canvas, 3, [(255, 214, 140), (255, 236, 200), (230, 170, 90)], 70, 80, (0, 0, 1, 0.62))
        table = Image.new("RGBA", (w, int(h * 0.3)), (214, 196, 168, 255))
        tramp = Image.linear_gradient("L").resize(table.size)
        table = Image.composite(Image.new("RGBA", table.size, (176, 150, 118, 255)), table, tramp)
        canvas.alpha_composite(table, (0, int(h * 0.7)))
        greens = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(greens)
        for i in range(26):
            base = (w * (0.02 + 0.037 * i), h * (0.72 + 0.02 * ((i * 7) % 3)))
            angle = -2.2 + 0.18 * ((i * 5) % 7)
            gdraw.polygon(leaf_polygon(base, angle, 150, 0.2), fill=(28, 70, 46, 255))
        for i, (cx, color) in enumerate(((0.12, (178, 34, 52)), (0.85, (196, 160, 82)), (0.93, (178, 34, 52)),
                                         (0.06, (196, 160, 82)))):
            r = 46 if i % 2 else 58
            gdraw.ellipse((cx * w - r, h * 0.74 - r, cx * w + r, h * 0.74 + r), fill=(*color, 255))
        canvas.alpha_composite(greens.filter(ImageFilter.GaussianBlur(5)))
    return encode_jpeg(canvas, quality=90)


def sample_scene_path(kind: str = "festive") -> str:
    """Write the sample scene once (under the media cache) and return its local path."""
    path = cache_dir() / "samples" / f"scene_{kind}_v1.jpg"
    if not path.is_file():
        write_bytes_atomic(path, sample_scene(kind))
    return str(path)
