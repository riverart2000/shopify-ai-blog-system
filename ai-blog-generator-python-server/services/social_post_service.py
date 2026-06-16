"""services/social_post_service.py — Generate short sales-focused social post variants."""
from __future__ import annotations

import re
from typing import Any

from . import llm_service

_SUPPORTED_PROVIDERS = ["instagram", "facebook", "x", "linkedin", "pinterest", "tiktok"]

_SOCIAL_PROMPT_ENDING = (
    "Return ONLY a single valid JSON object with exactly these fields:\n"
    '  "title": string — short campaign name (max 8 words)\n'
    '  "summary": string — one sentence post objective\n'
    '  "keywords": array of strings — 4 to 8 SEO/product terms\n'
    '  "hashtags": array of strings — 4 to 8 hashtags with # prefix\n'
    '  "content": string — EXACTLY these six lines in this order:\n'
    "instagram: <one short compelling post with CTA>\n"
    "facebook: <one short compelling post with CTA>\n"
    "x: <one short compelling post with CTA>\n"
    "linkedin: <one short compelling post with CTA>\n"
    "pinterest: <one short compelling post with CTA>\n"
    "tiktok: <one short compelling post with CTA>\n"
    "No markdown. No extra fields. Raw JSON only."
)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _cap_text(provider: str, text: str) -> str:
    # Keep a practical limit by network while preserving readability.
    hard_limits = {
        "x": 260,
        "instagram": 420,
        "facebook": 420,
        "linkedin": 420,
        "pinterest": 300,
        "tiktok": 280,
    }
    limit = hard_limits.get(provider, 420)
    trimmed = " ".join(_clean_text(text).split())
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[: max(0, limit - 1)].rstrip() + "..."


def _extract_provider_lines(content: str) -> dict[str, str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    out: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^(instagram|facebook|x|linkedin|pinterest|tiktok)\s*:\s*(.+)$", line, re.IGNORECASE)
        if not match:
            continue
        provider = match.group(1).lower().strip()
        text = match.group(2).strip()
        if provider and text:
            out[provider] = _cap_text(provider, text)
    return out


def _fallback_post_text(
    *,
    provider: str,
    product_title: str,
    product_url: str,
    brief_text: str,
    hashtags: list[str],
) -> str:
    hashtag_suffix = " ".join(tag for tag in hashtags[:4] if tag)
    teaser = brief_text.strip() or f"See why shoppers are choosing {product_title}."
    base = f"{teaser} Discover {product_title} at {product_url}."
    if provider == "x":
        return _cap_text(provider, f"{base} {hashtag_suffix}".strip())
    return _cap_text(provider, f"{base} {hashtag_suffix}".strip())


def _normalise_hashtags(raw: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        tag = _clean_text(value)
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.replace(" ", "")
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= 8:
            break
    return result


async def generate_social_post_variants(
    *,
    store_id: str,
    store_name: str,
    product_title: str,
    product_url: str,
    brief_text: str,
    model_id: str | None = None,
) -> dict[str, Any]:
    title = _clean_text(product_title)
    url = _clean_text(product_url)
    brief = _clean_text(brief_text)

    if not title:
        raise ValueError("product_title is required")

    prompt = (
        f"You are a direct-response social media copywriter for ecommerce.\n"
        f"Store: {store_name}\n"
        f"Goal: Write high quality short sales and marketing style social posts that create intrigue "
        f"and drive clicks to the store.\n"
        f"Product title: {title}\n"
        f"Product URL: {url or '(not provided)'}\n"
        f"Brief from merchant: {brief or '(not provided)'}\n\n"
        "Requirements:\n"
        "- Tone: confident, benefit-first, no fake urgency.\n"
        "- Mention a clear action to visit the product/store.\n"
        "- Keep each post concise and natural for that network.\n"
        "- Avoid repetitive openings across platforms."
    )

    generated = await llm_service.generate_text(
        store_id,
        prompt,
        model_id=model_id,
        prompt_ending_override=_SOCIAL_PROMPT_ENDING,
    )

    campaign_name = _clean_text(generated.get("title")) or f"{title} spotlight"
    summary = _clean_text(generated.get("summary"))
    keywords = [str(item).strip() for item in generated.get("keywords", []) if str(item).strip()][:8]
    hashtags = _normalise_hashtags(generated.get("hashtags", []))

    content = _clean_text(generated.get("content"))
    provider_texts = _extract_provider_lines(content)

    for provider in _SUPPORTED_PROVIDERS:
        if provider not in provider_texts:
            provider_texts[provider] = _fallback_post_text(
                provider=provider,
                product_title=title,
                product_url=url,
                brief_text=brief,
                hashtags=hashtags,
            )

    if not summary:
        summary = provider_texts.get("instagram") or provider_texts.get("facebook") or "Social post draft"

    return {
        "campaign_name": campaign_name,
        "summary": summary,
        "keywords": keywords,
        "hashtags": hashtags,
        "provider_texts": provider_texts,
        "generated_by": generated.get("_model_name", ""),
        "generated_provider": generated.get("_model_provider", ""),
    }
