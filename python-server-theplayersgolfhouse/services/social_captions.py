"""Per-platform share captions with UTM-tagged links.

Builds ready-to-copy caption variants for the platforms a blog post is shared on
(Pinterest, LinkedIn, Facebook, X, Substack/newsletter, Instagram). Every caption
embeds a UTM-tagged link so traffic from each channel is measurable in analytics.

Deterministic and side-effect free — no LLM calls, safe to run on every publish.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Platform key -> (display label, utm_source, utm_medium)
_PLATFORM_META = [
    ("pinterest", "Pinterest", "pinterest", "social"),
    ("linkedin", "LinkedIn", "linkedin", "social"),
    ("facebook", "Facebook", "facebook", "social"),
    ("x", "X (Twitter)", "twitter", "social"),
    ("substack", "Substack / Newsletter", "substack", "newsletter"),
    ("instagram", "Instagram", "instagram", "social"),
]

_X_MAX = 280


def with_utm(url: str, source: str, medium: str = "social", campaign: str = "blog_share") -> str:
    """Return ``url`` with UTM tracking params added (existing params preserved)."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(
        {
            "utm_source": source,
            "utm_medium": medium,
            "utm_campaign": campaign,
        }
    )
    return urlunparse(parts._replace(query=urlencode(query)))


def _hashtags(hashtags, limit: int):
    out: list[str] = []
    seen = set()
    for tag in hashtags or []:
        tag = (tag or "").strip()
        if not tag:
            continue
        tag = "#" + tag.lstrip("#")
        key = tag.lower()
        if key in seen or key == "#":
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


def build_captions(
    *,
    title: str,
    summary: str,
    keywords=None,
    hashtags=None,
    long_tail_keywords=None,
    article_url: str = "",
    pin_description: str = "",
):
    """Build a list of per-platform caption dicts: ``{key, label, text, url}``."""
    title = (title or "").strip()
    summary = (summary or "").strip()
    keywords = keywords or []
    hashtags = hashtags or []
    long_tail_keywords = long_tail_keywords or []

    links = {key: with_utm(article_url, src, med) for key, _label, src, med in _PLATFORM_META}

    texts: dict[str, str] = {}

    # Pinterest — keyword-rich description (LLM pin_description preferred)
    pin = (pin_description or "").strip()
    if not pin:
        pin = " ".join(filter(None, [summary, " ".join(_hashtags(hashtags, 3))])).strip()
    texts["pinterest"] = pin

    # LinkedIn — professional, a few hashtags
    li_tags = " ".join(_hashtags(hashtags, 3))
    texts["linkedin"] = "\n\n".join(filter(None, [title, summary, li_tags, f"Read more: {links['linkedin']}"]))

    # Facebook — conversational
    fb_tags = " ".join(_hashtags(hashtags, 3))
    texts["facebook"] = "\n\n".join(filter(None, [title, summary, fb_tags, links["facebook"]]))

    # X / Twitter — fits the 280-char limit (link counts as ~23 chars)
    x_tags = " ".join(_hashtags(hashtags, 2))
    reserved = 24 + (len(x_tags) + 1 if x_tags else 0)
    room = _X_MAX - reserved
    x_title = title if len(title) <= room else (title[: max(0, room - 1)].rstrip() + "\u2026")
    texts["x"] = " ".join(filter(None, [x_title, x_tags, links["x"]])).strip()

    # Substack / newsletter — long-form intro
    texts["substack"] = "\n\n".join(
        filter(None, [title, summary, f"Read the full post: {links['substack']}"])
    )

    # Instagram — hashtag heavy (link in bio, IG links aren't clickable in captions)
    ig_tags = _hashtags(hashtags, 8)
    if len(ig_tags) < 5:
        ig_tags = ig_tags + _hashtags(["#" + str(k) for k in keywords], 8 - len(ig_tags))
    texts["instagram"] = "\n\n".join(
        filter(None, [title, summary, "Link in bio \U0001f517", " ".join(ig_tags)])
    )

    captions = []
    for key, label, _src, _med in _PLATFORM_META:
        captions.append(
            {
                "key": key,
                "label": label,
                "text": texts.get(key, ""),
                "url": links.get(key, ""),
            }
        )
    return captions
