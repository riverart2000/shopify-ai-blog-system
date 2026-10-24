"""Event-driven rules: new product launch series, price drop / sale, back in stock.

They react to catalogue change events detected by the Shopify sync (webhooks
make this near-real-time; the 15-minute incremental sync is the safety net).
Each event is processed once (per-campaign cursor) and each product gets at
most one post per cooldown period.
"""
from __future__ import annotations

import time
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

from db.social_engine import products as product_store

from ...models import ContentType
from ..base import CampaignRule, PlanItem, RuleContext, register_rule
from ._common import already_planned, make_item, new_events


class LaunchStep(BaseModel):
    day_offset: int = Field(0, ge=0, le=30)
    content_type: str = ContentType.NEW_ARRIVAL
    template_key: str = ""


class NewProductConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    steps: list[LaunchStep] = Field(default_factory=lambda: [
        LaunchStep(day_offset=0, content_type=ContentType.NEW_ARRIVAL),
        LaunchStep(day_offset=2, content_type=ContentType.PRODUCT_VIDEO),
        LaunchStep(day_offset=5, content_type=ContentType.CAROUSEL),
    ])
    delay_minutes: int = Field(30, ge=0, le=1440)


class PriceDropConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    min_drop_pct: float = Field(10.0, ge=0, le=95)
    include_sale_started: bool = True
    delay_minutes: int = Field(15, ge=0, le=1440)
    within_minutes: int = Field(60, ge=5, le=10080)
    cooldown_days: int = Field(7, ge=0, le=90)
    content_type: str = ContentType.SALE_PROMO
    template_key: str = ""


class BackInStockConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    delay_minutes: int = Field(30, ge=0, le=1440)
    cooldown_days: int = Field(14, ge=0, le=90)
    content_type: str = ContentType.BACK_IN_STOCK
    template_key: str = ""


def _cooled_down(rctx: RuleContext, product_id: str, cooldown_days: int) -> bool:
    last = (rctx.state.get("last_post_by_product") or {}).get(product_id)
    return not last or time.time() - float(last) >= cooldown_days * 86400


def _remember(rctx: RuleContext, product_id: str) -> None:
    seen = dict(rctx.state.get("last_post_by_product") or {})
    seen[product_id] = int(time.time())
    rctx.state["last_post_by_product"] = seen


@register_rule
class NewProductLaunchRule(CampaignRule):
    rule_type = "new_product_launch"
    label = "New product launch series"
    priority = 8
    description = "When a product is added, publish a launch series (e.g. 3 posts over 7 days)."
    config_model = NewProductConfig
    default_interval_seconds = 600

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: NewProductConfig = rctx.config  # type: ignore[assignment]
        events = await new_events(rctx, ("product_created", "product_published"))
        items: list[PlanItem] = []
        for event in events:
            product = await product_store.get_product(rctx.store_id, event["product_id"])
            if not product or not product.get("available") or product.get("deleted_at"):
                continue
            for index, step in enumerate(config.steps):
                key = f"launch:{rctx.campaign_id}:{product['product_id']}:{index}"
                if await already_planned(rctx, key):
                    continue
                item = make_item(
                    rctx, dedupe_key=key, content_type=step.content_type, products=[product],
                    template_key=step.template_key, title=f"Launch: {product['title']}",
                    options={"launch_step": index + 1, "launch_steps": len(config.steps), "image_offset": index},
                )
                earliest = rctx.now + timedelta(days=step.day_offset, minutes=config.delay_minutes)
                if rctx.schedule_item(item, earliest=earliest):
                    items.append(item)
        return items


@register_rule
class PriceDropRule(CampaignRule):
    rule_type = "price_drop"
    label = "Price drop / sale alert"
    priority = 8
    description = "When a price drops or a sale starts, post a sale promo within the hour."
    config_model = PriceDropConfig
    default_interval_seconds = 300

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: PriceDropConfig = rctx.config  # type: ignore[assignment]
        types = ("price_drop", "sale_started") if config.include_sale_started else ("price_drop",)
        items: list[PlanItem] = []
        for event in await new_events(rctx, types):
            details = event.get("details") or {}
            pct = float(details.get("pct") or details.get("discount_pct") or 0)
            if event["event_type"] == "price_drop" and pct < config.min_drop_pct:
                continue
            product = await product_store.get_product(rctx.store_id, event["product_id"])
            if not product or not product.get("available"):
                continue
            if not _cooled_down(rctx, product["product_id"], config.cooldown_days):
                continue
            key = f"price:{rctx.campaign_id}:{product['product_id']}:{event['id']}"
            if await already_planned(rctx, key):
                continue
            discount = max(pct, float(product.get("discount_pct") or 0))
            item = make_item(
                rctx, dedupe_key=key, content_type=config.content_type, products=[product],
                template_key=config.template_key, title=f"Price drop: {product['title']}",
                texts={"discount_text": f"{discount:.0f}% off" if discount else "", "promo_text": "Now on sale"},
            )
            earliest = rctx.now + timedelta(minutes=config.delay_minutes)
            latest = earliest + timedelta(minutes=config.within_minutes)
            if rctx.schedule_item(item, earliest=earliest, latest=latest) or rctx.schedule_item(item, earliest=earliest):
                _remember(rctx, product["product_id"])
                items.append(item)
        return items


@register_rule
class BackInStockRule(CampaignRule):
    rule_type = "back_in_stock"
    label = "Back in stock"
    priority = 8
    description = "When a sold-out product is available again, announce it."
    config_model = BackInStockConfig
    default_interval_seconds = 600

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: BackInStockConfig = rctx.config  # type: ignore[assignment]
        items: list[PlanItem] = []
        for event in await new_events(rctx, ("back_in_stock",)):
            product = await product_store.get_product(rctx.store_id, event["product_id"])
            if not product or not product.get("available"):
                continue
            if not _cooled_down(rctx, product["product_id"], config.cooldown_days):
                continue
            key = f"restock:{rctx.campaign_id}:{product['product_id']}:{event['id']}"
            if await already_planned(rctx, key):
                continue
            item = make_item(
                rctx, dedupe_key=key, content_type=config.content_type, products=[product],
                template_key=config.template_key, title=f"Back in stock: {product['title']}",
            )
            if rctx.schedule_item(item, earliest=rctx.now + timedelta(minutes=config.delay_minutes)):
                _remember(rctx, product["product_id"])
                items.append(item)
        return items
