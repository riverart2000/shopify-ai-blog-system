"""Load promotional campaign details from ``campaign.txt``."""

from __future__ import annotations

import re
from pathlib import Path

from .models import Campaign
from .utils import get_logger

log = get_logger("campaign")

_DISCOUNT_RE = re.compile(r"(\d{1,3}\s*%[^,\n]*?(?:off|discount)?)", re.IGNORECASE)
_CODE_RE = re.compile(r"\b([A-Z][A-Z0-9]{3,})\b")
_FREE_SHIP_RE = re.compile(r"free\s+shipping", re.IGNORECASE)


def load_campaign(path: Path) -> Campaign:
    """Parse ``campaign.txt`` into a :class:`Campaign`.

    The file is free-form promotional text (e.g.
    ``20% LAUNCH DISCOUNT & FREE SHIPPING: LAUNCH20``). Returns an empty
    campaign (``has_offer == False``) if the file is missing/blank.
    """
    if not path.exists():
        return Campaign()
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return Campaign()

    discount = ""
    match = _DISCOUNT_RE.search(raw)
    if match:
        discount = re.sub(r"\s+", " ", match.group(1)).strip().rstrip(":").upper()
        if "off" not in discount.lower():
            discount = f"{discount} OFF"

    free_shipping = bool(_FREE_SHIP_RE.search(raw))

    # Discount code: prefer a token after a colon, else the last all-caps token.
    code = ""
    if ":" in raw:
        tail = raw.split(":")[-1].strip()
        code_match = _CODE_RE.search(tail)
        if code_match:
            code = code_match.group(1)
    if not code:
        codes = [
            c
            for c in _CODE_RE.findall(raw)
            if c not in {"FREE", "OFF", "LAUNCH", "DISCOUNT", "SHIPPING"}
        ]
        code = codes[-1] if codes else ""

    campaign = Campaign(
        raw=raw,
        headline=raw,
        discount=discount,
        code=code,
        free_shipping=free_shipping,
    )
    log.info(
        "Campaign loaded: discount=%r free_shipping=%s code=%r",
        campaign.discount,
        campaign.free_shipping,
        campaign.code,
    )
    return campaign
