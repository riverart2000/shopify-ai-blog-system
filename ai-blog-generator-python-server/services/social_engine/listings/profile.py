"""The one business profile every directory copies, and the copy Gemma drafts for it."""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from ..ai.base import LLMRequest
from ..ai.guard import claim_issues
from ..ai.llm import get_router
from ..context import StoreContext
from ..settings import ListingProfile
from .catalogue import Directory

logger = logging.getLogger("ai_blog_server.social_engine.listings")

REQUIRED = ("business_name", "short_description", "description", "email", "phone", "website")
RECOMMENDED = ("tagline", "long_description", "founder_name", "founder_bio", "postcode", "logo_url", "keywords", "services")
FIELD_LABELS = {
    "business_name": "business name", "tagline": "tagline", "short_description": "short description",
    "description": "description", "long_description": "long description", "founder_name": "founder name",
    "founder_bio": "founder bio", "email": "email", "phone": "phone", "website": "website", "postcode": "postcode",
    "logo_url": "logo", "keywords": "keywords", "services": "services", "address_line1": "street address",
}


def seeded_profile(sctx: StoreContext) -> ListingProfile:
    """The stored profile with blanks filled from the brand kit and the store record."""
    profile = sctx.settings.listings.profile.model_copy()
    brand = sctx.settings.brand
    if not profile.business_name:
        profile.business_name = brand.business_name or str(sctx.store.get("name") or "")
    if not profile.website:
        profile.website = sctx.storefront_url
    if not profile.description and brand.description:
        profile.description = brand.description[:1200]
    if not profile.tagline and brand.slogan:
        profile.tagline = brand.slogan[:120]
    if not profile.email:
        profile.email = str(sctx.store.get("contact_email") or sctx.store.get("email") or "")
    return profile


def missing_fields(profile: ListingProfile) -> dict[str, list[str]]:
    def empty(name: str) -> bool:
        value = getattr(profile, name, None)
        return not value
    return {
        "required": [FIELD_LABELS.get(f, f) for f in REQUIRED if empty(f)],
        "recommended": [FIELD_LABELS.get(f, f) for f in RECOMMENDED if empty(f)],
    }


