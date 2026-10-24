"""Built-in creative templates ("modern botanical" design system).

Each template declares which content types it serves and builds a layer spec
(see layout.py) for every format it supports. Builders work in pixels of the
target format and emit RELATIVE boxes, so the stored JSON specs are readable
and resolution-independent.

Type scale (px at 1080 wide, multiplied per format): eyebrow 22 · title 72 ·
body 30 · price 44 · button 24 · small 21. Margins come from `Frame` (story
keeps clear of the Instagram/TikTok UI at the top and bottom).
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import FORMAT_SIZES, ContentType, MediaFormat

IMAGE_FORMATS: tuple[str, ...] = (
    MediaFormat.SQUARE, MediaFormat.PORTRAIT, MediaFormat.STORY, MediaFormat.PIN, MediaFormat.LANDSCAPE,
)
VIDEO_FORMATS: tuple[str, ...] = (MediaFormat.STORY, MediaFormat.PORTRAIT, MediaFormat.SQUARE)

EYEBROW, TITLE, TITLE_MIN, BODY, PRICE, CTA, SMALL, BAR = 22, 72, 42, 30, 44, 24, 21, 50


@dataclass(frozen=True)
class Frame:
    fmt: str
    W: int
    H: int
    top: int
    bottom: int
    side: int
    mult: float

    @property
    def landscape(self) -> bool:
        return self.W > self.H

    def r(self, x: float, y: float, w: float, h: float) -> list[float]:
        return [round(x / self.W, 4), round(y / self.H, 4), round(max(0.0, w) / self.W, 4), round(max(0.0, h) / self.H, 4)]

    def s(self, px: float) -> float:
        """Design px (1080 basis) → size relative to canvas width."""
        return round(px * self.mult / self.W, 5)

    def px(self, design_px: float) -> float:
        return design_px * self.mult


def _frame(fmt: str, top: int, bottom: int, side: int, mult: float) -> Frame:
    w, h = FORMAT_SIZES[fmt]
    return Frame(fmt, w, h, top, bottom, side, mult)


FRAMES: dict[str, Frame] = {
    MediaFormat.SQUARE: _frame(MediaFormat.SQUARE, 64, 64, 72, 1.0),
    MediaFormat.PORTRAIT: _frame(MediaFormat.PORTRAIT, 80, 80, 80, 1.04),
    MediaFormat.STORY: _frame(MediaFormat.STORY, 220, 300, 88, 1.2),
    MediaFormat.PIN: _frame(MediaFormat.PIN, 72, 72, 72, 0.96),
    MediaFormat.LANDSCAPE: _frame(MediaFormat.LANDSCAPE, 52, 52, 60, 0.8),
}


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def background(style: str = "solid", color: str = "background", **extra: Any) -> dict:
    return {"type": "background", "box": [0, 0, 1, 1], "style": style, "color": color, **extra}


ACCENT_ON_BG = "pick:background:primary,on"   # brand primary when legible on the background, else the text colour


def eyebrow(f: Frame, text: str = "{eyebrow}", *, fallback: tuple[str, ...] = (), color: str = ACCENT_ON_BG,
            align: str = "left", size: float = EYEBROW, **extra: Any) -> dict:
    layer = {"type": "text", "text": text, "role": "label", "weight": "SemiBold", "size": f.s(size),
             "min_size": f.s(15), "uppercase": True, "tracking": 0.16, "max_lines": 1, "color": color,
             "align": align, "line_height": 1.2}
    if fallback:
        layer["fallback"] = list(fallback)
    layer.update(extra)
    return layer


def heading(f: Frame, text: str = "{title}", *, size: float = TITLE, min_size: float = TITLE_MIN, lines: int = 2,
            color: str = "on:background", align: str = "left", **extra: Any) -> dict:
    return {"type": "text", "text": text, "role": "heading", "size": f.s(size), "min_size": f.s(min_size),
            "max_lines": lines, "line_height": 1.04, "color": color, "align": align, **extra}


def body(f: Frame, text: str, *, lines: int = 2, color: str = "muted", align: str = "left", size: float = BODY,
         **extra: Any) -> dict:
    return {"type": "text", "text": text, "role": "body", "size": f.s(size), "min_size": f.s(size * 0.8),
            "max_lines": lines, "line_height": 1.34, "color": color, "align": align, **extra}


def price(f: Frame, *, color: str = "on:background", compare_color: str = "muted", size: float = PRICE, **extra: Any) -> dict:
    return {"type": "price", "size": f.s(size), "color": color, "compare_color": compare_color,
            "when": "show_price", **extra}


def button(f: Frame, text: str = "{cta}", *, fill: str = "cta", color: str | None = None, outline: str | None = None,
           arrow: bool = False, size: float = CTA, **extra: Any) -> dict:
    layer = {"type": "button", "text": text, "size": f.s(size), "fill": fill, "arrow": arrow, **extra}
    if color:
        layer["color"] = color
    if outline:
        layer["outline"] = outline
    return layer


def price_row(f: Frame, *, price_color: str = "on:background", compare_color: str = "muted", fill: str = "cta",
              button_color: str | None = None, outline: str | None = None, justify: str = "between",
              cta_text: str = "{cta}", gap_before: float = 30, arrow: bool = False, with_coupon: bool = False,
              coupon_border: str = "primary", coupon_color: str = "on:background", coupon_fill: str = "surface") -> dict:
    children = [price(f, color=price_color, compare_color=compare_color)]
    if with_coupon:
        children.append(inline_coupon(f, border=coupon_border, color=coupon_color, fill=coupon_fill,
                                      label_color=compare_color))
    children.append(button(f, cta_text, fill=fill, color=button_color, outline=outline, arrow=arrow))
    return {"type": "row", "justify": justify, "gap": f.s(24), "gap_before": f.s(gap_before), "children": children}


def inline_coupon(f: Frame, *, border: str = "primary", color: str = "on:background", fill: str = "surface",
                  label_color: str = "muted", size: float = 23, **extra: Any) -> dict:
    return {"type": "coupon", "text": "{coupon_code}", "label": "Use code", "size": f.s(size), "border": border,
            "fill": fill, "color": color, "label_color": label_color, "when": "has:coupon_code", **extra}


def logo(f: Frame, *, allowed: tuple[str, ...] = ("top_left", "top_right"), on: str = "background",
         band_top: list[float] | None = None, band_bottom: list[float] | None = None, max_w: float = 230,
         max_h: float = BAR, margin_x: float | None = None, color: str | None = None) -> dict:
    layer: dict[str, Any] = {
        "type": "logo", "position": "brand", "allowed": list(allowed), "max_w": f.s(max_w), "max_h": f.s(max_h),
        "margin_x": round((margin_x if margin_x is not None else f.side) / f.W, 4),
        "margin_y": round(f.top / f.H, 4), "margin_bottom": round(f.bottom / f.H, 4), "on": on,
        "fallback": "wordmark",
    }
    if band_top:
        layer["band_top"] = band_top
    if band_bottom:
        layer["band_bottom"] = band_bottom
    if color:
        layer["color"] = color
    return layer


def top_bar(f: Frame, *, text: str = "{website}", on: str = "background", color: str = "muted",
            x0: float | None = None, x1: float | None = None) -> list[dict]:
    left = f.side if x0 is None else x0
    right = f.W - f.side if x1 is None else x1
    band = f.r(left, f.top, right - left, f.px(BAR))
    return [
        logo(f, on=on, band_top=band, margin_x=None if x0 is None else (f.W - right if left > f.W / 2 else left)),
        {"type": "footer", "box": band, "text": text, "align": "opposite_logo", "align_default": "center",
         "valign": "middle", "size": f.s(SMALL), "color": color, "tracking": 0.06},
    ]


def bar_bottom(f: Frame) -> float:
    return f.top + f.px(BAR)


def circle_badge(f: Frame, text: str = "{badge}", *, anchor: str = "top_left", fill: str = "cta",
                 diameter: float = 150, when: Any = None, inset: float = 26, scale: float = 0.36,
                 fallback: tuple[str, ...] = ()) -> dict:
    layer = {"type": "badge", "shape": "circle", "text": text, "fill": fill, "anchor": anchor,
             "w": f.s(diameter), "h": f.s(diameter), "inset": f.s(inset), "text_scale": scale,
             "align": "center", "valign": "middle"}
    if fallback:
        layer["fallback"] = list(fallback)
    if when:
        layer["when"] = when
    return layer


def pill_badge(f: Frame, text: str, *, anchor: str = "top_left", fill: str = "background", color: str | None = None,
               when: Any = None, inset: float = 26, size: float = 20) -> dict:
    layer = {"type": "badge", "shape": "pill", "text": text, "fill": fill, "anchor": anchor, "w": f.s(420),
             "h": f.s(size * 2.4), "inset": f.s(inset), "size": f.s(size),
             "align": "right" if anchor.endswith("right") else "left", "valign": "top"}
    if color:
        layer["color"] = color
    if when:
        layer["when"] = when
    return layer


def ribbon(f: Frame, text: str, *, fill: str = "primary", width: float = 400, height: float = 66, top: float = 40) -> dict:
    return {"type": "badge", "shape": "ribbon", "text": text, "fill": fill, "anchor": "top_left",
            "w": f.s(width), "h": f.s(height), "inset_x": 0, "inset_y": f.s(top), "align": "left", "valign": "top"}


def sticker(f: Frame, text: str = "{sticker}", *, anchor: str = "top_right", size: float = 190, style: str | None = None,
            fill: str = "cta", inset: float = 22, when: Any = "has:sticker") -> dict:
    layer = {"type": "sticker", "text": text, "anchor": anchor, "w": f.s(size * 1.8), "h": f.s(size),
             "inset": f.s(inset), "fill": fill, "align": "right" if anchor.endswith("right") else "left",
             "valign": "top"}
    if style:
        layer["style"] = style
    if when:
        layer["when"] = when
    return layer


def product_card(f: Frame, *, card: str = "auto", overlays: list[dict] | None = None, radius: float = 26,
                 padding: float = 0.09, fit: str = "auto", **extra: Any) -> dict:
    return {"type": "image", "flex": 1, "card": card, "radius": f.s(radius), "shadow": True, "padding": padding,
            "fit": fit, "overlays": overlays or [], **extra}


def sprig(f: Frame, x: float, y: float, w: float, h: float, *, rotation: float = 0, color: str = "primary",
          opacity: float = 0.14, mirror: bool = False, leaves: int = 7, style: str = "fill") -> dict:
    return {"type": "botanical", "box": f.r(x, y, w, h), "rotation": rotation, "color": color, "opacity": opacity,
            "mirror": mirror, "leaves": leaves, "style": style}


def dots(f: Frame, *, color: str = ACCENT_ON_BG, align: str = "center") -> dict:
    return {"type": "dots", "size": f.s(12), "color": color, "align": align, "gap_before": f.s(30), "when": "slides>1"}


# ---------------------------------------------------------------------------
# Archetypes
# ---------------------------------------------------------------------------

OverlayFactory = Callable[[Frame], list[dict]]


def _no_overlays(f: Frame) -> list[dict]:
    return []


@dataclass(frozen=True)
class Copy:
    """What a product layout says (placeholders with fallbacks)."""
    eyebrow: str = "{eyebrow}"
    eyebrow_fallback: tuple[str, ...] = ("{collection}",)
    title: str = "{title}"
    title_fallback: tuple[str, ...] = ()
    body: str = ""
    body_lines: int = 2
    cta: str = "{cta}"
    arrow: bool = False
    show_price: bool = True
    coupon: bool = False
    countdown: bool = False          # show texts["countdown_label"] as a pill when present


def countdown_pill(f: Frame, *, fill: str = "primary", align: str = "left", **extra: Any) -> dict:
    return {"type": "badge", "shape": "pill", "text": "{countdown_label}", "fill": fill, "size": f.s(19),
            "align": align, "when": "has:countdown_label", "optional": True, **extra}


def framed(f: Frame, copy: Copy, *, overlays: OverlayFactory = _no_overlays, bg_style: str = "solid",
           card: str = "auto", carousel: bool = False, centered: bool = False, fit: str = "auto",
           title_size: float = TITLE, source: str = "product") -> list[dict]:
    """Classic framed card: brand bar, product card, eyebrow, serif title, price + CTA."""
    if f.landscape:
        return _framed_landscape(f, copy, overlays=overlays, bg_style=bg_style, card=card, carousel=carousel, fit=fit,
                                 source=source)
    align = "center" if centered else "left"
    lines = 3 if f.fmt == MediaFormat.STORY else 2
    children: list[dict] = [
        eyebrow(f, copy.eyebrow, fallback=copy.eyebrow_fallback, align=align),
        heading(f, copy.title, fallback=list(copy.title_fallback), lines=lines, align=align, gap_before=f.s(14), size=title_size),
    ]
    if copy.body:
        children.append(body(f, copy.body, lines=copy.body_lines, align=align, gap_before=f.s(14), optional=True,
                             when=f"has:{copy.body.strip('{}')}" if copy.body.startswith("{") else None))
    if copy.countdown:
        children.append(countdown_pill(f, align=align, gap_before=f.s(16)))
    if copy.show_price:
        children.append(price_row(f, justify="center" if centered else "between", cta_text=copy.cta, arrow=copy.arrow,
                                  with_coupon=copy.coupon))
    else:
        children.append(button(f, copy.cta, arrow=copy.arrow, gap_before=f.s(30), align=align))
    if carousel:
        children.append(dots(f, align=align))
    top = bar_bottom(f) + f.px(28)
    return [
        background(bg_style),
        *top_bar(f),
        {"type": "column", "box": f.r(f.side, top, f.W - 2 * f.side, f.H - f.bottom - top), "gap": f.s(36),
         "min_flex": 0.42, "align": align, "children": [
             product_card(f, card=card, overlays=overlays(f), fit=fit, source=source),
             {"type": "stack", "gap": 0, "align": align, "children": children},
         ]},
    ]


def _framed_landscape(f: Frame, copy: Copy, *, overlays: OverlayFactory, bg_style: str, card: str,
                      carousel: bool, fit: str, source: str) -> list[dict]:
    card_w = f.W * 0.5 - f.side - 10
    col_x = f.W * 0.5 + 34
    col_w = f.W - f.side - col_x
    children: list[dict] = [
        eyebrow(f, copy.eyebrow, fallback=copy.eyebrow_fallback),
        heading(f, copy.title, fallback=list(copy.title_fallback), lines=3, gap_before=f.s(14)),
    ]
    if copy.body:
        children.append(body(f, copy.body, lines=2, gap_before=f.s(14), optional=True,
                             when=f"has:{copy.body.strip('{}')}" if copy.body.startswith("{") else None))
    if copy.countdown:
        children.append(countdown_pill(f, gap_before=f.s(16)))
    if copy.show_price:
        children.append(price(f, gap_before=f.s(26)))
    if copy.coupon:
        children.append(inline_coupon(f, gap_before=f.s(22)))
    children.append(button(f, copy.cta, arrow=copy.arrow, gap_before=f.s(26)))
    if carousel:
        children.append(dots(f, align="left"))
    content_top = bar_bottom(f) + f.px(24)
    return [
        background(bg_style),
        logo(f, allowed=("top_right", "bottom_right"), band_top=f.r(col_x, f.top, col_w, f.px(BAR)),
             band_bottom=f.r(col_x, f.H - f.bottom - f.px(BAR), col_w, f.px(BAR))),
        {"type": "footer", "box": f.r(col_x, f.top, col_w, f.px(BAR)), "align": "opposite_logo",
         "align_default": "left", "valign": "middle", "size": f.s(SMALL)},
        {"type": "image", "box": f.r(f.side, f.top, card_w, f.H - f.top - f.bottom), "card": card,
         "radius": f.s(24), "shadow": True, "padding": 0.08, "fit": fit, "source": source, "overlays": overlays(f)},
        {"type": "stack", "box": f.r(col_x, content_top, col_w, f.H - f.bottom - content_top), "valign": "middle",
         "gap": 0, "children": children},
    ]


def editorial(f: Frame, copy: Copy, *, overlays: OverlayFactory = _no_overlays, panel: str = "secondary",
              carousel: bool = False, split: float | None = None, fit: str = "auto",
              source: str = "product") -> list[dict]:
    """Editorial: full-bleed photo with a solid brand panel carrying the copy."""
    text, muted, accent = f"on:{panel}", f"soft:{panel}", f"pick:{panel}:cta,accent,on"
    if f.landscape:
        img_w = f.W * 0.54
        col_x = img_w + f.side
        col_w = f.W - f.side - col_x
        children = [
            eyebrow(f, copy.eyebrow, fallback=copy.eyebrow_fallback, color=accent),
            heading(f, copy.title, fallback=list(copy.title_fallback), lines=3, color=text, gap_before=f.s(14)),
        ]
        if copy.show_price:
            children.append(price(f, color=text, compare_color=muted, gap_before=f.s(24)))
        children.append(button(f, copy.cta, arrow=copy.arrow, gap_before=f.s(26)))
        if carousel:
            children.append(dots(f, color=accent, align="left"))
        return [
            background("solid", panel, allow_override=False),
            {"type": "image", "box": f.r(0, 0, img_w, f.H), "fit": fit, "padding": 0.1, "source": source,
             "overlays": overlays(f)},
            logo(f, allowed=("top_right", "bottom_right"), on=panel, band_top=f.r(col_x, f.top, col_w, f.px(BAR))),
            {"type": "footer", "box": f.r(col_x, f.H - f.bottom - f.px(BAR), col_w, f.px(BAR)), "align": "left",
             "valign": "middle", "size": f.s(SMALL), "color": muted, "avoid_logo": True},
            {"type": "stack", "box": f.r(col_x, bar_bottom(f) + f.px(10), col_w, f.H - f.bottom - f.px(BAR) - bar_bottom(f) - f.px(20)),
             "valign": "middle", "gap": 0, "children": children},
        ]
    ratio = split or {MediaFormat.SQUARE: 0.56, MediaFormat.PORTRAIT: 0.6, MediaFormat.STORY: 0.56}.get(f.fmt, 0.58)
    split_y = f.H * ratio
    band = f.r(f.side, f.H - f.bottom - f.px(BAR), f.W - 2 * f.side, f.px(BAR))
    children = [
        eyebrow(f, copy.eyebrow, fallback=copy.eyebrow_fallback, color=accent),
        heading(f, copy.title, fallback=list(copy.title_fallback), lines=2 if f.fmt != MediaFormat.STORY else 3, color=text, gap_before=f.s(16)),
    ]
    if copy.body:
        children.append(body(f, copy.body, lines=2, color=muted, gap_before=f.s(14), optional=True,
                             when=f"has:{copy.body.strip('{}')}" if copy.body.startswith("{") else None))
    if copy.show_price:
        children.append(price_row(f, price_color=text, compare_color=muted, cta_text=copy.cta, arrow=copy.arrow))
    else:
        children.append(button(f, copy.cta, arrow=copy.arrow, gap_before=f.s(30)))
    if carousel:
        children.append(dots(f, color=accent, align="left"))
    stack_top = split_y + f.px(52)
    stack_bottom = f.H - f.bottom - f.px(BAR) - f.px(30)
    return [
        background("solid", panel, allow_override=False),
        {"type": "image", "box": f.r(0, 0, f.W, split_y), "fit": fit, "padding": 0.1, "source": source,
         "overlays": overlays(f)},
        logo(f, allowed=("bottom_left", "bottom_right"), on=panel, band_bottom=band),
        {"type": "footer", "box": band, "align": "opposite_logo", "align_default": "left", "valign": "middle",
         "size": f.s(SMALL), "color": muted},
        {"type": "stack", "box": f.r(f.side, stack_top, f.W - 2 * f.side, stack_bottom - stack_top),
         "valign": "middle", "gap": 0, "children": children},
    ]


def bold_promo(f: Frame, *, display: str, display_fallback: tuple[str, ...] = (), kicker: str = "",
               kicker_fallback: tuple[str, ...] = (), unit: str = "", unit_when: Any = None,
               body_text: str = "{promo_text}", overlays: OverlayFactory = _no_overlays,
               panel: str = "secondary", countdown: bool = False) -> list[dict]:
    """Bold brand-colour promo: big serif display figure, product card, coupon, CTA."""
    text, muted, accent = f"on:{panel}", f"soft:{panel}", f"pick:{panel}:cta,on"
    display_layer = heading(f, display, size=200 if not f.landscape else 150, min_size=90, lines=1, color=accent,
                            align="center", fallback=list(display_fallback), line_height=0.95)
    head: list[dict] = []
    if kicker:
        head.append(eyebrow(f, kicker, fallback=kicker_fallback, color=muted, align="center"))
    head.append(dict(display_layer, gap_before=f.s(10)))
    if unit:
        head.append(eyebrow(f, unit, color=text, align="center", size=26, gap_before=f.s(4),
                            **({"when": unit_when} if unit_when else {})))
    if countdown:
        head.append(countdown_pill(f, fill=text, align="center", gap_before=f.s(14)))
    copy_children = [
        heading(f, "{title}", size=54, min_size=34, lines=2, color=text, align="center", gap_before=f.s(30)),
        body(f, body_text, lines=2, color=muted, align="center", gap_before=f.s(10), optional=True,
             when=f"has:{body_text.strip('{}')}" if body_text.startswith("{") else None),
        {"type": "row", "justify": "center", "gap": f.s(24), "gap_before": f.s(24), "children": [
            price(f, color=accent, compare_color=muted),
            inline_coupon(f, border=accent, color=text, fill="none", label_color=muted),
            button(f, fill="cta"),
        ]},
    ]
    if f.landscape:
        row = copy_children[-1]
        copy_children[-1] = dict(row, children=[row["children"][0], row["children"][2]])
        copy_children.append(inline_coupon(f, border=accent, color=text, fill="none", label_color=muted,
                                           gap_before=f.s(20)))
    layers: list[dict] = [background("pattern", panel, pattern_color="accent", pattern_opacity=0.08, allow_override=False)]
    if f.landscape:
        col_w = f.W * 0.46
        layers += [
            logo(f, allowed=("top_left", "bottom_left"), on=panel, max_h=44),
            {"type": "image", "box": f.r(f.W * 0.52, f.top, f.W * 0.48 - f.side, f.H - f.top - f.bottom),
             "card": "auto", "radius": f.s(24), "shadow": True, "padding": 0.08, "overlays": overlays(f)},
            {"type": "stack", "box": f.r(f.side, bar_bottom(f) + f.px(6), col_w, f.H - f.bottom - bar_bottom(f) - f.px(6)),
             "valign": "middle", "gap": 0, "align": "center", "children": head + [dict(c) for c in copy_children]},
        ]
        return layers
    top = bar_bottom(f) + f.px(36)
    layers += [
        *top_bar(f, on=panel, color=muted),
        {"type": "column", "box": f.r(f.side, top, f.W - 2 * f.side, f.H - f.bottom - top), "gap": f.s(30),
         "min_flex": 0.3, "align": "center", "children": [
             {"type": "stack", "gap": 0, "align": "center", "children": head},
             product_card(f, card="auto", overlays=overlays(f), radius=24),
             {"type": "stack", "gap": 0, "align": "center", "children": copy_children},
         ]},
    ]
    return layers


def grid_layout(f: Frame, *, kicker: str, kicker_fallback: tuple[str, ...], title: str,
                title_fallback: tuple[str, ...], source: str = "products", captions: bool = True,
                arrangement: str = "auto", panel: str = "background", max_cells: int = 4,
                cta: str = "{cta}", show_price_row: bool = False, bg_style: str = "solid") -> list[dict]:
    """Headline + collage grid + CTA (collages, round-ups, single-product multi-image)."""
    dark = panel in {"secondary", "primary"}
    text = f"on:{panel}"
    soft = f"soft:{panel}" if dark else "muted"
    kicker_color = f"pick:{panel}:cta,on" if dark else ACCENT_ON_BG
    grid: dict[str, Any] = {
        "type": "grid", "source": source, "max": max_cells, "captions": captions, "arrangement": arrangement,
        "gap": f.s(22 if not f.landscape else 18), "radius": f.s(20), "card": "auto", "padding": 0.08,
        "caption_size": f.s(25), "price_size": f.s(24), "caption_lines": 2, "caption_color": text,
        "price_color": kicker_color, "shadow": not dark, "aspect": 0.95,
    }
    header = [
        eyebrow(f, kicker, fallback=kicker_fallback, color=kicker_color, align="center" if not f.landscape else "left"),
        heading(f, title, fallback=list(title_fallback), size=64, min_size=40, lines=2, color=text,
                align="center" if not f.landscape else "left", gap_before=f.s(12)),
    ]
    footer_children: list[dict]
    if show_price_row:
        footer_children = [price_row(f, price_color=text, compare_color=soft,
                                     justify="center" if not f.landscape else "between", gap_before=0)]
    else:
        footer_children = [button(f, cta, fill="cta")]
    layers: list[dict] = [background(bg_style if not dark else "solid", panel, allow_override=not dark)]
    if f.landscape:
        col_w = f.W * 0.36
        grid_x = f.side + col_w + f.px(40)
        layers += [
            logo(f, allowed=("top_left", "bottom_left"), on=panel, max_w=200),
            {"type": "stack", "box": f.r(f.side, bar_bottom(f) + f.px(10), col_w, f.H - f.bottom - bar_bottom(f) - f.px(10)),
             "valign": "middle", "gap": 0, "children": header + [dict(c, gap_before=f.s(28), align="left") for c in footer_children] + [
                 {"type": "footer", "align": "left", "gap_before": f.s(22), "color": soft,
                  "size": f.s(SMALL)}]},
            dict(grid, box=f.r(grid_x, f.top, f.W - f.side - grid_x, f.H - f.top - f.bottom), caption_lines=1,
                 caption_size=f.s(24), aspect=1.0),
        ]
        return layers
    top = bar_bottom(f) + f.px(26)
    layers += [
        *top_bar(f, on=panel, color=soft),
        {"type": "column", "box": f.r(f.side, top, f.W - 2 * f.side, f.H - f.bottom - top), "gap": f.s(34),
         "min_flex": 0.45, "align": "center", "children": [
             {"type": "stack", "gap": 0, "align": "center", "children": header},
             dict(grid, flex=1),
             {"type": "stack", "gap": 0, "align": "center", "children": footer_children},
         ]},
    ]
    return layers


def text_card(f: Frame, *, kicker: str, kicker_fallback: tuple[str, ...] = (), main: str,
              main_fallback: tuple[str, ...] = (), main_size: float = 70, main_lines: int = 6,
              italic: bool = False, quote_mark: bool = False, sub: str = "", sub_fallback: tuple[str, ...] = (),
              choices: list[str] | None = None, footnote: str = "", panel: str = "background",
              bg_style: str = "pattern", badge_kicker: bool = False, stars: bool = False,
              thumb: bool = False, cta: str = "", sub_style: str = "label") -> list[dict]:
    """Brand content: quote, fact, poll, quiz, review and greeting cards."""
    dark = panel in {"secondary", "primary"}
    text = f"on:{panel}"
    muted = f"soft:{panel}" if dark else "muted"
    accent = f"pick:{panel}:cta,on" if dark else ACCENT_ON_BG
    align = "center"
    boost = {MediaFormat.STORY: 1.2, MediaFormat.PORTRAIT: 1.1, MediaFormat.PIN: 1.12}.get(f.fmt, 1.0)
    main_size *= boost
    children: list[dict] = []
    if stars:
        children.append({"type": "stars", "size": f.s(40), "color": f"pick:{panel}:cta,on", "empty_color": "line" if not dark else "accent",
                         "rating": "{rating}", "align": align})
    if badge_kicker:
        children.append({"type": "badge", "shape": "pill", "text": kicker, "fill": "cta", "size": f.s(20),
                         "align": align, "gap_before": f.s(6)})
    elif kicker:
        children.append(eyebrow(f, kicker, fallback=kicker_fallback, color=accent, align=align,
                                gap_before=f.s(18) if stars else 0))
    if quote_mark:
        children.append({"type": "text", "text": "“", "role": "heading", "size": f.s(230), "max_lines": 1,
                         "line_height": 1.0, "tight": True, "color": "cta", "align": align, "gap_before": f.s(10)})
    children.append({"type": "text", "text": main, "fallback": list(main_fallback), "role": "italic" if italic else "heading",
                     "size": f.s(main_size), "min_size": f.s(main_size * 0.5), "max_lines": main_lines,
                     "line_height": 1.12, "color": text, "align": align, "gap_before": f.s(18 if quote_mark else 26)})
    if choices:
        children.append({"type": "choices", "options": choices, "size": f.s(31 * boost), "gap": f.s(18),
                         "fill": "surface" if not dark else "secondary", "border": "line" if not dark else "accent",
                         "letter_fill": "primary" if not dark else "cta", "color": text,
                         "direction": "column", "gap_before": f.s(40)})
    if sub:
        keys = "|".join(p.strip("{}") for p in (sub, *sub_fallback) if p.startswith("{"))
        children.append({"type": "divider", "length": f.s(64), "thickness": f.s(2), "color": "cta",
                         "align": align, "gap_before": f.s(32), "when": f"has:{keys}" if keys else None})
        if sub_style == "body":
            children.append(body(f, sub, fallback=list(sub_fallback), lines=3, color=muted, align=align, size=32,
                                 gap_before=f.s(24)))
        else:
            children.append(eyebrow(f, sub, fallback=sub_fallback, color=muted, align=align, size=21, tracking=0.12,
                                    gap_before=f.s(22)))
    if thumb:
        children.append({"type": "row", "justify": "center", "gap": f.s(22), "gap_before": f.s(40), "children": [
            {"type": "thumb", "w": f.s(112), "h": f.s(112), "radius": f.s(16), "card": "auto"},
            {"type": "stack", "w": f.s(420), "gap": 0, "children": [
                heading(f, "{title}", size=32, min_size=24, lines=2, color=text),
                price(f, size=28, color=accent, gap_before=f.s(6)),
            ]},
        ]})
    if footnote:
        children.append(eyebrow(f, footnote, color=muted, align=align, size=20, tracking=0.14, gap_before=f.s(40)))
    if cta:
        children.append(button(f, cta, fill="cta", gap_before=f.s(40), align=align))
    deco_color = "accent" if dark else "primary"
    deco_opacity = 0.22 if dark else 0.12
    if f.landscape:
        sprigs = [
            sprig(f, -f.W * 0.02, f.H * 0.38, f.W * 0.16, f.H * 0.7, rotation=-28, color=deco_color, opacity=deco_opacity),
            sprig(f, f.W * 0.86, -f.H * 0.08, f.W * 0.16, f.H * 0.7, rotation=152, color=deco_color, opacity=deco_opacity, mirror=True),
        ]
        content = f.r(f.W * 0.14, f.top + f.px(BAR) + f.px(6), f.W * 0.72, f.H - f.bottom - f.top - f.px(BAR) * 2 - f.px(12))
    else:
        sw = f.W * 0.34
        sprigs = [
            sprig(f, -sw * 0.18, f.H - f.bottom - sw * 1.1, sw, sw * 1.5, rotation=-32, color=deco_color, opacity=deco_opacity),
            sprig(f, f.W - sw * 0.82, f.top - sw * 0.35, sw, sw * 1.5, rotation=148, color=deco_color, opacity=deco_opacity, mirror=True),
        ]
        top = bar_bottom(f) + f.px(30)
        content = f.r(f.side + f.px(16), top, f.W - 2 * f.side - f.px(32), f.H - f.bottom - f.px(BAR) - f.px(30) - top)
    band_bottom = f.r(f.side, f.H - f.bottom - f.px(BAR), f.W - 2 * f.side, f.px(BAR))
    return [
        background(bg_style, panel, pattern_color=deco_color, allow_override=not dark),
        *sprigs,
        logo(f, allowed=("top_left", "top_right", "bottom_left", "bottom_right"), on=panel,
             band_top=f.r(f.side, f.top, f.W - 2 * f.side, f.px(BAR)), band_bottom=band_bottom),
        {"type": "footer", "box": band_bottom, "align": "opposite_logo", "align_default": "center", "valign": "middle",
         "size": f.s(SMALL), "color": muted},
        {"type": "stack", "box": content, "valign": "middle", "align": align, "gap": 0, "children": children},
    ]


# ---------------------------------------------------------------------------
# Template registry entries
# ---------------------------------------------------------------------------

Builder = Callable[[Frame], list[dict]]


@dataclass(frozen=True)
class TemplateDef:
    key: str
    name: str
    description: str
    content_types: tuple[str, ...]
    builder: Builder | None = None
    kind: str = "image"
    formats: tuple[str, ...] = IMAGE_FORMATS
    defaults: dict[str, str] = field(default_factory=dict)
    sample_texts: dict[str, str] = field(default_factory=dict)
    min_products: int = 1
    max_products: int = 1
    needs_images: bool = True
    required_texts: tuple[str, ...] = ()
    carousel: str = ""                 # "" | "auto" (products if several, else images)
    video: dict[str, Any] = field(default_factory=dict)
    style: str = ""

    @property
    def primary_content_type(self) -> str:
        return self.content_types[0]

    def spec(self, fmt: str) -> dict:
        return _cached_spec(self.key, fmt)

    def specs(self) -> dict[str, dict]:
        return {fmt: self.spec(fmt) for fmt in self.formats if self.builder is not None}

    def to_record(self) -> dict:
        """Row for `social_templates` (spec_json holds everything needed to render)."""
        return {
            "key": self.key, "name": self.name, "content_type": self.primary_content_type, "kind": self.kind,
            "formats": list(self.formats), "is_builtin": True,
            "spec": {
                "version": SPEC_VERSION, "description": self.description, "style": self.style,
                "content_types": list(self.content_types), "defaults": dict(self.defaults),
                "min_products": self.min_products, "max_products": self.max_products,
                "needs_images": self.needs_images, "required_texts": list(self.required_texts),
                "carousel": self.carousel, "video": dict(self.video), "formats": self.specs(),
            },
        }


SPEC_VERSION = 1
_REGISTRY: dict[str, TemplateDef] = {}


@functools.lru_cache(maxsize=512)
def _cached_spec(key: str, fmt: str) -> dict:
    template = _REGISTRY[key]
    if template.builder is None or fmt not in FRAMES:
        return {"layers": []}
    return {"layers": template.builder(FRAMES[fmt])}


def register(template: TemplateDef) -> TemplateDef:
    _REGISTRY[template.key] = template
    return template


CT = ContentType


def _ov_new(f: Frame) -> list[dict]:
    return [circle_badge(f, "{badge}", diameter=136, scale=0.3)]


def _ov_sale(f: Frame) -> list[dict]:
    return [circle_badge(f, "{badge}", fallback=("{discount}",), when="has:badge|discount", diameter=150, scale=0.42),
            sticker(f)]


def _ov_sale_bold(f: Frame) -> list[dict]:
    # The discount is already the display figure; a promotion's badge override still shows.
    return [circle_badge(f, "{badge}", when="has:badge", diameter=136, scale=0.42),
            sticker(f, anchor="top_right", size=170)]


def _ov_back(f: Frame) -> list[dict]:
    return [ribbon(f, "{badge}")]


def _ov_potd(f: Frame) -> list[dict]:
    return [sticker(f, "{sticker}", anchor="top_right", size=200, style="seal", when=None)]


def _ov_countdown(f: Frame) -> list[dict]:
    return [circle_badge(f, "{badge}", fallback=("{discount}",), when="has:badge|discount", diameter=140, scale=0.42),
            sticker(f, size=170)]


def _ov_holiday(f: Frame) -> list[dict]:
    return [sticker(f)]


def _ov_editorial_new(f: Frame) -> list[dict]:
    return [pill_badge(f, "{badge}", fill="background", inset=36 if not f.landscape else 26)]


def _ov_editorial_back(f: Frame) -> list[dict]:
    return [pill_badge(f, "{badge}", fill="cta", inset=36 if not f.landscape else 26)]


def _ov_sticker(f: Frame) -> list[dict]:
    return [sticker(f)]


PRODUCT = Copy()
PRODUCT_BODY = Copy(body="{subheadline}")

register(TemplateDef(
    "product_classic", "Product card · Classic",
    "Framed product card on ivory with serif title, price and Shop Now button.",
    (CT.SINGLE_PRODUCT, CT.PRODUCT_OF_THE_DAY, CT.NEW_ARRIVAL),
    builder=lambda f: framed(f, PRODUCT_BODY, overlays=_ov_sticker), style="classic",
))
register(TemplateDef(
    "product_editorial", "Product card · Editorial",
    "Full-bleed product photo over a deep jade panel — magazine style.",
    (CT.SINGLE_PRODUCT, CT.NEW_ARRIVAL, CT.BACK_IN_STOCK),
    builder=lambda f: editorial(f, PRODUCT_BODY, overlays=_ov_sticker), style="editorial",
))
register(TemplateDef(
    "new_arrival_classic", "New arrival · Classic",
    "Framed card with a gold NEW seal and 'New arrival' eyebrow.",
    (CT.NEW_ARRIVAL,), builder=lambda f: framed(f, Copy(eyebrow="{eyebrow}", eyebrow_fallback=("New arrival",),
                                                      body="{subheadline}"), overlays=_ov_new),
    defaults={"badge": "New"}, style="classic",
))
register(TemplateDef(
    "new_arrival_editorial", "New arrival · Editorial",
    "Full-bleed photo, 'Just landed' eyebrow and NEW tag on a jade panel.",
    (CT.NEW_ARRIVAL,), builder=lambda f: editorial(f, Copy(eyebrow="{eyebrow}", eyebrow_fallback=("Just landed",),
                                                         body="{subheadline}"), overlays=_ov_editorial_new),
    defaults={"badge": "New in"}, style="editorial",
))
register(TemplateDef(
    "sale_classic", "Sale · Classic",
    "Discount seal, struck-through compare-at price, promo line and coupon code.",
    (CT.SALE_PROMO,), builder=lambda f: framed(f, Copy(eyebrow="{occasion}", eyebrow_fallback=("{headline}", "Limited-time offer"),
                                                     body="{promo_text}", coupon=True, countdown=True), overlays=_ov_sale),
    style="classic",
))
register(TemplateDef(
    "sale_bold", "Sale · Bold",
    "Deep jade promo with a giant gold discount figure, product card and coupon.",
    (CT.SALE_PROMO,), builder=lambda f: bold_promo(
        f, display="{offer}", kicker="{occasion}", kicker_fallback=("{headline}", "Limited-time offer"),
        body_text="{promo_text}", overlays=_ov_sale_bold, countdown=True),
    style="bold",
))
register(TemplateDef(
    "back_in_stock_classic", "Back in stock · Classic",
    "Framed card with a jade 'Back in stock' ribbon.",
    (CT.BACK_IN_STOCK,), builder=lambda f: framed(f, Copy(eyebrow="{eyebrow}", eyebrow_fallback=("{collection}", "Restocked"),
                                                        body="{subheadline}"), overlays=_ov_back),
    defaults={"badge": "Back in stock"}, style="classic",
))
register(TemplateDef(
    "back_in_stock_editorial", "Back in stock · Editorial",
    "Full-bleed photo with a gold 'Back in stock' tag on a jade panel.",
    (CT.BACK_IN_STOCK,), builder=lambda f: editorial(f, Copy(eyebrow="{eyebrow}", eyebrow_fallback=("It's back",),
                                                           body="{subheadline}"), overlays=_ov_editorial_back),
    defaults={"badge": "Back in stock"}, style="editorial",
))
register(TemplateDef(
    "product_of_the_day", "Product of the day · Seal",
    "Framed card with a gold 'Product of the day' seal.",
    (CT.PRODUCT_OF_THE_DAY,), builder=lambda f: framed(f, Copy(eyebrow="{eyebrow}", eyebrow_fallback=("Today's pick",),
                                                             body="{subheadline}"), overlays=_ov_potd),
    defaults={"sticker": "Product of the day"}, style="classic",
))
register(TemplateDef(
    "product_of_the_day_spotlight", "Product of the day · Spotlight",
    "Centred spotlight on a botanical pattern with the day's pick.",
    (CT.PRODUCT_OF_THE_DAY,), builder=lambda f: framed(f, Copy(eyebrow="{eyebrow}", eyebrow_fallback=("Product of the day",),
                                                             body="{subheadline}"), bg_style="pattern", centered=True,
                                                     overlays=_ov_sticker),
    sample_texts={"sticker": "Deal of the Day"}, style="spotlight",
))
register(TemplateDef(
    "collage_grid", "Collage · Grid",
    "Headline over a 2–4 product grid with names and prices.",
    (CT.COLLAGE,), builder=lambda f: grid_layout(
        f, kicker="{eyebrow}", kicker_fallback=("{store_name}",), title="{headline}",
        title_fallback=("Our favourites",)),
    min_products=2, max_products=4, style="grid",
))
register(TemplateDef(
    "collage_mosaic", "Collage · Mosaic",
    "Photo mosaic (hero + tiles) on deep jade with a headline and CTA.",
    (CT.COLLAGE,), builder=lambda f: grid_layout(
        f, kicker="{eyebrow}", kicker_fallback=("{store_name}",), title="{headline}", title_fallback=("Fresh picks",),
        captions=False, arrangement="hero", panel="secondary"),
    min_products=2, max_products=4, style="mosaic",
))
register(TemplateDef(
    "collage_single_product", "Collage · One product, many angles",
    "Up to four images of one product with its name, price and CTA.",
    (CT.COLLAGE, CT.SINGLE_PRODUCT), builder=lambda f: grid_layout(
        f, kicker="{eyebrow}", kicker_fallback=("{collection}", "{store_name}"), title="{title}", title_fallback=(),
        source="images", captions=False, arrangement="hero", show_price_row=True),
    style="grid",
))
register(TemplateDef(
    "collection_roundup", "Collection round-up · Grid",
    "Collection title with a product grid — 'shop the edit'.",
    (CT.COLLECTION_ROUNDUP,), builder=lambda f: grid_layout(
        f, kicker="{eyebrow}", kicker_fallback=("Shop the collection",), title="{collection}",
        title_fallback=("{headline}", "Our edit")),
    min_products=2, max_products=4, sample_texts={"collection_title": "The Sleep Edit"}, style="grid",
))
register(TemplateDef(
    "collection_roundup_list", "Collection round-up · List",
    "Editorial list of products with thumbnails, names and prices.",
    (CT.COLLECTION_ROUNDUP,), builder=lambda f: grid_layout(
        f, kicker="{eyebrow}", kicker_fallback=("The edit",), title="{collection}",
        title_fallback=("{headline}", "Our edit"), arrangement="list", bg_style="pattern"),
    min_products=2, max_products=4, sample_texts={"collection_title": "The Sleep Edit"}, style="list",
))
register(TemplateDef(
    "countdown_classic", "Countdown · Classic",
    "Big serif countdown ('2 days left') over the product card.",
    (CT.COUNTDOWN,), builder=lambda f: countdown_layout(f, panel="background"),
    defaults={"countdown_label": "Last chance"}, style="classic",
))
register(TemplateDef(
    "countdown_bold", "Countdown · Bold",
    "Deep jade countdown with a giant gold number, coupon and CTA.",
    (CT.COUNTDOWN,), builder=lambda f: bold_promo(
        f, display="{countdown_number}", display_fallback=("{countdown_unit}",), kicker="{occasion}",
        kicker_fallback=("{headline}", "Ends soon"), unit="{countdown_unit}", unit_when="has:countdown_number",
        body_text="{promo_text}", overlays=_ov_sale_bold),
    defaults={"countdown_label": "Last chance"}, style="bold",
))
register(TemplateDef(
    "holiday_classic", "Holiday · Product",
    "Occasion headline over a botanical pattern with the product and offer.",
    (CT.HOLIDAY,), builder=lambda f: holiday_layout(f),
    defaults={"headline": "Seasonal favourites"}, style="classic",
))
register(TemplateDef(
    "holiday_greeting", "Holiday · Greeting card",
    "Product-free greeting card with botanical sprigs, message and optional code.",
    (CT.HOLIDAY,), builder=lambda f: text_card(
        f, kicker="{store_name}", main="{occasion}", main_fallback=("{headline}",), main_size=104, main_lines=3,
        sub="{subheadline}",
        sub_fallback=("{promo_text}",), panel="secondary", bg_style="pattern", cta="{cta}", sub_style="body"),
    defaults={"headline": "Season's greetings"}, min_products=0, needs_images=False, style="greeting",
))
register(TemplateDef(
    "seasonal_scene", "Seasonal · Scene",
    "Occasion promo built for an AI background scene: product framed on a card over the scene, "
    "occasion headline, promo line, coupon and CTA on a solid panel.",
    (CT.HOLIDAY, CT.SALE_PROMO), builder=lambda f: seasonal_layout(f),
    defaults={"headline": "Seasonal favourites"},
    sample_texts={"occasion": "Christmas", "promo_text": "Gift-ready wellness, wrapped with care",
                  "sticker": "Christmas Offer", "countdown_label": "Ends Dec 24"},
    style="seasonal",
))
register(TemplateDef(
    "quote_classic", "Brand · Quote",
    "Large serif quote with gold quote mark and author.",
    (CT.QUOTE,), builder=lambda f: text_card(
        f, kicker="", main="{quote}", main_size=66, quote_mark=True, sub="{quote_author}", sub_fallback=("{store_name}",)),
    min_products=0, needs_images=False, required_texts=("quote",), style="quote",
))
register(TemplateDef(
    "brand_fact", "Brand · Did you know?",
    "Fact card on deep jade with a gold 'Did you know?' tag.",
    (CT.QUOTE,), builder=lambda f: text_card(
        f, kicker="{eyebrow}", main="{fact}", main_fallback=("{quote}",), main_size=70, sub="{fact_source}",
        panel="secondary", bg_style="pattern", badge_kicker=True),
    defaults={"eyebrow": "Did you know?"}, min_products=0, needs_images=False, required_texts=("fact|quote",),
    style="fact",
))
register(TemplateDef(
    "brand_poll", "Brand · Poll",
    "Question with two answer options — 'vote in the comments'.",
    (CT.QUOTE,), builder=lambda f: text_card(
        f, kicker="{eyebrow}", main="{question}", main_fallback=("{headline}",), main_size=66, main_lines=3,
        choices=["option_a", "option_b"], footnote="{footnote}"),
    defaults={"eyebrow": "Quick poll", "footnote": "Vote in the comments"}, min_products=0, needs_images=False,
    required_texts=("question|headline", "option_a", "option_b"), style="poll",
))
register(TemplateDef(
    "brand_quiz", "Brand · Quiz",
    "Quiz question with up to four lettered options.",
    (CT.QUOTE,), builder=lambda f: text_card(
        f, kicker="{eyebrow}", main="{question}", main_fallback=("{headline}",), main_size=60, main_lines=3,
        choices=["option_a", "option_b", "option_c", "option_d"], footnote="{footnote}", panel="background",
        bg_style="solid"),
    defaults={"eyebrow": "Quiz time", "footnote": "Answer in the comments"}, min_products=0, needs_images=False,
    required_texts=("question|headline", "option_a", "option_b"), style="quiz",
))
register(TemplateDef(
    "review_spotlight", "Review spotlight",
    "Five gold stars, the review in serif italics, author and the product reviewed.",
    (CT.QUOTE,), builder=lambda f: text_card(
        f, kicker="", main="“{review_text}”", main_size=58, main_lines=6, italic=True,
        sub="{review_author}", stars=True, thumb=True, bg_style="solid"),
    min_products=0, needs_images=False, required_texts=("review_text",), style="review",
))
register(TemplateDef(
    "blog_article_classic", "Blog article · Classic",
    "Featured image card, article title and 'Read on the blog'.",
    (CT.BLOG_ARTICLE,), builder=lambda f: framed(
        f, Copy(eyebrow="{eyebrow}", eyebrow_fallback=("From the journal",), title="{headline}", title_fallback=("{title}",),
                body="{subheadline}", cta="{cta}", arrow=True, show_price=False), fit="cover", source="text:image_url"),
    defaults={"cta": "Read on the blog"}, min_products=0, needs_images=False, style="classic",
))
register(TemplateDef(
    "blog_article_editorial", "Blog article · Editorial",
    "Full-bleed featured image above a jade panel with the article title.",
    (CT.BLOG_ARTICLE,), builder=lambda f: editorial(
        f, Copy(eyebrow="{eyebrow}", eyebrow_fallback=("On the blog",), title="{headline}", title_fallback=("{title}",),
                body="{subheadline}", cta="{cta}", arrow=True, show_price=False), panel="primary", fit="cover", source="text:image_url"),
    defaults={"cta": "Read on the blog"}, min_products=0, needs_images=False, style="editorial",
))
register(TemplateDef(
    "carousel_classic", "Carousel · Classic",
    "One framed slide per product (or per image) with progress dots.",
    (CT.CAROUSEL,), builder=lambda f: framed(f, PRODUCT, carousel=True), carousel="auto",
    max_products=10, style="classic",
))
register(TemplateDef(
    "carousel_editorial", "Carousel · Editorial",
    "One editorial slide per product (or per image) with progress dots.",
    (CT.CAROUSEL,), builder=lambda f: editorial(f, PRODUCT, carousel=True), carousel="auto",
    max_products=10, style="editorial",
))
register(TemplateDef(
    "classic_video", "Classic video",
    "Slideshow reel: intro, Ken Burns product shots, price/sticker slide and end card.",
    (CT.PRODUCT_VIDEO,), kind="video", formats=VIDEO_FORMATS,
    video={"style": "classic", "max_images": 4}, style="video",
))
register(TemplateDef(
    "classic_video_multi", "Classic video · Multi-product",
    "Slideshow reel for 2–4 products (collages and round-ups) with an end card.",
    (CT.PRODUCT_VIDEO, CT.COLLAGE, CT.COLLECTION_ROUNDUP), kind="video", formats=VIDEO_FORMATS,
    min_products=1, max_products=4, video={"style": "multi", "max_images": 4},
    sample_texts={"collection_title": "The Sleep Edit"}, style="video",
))


def seasonal_layout(f: Frame) -> list[dict]:
    """Occasion card for promotions: the AI scene (options["background_image"]) fills the
    top, the real product sits framed on a card over it, the copy lives on a solid panel.
    Without a scene the top falls back to a brand-colour botanical pattern."""
    overlays = [
        circle_badge(f, "{badge}", fallback=("{discount}",), when="has:badge|discount", diameter=136, scale=0.42,
                     inset=22),
        sticker(f, anchor="top_right", size=170, inset=18),
    ]
    head = [
        eyebrow(f, "{eyebrow}", fallback=("{store_name}",), align="center"),
        heading(f, "{occasion}", fallback=["{headline}"], size=84, min_size=46, lines=2, align="center",
                gap_before=f.s(10)),
        body(f, "{promo_text}", lines=2, align="center", gap_before=f.s(10), optional=True, when="has:promo_text"),
        countdown_pill(f, align="center", gap_before=f.s(14)),
        price_row(f, justify="center", gap_before=24, with_coupon=not f.landscape),
    ]
    scene = background("pattern", "secondary", pattern_color="accent", pattern_opacity=0.08, scene_scrim=0.1,
                       allow_override=False)
    card = {"type": "image", "card": "auto", "radius": f.s(26), "shadow": True, "padding": 0.08, "overlays": overlays}
    if f.landscape:
        split_x = f.W * 0.56
        card_top = f.top + f.px(BAR) + f.px(16)
        size = min(split_x - 2 * f.side, f.H - f.bottom - card_top)
        col_x = split_x + f.side
        col_w = f.W - f.side - col_x
        return [
            scene,
            {"type": "shape", "box": f.r(split_x, 0, f.W - split_x, f.H), "fill": "background"},
            dict(logo(f, allowed=("top_left", "bottom_left"), on="surface"), plate="always"),
            dict(card, box=f.r((split_x - size) / 2, card_top, size, size)),
            {"type": "stack", "box": f.r(col_x, f.top, col_w, f.H - f.top - f.bottom - f.px(BAR)), "valign": "middle",
             "align": "center", "gap": 0, "children": [*head, inline_coupon(f, gap_before=f.s(18))]},
            {"type": "footer", "box": f.r(col_x, f.H - f.bottom - f.px(BAR), col_w, f.px(BAR)), "valign": "middle",
             "size": f.s(SMALL)},
        ]
    ratio = {MediaFormat.SQUARE: 0.54, MediaFormat.PORTRAIT: 0.56, MediaFormat.STORY: 0.5}.get(f.fmt, 0.55)
    split_y = f.H * ratio
    card_top = f.top + f.px(BAR) + f.px(24)
    card_h = split_y - card_top - f.px(40)
    card_w = min(f.W - 2 * f.side, card_h * 1.08)
    band = f.r(f.side, f.H - f.bottom - f.px(BAR), f.W - 2 * f.side, f.px(BAR))
    panel_top = split_y + f.px(40)
    return [
        scene,
        {"type": "shape", "box": f.r(0, split_y, f.W, f.H - split_y), "fill": "background"},
        dict(logo(f, allowed=("top_left", "top_right"), on="surface",
                  band_top=f.r(f.side, f.top, f.W - 2 * f.side, f.px(BAR))), plate="always"),
        dict(card, box=f.r((f.W - card_w) / 2, card_top, card_w, card_h)),
        {"type": "stack", "box": f.r(f.side, panel_top, f.W - 2 * f.side, f.H - f.bottom - f.px(BAR) - f.px(24) - panel_top),
         "valign": "middle", "align": "center", "gap": 0, "children": head},
        {"type": "footer", "box": band, "valign": "middle", "size": f.s(SMALL)},
    ]


def countdown_layout(f: Frame, *, panel: str = "background") -> list[dict]:
    head = [
        eyebrow(f, "{occasion}", fallback=("{headline}", "Ends soon"), align="center"),
        heading(f, "{countdown_number}", size=170, min_size=90, lines=1, color=ACCENT_ON_BG, align="center",
                line_height=0.92, gap_before=f.s(8), when="has:countdown_number"),
        eyebrow(f, "{countdown_unit}", color="on:background", align="center", size=27, tracking=0.2, gap_before=f.s(6),
                when="has:countdown_number"),
        heading(f, "{countdown_unit}", size=84, min_size=48, lines=2, color=ACCENT_ON_BG, align="center",
                gap_before=f.s(10), when="!has:countdown_number"),
    ]
    copy_children = [
        heading(f, "{title}", size=50, min_size=32, lines=2, align="center", gap_before=f.s(30)),
        price_row(f, justify="center", gap_before=22, with_coupon=not f.landscape),
    ]
    if f.landscape:
        copy_children.append(inline_coupon(f, gap_before=f.s(20)))
    if f.landscape:
        col_x = f.W * 0.52
        return [
            background("solid", panel),
            logo(f, allowed=("top_left", "bottom_left"), max_h=44),
            {"type": "image", "box": f.r(col_x, f.top, f.W - f.side - col_x, f.H - f.top - f.bottom), "card": "auto",
             "radius": f.s(24), "shadow": True, "padding": 0.08, "overlays": _ov_countdown(f)},
            {"type": "stack", "box": f.r(f.side, bar_bottom(f), col_x - f.side - f.px(40), f.H - f.bottom - bar_bottom(f)),
             "valign": "middle", "align": "center", "gap": 0, "children": head + [dict(c) for c in copy_children]},
        ]
    top = bar_bottom(f) + f.px(20)
    return [
        background("solid", panel),
        *top_bar(f),
        {"type": "column", "box": f.r(f.side, top, f.W - 2 * f.side, f.H - f.bottom - top), "gap": f.s(30),
         "min_flex": 0.32, "align": "center", "children": [
             {"type": "stack", "gap": 0, "align": "center", "children": head},
             product_card(f, overlays=_ov_countdown(f), radius=24),
             {"type": "stack", "gap": 0, "align": "center", "children": copy_children},
         ]},
    ]


def holiday_layout(f: Frame) -> list[dict]:
    head = [
        eyebrow(f, "{eyebrow}", fallback=("{store_name}",), align="center"),
        heading(f, "{occasion}", fallback=["{headline}"], size=84, min_size=48, lines=2, color=ACCENT_ON_BG,
                align="center", gap_before=f.s(10)),
        body(f, "{promo_text}", lines=2, align="center", gap_before=f.s(10), optional=True, when="has:promo_text"),
    ]
    copy_children = [
        heading(f, "{title}", size=44, min_size=30, lines=1, align="center", gap_before=f.s(30)),
        price_row(f, justify="center", gap_before=20, with_coupon=not f.landscape),
    ]
    if f.landscape:
        copy_children.append(inline_coupon(f, gap_before=f.s(20)))
    if f.landscape:
        col_x = f.W * 0.52
        return [
            background("pattern", "background", pattern_color="primary"),
            logo(f, allowed=("top_left", "bottom_left"), max_h=44),
            {"type": "image", "box": f.r(col_x, f.top, f.W - f.side - col_x, f.H - f.top - f.bottom), "card": "auto",
             "radius": f.s(24), "shadow": True, "padding": 0.08, "overlays": _ov_holiday(f)},
            {"type": "stack", "box": f.r(f.side, bar_bottom(f), col_x - f.side - f.px(40), f.H - f.bottom - bar_bottom(f)),
             "valign": "middle", "align": "center", "gap": 0, "children": head + copy_children},
        ]
    top = bar_bottom(f) + f.px(20)
    return [
        background("pattern", "background", pattern_color="primary"),
        *top_bar(f),
        {"type": "column", "box": f.r(f.side, top, f.W - 2 * f.side, f.H - f.bottom - top), "gap": f.s(30),
         "min_flex": 0.34, "align": "center", "children": [
             {"type": "stack", "gap": 0, "align": "center", "children": head},
             product_card(f, overlays=_ov_holiday(f), radius=24),
             {"type": "stack", "gap": 0, "align": "center", "children": copy_children},
         ]},
    ]


# ---------------------------------------------------------------------------
# Video slides (rendered with the same engine, composed by video.py)
# ---------------------------------------------------------------------------

def video_intro(f: Frame) -> list[dict]:
    """Intro slide: hero product card + title."""
    top = f.top + f.px(BAR) + f.px(30)
    return [
        background("gradient", "background"),
        *top_bar(f),
        {"type": "column", "box": f.r(f.side, top, f.W - 2 * f.side, f.H - f.bottom - top), "gap": f.s(40),
         "min_flex": 0.5, "align": "center", "children": [
             product_card(f, radius=28),
             {"type": "stack", "gap": 0, "align": "center", "children": [
                 eyebrow(f, "{eyebrow}", fallback=("{headline}", "{collection}", "{store_name}"), align="center"),
                 heading(f, "{intro_title}", fallback=["{title}"], size=78, lines=3, align="center", gap_before=f.s(14)),
             ]},
         ]},
    ]


def video_motion_overlay(f: Frame) -> list[dict]:
    """Transparent overlay drawn over Ken Burns product shots (caption + logo)."""
    band_h = f.px(230)
    y = f.H - f.bottom - band_h
    return [
        {"type": "scrim", "box": f.r(0, y - f.px(220), f.W, f.H - y + f.px(220)), "fill": "scrim",
         "gradient": {"from": 0.0, "to": 0.82, "ease": 1.3}},
        dict(logo(f, allowed=("top_left", "top_right"), on="surface",
                  band_top=f.r(f.side, f.top, f.W - 2 * f.side, f.px(BAR))), plate="always"),
        {"type": "stack", "box": f.r(f.side, y, f.W - 2 * f.side, band_h), "valign": "bottom", "align": "left",
         "gap": 0, "children": [
             eyebrow(f, "{caption_kicker}", color="soft:scrim"),
             heading(f, "{caption}", size=60, min_size=36, lines=2, color="on:scrim", gap_before=f.s(10)),
         ]},
    ]


def video_price(f: Frame) -> list[dict]:
    """Price / offer slide (sale badge and sticker when relevant)."""
    top = f.top + f.px(BAR) + f.px(30)
    return [
        background("pattern", "secondary", pattern_color="accent", pattern_opacity=0.08, allow_override=False),
        *top_bar(f, on="secondary", color="soft:secondary"),
        {"type": "column", "box": f.r(f.side, top, f.W - 2 * f.side, f.H - f.bottom - top), "gap": f.s(36),
         "min_flex": 0.4, "align": "center", "children": [
             {"type": "stack", "gap": 0, "align": "center", "children": [
                 eyebrow(f, "{occasion}", fallback=("{headline}", "{offer_kicker}"), color="pick:secondary:cta,on",
                         align="center"),
                 heading(f, "{title}", size=64, min_size=40, lines=2, color="on:secondary", align="center",
                         gap_before=f.s(14)),
             ]},
             product_card(f, radius=28, overlays=[
                 circle_badge(f, "{badge}", fallback=("{discount}",), when="has:badge|discount", diameter=170,
                              scale=0.4),
                 sticker(f, anchor="top_right", size=200),
             ]),
             {"type": "stack", "gap": 0, "align": "center", "children": [
                 price(f, size=64, color="pick:secondary:cta,on", compare_color="soft:secondary"),
                 body(f, "{promo_text}", lines=2, color="soft:secondary", align="center", gap_before=f.s(12), optional=True,
                      when="has:promo_text"),
                 {"type": "coupon", "text": "{coupon_code}", "label": "Use code", "size": f.s(27),
                  "border": "pick:secondary:cta,on", "fill": "none", "color": "on:secondary",
                  "label_color": "soft:secondary", "align": "center",
                  "gap_before": f.s(22), "when": "has:coupon_code"},
             ]},
         ]},
    ]


def video_end(f: Frame) -> list[dict]:
    """End card: logo, CTA, website."""
    return [
        background("pattern", "background", pattern_color="primary"),
        sprig(f, -f.W * 0.06, f.H * 0.6, f.W * 0.4, f.H * 0.36, rotation=-30, opacity=0.12),
        sprig(f, f.W * 0.66, f.H * 0.04, f.W * 0.4, f.H * 0.36, rotation=150, opacity=0.12, mirror=True),
        {"type": "logo", "position": "box", "box": f.r(f.side, f.H * 0.34 - f.px(75), f.W - 2 * f.side, f.px(150)),
         "max_w": f.s(460), "max_h": f.s(150), "fallback": "wordmark", "force": True, "on": "background"},
        {"type": "stack", "box": f.r(f.side, f.H * 0.34 + f.px(110), f.W - 2 * f.side, f.H * 0.5),
         "valign": "top", "align": "center", "gap": 0, "children": [
             heading(f, "{end_title}", fallback=["{slogan}", "Shop the full range"], size=70, lines=2, align="center"),
             button(f, "{cta}", fill="cta", size=30, gap_before=f.s(40), align="center"),
             {"type": "footer", "text": "{website}", "size": f.s(26), "color": ACCENT_ON_BG, "align": "center",
              "gap_before": f.s(30)},
         ]},
    ]


def video_multi_intro(f: Frame) -> list[dict]:
    top = f.top + f.px(BAR) + f.px(30)
    return [
        background("gradient", "background"),
        *top_bar(f),
        {"type": "column", "box": f.r(f.side, top, f.W - 2 * f.side, f.H - f.bottom - top), "gap": f.s(40),
         "min_flex": 0.5, "align": "center", "children": [
             {"type": "stack", "gap": 0, "align": "center", "children": [
                 eyebrow(f, "{eyebrow}", fallback=("{store_name}",), align="center"),
                 heading(f, "{headline}", fallback=["{collection}", "Our favourites"], size=78, lines=2, align="center",
                         gap_before=f.s(14)),
             ]},
             {"type": "grid", "flex": 1, "source": "products", "max": 4, "captions": False, "arrangement": "auto",
              "gap": f.s(22), "radius": f.s(22), "card": "auto", "padding": 0.08, "shadow": True},
         ]},
    ]


VIDEO_SLIDES: dict[str, Builder] = {
    "intro": video_intro, "motion_overlay": video_motion_overlay, "price": video_price, "end": video_end,
    "multi_intro": video_multi_intro,
}


@functools.lru_cache(maxsize=128)
def video_slide_spec(slide: str, fmt: str) -> dict:
    return {"layers": VIDEO_SLIDES[slide](FRAMES[fmt])}


def all_templates() -> list[TemplateDef]:
    return list(_REGISTRY.values())


def template(key: str) -> TemplateDef | None:
    return _REGISTRY.get(key)
