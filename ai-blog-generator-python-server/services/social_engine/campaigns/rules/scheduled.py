"""Calendar-driven rules: product of the day, collection round-up, occasions,
brand content (quotes/facts/polls/quizzes), blog SmartQ and evergreen reposts."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from db.social_engine import posts as post_store
from db.social_engine import products as product_store

from ...models import ContentType
from ..base import CampaignRule, PlanItem, ProductFilter, RuleContext, register_rule
from ..holidays import upcoming_occasions
from ..selection import candidate_products, pick_products, upcoming_product_ids
from ._common import already_planned, day_key, make_item

_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class ProductOfTheDayConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    time: str = Field("18:00", pattern=_HHMM)
    order: Literal["latest", "latest_then_random", "random", "least_posted", "on_sale_first"] = "least_posted"
    content_type: str = ContentType.PRODUCT_OF_THE_DAY
    template_key: str = ""


@register_rule
class ProductOfTheDayRule(CampaignRule):
    rule_type = "product_of_the_day"
    label = "Product of the day"
    priority = 5
    description = "One featured product every day at a fixed time."
    config_model = ProductOfTheDayConfig
    default_interval_seconds = 3600

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: ProductOfTheDayConfig = rctx.config  # type: ignore[assignment]
        busy = await upcoming_product_ids(rctx.store_id)
        items: list[PlanItem] = []
        for day in rctx.planning_days():
            key = f"potd:{rctx.campaign_id}:{day_key(day)}"
            if await already_planned(rctx, key):
                continue
            products = await pick_products(rctx, count=1, order=config.order, exclude_ids=busy)
            if not products:
                rctx.state["last_note"] = "No eligible product for today's product of the day."
                continue
            item = make_item(rctx, dedupe_key=key, content_type=config.content_type, products=products,
                             template_key=config.template_key, title=f"Product of the day: {products[0]['title']}")
            if rctx.schedule_item(item, day=day, fixed_time=config.time):
                busy.update(item.product_ids)
                items.append(item)
        return items


class CollectionRoundupConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    collection_ids: list[str] = Field(default_factory=list)
    weekday: int = Field(6, ge=0, le=6)
    time: str = Field("11:00", pattern=_HHMM)
    products_per_post: int = Field(4, ge=2, le=4)
    content_type: Literal["collection_roundup", "collage", "carousel", "product_video"] = "collection_roundup"
    template_key: str = ""


@register_rule
class CollectionRoundupRule(CampaignRule):
    rule_type = "collection_roundup"
    label = "Collection round-up"
    description = "A weekly collage of top products from a collection, rotating through collections."
    config_model = CollectionRoundupConfig
    default_interval_seconds = 6 * 3600

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: CollectionRoundupConfig = rctx.config  # type: ignore[assignment]
        collections = await product_store.list_collections(rctx.store_id)
        if config.collection_ids:
            wanted = set(config.collection_ids)
            collections = [c for c in collections if c["collection_id"] in wanted]
        collections = [c for c in collections if c.get("products_count", 0) >= 2]
        if not collections:
            rctx.state["last_note"] = "No collections with at least two products."
            return []
        items: list[PlanItem] = []
        for day in rctx.planning_days(days_ahead=7):
            if day.weekday() != config.weekday:
                continue
            key = f"roundup:{rctx.campaign_id}:{day_key(day)}"
            if await already_planned(rctx, key):
                continue
            index = int(rctx.state.get("collection_index", 0)) % len(collections)
            collection = collections[index]
            rctx.state["collection_index"] = index + 1
            pf = ProductFilter(**{**rctx.product_filter.model_dump(), "collection_ids": [collection["collection_id"]]})
            candidates = await candidate_products(rctx, pf)
            products = await pick_products(rctx, count=config.products_per_post, order="least_posted",
                                           candidates=candidates, allow_reposting=True)
            if len(products) < 2:
                continue
            item = make_item(
                rctx, dedupe_key=key, content_type=config.content_type, products=products,
                template_key=config.template_key, title=f"{collection['title']} round-up",
                texts={"collection_title": collection["title"], "headline": collection["title"]},
                options={"collection_id": collection["collection_id"], "collection_handle": collection.get("handle", "")},
            )
            if rctx.schedule_item(item, day=day, fixed_time=config.time):
                items.append(item)
        return items


class HolidayConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    occasions: list[str] = Field(default_factory=lambda: [
        "new_year", "valentines", "mothers_day_uk", "fathers_day_uk", "black_friday",
        "cyber_monday", "christmas", "self_care_day", "world_mental_health_day",
    ])
    custom_occasions: list[dict] = Field(default_factory=list)
    days_before: list[int] = Field(default_factory=lambda: [3, 0])
    content_type: str = ContentType.HOLIDAY
    template_key: str = ""
    feature_products: bool = True
    lookahead_days: int = Field(14, ge=1, le=60)


@register_rule
class HolidayRule(CampaignRule):
    rule_type = "holiday_calendar"
    label = "Holiday & occasion calendar"
    priority = 9
    description = "Seasonal posts ahead of key dates (Christmas, Black Friday, Mother's Day, wellness days...)."
    config_model = HolidayConfig
    default_interval_seconds = 6 * 3600

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: HolidayConfig = rctx.config  # type: ignore[assignment]
        today = rctx.local_today()
        window_end = today + timedelta(days=config.lookahead_days)
        items: list[PlanItem] = []
        for occasion_day, occasion in upcoming_occasions(today, window_end, keys=config.occasions,
                                                         custom=config.custom_occasions):
            for before in sorted(set(config.days_before), reverse=True):
                post_day = occasion_day - timedelta(days=before)
                if post_day < today or post_day > today + timedelta(days=rctx.sctx.settings.schedule.plan_days_ahead):
                    continue
                key = f"holiday:{rctx.campaign_id}:{occasion.key}:{day_key(occasion_day)}:{before}"
                if await already_planned(rctx, key):
                    continue
                products = await pick_products(rctx, count=1, order="least_posted", allow_reposting=True) \
                    if config.feature_products else []
                item = make_item(
                    rctx, dedupe_key=key, content_type=config.content_type, products=products,
                    template_key=config.template_key, title=occasion.name,
                    texts={"occasion": occasion.name, "headline": occasion.name, "greeting": occasion.greeting,
                           "days_until": str(before), "theme": occasion.theme},
                )
                if await rctx.schedule_item_with_priority(item, day=post_day):
                    items.append(item)
        return items


class BrandContentConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kinds: list[Literal["quote", "fact", "poll", "quiz", "brand_awareness", "brand_value"]] = Field(
        default_factory=lambda: ["quote", "fact", "poll", "quiz"])
    posts_per_day: int = Field(1, ge=1, le=3)
    every_n_days: int = Field(1, ge=1, le=7)
    topic_hint: str = Field("", max_length=300)
    template_key: str = ""


@register_rule
class BrandContentRule(CampaignRule):
    rule_type = "brand_content"
    label = "Brand content (quotes, facts, polls, quizzes)"
    description = "Engagement posts written by Gemma: wellness quotes, facts, polls and quizzes."
    config_model = BrandContentConfig
    default_interval_seconds = 3 * 3600

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: BrandContentConfig = rctx.config  # type: ignore[assignment]
        items: list[PlanItem] = []
        for day in rctx.planning_days():
            if day.toordinal() % config.every_n_days:
                continue
            for index in range(config.posts_per_day):
                key = f"brand:{rctx.campaign_id}:{day_key(day)}:{index}"
                if await already_planned(rctx, key):
                    continue
                kind = config.kinds[(day.toordinal() + index) % len(config.kinds)]
                item = make_item(
                    rctx, dedupe_key=key, content_type=ContentType.QUOTE, products=[],
                    template_key=config.template_key, title=f"Brand {kind.replace('_', ' ')}",
                    texts={"brand_kind": kind, "topic_hint": config.topic_hint},
                )
                if rctx.schedule_item(item, day=day):
                    items.append(item)
        return items


class BlogSmartQConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    posts_per_week: int = Field(3, ge=1, le=14)
    blog_handles: list[str] = Field(default_factory=list)
    template_key: str = ""


@register_rule
class BlogSmartQRule(CampaignRule):
    rule_type = "blog_smartq"
    label = "Blog SmartQ"
    description = "Shares your Shopify blog articles, newest first, rotating once all are shared."
    config_model = BlogSmartQConfig
    default_interval_seconds = 6 * 3600

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        import shopify_client

        config: BlogSmartQConfig = rctx.config  # type: ignore[assignment]
        articles = await shopify_client.fetch_store_articles(rctx.sctx.store_config, limit_per_blog=50)
        if config.blog_handles:
            articles = [a for a in articles if a.blog_handle in set(config.blog_handles)]
        articles.sort(key=lambda a: a.published_at or "", reverse=True)
        if not articles:
            rctx.state["last_note"] = "No published blog articles found."
            return []
        shared = dict(rctx.state.get("shared_articles") or {})
        items: list[PlanItem] = []
        per_day = max(1, round(config.posts_per_week / 7)) if config.posts_per_week >= 7 else 1
        for day in rctx.planning_days():
            if config.posts_per_week < 7 and (day.toordinal() * config.posts_per_week) % 7 >= config.posts_per_week:
                continue
            for index in range(per_day):
                key = f"blog:{rctx.campaign_id}:{day_key(day)}:{index}"
                if await already_planned(rctx, key):
                    continue
                unshared = [a for a in articles if str(a.id) not in shared]
                if not unshared:  # rotate once every article has been shared
                    shared = {}
                    unshared = list(articles)
                article = unshared[0]
                item = make_item(
                    rctx, dedupe_key=key, content_type=ContentType.BLOG_ARTICLE, products=[],
                    template_key=config.template_key, title=article.title,
                    texts={"article_title": article.title, "article_url": article.article_url,
                           "article_image": article.image_url or "", "headline": article.title,
                           "excerpt": (article.summary_html or "")[:500]},
                    options={"link_override": article.article_url, "article_id": str(article.id)},
                )
                if rctx.schedule_item(item, day=day):
                    shared[str(article.id)] = int(rctx.now.timestamp())
                    items.append(item)
        rctx.state["shared_articles"] = shared
        return items


class EvergreenConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    min_age_days: int = Field(30, ge=7, le=365)
    posts_per_week: int = Field(2, ge=1, le=14)
    lookback_days: int = Field(180, ge=30, le=730)


@register_rule
class EvergreenRule(CampaignRule):
    rule_type = "evergreen"
    label = "Evergreen best-sellers"
    description = "Re-shares your best-performing posts (fresh caption and image) after a rest period."
    config_model = EvergreenConfig
    default_interval_seconds = 12 * 3600

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:
        config: EvergreenConfig = rctx.config  # type: ignore[assignment]
        since = int(rctx.now.timestamp()) - config.lookback_days * 86400
        top = await post_store.top_posts(rctx.store_id, since=since, limit=30)
        reposted = dict(rctx.state.get("reposted") or {})
        cutoff = int(rctx.now.timestamp()) - config.min_age_days * 86400
        items: list[PlanItem] = []
        week = rctx.local_today().isocalendar()
        week_key = f"{week.year}-W{week.week:02d}"
        planned_this_week = int((rctx.state.get("weekly") or {}).get(week_key, 0))
        for row in top:
            if planned_this_week >= config.posts_per_week:
                break
            if (row.get("published_at") or 0) > cutoff or reposted.get(row["post_id"], 0) > cutoff:
                continue
            products = await product_store.get_products(rctx.store_id, row.get("product_ids") or [])
            products = [p for p in products if p.get("available") and not p.get("deleted_at")]
            if not products and row.get("product_ids"):
                continue
            key = f"evergreen:{rctx.campaign_id}:{row['post_id']}:{week_key}"
            if await already_planned(rctx, key):
                continue
            item = make_item(rctx, dedupe_key=key, content_type=row["content_type"], products=products,
                             template_key=row.get("template_key") or "", title=f"Encore: {row.get('title', '')}",
                             options={"repost_of": row["post_id"], "image_offset": 1})
            if rctx.schedule_item(item, earliest=rctx.now + timedelta(hours=2)):
                reposted[row["post_id"]] = int(rctx.now.timestamp())
                planned_this_week += 1
                items.append(item)
        rctx.state["reposted"] = reposted
        rctx.state["weekly"] = {week_key: planned_this_week}
        return items
