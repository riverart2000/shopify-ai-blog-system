"""Promotions — Outfy PromoQ.

Pick a theme (occasion / offer / new arrivals / custom), a scope (whole store,
collections or specific products), an optional coupon code, promo lines that
rotate at random, a start date and up to 15 days. Each day it posts N promos
per network at times inside the window; the final days switch to countdown
posts. Promotions outrank SmartQ: on a full day they take a slot from a
not-yet-approved SmartQ post.

Postiz tools are used where they help:
* `ai_background` — a Postiz AI scene (seasonal backdrop) behind the real product,
* `postiz_media_search` — rotate designs you made with Postiz's design editor
  templates (found in the Postiz media library by file name),
* `hero_ai_video` — one Postiz AI video on the first day (uses a video credit).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...models import ContentType
from ..base import CampaignRule, PlanItem, ProductFilter, RuleContext, register_rule
from ..selection import candidate_products, pick_products
from ..themes import THEMES, get_theme
from ._common import already_planned, day_key, make_item

logger = logging.getLogger("ai_blog_server.social_engine.campaigns")

_PROMO_TYPES = {
    ContentType.SALE_PROMO, ContentType.PRODUCT_VIDEO, ContentType.COLLAGE, ContentType.COUNTDOWN,
    ContentType.NEW_ARRIVAL, ContentType.BACK_IN_STOCK, ContentType.CAROUSEL, ContentType.HOLIDAY,
    ContentType.QUOTE, ContentType.SINGLE_PRODUCT, ContentType.AI_IMAGE_AD,
}


class PromotionConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    theme_key: str = "special_offer"
    theme: str = Field("", max_length=80)               # display title; defaults to the theme label
    scope: Literal["store", "collections", "products"] = "store"
    collection_ids: list[str] = Field(default_factory=list)
    product_ids: list[str] = Field(default_factory=list)
    coupon_code: str = Field("", max_length=40)
    discount_text: str = Field("", max_length=40)
    promo_texts: list[str] = Field(default_factory=list)
    start_date: date
    duration_days: int = Field(7, ge=1, le=15)
    posts_per_day: int = Field(1, ge=1, le=3)
    content_types: list[str] = Field(default_factory=list)
    countdown_days: int = Field(2, ge=0, le=5)
    landing_url: str = ""
    caption_mode: Literal["ai", "custom"] = "ai"
    custom_caption: str = Field("", max_length=2000)
    show_logo: bool = True
    ai_background: bool | None = None
    postiz_media_search: str = Field("", max_length=80)
    hero_ai_video: bool = False

    @field_validator("theme_key")
    @classmethod
    def _theme(cls, value: str) -> str:
        if value not in THEMES:
            raise ValueError(f"Unknown promotion theme {value!r}")
        return value

    @field_validator("promo_texts")
    @classmethod
    def _texts(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if any(len(v) > 60 for v in cleaned):
            raise ValueError("Promo lines must be 60 characters or fewer")
        return cleaned

    @field_validator("content_types")
    @classmethod
    def _types(cls, value: list[str]) -> list[str]:
        bad = [v for v in value if v not in _PROMO_TYPES]
        if bad:
            raise ValueError(f"Unsupported promotion content types: {', '.join(bad)}")
        return value

    @model_validator(mode="after")
    def _custom_caption(self) -> "PromotionConfig":
        if self.caption_mode == "custom" and not self.custom_caption.strip():
            raise ValueError("Write the custom caption or switch the caption to AI")
        if self.scope == "collections" and not self.collection_ids:
            raise ValueError("Choose at least one collection")
        if self.scope == "products" and not self.product_ids:
            raise ValueError("Choose at least one product")
        return self

    @property
    def end_date(self) -> date:
        return self.start_date + timedelta(days=self.duration_days - 1)

    @property
    def title(self) -> str:
        return self.theme or get_theme(self.theme_key).label

    def effective_content_types(self) -> list[str]:
        return self.content_types or list(get_theme(self.theme_key).content_types)


def promotion_status(config: dict, campaign_status: str, today: date) -> str:
    """upcoming | running | ended | paused (for the Promotions tab)."""
    try:
        parsed = PromotionConfig.model_validate(config)
    except Exception:
        return "invalid"
    if campaign_status != "active":
        return "paused" if campaign_status == "paused" else campaign_status
    if today < parsed.start_date:
        return "upcoming"
    if today > parsed.end_date:
        return "ended"
    return "running"


@register_rule
class PromotionRule(CampaignRule):
    rule_type = "promotion"
    label = "Promotion (PromoQ)"
    description = "A holiday, event, sale or launch promotion that runs for up to 15 days with rotating promo lines."
    config_model = PromotionConfig
    default_interval_seconds = 3600
    allowed_content_types = sorted(_PROMO_TYPES)
    priority = 10

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: PromotionConfig = rctx.config  # type: ignore[assignment]
        theme = get_theme(config.theme_key)
        days = [d for d in rctx.planning_days() if config.start_date <= d <= config.end_date]
        if not days:
            return []
        pf = ProductFilter(**rctx.product_filter.model_dump())
        if config.scope == "collections":
            pf.collection_ids = config.collection_ids
        elif config.scope == "products":
            pf.product_ids = config.product_ids
        candidates = await candidate_products(rctx, pf)
        types = config.effective_content_types()
        needs_products = any(t not in (ContentType.QUOTE, ContentType.HOLIDAY) for t in types)
        if needs_products and not candidates:
            rctx.state["last_note"] = "No eligible products in the promotion scope."
            return []
        promo_texts = await self._promo_texts(rctx, config, candidates)
        ai_background = rctx.sctx.settings.postiz.ai_backgrounds_for_promotions \
            if config.ai_background is None else config.ai_background
        items: list[PlanItem] = []
        used: set[str] = set()
        for day in days:
            days_left = (config.end_date - day).days
            for index in range(config.posts_per_day):
                key = f"promo:{rctx.campaign_id}:{day_key(day)}:{index}"
                if await already_planned(rctx, key):
                    continue
                countdown = bool(config.countdown_days) and days_left < config.countdown_days
                hero_video = config.hero_ai_video and day == config.start_date and index == 0
                if hero_video:
                    content_type = ContentType.AI_VIDEO
                elif countdown:
                    content_type = ContentType.COUNTDOWN
                else:
                    content_type = rctx.rng.choice(types)
                need = 3 if content_type in (ContentType.COLLAGE, ContentType.CAROUSEL) else \
                    0 if content_type in (ContentType.QUOTE, ContentType.HOLIDAY) and not candidates else 1
                products: list[dict] = []
                if need:
                    products = await pick_products(rctx, count=need, order="random", candidates=candidates,
                                                   exclude_ids=used, allow_reposting=True)
                    if not products:
                        used.clear()
                        products = await pick_products(rctx, count=need, order="random", candidates=candidates,
                                                       allow_reposting=True)
                    if not products:
                        continue
                label = "Last day" if days_left == 0 else f"{days_left + 1} days left"
                texts = {
                    "promo_text": rctx.rng.choice(promo_texts) if promo_texts else config.title,
                    "theme": config.title,
                    "occasion": config.title if theme.category == "occasion" else "",
                    "coupon_code": config.coupon_code,
                    "discount_text": config.discount_text,
                    "countdown_label": label if countdown else "",
                    "sticker": theme.sticker or config.title,
                }
                options = {
                    "theme_key": theme.key,
                    "promotion": True,
                    "show_logo": config.show_logo,
                    "ai_background": bool(ai_background),
                    "postiz_media_search": config.postiz_media_search,
                    "priority": self.priority,
                }
                if config.landing_url:
                    options["link_override"] = config.landing_url
                if config.caption_mode == "custom":
                    options["custom_caption"] = config.custom_caption
                item = make_item(
                    rctx, dedupe_key=key, content_type=content_type, products=products,
                    title=f"{config.title}: {products[0]['title']}" if products else config.title,
                    texts=texts, options=options,
                )
                if await rctx.schedule_item_with_priority(item, day=day):
                    used.update(item.product_ids)
                    items.append(item)
        return items

    async def _promo_texts(self, rctx: RuleContext, config: PromotionConfig, products: list[dict]) -> list[str]:
        if config.promo_texts:
            return config.promo_texts
        cached = rctx.state.get("promo_texts")
        if cached:
            return list(cached)
        theme = get_theme(config.theme_key)
        texts: list[str] = []
        try:
            from ...ai.decisions import generate_promo_texts

            texts = await generate_promo_texts(
                rctx.sctx, theme=config.title, products=products[:8],
                discount_text=config.discount_text, coupon_code=config.coupon_code, count=5,
            )
        except Exception as exc:
            logger.warning("Promotion %s: Gemma promo texts unavailable: %s", rctx.campaign_id, exc)
        texts = texts or list(theme.promo_texts) or [config.discount_text or config.title]
        rctx.state["promo_texts"] = texts
        return texts
