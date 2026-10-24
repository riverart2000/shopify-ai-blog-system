"""Gemma as the auto-pilot's brain.

Every function validates the model's choices against what it was given:
invented ids, disallowed content types, unknown templates, invented numbers
and claim wording are dropped and reported, never passed on. The caller fills
any gaps (`uncovered_slot_ids`) with its deterministic strategy.
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..context import StoreContext
from ..errors import ValidationError
from ..models import ContentType
from . import prompts
from .base import LLMRequest
from .guard import claim_issues
from .llm import get_router
from .text import URL_RE, extract_hashtags, find_terms, strip_emojis, tidy, trim_to_limit

logger = logging.getLogger("ai_blog_server.social_engine.ai")

MAX_CANDIDATES = 40
PROMO_TEXT_MAX = 40
BrandKind = Literal["quote", "fact", "poll", "quiz", "brand_awareness", "brand_value"]

# (minimum, maximum) products per content type; maxima are further capped by
# `max_products_per_post`.
PRODUCT_RULES: dict[str, tuple[int, int]] = {
    ContentType.SINGLE_PRODUCT: (1, 1),
    ContentType.NEW_ARRIVAL: (1, 1),
    ContentType.BACK_IN_STOCK: (1, 1),
    ContentType.PRODUCT_OF_THE_DAY: (1, 1),
    ContentType.PRODUCT_VIDEO: (1, 1),
    ContentType.AI_IMAGE_AD: (1, 1),
    ContentType.AI_VIDEO: (1, 1),
    ContentType.SALE_PROMO: (1, 4),
    ContentType.COUNTDOWN: (0, 4),
    ContentType.COLLAGE: (1, 4),
    ContentType.CAROUSEL: (1, 10),
    ContentType.COLLECTION_ROUNDUP: (2, 4),
    ContentType.HOLIDAY: (0, 1),
    ContentType.QUOTE: (0, 0),
    ContentType.BLOG_ARTICLE: (0, 0),
}


def normalise_content_type(value: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


def product_bounds(content_type: str, max_products_per_post: int) -> tuple[int, int]:
    low, high = PRODUCT_RULES.get(content_type, (1, 1))
    if high > 1:
        high = max(low, min(high, max_products_per_post))
    return low, high


def _as_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        return [_as_str(value)] if _as_str(value) else []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("text") or item.get("id") or item.get("product_id") or item.get("value")
            if _as_str(item):
                out.append(_as_str(item))
        return out
    raise ValueError("expected a list")


# ---------------------------------------------------------------------------
# Content plan
# ---------------------------------------------------------------------------

class PlannedSlot(BaseModel):
    slot_id: str
    product_ids: list[str] = Field(default_factory=list)
    content_type: str
    template_key: str = ""
    angle: str = ""
    reason: str = ""


class RejectedChoice(BaseModel):
    slot_id: str = ""
    field: str = ""
    value: Any = None
    reason: str


class ContentPlan(BaseModel):
    slots: list[PlannedSlot] = Field(default_factory=list)
    uncovered_slot_ids: list[str] = Field(default_factory=list)
    rejected: list[RejectedChoice] = Field(default_factory=list)
    summary: str = ""
    model: str = ""
    used_fallback: bool = False
    source: str = "ai"            # ai | none (AI decisions off, nothing feasible, or unusable output)
    error: str = ""


class _SlotDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slot_id: str
    product_ids: list[str] = Field(default_factory=list)
    content_type: str = ""
    template_key: str = ""
    angle: str = ""
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data: Any) -> Any:
        if isinstance(data, dict) and "product_ids" not in data:
            for alias in ("products", "product_id", "product"):
                if alias in data:
                    data = {**data, "product_ids": data[alias]}
                    break
        return data

    @field_validator("slot_id", "content_type", "template_key", "angle", "reason", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return _as_str(value)

    @field_validator("product_ids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> list[str]:
        return _as_str_list(value)


class _PlanDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slots: list[_SlotDraft] = Field(default_factory=list)
    summary: str = ""

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if isinstance(data, list):
            return {"slots": data}
        if isinstance(data, dict) and "slots" not in data:
            for alias in ("plan", "posts", "choices"):
                if isinstance(data.get(alias), list):
                    return {**data, "slots": data[alias]}
        return data

    @field_validator("summary", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return _as_str(value)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pre_rank_candidates(
    candidates: list[dict],
    *,
    recent_posts: Iterable[dict] = (),
    allow_repeats: bool = False,
    limit: int = MAX_CANDIDATES,
) -> list[dict]:
    """Deterministic shortlist so the prompt stays compact.

    Favours products not posted for a while (never-posted first), strong
    performers, sale and new products and rarely-posted products; penalises
    products in the most recent posts. Ties break on title then id.
    """
    recent_ids: list[str] = []
    for post in recent_posts:
        recent_ids.extend(str(pid) for pid in post.get("product_ids") or [])
    very_recent = set(recent_ids[:10])
    unique: dict[str, dict] = {}
    for candidate in candidates:
        pid = _as_str(candidate.get("product_id"))
        if pid and pid not in unique:
            unique[pid] = candidate
    top_perf = max((_float(c.get("performance_score")) for c in unique.values()), default=0.0)
    scale = top_perf if top_perf > 1 else 1.0

    def _score(candidate: dict) -> float:
        days = candidate.get("days_since_posted")
        recency = 1.15 if days is None else min(max(_float(days), 0.0), 30.0) / 30.0
        perf = max(0.0, _float(candidate.get("performance_score"))) / scale
        score = 3.0 * recency + 2.0 * perf
        if candidate.get("on_sale"):
            score += 1.5 + min(_float(candidate.get("discount_pct")), 50.0) / 50.0
        if candidate.get("is_new"):
            score += 1.5
        score += 1.0 / (1.0 + max(0.0, _float(candidate.get("post_count"))))
        if _as_str(candidate.get("product_id")) in very_recent:
            score -= 2.0 if allow_repeats else 5.0
        return score

    ranked = sorted(
        unique.values(),
        key=lambda c: (-_score(c), _as_str(c.get("title")).casefold(), _as_str(c.get("product_id"))),
    )
    return ranked[:max(0, limit)]


def _compact_candidate(candidate: dict) -> dict:
    days = candidate.get("days_since_posted")
    compact: dict[str, Any] = {
        "id": _as_str(candidate.get("product_id")),
        "title": _as_str(candidate.get("title"))[:70],
        "price": candidate.get("price"),
        "sale_pct": round(_float(candidate.get("discount_pct"))) if candidate.get("on_sale") else 0,
        "new": bool(candidate.get("is_new")),
        "days": "never" if days is None else int(_float(days)),
        "posts": int(_float(candidate.get("post_count"))),
        "perf": round(_float(candidate.get("performance_score")), 2),
    }
    collections = [c if isinstance(c, str) else _as_str((c or {}).get("title")) for c in candidate.get("collections") or []]
    if collections:
        compact["collections"] = [c for c in collections if c][:2]
    tags = [_as_str(tag) for tag in candidate.get("tags") or [] if _as_str(tag)]
    if tags:
        compact["tags"] = tags[:4]
    return compact


def _eligibility_problem(content_type: str, candidate: dict) -> str:
    if content_type == ContentType.SALE_PROMO and not (candidate.get("on_sale") or _float(candidate.get("discount_pct")) > 0):
        return "sale_promo needs a product that is on sale"
    if content_type == ContentType.NEW_ARRIVAL and "is_new" in candidate and not candidate.get("is_new"):
        return "new_arrival needs a product marked new"
    if content_type == ContentType.BACK_IN_STOCK and "back_in_stock" in candidate and not candidate.get("back_in_stock"):
        return "back_in_stock needs a product that is back in stock"
    return ""


def validate_plan(
    draft: _PlanDraft,
    *,
    slots: list[dict],
    candidates: list[dict],
    allowed_content_types: list[str],
    templates_by_type: dict[str, list[str]],
    max_products_per_post: int,
    allow_repeats: bool,
) -> ContentPlan:
    """Keep only valid choices; everything dropped is listed in `rejected`."""
    slot_ids = [_as_str(slot.get("slot_id")) for slot in slots]
    by_id = {_as_str(c.get("product_id")): c for c in candidates if _as_str(c.get("product_id"))}
    allowed = {normalise_content_type(item) for item in allowed_content_types}
    templates = {normalise_content_type(key): [str(v) for v in value or []] for key, value in templates_by_type.items()}
    covered: dict[str, PlannedSlot] = {}
    used: set[str] = set()
    rejected: list[RejectedChoice] = []

    for item in draft.slots:
        sid = item.slot_id
        if sid not in slot_ids:
            rejected.append(RejectedChoice(slot_id=sid, field="slot_id", value=sid, reason="unknown slot id"))
            continue
        if sid in covered:
            rejected.append(RejectedChoice(slot_id=sid, field="slot_id", value=sid, reason="duplicate entry for this slot"))
            continue
        content_type = normalise_content_type(item.content_type)
        if content_type not in allowed:
            rejected.append(RejectedChoice(
                slot_id=sid, field="content_type", value=item.content_type, reason="content type is not allowed",
            ))
            continue
        low, high = product_bounds(content_type, max_products_per_post)
        product_ids: list[str] = []
        for pid in item.product_ids:
            if pid in product_ids:
                continue
            if pid not in by_id:
                rejected.append(RejectedChoice(
                    slot_id=sid, field="product_ids", value=pid, reason="not one of the candidate products",
                ))
                continue
            if not allow_repeats and pid in used:
                rejected.append(RejectedChoice(
                    slot_id=sid, field="product_ids", value=pid, reason="already used in another slot",
                ))
                continue
            problem = _eligibility_problem(content_type, by_id[pid])
            if problem:
                rejected.append(RejectedChoice(slot_id=sid, field="product_ids", value=pid, reason=problem))
                continue
            product_ids.append(pid)
        if len(product_ids) > high:
            rejected.append(RejectedChoice(
                slot_id=sid, field="product_ids", value=product_ids[high:],
                reason=f"{content_type} takes at most {high} product(s)",
            ))
            product_ids = product_ids[:high]
        if len(product_ids) < low:
            rejected.append(RejectedChoice(
                slot_id=sid, field="slot", value=item.product_ids,
                reason=f"{content_type} needs at least {low} valid product(s)",
            ))
            continue
        template_key = item.template_key
        if template_key and template_key not in templates.get(content_type, []):
            rejected.append(RejectedChoice(
                slot_id=sid, field="template_key", value=template_key,
                reason=f"unknown template for {content_type}; the default will be used",
            ))
            template_key = ""
        used.update(product_ids)
        covered[sid] = PlannedSlot(
            slot_id=sid, product_ids=product_ids, content_type=content_type, template_key=template_key,
            angle=tidy(item.angle)[:300], reason=tidy(item.reason)[:300],
        )
    return ContentPlan(
        slots=[covered[sid] for sid in slot_ids if sid in covered],
        uncovered_slot_ids=[sid for sid in slot_ids if sid not in covered],
        rejected=rejected,
        summary=tidy(draft.summary)[:400],
    )


async def choose_content_plan(
    sctx: StoreContext,
    *,
    slots: list[dict],
    candidates: list[dict],
    allowed_content_types: list[str],
    templates_by_type: dict[str, list[str]],
    recent_posts: list[dict],
    performance: dict,
    max_products_per_post: int = 4,
    allow_repeats: bool = False,
    subject_id: str = "",
) -> ContentPlan:
    """Ask Gemma what to post in each slot.

    slot: {slot_id, local_time, providers[]}; candidate: {product_id, title,
    price, on_sale, discount_pct, collections, tags, days_since_posted,
    post_count, is_new, performance_score}. Raises ExternalServiceError when
    the model is unreachable; unusable output returns a plan with every slot
    uncovered (source="none") so the deterministic strategy takes over.
    """
    slot_ids = [_as_str(slot.get("slot_id")) for slot in slots]
    if not slots:
        return ContentPlan()
    if not sctx.settings.ai.ai_decisions:
        return ContentPlan(uncovered_slot_ids=slot_ids, source="none", error="AI decisions are turned off.")
    allowed = list(dict.fromkeys(normalise_content_type(item) for item in allowed_content_types if item))
    ranked = pre_rank_candidates(candidates, recent_posts=recent_posts, allow_repeats=allow_repeats)
    feasible = any(product_bounds(ct, max_products_per_post)[0] == 0 for ct in allowed) or bool(ranked)
    if not allowed or not feasible:
        return ContentPlan(
            uncovered_slot_ids=slot_ids, source="none",
            error="No allowed content type can be filled with the available candidates.",
        )
    templates = {normalise_content_type(key): list(value or []) for key, value in templates_by_type.items()}
    prompt = prompts.content_plan_prompt(
        slots=slots,
        candidates=[_compact_candidate(candidate) for candidate in ranked],
        allowed_content_types=allowed,
        templates_by_type=templates,
        recent_posts=recent_posts,
        performance=performance,
        max_products_per_post=max_products_per_post,
        allow_repeats=allow_repeats,
        product_rules=PRODUCT_RULES,
    )

    def _postprocess(draft: _PlanDraft) -> ContentPlan:
        plan = validate_plan(
            draft, slots=slots, candidates=candidates, allowed_content_types=allowed,
            templates_by_type=templates, max_products_per_post=max_products_per_post, allow_repeats=allow_repeats,
        )
        if not plan.slots:
            reasons = "; ".join(
                f"{item.field} {item.value!r}: {item.reason}" for item in plan.rejected[:6]
            ) or "no slots were returned"
            raise ValueError(f"No slot could be used ({reasons}). Use only the listed slot ids, product ids and content types.")
        return plan

    ai = sctx.settings.ai
    try:
        _, meta = await get_router(sctx).structured(
            "decide.content_plan",
            LLMRequest(system=prompts.DECISION_SYSTEM, prompt=prompt, json_mode=True,
                       temperature=ai.decision_temperature, max_tokens=min(8192, 500 + 170 * len(slots))),
            _PlanDraft,
            ctx=sctx.call_context(subject_id),
            decision_type="content_plan",
            subject_id=subject_id,
            postprocess=_postprocess,
        )
    except ValidationError as exc:
        return ContentPlan(
            uncovered_slot_ids=slot_ids, source="none", error=exc.message,
            rejected=[RejectedChoice(reason=f"AI plan was unusable: {exc.message}")],
        )
    plan: ContentPlan = meta.result
    plan.model = meta.model
    plan.used_fallback = meta.used_fallback
    return plan


# ---------------------------------------------------------------------------
# PromoQ texts
# ---------------------------------------------------------------------------

class _PromoDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    texts: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if isinstance(data, list):
            return {"texts": data}
        if isinstance(data, dict) and "texts" not in data:
            for alias in ("lines", "promo_texts", "items"):
                if alias in data:
                    return {**data, "texts": data[alias]}
        return data

    @field_validator("texts", mode="before")
    @classmethod
    def _texts(cls, value: Any) -> list[str]:
        return _as_str_list(value)


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", ".").rstrip("0").rstrip(".") or "0" for match in _NUMBER_RE.finditer(text or "")}


def clean_promo_text(text: str) -> str:
    value = URL_RE.sub("", text or "")
    value, _ = extract_hashtags(value)
    value = strip_emojis(value)
    return " ".join(value.split()).strip(" \"'“”‘’")


async def generate_promo_texts(
    sctx: StoreContext,
    *,
    theme: str,
    products: list[dict],
    discount_text: str = "",
    coupon_code: str = "",
    count: int = 5,
) -> list[str]:
    """Rotating PromoQ lines (≤ 40 characters, compliant, numbers only from the offer)."""
    count = max(1, min(int(count), 12))
    allowed_numbers = _numbers(discount_text) | _numbers(coupon_code)
    for product in products:
        discount = _float(product.get("discount_pct"))
        if discount > 0:
            allowed_numbers |= _numbers(f"{discount:g}") | _numbers(f"{round(discount)}")
    banned = sctx.settings.captions.banned_terms

    def _postprocess(draft: _PromoDraft) -> list[str]:
        kept: list[str] = []
        seen: set[str] = set()
        for raw in draft.texts:
            text = clean_promo_text(raw)
            key = text.casefold()
            if (
                not text or len(text) > PROMO_TEXT_MAX or key in seen
                or claim_issues(text, "", "promo text") or find_terms(text, banned)
                or not _numbers(text) <= allowed_numbers
            ):
                continue
            seen.add(key)
            kept.append(text)
        if not kept:
            raise ValueError(
                f"No usable lines: each must be at most {PROMO_TEXT_MAX} characters, distinct, free of health claims, "
                "and use only the numbers given in the offer."
            )
        return kept[:count]

    _, meta = await get_router(sctx).structured(
        "decide.promo_texts",
        LLMRequest(
            system=prompts.DECISION_SYSTEM,
            prompt=prompts.promo_texts_prompt(  # over-generate: some lines fail validation
                theme=theme, products=products, discount_text=discount_text, coupon_code=coupon_code,
                count=count + 3, brand=sctx.settings.brand,
            ),
            json_mode=True, temperature=sctx.settings.ai.temperature, max_tokens=300 + 40 * (count + 3),
        ),
        _PromoDraft,
        ctx=sctx.call_context(),
        decision_type="promo_texts",
        subject_id=theme[:80],
        postprocess=_postprocess,
    )
    return list(meta.result)


# ---------------------------------------------------------------------------
# Brand content (quote, fact, poll, quiz, awareness, value)
# ---------------------------------------------------------------------------

class BrandContent(BaseModel):
    kind: str
    headline: str
    body: str = ""
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    caption: str
    alt_text: str = ""
    model: str = ""
    used_fallback: bool = False


class _BrandDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: str = ""
    body: str = ""
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    caption: str = ""

    @field_validator("headline", "body", "answer", "caption", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return _as_str(value)

    @field_validator("options", mode="before")
    @classmethod
    def _options(cls, value: Any) -> list[str]:
        return _as_str_list(value)


def _similar(text: str, recent: list[str]) -> str:
    candidate = " ".join(text.casefold().split())
    for item in recent:
        other = " ".join(str(item or "").casefold().split())
        if other and (candidate == other or difflib.SequenceMatcher(None, candidate, other).ratio() >= 0.85):
            return item
    return ""


async def generate_brand_content(
    sctx: StoreContext,
    *,
    kind: BrandKind,
    topic_hint: str = "",
    recent: list[str] | None = None,
) -> BrandContent:
    if kind not in prompts.BRAND_CONTENT_GUIDANCE:
        raise ValidationError(f"Unknown brand content kind '{kind}'.")
    recent = list(recent or [])
    settings = sctx.settings
    captions = settings.captions

    def _postprocess(draft: _BrandDraft) -> BrandContent:
        headline = clean_promo_text(draft.headline)
        body = clean_promo_text(draft.body)
        if not headline:
            raise ValueError("headline is empty")
        if len(headline) > 60:
            raise ValueError(f"headline is {len(headline)} characters; the maximum is 60")
        if len(body) > 220:
            raise ValueError(f"body is {len(body)} characters; the maximum is 220")
        options: list[str] = []
        answer = ""
        if kind in ("poll", "quiz"):
            options = list(dict.fromkeys(clean_promo_text(option) for option in draft.options if clean_promo_text(option)))
            if not 2 <= len(options) <= 4 or any(len(option) > 25 for option in options):
                raise ValueError("a poll or quiz needs 2–4 distinct options of at most 25 characters each")
            if kind == "quiz":
                answer = next((o for o in options if o.casefold() == clean_promo_text(draft.answer).casefold()), "")
                if not answer:
                    raise ValueError("answer must be exactly one of the options")
        caption_text = URL_RE.sub("", draft.caption)
        caption_text, _ = extract_hashtags(caption_text)
        caption_text = tidy(caption_text)
        if captions.emoji_level == "off":
            caption_text = strip_emojis(caption_text)
        caption_text = trim_to_limit(caption_text, 600)
        if not caption_text:
            raise ValueError("caption is empty")
        combined = "\n".join([headline, body, caption_text, *options])
        problems = [issue.message for issue in claim_issues(combined, "", "the content")]
        if problems:
            raise ValueError("contains claim wording: " + "; ".join(problems[:3]))
        banned_found = find_terms(combined, captions.banned_terms)
        if banned_found:
            raise ValueError(f"uses banned terms: {', '.join(banned_found)}")
        repeated = _similar(headline, recent) or (_similar(body, recent) if body else "")
        if repeated:
            raise ValueError(f"repeats a recent post: {repeated[:80]!r}")
        # Deterministic: the graphic is rendered from these texts, so describe exactly that
        # (the model cannot see it and tends to invent photos and props).
        alt_parts = [f'{settings.brand.business_name or "Brand"} graphic: "{headline}"', body]
        if options:
            alt_parts.append("Options: " + ", ".join(options))
        alt_text = trim_to_limit(" ".join(part for part in alt_parts if part), 250)
        return BrandContent(
            kind=kind, headline=headline, body=body, options=options, answer=answer, caption=caption_text,
            alt_text=alt_text,
        )

    _, meta = await get_router(sctx).structured(
        "decide.brand_content",
        LLMRequest(
            system=prompts.caption_system_prompt(settings.brand, captions),
            prompt=prompts.brand_content_prompt(
                kind=kind, topic_hint=topic_hint, recent=recent, brand=settings.brand, language=captions.language,
            ),
            json_mode=True, temperature=settings.ai.temperature, max_tokens=700,
        ),
        _BrandDraft,
        ctx=sctx.call_context(),
        decision_type="brand_content",
        subject_id=kind,
        postprocess=_postprocess,
    )
    content: BrandContent = meta.result
    content.model = meta.model
    content.used_fallback = meta.used_fallback
    return content


# ---------------------------------------------------------------------------
# Performance insights
# ---------------------------------------------------------------------------

class Insights(BaseModel):
    summary: str
    recommendations: list[str] = Field(default_factory=list)
    content_type_weights: dict[str, float] = Field(default_factory=dict)
    best_hours: list[int] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)   # model output removed by validation
    model: str = ""
    used_fallback: bool = False


class _InsightsDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    recommendations: list[str] = Field(default_factory=list)
    content_type_weights: dict[str, Any] = Field(default_factory=dict)
    best_hours: list[Any] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return _as_str(value)

    @field_validator("recommendations", mode="before")
    @classmethod
    def _list(cls, value: Any) -> list[str]:
        return _as_str_list(value)

    @field_validator("content_type_weights", mode="before")
    @classmethod
    def _weights(cls, value: Any) -> dict:
        if value is None:
            return {}
        if isinstance(value, list):
            return {str(item.get("content_type")): item.get("weight") for item in value if isinstance(item, dict)}
        return value

    @field_validator("best_hours", mode="before")
    @classmethod
    def _hours(cls, value: Any) -> list:
        return [] if value is None else value if isinstance(value, list) else [value]


def _walk(value: Any, parent: str = "") -> Iterable[tuple[str, str, Any]]:
    """Yield (parent_key, key, value) for every dict entry and list item."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield parent, str(key), item
            yield from _walk(item, str(key))
    elif isinstance(value, list):
        for item in value:
            yield parent, "", item
            yield from _walk(item, parent)


