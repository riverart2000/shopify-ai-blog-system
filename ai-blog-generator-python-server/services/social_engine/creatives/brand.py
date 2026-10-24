"""Brand kit → concrete colours and fonts for the renderers.

* Colour tokens: primary, text, background, cta, accent, secondary (BrandKit.colors)
  plus derived tokens (white, ink, muted, line, surface, scrim) and
  `auto_on:<token>`, which picks the brand ink or white — whichever has the
  higher WCAG contrast on that token.
* Fonts are loaded once per (file, size, variation) and cached.
* `build_palette(primary)` derives Outfy's 5-colour kit from one colour.
"""
from __future__ import annotations

import colorsys
import functools
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from PIL import ImageFont

from ..settings import BrandKit

logger = logging.getLogger("ai_blog_server.social_engine.creatives")

RGB = tuple[int, int, int]

FONT_DIR = Path(__file__).resolve().parent / "fonts"
SANS_FONT = "InstrumentSans-Variable.ttf"
SERIF_FONT = "InstrumentSerif-Regular.ttf"
SERIF_ITALIC_FONT = "InstrumentSerif-Italic.ttf"

BRAND_TOKENS = ("primary", "text", "background", "cta", "accent", "secondary")
WHITE: RGB = (255, 255, 255)
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------

def hex_to_rgb(value: str) -> RGB:
    match = _HEX.match(str(value or "").strip())
    if not match:
        raise ValueError(f"Colour must be #RRGGBB, got {value!r}")
    raw = match.group(1)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def rgb_to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(round(c)))) for c in rgb[:3]))


def mix(a: RGB, b: RGB, t: float) -> RGB:
    """Linear blend: t=0 → a, t=1 → b."""
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def relative_luminance(rgb: RGB) -> float:
    def channel(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGB, b: RGB) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def readable_on(background: RGB, *, ink: RGB = (21, 37, 28), light: RGB = WHITE) -> RGB:
    """Ink or white, whichever reads better on `background` (WCAG contrast)."""
    ink_ratio = contrast_ratio(ink, background)
    light_ratio = contrast_ratio(light, background)
    best = ink if ink_ratio >= light_ratio else light
    if max(ink_ratio, light_ratio) < 4.5:
        black_ratio = contrast_ratio((0, 0, 0), background)
        if black_ratio > max(ink_ratio, light_ratio):
            return (0, 0, 0)
    return best


# ---------------------------------------------------------------------------
# Palette builder (Outfy: primary, text, CTA + 2 complementary)
# ---------------------------------------------------------------------------

def _hls(rgb: RGB) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))


def _from_hls(h: float, l: float, s: float) -> RGB:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def build_palette(primary: str) -> dict[str, str]:
    """Derive a harmonious kit from one primary colour.

    Returns every BrandColors key: `primary`, `text` (a near-black of the same
    hue), `cta` (a warm complementary accent that contrasts with the
    background), `accent` and `secondary` (the two complementary colours: a
    soft muted tint and a deep shade) plus a warm off-white `background`.
    """
    base = hex_to_rgb(primary)
    h, l, s = _hls(base)
    text = _from_hls(h, 0.12, min(s, 0.35))
    background = _from_hls(h + 0.08, 0.95, min(0.35, s * 0.6 + 0.08))
    secondary = _from_hls(h, max(0.16, l * 0.72), s * 0.95)
    accent = _from_hls(h + 0.02, 0.72, s * 0.22)
    cta_h = (h + 0.5) % 1.0
    # Pull complementary hues that land in green/blue toward a warm gold so the CTA
    # stays "premium" rather than neon.
    if 0.2 < cta_h < 0.75:
        cta_h = 0.11
    cta = _from_hls(cta_h, 0.5, 0.45)
    if contrast_ratio(cta, background) < 2.5:
        cta = _from_hls(cta_h, 0.38, 0.5)
    if contrast_ratio(base, background) < 3.0:
        base = _from_hls(h, 0.32, s)
    return {
        "primary": rgb_to_hex(base),
        "text": rgb_to_hex(text),
        "background": rgb_to_hex(background),
        "cta": rgb_to_hex(cta),
        "accent": rgb_to_hex(accent),
        "secondary": rgb_to_hex(secondary),
    }


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def font_path(name: str, *, fallback: str = SANS_FONT) -> str:
    """Resolve a font file name (bundled) or absolute path; falls back to bundled fonts."""
    candidates: list[Path] = []
    if name:
        given = Path(name).expanduser()
        candidates.append(given if given.is_absolute() else FONT_DIR / given.name)
    candidates.append(FONT_DIR / fallback)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return str(FONT_DIR / SANS_FONT)


