"""Per-concept creative specifications shared by all prompt generators.

Defines which concepts should depict the ideal client (a real person), the
preferred aspect ratio, and how to map an aspect ratio to concrete pixel
dimensions capped at ~1K on the longest side.
"""

from __future__ import annotations

from typing import Tuple

# Whether the concept's image should feature the ideal client (a person).
# Product-forward / graphic concepts default to no person.
CONCEPT_NEEDS_PERSON = {
    "lifestyle-image": True,
    "problem-solution": True,
    "social-proof": True,
    "quick-wellness-tip": True,
    "did-you-know": False,
    "educational-infographic": False,
    "benefits-graphic": False,
    "premium-brand-image": False,
    "myth-vs-fact": False,
}

# Preferred aspect ratio per concept (portrait 4:5 suits feed ads; 1:1 default).
CONCEPT_ASPECT = {
    "lifestyle-image": "4:5",
    "problem-solution": "4:5",
    "social-proof": "4:5",
    "quick-wellness-tip": "4:5",
}

# Longest-side pixel budget ("1K").
BASE_RESOLUTION = 1024

_RATIOS = {
    "1:1": (1, 1),
    "4:5": (4, 5),
    "5:4": (5, 4),
    "3:4": (3, 4),
    "4:3": (4, 3),
    "9:16": (9, 16),
    "16:9": (16, 9),
}


def needs_person(slug: str) -> bool:
    return CONCEPT_NEEDS_PERSON.get(slug, False)


def aspect_for(slug: str) -> str:
    return CONCEPT_ASPECT.get(slug, "1:1")


def dimensions_for(aspect_ratio: str, base: int = BASE_RESOLUTION) -> Tuple[int, int]:
    """Return (width, height) for an aspect ratio, longest side == ``base``.

    Values are rounded to multiples of 8 (friendly to image models).
    """
    w_ratio, h_ratio = _RATIOS.get(aspect_ratio, (1, 1))
    if w_ratio >= h_ratio:
        width = base
        height = round(base * h_ratio / w_ratio)
    else:
        height = base
        width = round(base * w_ratio / h_ratio)

    def _round8(value: int) -> int:
        return max(8, int(round(value / 8)) * 8)

    return _round8(width), _round8(height)