def _hour(value: Any) -> int | None:
    match = re.match(r"^\s*(\d{1,2})(?::\d{2})?\s*$", str(value))
    if match and 0 <= int(match.group(1)) <= 23:
        return int(match.group(1))
    return None


def rollup_facts(rollups: dict) -> tuple[list[str], list[int], list[float]]:
    """(content types present, hours present, numbers present) in the roll-ups."""
    known = {str(item) for item in ContentType}
    types: list[str] = []
    hours: list[int] = []
    numbers: list[float] = []
    for parent, key, value in _walk(rollups):
        for text in (key, value if isinstance(value, str) else ""):
            normalised = normalise_content_type(text)
            if normalised in known and normalised not in types:
                types.append(normalised)
        if key == "content_type" and isinstance(value, str) and value.strip():
            normalised = normalise_content_type(value)
            if normalised not in types:
                types.append(normalised)
        if "content_type" in parent and key and not isinstance(value, (str, int, float)):
            normalised = normalise_content_type(key)
            if normalised and normalised not in types:
                types.append(normalised)
        scalar = not isinstance(value, (dict, list))
        hour_candidates: list[Any] = []
        if "hour" in parent.lower():
            # {"by_hour": {"9": {...}}} → key; {"best_hours": [9, 18]} → list item
            hour_candidates.append(key if key else (value if scalar else None))
        if "hour" in key.lower() and scalar:   # {"hour": 9}, {"best_hour": "18:00"}
            hour_candidates.append(value)
        for candidate in hour_candidates:
            hour = _hour(candidate) if candidate is not None else None
            if hour is not None and hour not in hours:
                hours.append(hour)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            numbers.append(float(value))
        elif isinstance(value, str):
            numbers.extend(float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", value))
    return types, sorted(hours), numbers


def _allowed_number_set(numbers: list[float]) -> list[float]:
    allowed: set[float] = set(range(0, 11))
    for number in numbers:
        for value in (number, number * 100) if 0 < abs(number) <= 1 else (number,):
            allowed.update({round(value, 2), round(value, 1), round(value, 0), float(int(value))})
    return sorted(allowed)


def numbers_not_in(text: str, allowed: list[float]) -> list[str]:
    missing = []
    for match in re.finditer(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?", text or ""):
        number = float(match.group(0).replace(",", ""))
        if not any(abs(number - value) < 1e-6 for value in allowed):
            missing.append(match.group(0))
    return missing


async def performance_insights(sctx: StoreContext, *, rollups: dict) -> Insights:
    """Summarise roll-ups and propose content-type weights and best hours.

    Expected (flexible) shape: {"period_days": 28, "by_content_type": {"single_product":
    {"posts": 12, "engagement_rate": 0.031}}, "by_hour": {"9": {...}}, "by_provider": {...}}.
    Weights may only name content types present in the roll-ups, hours only
    hours present, and text may only quote numbers that appear in the input.
    """
    types, hours, numbers = rollup_facts(rollups or {})
    if not rollups or not numbers:
        return Insights(summary="Not enough performance data yet.")
    allowed_numbers = _allowed_number_set(numbers + [float(hour) for hour in hours])

    def _postprocess(draft: _InsightsDraft) -> Insights:
        dropped: list[str] = []
        invented = numbers_not_in(draft.summary, allowed_numbers)
        if invented:
            raise ValueError(f"summary mentions numbers that are not in the roll-ups: {', '.join(invented[:5])}")
        if not draft.summary:
            raise ValueError("summary is empty")
        recommendations = []
        for item in draft.recommendations:
            bad = numbers_not_in(item, allowed_numbers)
            if bad:
                dropped.append(f"recommendation with numbers not in the data ({', '.join(bad[:3])}): {item[:120]}")
            elif claim_issues(item, "", "recommendation"):
                dropped.append(f"recommendation with claim wording: {item[:120]}")
            else:
                recommendations.append(tidy(item))
        weights: dict[str, float] = {}
        for key, value in draft.content_type_weights.items():
            content_type = normalise_content_type(key)
            weight = _float(value, default=-1.0)
            if content_type not in types:
                dropped.append(f"weight for '{key}' (content type not in the data)")
            elif weight < 0:
                dropped.append(f"weight for '{key}' (not a number)")
            else:
                weights[content_type] = round(min(3.0, max(0.2, weight)), 2)
        best_hours: list[int] = []
        for value in draft.best_hours:
            hour = _hour(value)
            if hour is None or hour not in hours:
                dropped.append(f"best hour {value!r} (not in the data)")
            elif hour not in best_hours:
                best_hours.append(hour)
        return Insights(
            summary=tidy(draft.summary), recommendations=recommendations[:5], content_type_weights=weights,
            best_hours=best_hours, dropped=dropped,
        )

    _, meta = await get_router(sctx).structured(
        "decide.insights",
        LLMRequest(
            system=prompts.DECISION_SYSTEM,
            prompt=prompts.insights_prompt(rollups=rollups, content_types=types, hours=hours),
            json_mode=True, temperature=sctx.settings.ai.decision_temperature, max_tokens=900,
        ),
        _InsightsDraft,
        ctx=sctx.call_context(),
        decision_type="insights",
        subject_id="performance",
        postprocess=_postprocess,
    )
    insights: Insights = meta.result
    insights.model = meta.model
    insights.used_fallback = meta.used_fallback
    return insights
