"""SmartQ — Outfy's always-on auto-pilot.

Every day (planned `plan_days_ahead` days in advance) it keeps N posts per
content type going to every selected network, at times inside the posting
window that respect each channel's daily limit (store default: 2 per day).

Slots are *kept filled*: a post that is rejected, fails or is skipped (sold
out) no longer counts, so the next evaluation plans a replacement while the
day's window is still open. When every eligible product was posted recently,
the least-recently-posted product is reused rather than leaving a gap
(`always_fill_daily_slots`).

With order "ai", Gemma chooses the product(s), content type and creative angle
for each slot from the eligible catalogue; anything it leaves uncovered is
filled deterministically.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from db.social_engine import posts as post_store

from ...models import ContentType
from ..base import CampaignRule, PlanItem, RuleContext, register_rule
from ..selection import candidate_products, pick_products, upcoming_product_ids
from ._common import day_key, make_item, performance_scores, products_needed, summaries

logger = logging.getLogger("ai_blog_server.social_engine.campaigns")

_SMARTQ_TYPES = {
    ContentType.SINGLE_PRODUCT, ContentType.COLLAGE, ContentType.PRODUCT_VIDEO, ContentType.CAROUSEL,
    ContentType.SALE_PROMO, ContentType.PRODUCT_OF_THE_DAY, ContentType.NEW_ARRIVAL,
}
# Posts in these states occupy their slot; failed/rejected/cancelled ones free it.
_SLOT_HOLDING = {
    "planned", "generating", "pending_approval", "approved", "publishing", "scheduled",
    "published", "partially_published",
}


class SmartQConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    order: Literal["latest", "latest_then_random", "random", "least_posted", "on_sale_first", "ai"] = "ai"
    posts_per_day: dict[str, int] = Field(default_factory=lambda: {"single_product": 1, "product_video": 1})
    template_keys: dict[str, list[str]] = Field(default_factory=dict)
    max_products_per_post: int = Field(4, ge=2, le=4)
    allow_reposting: bool | None = None
    strategy_hint: str = Field("", max_length=500)

    @field_validator("posts_per_day")
    @classmethod
    def _counts(cls, value: dict[str, int]) -> dict[str, int]:
        cleaned: dict[str, int] = {}
        for content_type, count in value.items():
            if content_type not in _SMARTQ_TYPES:
                raise ValueError(f"SmartQ does not support content type {content_type!r}")
            if not 0 <= int(count) <= 3:
                raise ValueError(
                    f"{content_type}: posts per day must be 0–3 per campaign; create another campaign to post more"
                )
            cleaned[content_type] = int(count)
        if not any(cleaned.values()):
            raise ValueError("Set at least one post per day")
        return cleaned

    @property
    def total_per_day(self) -> int:
        return sum(self.posts_per_day.values())


def _slot_key(campaign_id: str, day: str, content_type: str, index: int) -> str:
    return f"smartq:{campaign_id}:{day}:{content_type}:{index}"


async def open_slots(rctx: RuleContext, config: SmartQConfig, day) -> list[dict]:
    """Slots for `day` that have no live post (missing or freed by a failure)."""
    prefix = f"smartq:{rctx.campaign_id}:{day_key(day)}:"
    existing = await post_store.list_posts_by_dedupe_prefix(rctx.store_id, prefix)
    used_keys = {row["dedupe_key"] for row in existing}
    holding: Counter[str] = Counter()
    for row in existing:
        if row["status"] in _SLOT_HOLDING:
            parts = row["dedupe_key"].split(":")
            holding[parts[3] if len(parts) > 4 else row["content_type"]] += 1
    slots: list[dict] = []
    for content_type, count in config.posts_per_day.items():
        missing = count - holding.get(content_type, 0)
        index = 0
        for _ in range(max(0, missing)):
            while _slot_key(rctx.campaign_id, day_key(day), content_type, index) in used_keys:
                index += 1
            key = _slot_key(rctx.campaign_id, day_key(day), content_type, index)
            used_keys.add(key)
            slots.append({"slot_id": key, "content_type": content_type, "day": day,
                          "refill": index >= count})
    return slots


@register_rule
class SmartQRule(CampaignRule):
    rule_type = "smartq"
    label = "SmartQ auto-pilot"
    description = ("Posts your catalogue every day across networks, keeping each day's slots filled "
                   "and rotating products (Outfy SmartQ).")
    config_model = SmartQConfig
    default_interval_seconds = 3600
    allowed_content_types = sorted(_SMARTQ_TYPES)

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: SmartQConfig = rctx.config  # type: ignore[assignment]
        candidates = await candidate_products(rctx)
        if not candidates:
            rctx.state["last_note"] = "No eligible products (published, in stock, not excluded)."
            return []
        if config.total_per_day > rctx.sctx.settings.schedule.posts_per_channel_per_day:
            rctx.state["last_note"] = (
                f"This campaign asks for {config.total_per_day} posts a day but channels are limited to "
                f"{rctx.sctx.settings.schedule.posts_per_channel_per_day}; extra slots will wait for free capacity."
            )
        busy = await upcoming_product_ids(rctx.store_id)
        templates = await _templates_by_type(config)
        use_ai = config.order == "ai" and rctx.sctx.settings.ai.ai_decisions
        scores = await performance_scores(rctx) if use_ai else {}
        always_fill = rctx.sctx.settings.schedule.always_fill_daily_slots
        items: list[PlanItem] = []

        for day in rctx.planning_days():
            slots = await open_slots(rctx, config, day)
            if not slots:
                continue
            choices = await _ai_choices(rctx, config, slots, candidates, busy, templates, scores) if use_ai else {}

            for slot in slots:
                choice = dict(choices.get(slot["slot_id"]) or {})
                if choice.get("content_type") and choice["content_type"] != slot["content_type"]:
                    # The daily mix per content type is the campaign's contract (Outfy SmartQ);
                    # keep Gemma's products and angle but not a different format.
                    choice["content_type"], choice["template_key"] = slot["content_type"], ""
                content_type = choice.get("content_type") or slot["content_type"]
                need = max(1, products_needed(content_type, config.max_products_per_post))
                by_id = {str(p["product_id"]): p for p in candidates}
                products = [by_id[pid] for pid in choice.get("product_ids") or [] if pid in by_id and pid not in busy][:need]
                if len(products) < need:
                    products += await pick_products(
                        rctx, count=need - len(products), order=config.order, candidates=candidates,
                        exclude_ids=busy | {str(p["product_id"]) for p in products},
                        allow_reposting=config.allow_reposting,
                    )
                if len(products) < need and always_fill:
                    # Keep the slot filled: reuse the least-recently-posted products.
                    products += await pick_products(
                        rctx, count=need - len(products), order="least_posted", candidates=candidates,
                        exclude_ids={str(p["product_id"]) for p in products}, allow_reposting=True,
                    )
                    if len(products) < need:
                        products += [p for p in candidates if p not in products][: need - len(products)]
                if not products:
                    rctx.state["last_note"] = "Ran out of eligible products to post."
                    continue
                if len(products) < need and content_type in (ContentType.COLLAGE, ContentType.CAROUSEL):
                    content_type = ContentType.SINGLE_PRODUCT  # not enough products for a collage
                template_key = choice.get("template_key") or _first(templates.get(content_type))
                item = make_item(
                    rctx, dedupe_key=slot["slot_id"], content_type=content_type, products=products,
                    template_key=template_key, angle=choice.get("angle", ""),
                    options={"decided_by": "gemma" if choice else config.order,
                             "decision_reason": choice.get("reason", ""), "refill": slot["refill"]},
                )
                if config.strategy_hint:
                    item.brief = config.strategy_hint
                if not rctx.schedule_item(item, day=slot["day"]):
                    logger.info("SmartQ %s: no free slot on %s for %s", rctx.campaign_id, slot["day"], content_type)
                    continue
                busy.update(item.product_ids)
                items.append(item)
        return items


def _first(values: list[str] | None) -> str:
    return values[0] if values else ""


async def _templates_by_type(config: SmartQConfig) -> dict[str, list[str]]:
    from ...creatives import templates_for

    types = set(config.posts_per_day) | {ContentType.SINGLE_PRODUCT}
    return {ct: config.template_keys.get(ct) or list(templates_for(ct)) for ct in types}


async def _ai_choices(
    rctx: RuleContext,
    config: SmartQConfig,
    slots: list[dict],
    candidates: list[dict],
    busy: set[str],
    templates: dict[str, list[str]],
    scores: dict[str, float],
) -> dict[str, dict]:
    """Ask Gemma to fill the day's slots; failures fall back to deterministic picks."""
    from ...ai.decisions import choose_content_plan

    available = [p for p in candidates if str(p["product_id"]) not in busy] or list(candidates)
    recent = await post_store.list_posts(rctx.store_id, statuses=("scheduled", "published"), order="newest", limit=20)
    slot_specs = [
        {"slot_id": s["slot_id"], "local_time": s["day"].isoformat(), "content_type_hint": s["content_type"],
         "providers": sorted({c.get("provider", "") for c in rctx.channels})}
        for s in slots
    ]
    try:
        plan = await choose_content_plan(
            rctx.sctx,
            slots=slot_specs,
            candidates=summaries(rctx, available, scores),
            allowed_content_types=sorted({s["content_type"] for s in slots}),
            templates_by_type=templates,
            recent_posts=[{"title": r.get("title"), "content_type": r.get("content_type"),
                           "product_ids": r.get("product_ids")} for r in recent],
            performance={"product_scores": dict(sorted(scores.items(), key=lambda kv: -kv[1])[:20])},
            max_products_per_post=config.max_products_per_post,
            allow_repeats=False,
            subject_id=rctx.campaign_id,
        )
    except Exception as exc:  # Gemma unavailable → deterministic plan, but say so
        rctx.state["last_note"] = f"Gemma planning unavailable ({type(exc).__name__}: {exc}); used fallback order."
        logger.warning("SmartQ %s: Gemma planning failed: %s", rctx.campaign_id, exc)
        return {}
    if plan.source != "ai" or plan.error:
        rctx.state["last_note"] = (f"Gemma plan not used ({plan.error or plan.source}); "
                                   f"filled {len(slots)} slot(s) with the fallback order.")
    elif plan.rejected:
        rctx.state["last_note"] = f"Gemma planned {len(plan.slots)} slot(s); {len(plan.rejected)} choice(s) rejected by validation."
    return {slot.slot_id: slot.model_dump() for slot in plan.slots}
