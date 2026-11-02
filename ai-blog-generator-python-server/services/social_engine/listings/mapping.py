"""Map a directory form (snapshot refs) to profile fields.

Label heuristics first — they cover every ordinary form and need no model.
Gemma is asked only about the fields that stay unmatched, and its answer is
validated against the refs and field names it was shown. Fields that take
money, identity or passwords are never filled: they are listed for the owner.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

from pydantic import BaseModel, Field

from ..ai.base import LLMRequest
from ..ai.llm import get_router
from ..context import StoreContext
from .relay import Snapshot

logger = logging.getLogger("ai_blog_server.social_engine.listings")

NEVER_FILL = re.compile(
    r"password|passcode|card number|cvc|cvv|expiry|sort code|account number|iban|vat|company number|"
    r"companies house|passport|driving licence|national insurance|date of birth|dob|captcha|verification code|"
    r"one.time|otp|security code|utr|charity", re.I,
)

# Order matters: the first matching rule wins. (pattern, profile value key)
RULES: tuple[tuple[str, str], ...] = (
    (r"first name|forename|given name", "founder_first_name"),
    (r"last name|surname|family name", "founder_last_name"),
    (r"your name|contact name|full name|owner name|name of (the )?(contact|owner|person)", "founder_name"),
    (r"business name|company name|organi[sz]ation( name)?|trading name|store name|shop name|name of (your )?(business|company|organi[sz]ation|venue|listing)|^(business|company|listing|page) ?(title)?$|^name$|^title$", "business_name"),
    (r"e-?mail", "email"),
    (r"mobile|telephone|phone|contact number", "phone"),
    (r"web ?site|web address|\burl\b|home ?page|domain", "website"),
    (r"tagline|slogan|strap ?line|headline|one.?liner", "tagline"),
    (r"short (description|bio|summary)|summary|snippet|meta description|brief description", "short_description"),
    (r"long description|full description|about (the|your) (business|company)|tell us about|^about( us)?$|^bio(graphy)?$|overview|^description$|describe", "description"),
    (r"keywords?|tags|search terms", "keywords"),
    (r"categor|industry|business type|sector|what (do you|does your business) do|services? offered|speciali", "category"),
    (r"address line ?1|street( address)?|address ?1|^address$|building|house (name|number)", "address_line1"),
    (r"address line ?2|address ?2|^apartment|^suite|^unit", "address_line2"),
    (r"\bcity\b|\btown\b|locality", "city"),
    (r"post ?code|zip|postal code", "postcode"),
    (r"\bcountry\b", "country"),
    (r"county|region|state|province", "city"),
    (r"service area|areas? (you )?(serve|cover)|coverage", "service_area"),
    (r"opening (hours|times)|hours", "hours"),
    (r"year (founded|established|started)|founded|established since|in business since", "founded_year"),
    (r"instagram", "social_instagram"),
    (r"facebook", "social_facebook"),
    (r"tiktok", "social_tiktok"),
    (r"youtube", "social_youtube"),
    (r"linkedin", "social_linkedin"),
    (r"twitter|\bx\b (profile|url|handle)|x\.com", "social_x"),
    (r"pinterest", "social_pinterest"),
    (r"payment (methods|options|types)|accepted payments", "payment_methods"),
    (r"price range|pricing", "price_range"),
    (r"logo", "logo_url"),
    (r"cover (photo|image)|banner", "cover_image_url"),
)
_COMPILED = tuple((re.compile(p, re.I), key) for p, key in RULES)
TEXT_ROLES = {"textbox", "combobox", "searchbox", "spinbutton"}


@dataclass
class FieldPlan:
    ref: str
    label: str
    key: str
    value: str
    source: str = "rule"     # rule | model


@dataclass
class MappingResult:
    fill: list[FieldPlan] = field(default_factory=list)
    left_for_you: list[dict] = field(default_factory=list)   # [{label, reason}]
    unmatched: list[dict] = field(default_factory=list)      # [{ref, label}] text fields nothing matched
    model: str = ""

    def summary(self) -> dict:
        return {
            "filled": [{"label": p.label, "field": p.key, "chars": len(p.value), "source": p.source} for p in self.fill],
            "left_for_you": self.left_for_you,
            "unmatched": [u["label"] for u in self.unmatched],
            "model": self.model,
        }


def match_label(label: str) -> str:
    """The profile value key a form label maps to ('' when nothing fits)."""
    text = re.sub(r"[\*:]+$", "", (label or "").strip())
    if not text:
        return ""
    for pattern, key in _COMPILED:
        if pattern.search(text):
            return key
    return ""


def plan_by_rules(snapshot: Snapshot, values: dict[str, str]) -> MappingResult:
    result = MappingResult()
    used: set[str] = set()
    for item in snapshot.fields():
        label = item["name"]
        if item["role"] not in TEXT_ROLES:
            continue                     # checkboxes, radios and lists are the owner's choices
        if NEVER_FILL.search(label):
            result.left_for_you.append({"label": label, "reason": "money, identity or password: yours to type"})
            continue
        key = match_label(label)
        if not key:
            result.unmatched.append({"ref": item["ref"], "label": label})
            continue
        value = values.get(key, "")
        if not value:
            result.left_for_you.append({"label": label, "reason": f"profile has no {key.replace('_', ' ')}"})
            continue
        if key in used and key in {"business_name", "email", "phone", "website"}:
            pass                         # repeated confirm fields get the same value
        used.add(key)
        result.fill.append(FieldPlan(item["ref"], label, key, value))
    return result


class _Guess(BaseModel):
    ref: str
    field: str = ""


class _Guesses(BaseModel):
    matches: list[_Guess] = Field(default_factory=list)


async def refine_with_model(sctx: StoreContext, result: MappingResult, values: dict[str, str], *, subject_id: str) -> MappingResult:
    """Ask Gemma about the unmatched text fields only; keep answers that name real refs and real fields."""
    if not result.unmatched:
        return result
    keys = sorted(k for k in values if k not in {"full_address"})
    lines = [
        "A business is filling in a directory form. For each form field below, say which profile field belongs in it,",
        "or leave `field` empty when none of them fits (for example questions, passwords, choices, or anything about",
        "payment or identity). Only use the profile field names given.",
        "",
        "Profile fields: " + ", ".join(keys),
        "",
        "Form fields:",
        *[f"- ref {u['ref']}: \"{u['label']}\"" for u in result.unmatched[:30]],
        "",
        'Return JSON: {"matches": [{"ref": "...", "field": "..."}]}',
    ]
    try:
        draft, meta = await get_router(sctx).structured(
            "listings.map", LLMRequest(system="You map web-form fields to a business profile.", prompt="\n".join(lines),
                                       json_mode=True, temperature=0, max_tokens=600),
            _Guesses, ctx=sctx.call_context(subject_id), decision_type="listings.map", subject_id=subject_id,
        )
    except Exception as exc:
        logger.info("Field mapping without the model: %s", exc, extra={"store_id": sctx.store_id})
        return result
    by_ref = {u["ref"]: u for u in result.unmatched}
    taken: set[str] = set()
    for guess in draft.matches:
        item = by_ref.get(guess.ref)
        key = (guess.field or "").strip()
        if not item or key not in values or guess.ref in taken or NEVER_FILL.search(item["label"]):
            continue
        taken.add(guess.ref)
        result.fill.append(FieldPlan(guess.ref, item["label"], key, values[key], source="model"))
    result.unmatched = [u for u in result.unmatched if u["ref"] not in taken]
    result.model = meta.model
    return result