_FONT_CACHE = threading.local()
_FONT_CACHE_LIMIT = 512


def load_font(path: str, size: int, variation: str = "") -> ImageFont.FreeTypeFont:
    """Cached font. The cache is per thread: FreeType faces must not be shared
    between the worker threads that render images concurrently."""
    cache: dict | None = getattr(_FONT_CACHE, "fonts", None)
    if cache is None:
        cache = _FONT_CACHE.fonts = {}
    key = (path, max(4, int(size)), variation)
    font = cache.get(key)
    if font is None:
        if len(cache) >= _FONT_CACHE_LIMIT:
            cache.clear()
        font = ImageFont.truetype(path, key[1])
        if variation:
            try:
                font.set_variation_by_name(variation)
            except (OSError, ValueError):
                pass
        cache[key] = font
    return font


@functools.lru_cache(maxsize=64)
def _notdef_signature(path: str, variation: str) -> bytes:
    font = load_font(path, 32, variation)
    mask = font.getmask("￿")
    return bytes(mask) + repr(mask.size).encode()


@functools.lru_cache(maxsize=8192)
def font_has_glyph(path: str, variation: str, char: str) -> bool:
    if char.isspace():
        return True
    font = load_font(path, 32, variation)
    mask = font.getmask(char)
    return (bytes(mask) + repr(mask.size).encode()) != _notdef_signature(path, variation)


_REPLACEMENTS = {
    "★": "*", "☆": "*", "→": "->", "←": "<-", "½": "1/2", "¼": "1/4", "¾": "3/4",
    " ": " ", " ": " ", "​": "", "️": "",
}


def sanitize_text(text: str, path: str, variation: str = "") -> str:
    """Replace or drop characters the font cannot draw (emoji, symbols)."""
    out: list[str] = []
    for char in text:
        if font_has_glyph(path, variation, char):
            out.append(char)
            continue
        replacement = _REPLACEMENTS.get(char)
        if replacement is not None and all(font_has_glyph(path, variation, c) for c in replacement):
            out.append(replacement)
    return re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()


# ---------------------------------------------------------------------------
# Resolved brand
# ---------------------------------------------------------------------------

def display_website(website: str) -> str:
    """'https://www.bioluxelab.com/' → 'bioluxelab.com'."""
    value = (website or "").strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.netloc or parsed.path).lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/") if parsed.netloc else ""
    return f"{host}{path}"


@dataclass
class ResolvedBrand:
    colors: dict[str, RGB]
    heading_font: str
    body_font: str
    italic_font: str
    business_name: str = ""
    website: str = ""
    cta_text: str = "Shop Now"
    slogan: str = ""
    logo_position: str = "top_left"
    logo_opacity: float = 1.0
    currency_symbol: str = "£"
    extra: dict[str, RGB] = field(default_factory=dict)

    def color(self, token: str | None, *, default: str = "text") -> RGB:
        return resolve_color(token or default, self)

    def font(self, role: str, size: int, weight: str = "") -> ImageFont.FreeTypeFont:
        path, variation = self.font_spec(role, weight)
        return load_font(path, size, variation)

    def font_spec(self, role: str, weight: str = "") -> tuple[str, str]:
        if role == "heading":
            return self.heading_font, ""
        if role == "italic":
            return self.italic_font, ""
        variation = weight or ("SemiBold" if role in {"label", "button", "price"} else "Regular")
        if not self.body_font.endswith("InstrumentSans-Variable.ttf"):
            variation = ""
        return self.body_font, variation


