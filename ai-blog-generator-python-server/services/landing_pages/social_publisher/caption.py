"""Compose the ready-to-paste social post text for one generated image.

The file contains ONLY the text a user copies straight into a social platform:
the title, the caption/description, the offer, long-tail keywords and hashtags —
nothing else (no labels, separators or metadata).
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_HASHTAG_RE = re.compile(r"#\w+")


def build_post_text(product: Dict, campaign: Dict, concept: Dict) -> str:
    """Return the full paste-ready post text for one concept."""
    title = (product.get("title") or "").strip()

    body, tags_in_body = _split_caption_and_hashtags(concept.get("social_text") or "")

    offer = _offer_line(campaign)
    if offer and _offer_already_present(body, campaign):
        offer = ""  # avoid repeating the offer if the caption already mentions it

    link = _product_link(product, campaign)
    keywords = _format_keywords(concept.get("keywords"))
    hashtags = _merge_hashtags(tags_in_body, concept.get("hashtags"))

    blocks: List[str] = []
    if title:
        blocks.append(title)
    if body:
        blocks.append(body)
    if offer:
        blocks.append(offer)
    if link:
        blocks.append(link)
    if keywords:
        blocks.append(keywords)
    if hashtags:
        blocks.append(hashtags)

    return "\n\n".join(blocks).strip() + "\n"


# ----------------------------------------------------------------------
# Product link (with auto-applied discount)
# ----------------------------------------------------------------------

def _product_link(product: Dict, campaign: Dict) -> str:
    """Return the product URL, adding ``?discount=CODE`` so the code auto-applies.

    Any existing query parameters (e.g. ``variant``) are preserved.
    """
    url = (product.get("url") or "").strip()
    if not url:
        return ""
    code = (campaign or {}).get("code")
    if not code:
        return url
    return _add_query_param(url, "discount", code)


def _add_query_param(url: str, key: str, value: str) -> str:
    parts = urlparse(url)
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    params.append((key, value))
    return urlunparse(parts._replace(query=urlencode(params)))


# ----------------------------------------------------------------------
# Hashtags
# ----------------------------------------------------------------------

def _split_caption_and_hashtags(text: str) -> Tuple[str, List[str]]:
    """Split a caption into its body and any trailing hashtag block.

    Trailing lines that consist solely of hashtags are removed from the body so
    the hashtags can be normalised and re-appended once, without duplication.
    """
    lines = text.rstrip().splitlines()
    trailing_tags: List[str] = []
    while lines:
        stripped = lines[-1].strip()
        if stripped and _is_only_hashtags(stripped):
            trailing_tags = _HASHTAG_RE.findall(stripped) + trailing_tags
            lines.pop()
        elif not stripped:
            lines.pop()
        else:
            break
    body = "\n".join(lines).strip()
    return body, trailing_tags


def _is_only_hashtags(line: str) -> bool:
    without_tags = _HASHTAG_RE.sub("", line).strip()
    return without_tags == "" and "#" in line


def _merge_hashtags(*groups: object) -> str:
    seen: List[str] = []
    lower_seen = set()
    for group in groups:
        items: List = group if isinstance(group, list) else [group]
        for tag in items:
            tag = str(tag).strip()
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = "#" + tag.lstrip("#")
            if tag.lower() not in lower_seen:
                lower_seen.add(tag.lower())
                seen.append(tag)
    return " ".join(seen)


# ----------------------------------------------------------------------
# Offer + keywords
# ----------------------------------------------------------------------

def _offer_line(campaign: Dict) -> str:
    if not campaign or not campaign.get("raw"):
        return ""
    bits: List[str] = []
    if campaign.get("discount"):
        bits.append(campaign["discount"])
    if campaign.get("free_shipping"):
        bits.append("FREE SHIPPING")
    offer = " + ".join(bits) if bits else campaign.get("raw", "")
    text = f"🎉 {offer}" if offer else ""
    if campaign.get("code"):
        text = f"{text} — use code {campaign['code']}".strip(" —")
    return text


def _offer_already_present(body: str, campaign: Dict) -> bool:
    lower = body.lower()
    code = (campaign.get("code") or "").lower()
    if code and code in lower:
        return True
    discount = (campaign.get("discount") or "").lower()
    if discount and discount in lower:
        return True
    return False


def _format_keywords(keywords: object) -> str:
    if not keywords:
        return ""
    if isinstance(keywords, str):
        parts = [k.strip() for k in keywords.split(",")]
    else:
        parts = [str(k).strip() for k in keywords]
    seen: List[str] = []
    lower_seen = set()
    for part in parts:
        if part and part.lower() not in lower_seen:
            lower_seen.add(part.lower())
            seen.append(part)
    return ", ".join(seen)
