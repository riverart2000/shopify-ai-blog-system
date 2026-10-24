"""UTM tracking links for every published social post.

`tracked_url` keeps the original query string byte-for-byte (only existing
`utm_*` keys that we set are replaced), so Shopify variant/discount parameters
survive untouched.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from .settings import UtmSettings

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_PROVIDER_ALIASES: dict[str, str] = {
    "instagram-standalone": "instagram",
    "linkedin-page": "linkedin",
    "tiktok-business": "tiktok",
}
_UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content")


def slugify(text: str, *, max_length: int = 80) -> str:
    """Lower-case ASCII slug: 'Black Friday – 20% off!' → 'black-friday-20-off'."""
    normalised = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", normalised.lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug


def utm_source_for(provider: str, settings: UtmSettings) -> str:
    key = (provider or "").strip().lower()
    override = settings.source_overrides.get(provider) or settings.source_overrides.get(key)
    if override:
        return override.strip()
    if key in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[key]
    for suffix in ("-standalone", "-page"):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key or "social"


def tracked_url(url: str, *, provider: str, campaign: str, content: str, settings: UtmSettings) -> str:
    """Return `url` with utm_source/medium/campaign/content added.

    Returns the URL unchanged when tracking is disabled or the URL is empty.
    Empty `campaign`/`content` values are omitted rather than sent blank.
    """
    url = (url or "").strip()
    if not url or not settings.enabled:
        return url
    parts = urlsplit(url)
    values = {
        "utm_source": utm_source_for(provider, settings),
        "utm_medium": (settings.medium or "social").strip(),
        "utm_campaign": slugify(campaign),
        "utm_content": str(content or "").strip(),
    }
    replaced = {key for key, value in values.items() if value}
    kept = [
        segment for segment in parts.query.split("&")
        if segment and unquote_plus(segment.split("=", 1)[0]) not in replaced
    ]
    added = urlencode([(key, values[key]) for key in _UTM_KEYS if values[key]])
    query = "&".join([*kept, added] if added else kept)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