def resolve_brand(brand: BrandKit | None) -> ResolvedBrand:
    brand = brand or BrandKit()
    colors = {token: hex_to_rgb(getattr(brand.colors, token)) for token in BRAND_TOKENS}
    ink = colors["text"]
    background = colors["background"]
    derived = {
        "white": WHITE,
        "ink": ink,
        "muted": mix(ink, background, 0.38),
        "line": mix(ink, background, 0.82),
        # Slightly lifted card colour: whiter on light backgrounds, a touch lighter on dark ones.
        "surface": mix(background, WHITE, 0.7 if relative_luminance(background) > 0.4 else 0.1),
        "scrim": mix(colors["secondary"], (0, 0, 0), 0.25),
        "tint": mix(colors["accent"], background, 0.55),
    }
    colors.update(derived)
    heading = font_path(brand.heading_font, fallback=SERIF_FONT)
    italic_candidate = Path(heading).with_name(Path(heading).stem.replace("-Regular", "") + "-Italic.ttf")
    italic = str(italic_candidate) if italic_candidate.is_file() else font_path(SERIF_ITALIC_FONT)
    return ResolvedBrand(
        colors=colors,
        heading_font=heading,
        body_font=font_path(brand.body_font, fallback=SANS_FONT),
        italic_font=italic,
        business_name=(brand.business_name or "").strip(),
        website=display_website(brand.website),
        cta_text=(brand.cta_text or "Shop Now").strip() or "Shop Now",
        slogan=(brand.slogan or "").strip(),
        logo_position=brand.logo_position,
        logo_opacity=float(brand.logo_opacity),
        currency_symbol=brand.currency_symbol or "",
    )


def on_color(background: RGB, palette: Mapping[str, RGB]) -> RGB:
    """The palette's own text or background colour that reads best on `background`
    (falls back to white/black when neither reaches WCAG AA 4.5:1)."""
    candidates = [palette["text"], palette["background"]]
    best = max(candidates, key=lambda c: contrast_ratio(c, background))
    if contrast_ratio(best, background) >= 4.5:
        return best
    return max([*candidates, WHITE, (0, 0, 0)], key=lambda c: contrast_ratio(c, background))


def resolve_color(token: str, brand: ResolvedBrand, *, colors: Mapping[str, RGB] | None = None) -> RGB:
    """Turn a colour token into RGB.

    Besides palette tokens and `#rrggbb`, palette-safe forms keep any themed
    palette legible: `on:<bg>` (best of the palette's text/background colours on
    <bg>), `soft:<bg>` (a quieter `on:` colour that still reaches 4.5:1),
    `pick:<bg>:<c1>,<c2>,...` (first candidate with ≥3:1 on <bg>, `on` allowed)
    and `auto_on:<bg>` (brand ink or white).
    """
    palette = colors or brand.colors
    value = (token or "text").strip()
    if value.startswith("on:"):
        return on_color(resolve_color(value[3:], brand, colors=palette), palette)
    if value.startswith("soft:"):
        background = resolve_color(value[5:], brand, colors=palette)
        strong = on_color(background, palette)
        for amount in (0.32, 0.22, 0.12):
            candidate = mix(strong, background, amount)
            if contrast_ratio(candidate, background) >= 4.5:
                return candidate
        return strong
    if value.startswith("pick:"):
        background_token, _, names = value[5:].partition(":")
        background = resolve_color(background_token, brand, colors=palette)
        for name in [n.strip() for n in names.split(",") if n.strip()]:
            candidate = on_color(background, palette) if name == "on" else resolve_color(name, brand, colors=palette)
            if contrast_ratio(candidate, background) >= 3.0:
                return candidate
        return on_color(background, palette)
    if value.startswith("auto_on:"):
        background = resolve_color(value.split(":", 1)[1], brand, colors=palette)
        return readable_on(background, ink=palette.get("ink", palette["text"]))
    if value.startswith("#"):
        try:
            return hex_to_rgb(value)
        except ValueError:
            return palette["text"]
    if "@" in value:
        # "<token>@<0-1>": token blended toward the background, e.g. "primary@0.2".
        name, _, amount = value.partition("@")
        try:
            return mix(palette["background"], resolve_color(name, brand, colors=palette), float(amount))
        except ValueError:
            return palette.get(name, palette["text"])
    if value in brand.extra:
        return brand.extra[value]
    return palette.get(value, palette["text"])