def _cut(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    for stop in (". ", "! ", "? "):
        idx = cut.rfind(stop)
        if idx >= limit // 2:
            return cut[: idx + 1].strip()
    idx = cut.rfind(" ")
    return (cut[:idx] if idx >= limit // 2 else cut).rstrip(",;:- ") + "…"


def description_for(profile: ListingProfile, directory: Directory, field: str = "description") -> str:
    """The best description that fits this directory's limit: longest variant first, cut at a sentence."""
    limit = directory.limits.get(field, 0)
    candidates = {
        "tagline": [profile.tagline, profile.short_description],
        "short_description": [profile.short_description, profile.description, profile.tagline],
        "description": [profile.description, profile.long_description, profile.short_description],
        "long_description": [profile.long_description, profile.description, profile.short_description],
    }.get(field, [profile.description])
    texts = [t for t in candidates if t]
    if not texts:
        return ""
    if limit:
        for text in texts:
            if len(text) <= limit:
                return text
        return _cut(texts[0], limit)
    return texts[0]


def values_for(profile: ListingProfile, directory: Directory) -> dict[str, str]:
    """Every profile field as the text a form would take, with descriptions cut to the directory's limits."""
    values: dict[str, str] = {}
    for name in ListingProfile.model_fields:
        value = getattr(profile, name)
        if isinstance(value, list):
            values[name] = ", ".join(str(v) for v in value if v)
        elif isinstance(value, dict):
            for key, link in value.items():
                if link:
                    values[f"social_{key}"] = str(link)
        elif value is None:
            values[name] = ""
        else:
            values[name] = str(value)
    for field in ("tagline", "short_description", "description", "long_description"):
        values[field] = description_for(profile, directory, field)
    names = profile.founder_name.split()
    values["founder_first_name"] = names[0] if names else ""
    values["founder_last_name"] = " ".join(names[1:]) if len(names) > 1 else ""
    values["full_address"] = ", ".join(p for p in (profile.address_line1, profile.address_line2, profile.city, profile.postcode) if p)
    values["category"] = profile.categories[0] if profile.categories else ""
    return {k: v for k, v in values.items() if v}


# ---------------------------------------------------------------------- Gemma writes the copy

class ProfileCopy(BaseModel):
    tagline: str = Field("", max_length=160)
    short_description: str = Field("", max_length=400)
    description: str = Field("", max_length=1400)
    long_description: str = Field("", max_length=4200)
    founder_bio: str = Field("", max_length=1600)
    keywords: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


COPY_FIELDS = ("tagline", "short_description", "description", "long_description", "founder_bio", "keywords", "categories")


def _copy_prompt(sctx: StoreContext, profile: ListingProfile, products: list[dict]) -> str:
    brand = sctx.settings.brand
    types = sorted({str(p.get("product_type") or "").strip() for p in products if p.get("product_type")})[:12]
    titles = [str(p.get("title") or "") for p in products[:20]]
    lines = [
        "Write the directory-listing copy for this business. Directories show it next to the name, so it must be",
        "plain, specific and true. Describe what is sold and for whom; never promise a health result, weight loss,",
        "body shaping, pain relief or any medical effect (UK advertising rules). No hashtags, no emoji, no prices.",
        "",
        f"Business: {profile.business_name or brand.business_name}",
        f"Website: {profile.website or sctx.storefront_url}",
        f"What it sells (owner's words): {brand.what_you_sell or brand.description or '—'}",
        f"Current description: {profile.description or brand.description or '—'}",
        f"Founder: {profile.founder_name or '—'}; bio notes: {profile.founder_bio or '—'}",
        f"Services: {', '.join(profile.services) or '—'}",
        f"Service area: {profile.service_area}",
        f"Product types: {', '.join(types) or '—'}",
        f"Example products: {'; '.join(titles) or '—'}",
        "",
        "Return JSON with exactly these keys:",
        "tagline (under 100 characters), short_description (under 250 characters, one or two sentences),",
        "description (under 700 characters), long_description (under 2000 characters, three short paragraphs:",
        "what you sell, who it is for and the coaching/workshops, how to buy), founder_bio (under 900 characters,",
        "third person), keywords (8 to 12 short search phrases), categories (3 directory categories).",
    ]
    return "\n".join(lines)


async def suggest_copy(sctx: StoreContext, *, products: list[dict] | None = None) -> dict[str, Any]:
    """Draft the listing copy with Gemma; every field is checked for claims before it is offered."""
    profile = seeded_profile(sctx)
    router = get_router(sctx)
    draft, meta = await router.structured(
        "listings.copy",
        LLMRequest(system="You write accurate, calm business descriptions for UK directories.",
                   prompt=_copy_prompt(sctx, profile, products or []), json_mode=True,
                   temperature=sctx.settings.ai.decision_temperature, max_tokens=2200),
        ProfileCopy, ctx=sctx.call_context("listings:copy"), decision_type="listings.copy", subject_id="profile",
    )
    warnings: list[str] = []
    copy = draft.model_dump()
    for field in ("tagline", "short_description", "description", "long_description", "founder_bio"):
        text = re.sub(r"\s+", " ", str(copy.get(field) or "")).strip()
        issues = claim_issues(text, "listing", field)
        if issues:
            warnings.append(f"{FIELD_LABELS.get(field, field)}: {'; '.join(i.message for i in issues[:3])}")
        copy[field] = text
    copy["keywords"] = [str(k).strip() for k in copy.get("keywords") or [] if str(k).strip()][:12]
    copy["categories"] = [str(c).strip() for c in copy.get("categories") or [] if str(c).strip()][:3]
    return {"copy": copy, "warnings": warnings, "model": meta.model}
