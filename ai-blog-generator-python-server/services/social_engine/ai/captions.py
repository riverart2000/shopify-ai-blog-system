"""Outfy-style captions: Gemma writes the copy, deterministic code enforces the rules.

The model is asked for ONE JSON object (on-image texts, a base caption,
hashtags and one caption per network family). Everything a network can reject
is then enforced in code, so it holds even when the model misbehaves:

* length (min of the Postiz channel limit and the provider profile; X counts
  every URL as 23 and emoji/CJK as 2), trimmed at a sentence/word boundary
  while reserving room for the link and hashtags (never cut through either);
* link policy (Outfy all / all-except-Instagram / none / custom URL combined
  with the network's inline | bio | settings_link | first_comment | none);
* CTA, price and emoji level; banned terms;
* hashtags: model + product tags + hashtag cloud (not on Pinterest/Threads),
  #CamelCase, de-duplicated, capped at min(settings, network maximum).
"""
from __future__ import annotations

import logging
import random
import re
import zlib
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from ..context import StoreContext
from ..errors import ValidationError
from ..models import ContentType, ProviderProfile, profile_for
from ..settings import CaptionSettings, EngineSettings
from ..utm import tracked_url
from . import prompts
from .base import CaptionChannel, CaptionRequest, CaptionResult, ChannelCaption, LLMRequest
from .guard import CLAIM_CODES, check_captions
from .llm import get_router
from .text import (
    URL_RE, extract_hashtags, limit_emojis, measure_for, normalise_hashtags, remove_link_in_bio,
    remove_terms, remove_urls, strip_emojis, strip_html, tidy, trim_to_limit, truncate_words,
)

logger = logging.getLogger("ai_blog_server.social_engine.ai")

FIELD_LIMITS: dict[str, int] = {"headline": 40, "subheadline": 70, "cta": 22, "hook": 80, "alt_text": 250}
EMOJI_CAPS: dict[str, int] = {"off": 0, "light": 4, "heavy": 8}
NO_CLOUD_PROVIDERS = frozenset({"pinterest", "threads"})
CLOUD_SAMPLE = 5
PROVIDER_GROUPS: dict[str, str] = {
    "instagram-standalone": "instagram", "linkedin-page": "linkedin", "tiktok-business": "tiktok",
}
LINK_IN_BIO: dict[str, str] = {
    "en": "Link in bio", "fr": "Lien en bio", "de": "Link in der Bio", "es": "Enlace en la bio",
    "it": "Link in bio", "nl": "Link in bio", "pt": "Link na bio", "sv": "Länk i bion", "da": "Link i bio",
    "no": "Lenke i bio", "nb": "Lenke i bio", "pl": "Link w bio",
}
_INTERNAL_TAG = re.compile(r"^(?:no|not|hide|hidden|exclude|excluded|internal|draft|test)[-_ ]", re.I)
PRICE_RE = re.compile(
    r"(?:[£$€]\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{1,2})?\s?(?:GBP|USD|EUR|pounds?)\b)", re.I,
)


# ---------------------------------------------------------------------------
# Model output schema (lenient on shape, strict on substance)
# ---------------------------------------------------------------------------

def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value)
    if isinstance(value, dict):
        return _as_text(value.get("text") or value.get("caption") or "")
    return str(value)


class ChannelDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    caption: str = ""
    title: str = ""

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if data is None:
            return {}
        if isinstance(data, str):
            return {"caption": data}
        return data

    @field_validator("caption", "title", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return _as_text(value)


class CaptionDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: str = ""
    subheadline: str = ""
    cta: str = ""
    hook: str = ""
    alt_text: str = ""
    base_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    channels: dict[str, ChannelDraft] = Field(default_factory=dict)

    @field_validator("headline", "subheadline", "cta", "hook", "alt_text", "base_caption", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return _as_text(value)

    @field_validator("hashtags", mode="before")
    @classmethod
    def _tags(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [part for part in re.split(r"[,\s]+", value) if part]
        if isinstance(value, list):
            return [_as_text(item) for item in value if _as_text(item).strip()]
        raise ValueError("hashtags must be a list of strings")

    @field_validator("channels", mode="before")
    @classmethod
    def _channels(cls, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, list):  # [{"network": "x", "caption": "…"}]
            mapped: dict[str, Any] = {}
            for item in value:
                if isinstance(item, dict):
                    key = item.get("key") or item.get("channel") or item.get("network") or item.get("provider")
                    if key:
                        mapped[str(key)] = item
            return mapped
        return value

    @model_validator(mode="after")
    def _has_copy(self) -> "CaptionDraft":
        if not self.base_caption.strip() and not any(item.caption.strip() for item in self.channels.values()):
            raise ValueError("base_caption is empty and no channel captions were given")
        return self


# ---------------------------------------------------------------------------
# Settings and per-channel plan
# ---------------------------------------------------------------------------

def effective_caption_settings(settings: CaptionSettings, overrides: dict[str, Any] | None) -> CaptionSettings:
    """Campaign-level overrides (any CaptionSettings field) merged over the store settings."""
    if not overrides:
        return settings
    data = settings.model_dump()
    data.update({key: value for key, value in overrides.items() if key in CaptionSettings.model_fields})
    try:
        return CaptionSettings.model_validate(data)
    except PydanticValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        raise ValidationError(f"Invalid caption overrides: {problems}") from exc


@dataclass
class ChannelPlan:
    channel: CaptionChannel
    profile: ProviderProfile
    key: str                        # network family used in the prompt (instagram, linkedin, x…)
    max_length: int
    link_url: str
    link_mode: str                  # inline | bio | settings_link | first_comment | none
    hashtag_cap: int
    allow_cloud: bool
    measure: Callable[[str], int]


def network_key(provider: str) -> str:
    key = (provider or "").strip().lower()
    return PROVIDER_GROUPS.get(key, key or "other")


def channel_link(channel: CaptionChannel, captions: CaptionSettings, request: CaptionRequest, settings: EngineSettings) -> str:
    provider = (channel.provider or "").lower()
    if captions.link_policy == "none":
        return ""
    if captions.link_policy == "all_except_instagram" and provider.startswith("instagram"):
        return ""
    if captions.link_policy == "custom" and captions.custom_url.strip():
        return tracked_url(
            captions.custom_url.strip(), provider=provider,
            campaign=request.texts.get("campaign") or request.content_type,
            content=request.texts.get("utm_content") or request.content_type, settings=settings.utm,
        )
    return (channel.link_url or "").strip()


def plan_channels(request: CaptionRequest, settings: EngineSettings, captions: CaptionSettings) -> list[ChannelPlan]:
    plans: list[ChannelPlan] = []
    for channel in request.channels:
        profile = profile_for(channel.provider)
        limit = profile.max_length
        if channel.max_length and channel.max_length > 0:
            limit = min(limit, int(channel.max_length))
        link = channel_link(channel, captions, request, settings)
        mode = profile.link_policy if link else "none"
        plans.append(ChannelPlan(
            channel=channel, profile=profile, key=network_key(channel.provider), max_length=max(1, limit),
            link_url=link, link_mode=mode, hashtag_cap=max(0, min(captions.hashtags_count, profile.hashtags_max)),
            allow_cloud=network_key(channel.provider) not in NO_CLOUD_PROVIDERS,
            measure=measure_for(channel.provider),
        ))
    return plans


# ---------------------------------------------------------------------------
# Hashtags
# ---------------------------------------------------------------------------

def _collection_keys(value: Any) -> set[str]:
    raw: list[str] = []
    if isinstance(value, dict):
        raw = [str(value.get(key) or "") for key in ("id", "collection_id", "gid", "handle", "title")]
    else:
        raw = [str(value or "")]
    keys: set[str] = set()
    for item in raw:
        item = item.strip()
        if item:
            keys.add(item.casefold())
            keys.add(item.rstrip("/").rsplit("/", 1)[-1].casefold())
    return keys


def product_tags(products: list[dict], captions: CaptionSettings, excluded: list[str]) -> list[str]:
    if not captions.use_product_tags:
        return []
    blocked = {str(tag).strip().casefold() for tag in excluded}
    tags: list[str] = []
    for product in products:
        for tag in product.get("tags") or []:
            tag = str(tag or "").strip()
            if (
                tag and tag.casefold() not in blocked and ":" not in tag and len(tag) <= 30
                and not _INTERNAL_TAG.match(tag)
            ):
                tags.append(tag)
    return tags


def cloud_tags(products: list[dict], captions: CaptionSettings, rng: random.Random) -> list[str]:
    """Up to 5 random tags from the collection-mapped cloud(s), else the default cloud."""
    if not captions.hashtag_clouds:
        return []
    product_keys: set[str] = set()
    for product in products:
        for collection in product.get("collections") or []:
            product_keys |= _collection_keys(collection)
    matched = [
        cloud for cloud in captions.hashtag_clouds
        if any(_collection_keys(cid) & product_keys for cid in cloud.collection_ids)
    ]
    chosen = matched or [cloud for cloud in captions.hashtag_clouds if cloud.is_default]
    pool = normalise_hashtags(tag for cloud in chosen for tag in cloud.tags)
    rng.shuffle(pool)
    return pool[:CLOUD_SAMPLE]


def merge_hashtags(sources: list[list[str]], cap: int) -> list[str]:
    """Round-robin across sources (model, product tags, cloud) so each contributes."""
    queues = [normalise_hashtags(source) for source in sources]
    best_case: dict[str, str] = {}
    for queue in queues:  # 'Eveningritual' from the model loses to 'EveningRitual' from a tag
        for tag in queue:
            key = tag.casefold()
            if sum(char.isupper() for char in tag) > sum(char.isupper() for char in best_case.get(key, "")):
                best_case[key] = tag
    seen: set[str] = set()
    merged: list[str] = []
    while len(merged) < cap and any(queues):
        for queue in queues:
            if not queue or len(merged) >= cap:
                continue
            key = queue.pop(0).casefold()
            if key not in seen:
                seen.add(key)
                merged.append(best_case[key])
    return merged


# ---------------------------------------------------------------------------
# Text clean-up and assembly
# ---------------------------------------------------------------------------

def apply_emoji_level(text: str, level: str) -> str:
    cap = EMOJI_CAPS.get(level, 4)
    return strip_emojis(text) if cap == 0 else limit_emojis(text, cap)


def clean_body(text: str, captions: CaptionSettings) -> tuple[str, list[str], list[str]]:
    """Remove URLs, link-in-bio wording and hashtags (returned), price if off, banned terms, extra emojis."""
    value = remove_urls(text or "")
    value = remove_link_in_bio(value)
    value, tags = extract_hashtags(value)
    if not captions.include_price:
        value = PRICE_RE.sub("", value)
    value, removed = remove_terms(value, captions.banned_terms)
    value = apply_emoji_level(value, captions.emoji_level)
    return tidy(value), tags, removed


def short_text(text: str, limit: int, banned: list[str]) -> str:
    """On-image texts: no URLs, hashtags or emojis (renderer fonts lack emoji glyphs)."""
    value = URL_RE.sub("", text or "")
    value, _ = extract_hashtags(value)
    value, _ = remove_terms(value, banned)
    value = strip_emojis(value)
    value = " ".join(value.split()).strip(" \"'“”‘’")
    return truncate_words(value, limit)


def _language_key(language: str) -> str:
    return (language or "en").strip().lower().split("-")[0]


def link_lines(plan: ChannelPlan, captions: CaptionSettings, cta: str) -> tuple[str, str]:
    """Return (line appended to the caption, first comment) for this channel."""
    url = plan.link_url
    use_cta = captions.include_cta and bool(cta)
    if plan.link_mode == "inline":
        return (f"{cta}: {url}" if use_cta else url), ""
    if plan.link_mode == "bio":
        phrase = LINK_IN_BIO.get(_language_key(captions.language), LINK_IN_BIO["en"])
        line = f"{cta} — {phrase[0].lower()}{phrase[1:]}." if use_cta else f"{phrase}."
        comment = ""
        if captions.link_in_first_comment and plan.profile.supports_comments:
            comment = f"{cta}: {url}" if use_cta else url
        return line, comment
    if plan.link_mode == "first_comment":
        line = f"{cta} — link in the first comment." if use_cta else "Link in the first comment."
        return line, (f"{cta}: {url}" if use_cta else url)
    return "", ""  # settings_link (publisher puts it in provider settings) or none


def assemble(plan: ChannelPlan, body: str, link_line: str, tags: list[str], warnings: list[str]) -> tuple[str, list[str], str]:
    """Fit body + link + hashtags in the channel limit. Returns (caption, tags, link_line)."""
    measure = plan.measure
    limit = plan.max_length
    tags = list(tags)

    def _suffix() -> str:
        return "".join(f"\n\n{part}" for part in (link_line, " ".join(tags)) if part)

    min_body = min(measure(body), max(40, limit // 3))
    while True:
        room = limit - measure(_suffix())
        if room >= min_body or (not tags and not link_line):
            break
        if tags:
            tags.pop()
        elif plan.link_mode == "inline" and link_line != plan.link_url:
            link_line = plan.link_url          # drop the CTA words, keep the bare link
        else:
            warnings.append(
                f"{plan.profile.label}: the link did not fit within {limit} characters and was left out."
            )
            link_line = ""
    suffix = _suffix()
    trimmed = trim_to_limit(body, max(0, limit - measure(suffix)), measure) if body else ""
    caption = (trimmed + suffix).strip()
    if measure(caption) > limit:  # safety net; unreachable with additive measures
        caption = trim_to_limit(caption, limit, measure)
    return caption, tags, link_line


def make_title(plan: ChannelPlan, candidates: list[str], captions: CaptionSettings) -> str:
    if plan.profile.title_max <= 0:
        return ""
    for candidate in candidates:
        value = URL_RE.sub("", candidate or "")
        value, _ = extract_hashtags(value)
        value, _ = remove_terms(value, captions.banned_terms)
        value = strip_emojis(value) if captions.emoji_level == "off" else limit_emojis(value, 1)
        value = " ".join(value.split()).strip(" \"'“”")
        if value:
            return truncate_words(value, plan.profile.title_max)
    return ""


def _allowed_prices(products: list[dict], settings: EngineSettings, texts: dict[str, str]) -> list[float]:
    values: list[float] = []
    for product in products:
        price, price_max, compare = prompts.product_price(product, settings.brand)
        values.extend(value for value in (price, price_max, compare) if value > 0)
        discount = float(product.get("discount_pct") or 0)
        if compare > 0 and discount > 0:
            values.append(compare * (1 - discount / 100))
    for text in texts.values():
        values.extend(_amount(match.group(0)) for match in PRICE_RE.finditer(str(text or "")))
    return values


def _amount(text: str) -> float:
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(digits)
    except ValueError:
        return -1.0


# ---------------------------------------------------------------------------
# Result building (shared by AI and template captions)
# ---------------------------------------------------------------------------

def _seed(request: CaptionRequest) -> int:
    ids = ",".join(str(product.get("product_id") or product.get("id") or "") for product in request.products)
    return zlib.crc32(f"{request.correlation_id}|{request.content_type}|{ids}".encode())


def _first_title(request: CaptionRequest) -> str:
    for product in request.products:
        if product.get("title"):
            return str(product["title"])
    return str(request.texts.get("blog_title") or request.texts.get("promo_text") or "")


def build_result(
    draft: CaptionDraft,
    plans: list[ChannelPlan],
    request: CaptionRequest,
    settings: EngineSettings,
    captions: CaptionSettings,
    *,
    model: str = "",
    used_fallback: bool = False,
    source: str = "ai",
) -> CaptionResult:
    warnings: list[str] = []
    banned = captions.banned_terms
    brand = settings.brand
    title = _first_title(request)

    headline = short_text(draft.headline, FIELD_LIMITS["headline"], banned) or truncate_words(title, FIELD_LIMITS["headline"])
    subheadline = short_text(draft.subheadline, FIELD_LIMITS["subheadline"], banned)
    # A clipped call to action ("Shop the") is worse than none: if the model's CTA
    # doesn't fit, use the brand CTA instead of cutting it.
    model_cta = short_text(draft.cta, 200, banned)
    cta = model_cta if 0 < len(model_cta) <= FIELD_LIMITS["cta"] else truncate_words(brand.cta_text, FIELD_LIMITS["cta"])
    hook = short_text(draft.hook, FIELD_LIMITS["hook"], banned)
    alt_text = short_text(draft.alt_text, FIELD_LIMITS["alt_text"], banned)
    if not alt_text and title:
        alt_text = truncate_words(f"{title} from {brand.business_name}".strip(), FIELD_LIMITS["alt_text"])

    first_channel_caption = next((item.caption for item in draft.channels.values() if item.caption.strip()), "")
    base_caption, base_tags, removed = clean_body(draft.base_caption or first_channel_caption, captions)
    removed_terms: set[str] = set(removed)

    model_tags = [*draft.hashtags, *base_tags]
    prod_tags = product_tags(request.products, captions, settings.content.excluded_tags)
    rng = random.Random(_seed(request))
    clouds = cloud_tags(request.products, captions, rng)
    allowed_prices = _allowed_prices(request.products, settings, request.texts)

    channel_captions: dict[str, ChannelCaption] = {}
    for plan in plans:
        channel = plan.channel
        item = draft.channels.get(channel.channel_id) or draft.channels.get(plan.key) or draft.channels.get(channel.provider)
        raw = item.caption if item and item.caption.strip() else ""
        if raw:
            body, inline_tags, removed = clean_body(raw, captions)
            removed_terms.update(removed)
        else:
            body, inline_tags = base_caption, []
        sources = [model_tags + inline_tags, prod_tags, clouds if plan.allow_cloud else []]
        if plan.key == "youtube":
            sources.insert(0, ["Shorts"])
        tags = merge_hashtags(sources, plan.hashtag_cap)
        link_line, first_comment = link_lines(plan, captions, cta)
        caption, tags, link_line = assemble(plan, body, link_line, tags, warnings)
        channel_title = make_title(plan, [item.title if item else "", headline, title], captions)
        if captions.include_price and allowed_prices:
            for match in PRICE_RE.finditer(caption):
                amount = _amount(match.group(0))
                if amount >= 0 and not any(abs(amount - allowed) < 0.011 for allowed in allowed_prices):
                    warnings.append(f"{plan.profile.label} caption mentions {match.group(0)}, which does not match the product price.")
        channel_captions[channel.channel_id] = ChannelCaption(
            channel_id=channel.channel_id, provider=channel.provider, caption=caption,
            first_comment=first_comment, title=channel_title, hashtags=tags,
        )

    for term in sorted(removed_terms, key=str.casefold):
        warnings.append(f"Removed banned term '{term}' from the captions.")
    return CaptionResult(
        base_caption=base_caption,
        hashtags=merge_hashtags([model_tags, prod_tags], captions.hashtags_count),
        alt_text=alt_text,
        channel_captions=channel_captions,
        headline=headline,
        subheadline=subheadline,
        cta=cta,
        hook=hook,
        model=model,
        used_fallback=used_fallback,
        source=source,
        warnings=list(dict.fromkeys(warnings)),
    )


# ---------------------------------------------------------------------------
# AI captions
# ---------------------------------------------------------------------------

def _network_specs(plans: list[ChannelPlan], captions: CaptionSettings, cta: str) -> tuple[list[str], list[str]]:
    by_key: dict[str, list[ChannelPlan]] = {}
    for plan in plans:
        by_key.setdefault(plan.key, []).append(plan)
    lines: list[str] = []
    for key, group in by_key.items():
        budget = 10_000
        for plan in group:
            link_line, _ = link_lines(plan, captions, cta)
            reserve = (plan.measure(link_line) + 2 if link_line else 0) + (plan.hashtag_cap * 18 + 2 if plan.hashtag_cap else 0)
            budget = min(budget, plan.max_length - reserve)
        budget = max(60, min(prompts.TARGET_CAPTION_LENGTH.get(key, 400), budget))
        modes = {plan.link_mode for plan in group}
        if modes & {"inline", "bio", "first_comment"}:
            link_note = (
                "A call to action with the link is appended automatically, so do not write your own call to action."
                if captions.include_cta else "The link is appended automatically; no call to action."
            )
        elif "settings_link" in modes:
            link_note = (
                "End with a short call to action without any link or 'link in bio' (the pin itself links to the product)."
                if captions.include_cta else "No call to action and no links (the pin itself links to the product)."
            )
        else:
            link_note = (
                "This post has no link: any call to action must not mention a link or the bio."
                if captions.include_cta else "No link and no call to action."
            )
        title_max = max(plan.profile.title_max for plan in group)
        lines.append(prompts.network_line(key, group[0].profile, budget=budget, link_note=link_note, title_max=title_max))
    return lines, list(by_key)


def caption_llm_request(request: CaptionRequest, settings: EngineSettings, captions: CaptionSettings, plans: list[ChannelPlan], feedback: str = "") -> LLMRequest:
    cta = truncate_words(settings.brand.cta_text, FIELD_LIMITS["cta"])
    network_lines, keys = _network_specs(plans, captions, cta)
    prompt = prompts.caption_prompt(
        content_type=request.content_type,
        products_text=prompts.products_block(request.products, settings.brand, include_price=captions.include_price),
        details_text=prompts.details_block(request.texts),
        angle=request.angle,
        brief=request.brief,
        captions=captions,
        brand=settings.brand,
        network_lines=network_lines,
        network_keys=keys,
        feedback=feedback,
    )
    return LLMRequest(
        system=prompts.caption_system_prompt(settings.brand, captions),
        prompt=prompt,
        json_mode=True,
        temperature=settings.ai.temperature,
        max_tokens=max(settings.ai.max_tokens, min(8192, 700 + 380 * len(keys))),
    )


def _claim_feedback(result: CaptionResult, settings: EngineSettings) -> list[str]:
    issues = check_captions(result, settings=settings).issues
    return list(dict.fromkeys(issue.message for issue in issues if issue.code in CLAIM_CODES))


async def generate_captions(request: CaptionRequest, sctx: StoreContext) -> CaptionResult:
    """Write captions for every channel of a post.

    Uses template captions when the effective caption source is "template",
    or when the model's output stays unusable after repair (with a warning).
    Raises ExternalServiceError when no model could be reached (the job retries).
    """
    settings = sctx.settings
    captions = effective_caption_settings(settings.captions, request.caption_overrides)
    if captions.source == "template":
        return template_captions(request, sctx)
    if not request.channels:
        raise ValidationError("The caption request has no channels.")
    plans = plan_channels(request, settings, captions)
    router = get_router(sctx)
    ctx = sctx.call_context(request.correlation_id)
    retries = settings.guards.max_caption_retries
    feedback = ""
    rewrites = 0
    for attempt in range(retries + 1):
        llm_request = caption_llm_request(request, settings, captions, plans, feedback)
        try:
            draft, meta = await router.structured(
                "captions", llm_request, CaptionDraft, ctx=ctx, decision_type="captions",
                subject_id=request.correlation_id,
            )
        except ValidationError as exc:
            result = template_captions(request, sctx)
            result.warnings.insert(0, f"AI captions were unusable, so template captions were used ({exc.message}).")
            return result
        result = build_result(
            draft, plans, request, settings, captions, model=meta.model, used_fallback=meta.used_fallback, source="ai",
        )
        problems = _claim_feedback(result, settings)
        if not problems:
            if rewrites:
                result.warnings.append(f"Captions were rewritten {rewrites} time(s) to remove claim wording.")
            return result
        if attempt >= retries:
            result.warnings.append(
                "Claim wording remained after rewriting; the content guard will flag it." if rewrites
                else "Claim wording found; the content guard will flag it."
            )
            return result
        rewrites += 1
        logger.info(
            "Rewriting captions to remove claim wording: %s", "; ".join(problems)[:300],
            extra={"operation": "social_engine.llm.captions", "store_id": sctx.store_id,
                   "correlation_id": request.correlation_id},
        )
        feedback = (
            "The previous draft used wording that is not allowed for food supplements. Rewrite every caption "
            "without these statements (describe ingredients, ritual and authorised-style wording from the "
            "product data instead):\n" + "\n".join(f"- {problem}" for problem in problems[:8])
        )
    raise ValidationError("Caption generation ended without a result.")  # pragma: no cover


# ---------------------------------------------------------------------------
# Template captions (Outfy non-AI mode)
# ---------------------------------------------------------------------------

PLACEHOLDERS = (
    "PRODUCT_TITLE", "PRODUCT_DESC", "PRODUCT_PRICE", "PRODUCT_TAGS", "PRODUCT_URL", "STORE_URL", "TAG_CLOUD",
    "STORE_NAME", "PROMO_TEXT", "DISCOUNT_TEXT", "COUPON_CODE", "OCCASION", "ENDS_AT", "QUOTE_TEXT",
    "BLOG_TITLE", "BLOG_EXCERPT",
)
_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*(?P<a>%s)\s*\}\}|\{\s*(?P<b>%s)\s*\}|\[\s*(?P<c>%s)\s*\]|\b(?P<d>%s)\b"
    % tuple(["|".join(PLACEHOLDERS)] * 4)
)
_TRAILING_SEPARATOR = re.compile(r"[ \t]*[—–\-:|•,][ \t]*$")

_OPENERS: dict[str, str] = {
    ContentType.NEW_ARRIVAL: "New in: PRODUCT_TITLE",
    ContentType.BACK_IN_STOCK: "Back in stock: PRODUCT_TITLE",
    ContentType.PRODUCT_OF_THE_DAY: "Product of the day: PRODUCT_TITLE",
    ContentType.SALE_PROMO: "PROMO_TEXT\nPRODUCT_TITLE\nDISCOUNT_TEXT\nUse code COUPON_CODE at checkout.",
    ContentType.COUNTDOWN: "PROMO_TEXT\nPRODUCT_TITLE\nEnds ENDS_AT.",
    ContentType.COLLAGE: "Our edit: PRODUCT_TITLE",
    ContentType.COLLECTION_ROUNDUP: "Our edit: PRODUCT_TITLE",
    ContentType.CAROUSEL: "Swipe through: PRODUCT_TITLE",
    ContentType.HOLIDAY: "OCCASION\nPRODUCT_TITLE",
    ContentType.QUOTE: "QUOTE_TEXT\n— STORE_NAME",
    ContentType.BLOG_ARTICLE: "BLOG_TITLE\n\nBLOG_EXCERPT",
}
_NO_DESCRIPTION = frozenset({ContentType.QUOTE, ContentType.BLOG_ARTICLE})


def builtin_template(content_type: str, *, short: bool) -> str:
    opener = _OPENERS.get(content_type, "PRODUCT_TITLE")
    if content_type in _NO_DESCRIPTION:
        return opener
    return f"{opener}\nPRODUCT_PRICE" if short else f"{opener}\n\nPRODUCT_DESC\n\nPRODUCT_PRICE"


def render_template(template: str, values: dict[str, str]) -> str:
    """Replace placeholders; drop lines whose placeholders all rendered empty."""
    def _key(match: re.Match) -> str:
        return match.group("a") or match.group("b") or match.group("c") or match.group("d")

    lines: list[str] = []
    for line in (template or "").split("\n"):
        found = [values.get(_key(match), "") for match in _PLACEHOLDER_RE.finditer(line)]
        if found and not any(value.strip() for value in found):
            continue
        rendered = _PLACEHOLDER_RE.sub(lambda match: values.get(_key(match), ""), line)
        lines.append(_TRAILING_SEPARATOR.sub("", rendered) if found else rendered)
    return tidy("\n".join(lines))


def _join_titles(titles: list[str]) -> str:
    titles = [title for title in titles if title]
    if len(titles) <= 1:
        return titles[0] if titles else ""
    return ", ".join(titles[:-1]) + " & " + titles[-1]


def placeholder_values(request: CaptionRequest, sctx: StoreContext, captions: CaptionSettings) -> dict[str, str]:
    settings = sctx.settings
    products = request.products
    first = products[0] if products else {}
    description = strip_html(str(first.get("description") or ""))
    rng = random.Random(_seed(request))
    store_url = sctx.storefront_url
    texts = request.texts
    return {
        "PRODUCT_TITLE": _join_titles([str(product.get("title") or "").strip() for product in products]),
        "PRODUCT_DESC": trim_to_limit(description, 280) if description else "",
        "PRODUCT_PRICE": prompts.price_text(first, settings.brand) if (captions.include_price and first) else "",
        "PRODUCT_TAGS": " ".join(normalise_hashtags(product_tags(
            products, captions.model_copy(update={"use_product_tags": True}), settings.content.excluded_tags,
        ))),
        "PRODUCT_URL": "",   # the link is placed by the channel's link policy
        "STORE_URL": re.sub(r"^https?://", "", store_url).rstrip("/") if store_url else "",
        "TAG_CLOUD": " ".join(cloud_tags(products, captions, rng)),
        "STORE_NAME": settings.brand.business_name,
        "PROMO_TEXT": str(texts.get("promo_text") or ""),
        "DISCOUNT_TEXT": str(texts.get("discount_text") or ""),
        "COUPON_CODE": str(texts.get("coupon_code") or ""),
        "OCCASION": str(texts.get("occasion") or ""),
        "ENDS_AT": str(texts.get("ends_at") or texts.get("end_date") or ""),
        "QUOTE_TEXT": str(texts.get("quote") or ""),
        "BLOG_TITLE": str(texts.get("blog_title") or ""),
        "BLOG_EXCERPT": str(texts.get("blog_excerpt") or ""),
    }


def template_captions(request: CaptionRequest, sctx: StoreContext) -> CaptionResult:
    """Outfy's non-AI captions: per-provider templates (settings.captions.templates
    keyed by provider or "default") with built-in defaults per content type."""
    settings = sctx.settings
    captions = effective_caption_settings(settings.captions, request.caption_overrides)
    plans = plan_channels(request, settings, captions)
    values = placeholder_values(request, sctx, captions)
    templates = captions.templates
    channels: dict[str, ChannelDraft] = {}
    for plan in plans:
        template = (
            templates.get(plan.channel.provider) or templates.get(plan.key) or templates.get("default")
            or builtin_template(request.content_type, short=plan.max_length <= 500)
        )
        channel_values = values if plan.allow_cloud else {**values, "TAG_CLOUD": ""}
        channels[plan.channel.channel_id] = ChannelDraft(caption=render_template(template, channel_values))
    base_template = templates.get("default") or builtin_template(request.content_type, short=False)
    first_title = _first_title(request)
    draft = CaptionDraft.model_construct(
        headline=first_title,
        subheadline=str(request.texts.get("promo_text") or request.texts.get("discount_text") or ""),
        cta=settings.brand.cta_text,
        hook="",
        alt_text=f"{first_title} from {settings.brand.business_name}".strip() if first_title else "",
        base_caption=render_template(base_template, {**values, "TAG_CLOUD": ""}),  # clouds are per channel
        hashtags=[],
        channels=channels,
    )
    return build_result(draft, plans, request, settings, captions, model="", used_fallback=False, source="template")
